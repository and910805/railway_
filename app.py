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

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # 沒有 task.line_user_id 時的 fallback
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
    """
    主動推播訊息
    - 若沒傳 to_user_id：就用 LINE_TARGET_USER_ID
    """
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過 push", flush=True)
        return

    if not to_user_id:
        if not LINE_TARGET_USER_ID:
            print("⚠️ 沒有指定 userId，也沒設定 LINE_TARGET_USER_ID，略過推播", flush=True)
            return
        to_user_id = LINE_TARGET_USER_ID

    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": [{"type": "text", "text": message}]}

    print(">>> SENDING PUSH TO", to_user_id, flush=True)
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("push status:", resp.status_code, resp.text[:200], flush=True)
    except Exception as e:
        print("push error:", e, flush=True)


def get_line_profile(user_id: str) -> dict | None:
    """拿 displayName 用（可選）"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return None
    url = "https://api.line.me/v2/bot/profile/" + user_id
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print("get_line_profile error:", e, flush=True)
    return None


# ========= 排程工作 =========
def job_check_and_notify():
    print("[job] 開始檢查所有任務票況...", flush=True)
    today = datetime.date.today().strftime("%Y-%m-%d")

    try:
        # ✅ 修：db_tasks 需要 today_yyyymmdd（就算你忘了帶，db_tasks 也有 default 了）
        expire_past_tasks(today)

        tasks = get_active_future_tasks(today)
        print("[job] active future tasks =", len(tasks), flush=True)

        cooldown_minutes = int(os.getenv("NOTIFY_COOLDOWN_MINUTES", "10"))
        now_dt = datetime.datetime.now()

        for task in tasks:
            # ✅ 防止一直重複推播：上次通知後冷卻 N 分鐘
            last = task.get("last_notify_at")
            if last and cooldown_minutes > 0:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    if (now_dt - last_dt).total_seconds() < cooldown_minutes * 60:
                        print(f"[job] task #{task['id']} 最近已通知({last})，冷卻中，跳過", flush=True)
                        continue
                except Exception:
                    pass

            try:
                has_ticket = check_task_has_ticket(task)
            except Exception as e:
                print("[job] task #%s error: %s" % (task["id"], e), flush=True)
                continue

            if has_ticket:
                msg = (
                    "🔥 台鐵可能有票！\n"
                    f"{task['description']}\n"
                    f"日期：{task['ride_date']}\n"
                    f"時間：{task['start_time']}~{task['end_time']}\n"
                    f"車次關鍵字：{task['train_keyword']}  "
                    f"(餘座 ≥ {task['min_seats']})"
                )
                line_push(msg, to_user_id=task.get("line_user_id"))
                mark_notified(task["id"])
            else:
                print("[job] task #%s 目前沒有符合票" % task["id"], flush=True)

    except Exception as e:
        print("[job] 發生錯誤：", e, flush=True)


scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()
print(f"Scheduler started: every {interval_minutes} minutes.", flush=True)


# ========= 一般 Routes =========
@app.get("/")
def index():
    return "ok", 200


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/manual-check")
def manual_check():
    """手動觸發一次檢查"""
    job_check_and_notify()
    return jsonify({"ok": True})


@app.get("/test-push")
def test_push():
    """
    強制測試推播（用 LINE_TARGET_USER_ID 或指定 userId）。
    """
    line_push("🔔 測試訊息：來自台鐵搶票機器人 test-push")
    return jsonify({"status": "test-push-called"})


# ========= 共用的 LINE events 處理 =========
def process_line_events(body):
    """
    指令範例（空白分隔）：
      新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00
      list
      help
    """
    if not body or "events" not in body:
        return

    for ev in body.get("events", []):
        if ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        if msg.get("type") != "text":
            continue

        text = (msg.get("text") or "").strip()
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")

        if not reply_token:
            continue

        if text.lower() in ("help", "幫助", "說明"):
            help_msg = (
                "📌 指令說明：\n\n"
                "1) 新增任務（空白分隔，共 8 段）\n"
                "新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00\n\n"
                "2) 列出任務：list\n"
            )
            line_reply(reply_token, help_msg)
            continue

        if text.lower() in ("list", "列表"):
            _handle_list_tasks(user_id, reply_token)
            continue

        if text.startswith("新增"):
            _handle_create_task(user_id, reply_token, text)
            continue

        line_reply(reply_token, "看不懂指令，輸入 help 看用法。")


def _handle_create_task(user_id, reply_token, text: str):
    """
    新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00
    """
    if not user_id:
        if reply_token:
            line_reply(reply_token, "找不到 userId，無法建立任務 QQ")
        return

    try:
        parts = text.split()
        if len(parts) != 8:
            raise ValueError("parts length mismatch")

        _, ride_date, start_station, end_station, train_kw, min_seats, start_time, end_time = parts

        profile = get_line_profile(user_id)
        display_name = None
        if profile and "displayName" in profile:
            display_name = profile["displayName"]

        description = f"{start_station} → {end_station} {ride_date} {start_time}-{end_time} {train_kw} >= {min_seats}"

        task = create_task(
            line_user_id=user_id,
            line_display_name=display_name,
            description=description,
            ride_date=ride_date,
            start_station=start_station,
            end_station=end_station,
            start_time=start_time,
            end_time=end_time,
            train_keyword=train_kw,
            min_seats=int(min_seats),
        )

        ok_msg = (
            "✅ 已建立監控任務！\n"
            f"[#{task['id']}] {task['ride_date']} {task['start_time']}~{task['end_time']}\n"
            f"{task['start_station']} → {task['end_station']}\n"
            f"車次關鍵字：{task['train_keyword']} / 餘座門檻：{task['min_seats']}"
        )
        line_reply(reply_token, ok_msg)

    except Exception as e:
        print("create_task error:", e, flush=True)
        if reply_token:
            help_msg = (
                "建立任務失敗，格式可能錯誤 QQ\n\n"
                "請用下面格式再試一次：\n"
                "新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00\n"
                "（中間用空白分開）"
            )
            line_reply(reply_token, help_msg)


def _handle_list_tasks(user_id, reply_token):
    """列出目前這個 user 的監控任務"""
    if not user_id:
        if reply_token:
            line_reply(reply_token, "找不到 userId，無法查詢 QQ")
        return

    today = datetime.date.today().strftime("%Y-%m-%d")
    tasks = get_active_future_tasks(today)
    my_tasks = [t for t in tasks if t.get("line_user_id") == user_id]

    if not my_tasks:
        line_reply(reply_token, "目前你沒有任何監控任務。")
        return

    lines = ["📋 目前監控的任務："]
    for t in my_tasks:
        line = (
            f"[#{t['id']}] {t['ride_date']} {t['start_time']}~{t['end_time']}\n"
            f"    {t['start_station']} → {t['end_station']}\n"
            f"    車次關鍵字：{t['train_keyword']}  "
            f"餘座門檻：{t['min_seats']} 張\n"
        )
        lines.append(line)

    msg = "\n".join(lines)
    line_reply(reply_token, msg)


# ========= Webhook =========
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "ok", 200

    body = request.get_json(silent=True) or {}
    process_line_events(body)
    return jsonify({"ok": True})


@app.route("/webhook-debug", methods=["POST", "GET"])
def webhook_debug():
    if request.method == "GET":
        return "ok", 200

    body = request.get_json(silent=True) or {}
    print("=== /webhook-debug body ===", flush=True)
    print(body, flush=True)
    process_line_events(body)
    return jsonify({"ok": True})
