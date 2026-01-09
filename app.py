# app.py
import os
import datetime
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

from db_tasks import (
    create_task,
    expire_past_tasks,
    get_active_future_tasks,
    mark_notified,
)
from checker import check_task_has_ticket

app = Flask(__name__)

# 環境變數設定 (請在 Zeabur 後台設定)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # Fallback ID
TARGET_DESC = os.getenv("TARGET_DESC", "台鐵搶票機器人")

print("=== DEBUG ENV AT STARTUP ===", flush=True)
print("LINE_CHANNEL_ACCESS_TOKEN set?:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("LINE_TARGET_USER_ID:", LINE_TARGET_USER_ID, flush=True)
print("TARGET_DESC:", TARGET_DESC, flush=True)
print("============================", flush=True)


# ========= LINE API 基本封裝 =========
def _line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def line_reply(reply_token: str, message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過 reply", flush=True)
        return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("reply status:", resp.status_code, resp.text[:200], flush=True)
    except Exception as e:
        print("reply error:", e, flush=True)


def line_push(message: str, to_user_id: str | None = None):
    """主動推播訊息"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    
    to_user_id = to_user_id or LINE_TARGET_USER_ID
    if not to_user_id:
        return

    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=_line_headers(), json=body, timeout=10)
    except Exception as e:
        print("push error:", e, flush=True)


def get_line_profile(user_id: str) -> dict | None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return None
    url = "https://api.line.me/v2/bot/profile/" + user_id
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except:
        return None


# ========= 排程工作 ( job_check_and_notify ) =========
def job_check_and_notify():
    print(f"⏰ {datetime.datetime.now()} [job] 開始巡邏票況...", flush=True)
    today = datetime.date.today().strftime("%Y-%m-%d")

    try:
        expire_past_tasks(today)
        tasks = get_active_future_tasks(today)
        print(f"[job] 掃描中... 目前共有 {len(tasks)} 個活動任務", flush=True)

        cooldown_minutes = int(os.getenv("NOTIFY_COOLDOWN_MINUTES", "10"))
        now_dt = datetime.datetime.now()

        for task in tasks:
            # 防止重複推播
            last = task.get("last_notify_at")
            if last and cooldown_minutes > 0:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    if (now_dt - last_dt).total_seconds() < cooldown_minutes * 60:
                        continue
                except:
                    pass

            if check_task_has_ticket(task):
                msg = (
                    "🔥 臭寶（吳芃秀）快訂票！\n"
                    f"符合條件的票出現了：\n{task['description']}\n"
                    f"日期：{task['ride_date']}\n"
                    f"時間：{task['start_time']}~{task['end_time']}\n"
                    f"餘座門檻：{task['min_seats']}"
                )
                line_push(msg, to_user_id=task.get("line_user_id"))
                mark_notified(task["id"])

    except Exception as e:
        print("[job] 發生錯誤：", e, flush=True)


scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "10"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()


# ========= 一般 Routes (保留妳所有原本的功能) =========
@app.route("/")
def index():
    return f"🚀 {TARGET_DESC} 運行中！", 200

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/manual-check")
def manual_check():
    job_check_and_notify()
    return jsonify({"ok": True})

@app.route("/test-push")
def test_push():
    line_push("🔔 測試訊息：臭寶妳好！這是測試推播。")
    return jsonify({"status": "test-push-called"})


# ========= LINE Event 處理 (包含 10 小時防呆) =========
def process_line_events(body):
    if not body or "events" not in body: return

    for ev in body.get("events", []):
        if ev.get("type") != "message": continue
        msg = ev.get("message") or {}
        if msg.get("type") != "text": continue

        text = (msg.get("text") or "").strip()
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")

        if text.lower() in ("help", "幫助", "說明"):
            help_msg = "📌 指令說明：\n\n1) 新增任務\n新增 2026/01/27 1000-臺北 4400-高雄 * 1 06:00 12:00\n\n2) 列出任務：list"
            line_reply(reply_token, help_msg)
            continue

        if text.lower() in ("list", "列表"):
            _handle_list_tasks(user_id, reply_token)
            continue

        if text.startswith("新增"):
            _handle_create_task(user_id, reply_token, text)
            continue

def _handle_create_task(user_id, reply_token, text: str):
    if not user_id: return

    try:
        parts = text.split()
        if len(parts) != 8: raise ValueError
        _, ride_date, start_station, end_station, train_kw, min_seats, start_time, end_time = parts

        # 🚀 臭寶專屬防呆：10 小時檢查
        h1 = int(start_time.split(':')[0])
        h2 = int(end_time.split(':')[0])
        if (h2 - h1) > 10:
            line_reply(reply_token, "⚠️ 台鐵官網限制時段不能超過 10 小時喔！請改短一點。")
            return

        profile = get_line_profile(user_id)
        display_name = profile.get("displayName") if profile else None
        description = f"{start_station} → {end_station} {ride_date} {start_time}-{end_time} {train_kw}"

        task = create_task(user_id, display_name, description, ride_date, start_station, end_station, start_time, end_time, train_kw, min_seats)
        line_reply(reply_token, f"✅ 已建立任務！[#{task['id']}] {description}")
    except:
        line_reply(reply_token, "❌ 格式錯誤！範例：\n新增 2026/01/27 1000-臺北 4400-高雄 * 1 06:00 12:00")

def _handle_list_tasks(user_id, reply_token):
    today = datetime.date.today().strftime("%Y-%m-%d")
    tasks = [t for t in get_active_future_tasks(today) if t.get("line_user_id") == user_id]
    if not tasks:
        line_reply(reply_token, "目前沒有監控中的任務。")
        return
    msg = "📋 監控列表：\n" + "\n".join([f"[#{t['id']}] {t['description']}" for t in tasks])
    line_reply(reply_token, msg)


# ========= Webhook =========
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET": return "ok", 200
    body = request.get_json(silent=True) or {}
    process_line_events(body)
    return jsonify({"ok": True})

@app.route("/webhook-debug", methods=["POST", "GET"])
def webhook_debug():
    if request.method == "GET": return "ok", 200
    body = request.get_json(silent=True) or {}
    print("=== DEBUG WEBHOOK ===", body, flush=True)
    process_line_events(body)
    return jsonify({"ok": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))