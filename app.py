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
    delete_task,  # 🚀 這裡新增了匯入
)
from checker import check_task_has_ticket

app = Flask(__name__)

# 環境變數設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  
TARGET_DESC = os.getenv("TARGET_DESC", "台鐵搶票機器人")

# ========= LINE API 基本封裝 =========
def _line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def line_reply(reply_token: str, message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=_line_headers(), json=body, timeout=10)
    except Exception as e:
        print(f"reply error: {e}", flush=True)

def line_push(message: str, to_user_id: str | None = None):
    to_user_id = to_user_id or LINE_TARGET_USER_ID
    if not LINE_CHANNEL_ACCESS_TOKEN or not to_user_id: return
    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=_line_headers(), json=body, timeout=10)
    except Exception as e:
        print(f"push error: {e}", flush=True)

def get_line_profile(user_id: str) -> dict | None:
    if not LINE_CHANNEL_ACCESS_TOKEN: return None
    url = "https://api.line.me/v2/bot/profile/" + user_id
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except: return None

# ========= 排程工作 =========
def job_check_and_notify():
    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        expire_past_tasks(today)
        tasks = get_active_future_tasks(today)
        cooldown_minutes = int(os.getenv("NOTIFY_COOLDOWN_MINUTES", "10"))
        now_dt = datetime.datetime.now()

        for task in tasks:
            last = task.get("last_notify_at")
            if last and cooldown_minutes > 0:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    if (now_dt - last_dt).total_seconds() < cooldown_minutes * 60:
                        continue
                except: pass

            if check_task_has_ticket(task):
                msg = f"🔥 臭寶（吳芃秀）快訂票！\n符合條件的票出現了：\n{task['description']}"
                line_push(msg, to_user_id=task.get("line_user_id"))
                mark_notified(task["id"])
    except Exception as e:
        print(f"job error: {e}", flush=True)

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(job_check_and_notify, "interval", minutes=int(os.getenv("CHECK_INTERVAL_MINUTES", "10")))
scheduler.start()

# ========= LINE Event 處理 =========
def process_line_events(body):
    if not body or "events" not in body: return
    for ev in body.get("events", []):
        if ev.get("type") != "message" or ev["message"].get("type") != "text": continue
        
        text = ev["message"]["text"].strip()
        reply_token = ev["replyToken"]
        user_id = ev["source"].get("userId")

        # 🚀 這裡更新了 Help 訊息
        if text.lower() in ("help", "幫助", "說明"):
            help_msg = "📌 指令說明：\n\n1) 新增任務\n新增 2026/01/27 1000-臺北 4400-高雄 * 1 06:00 12:00\n\n2) 列出任務：list\n\n3) 刪除任務：刪除 ID\n範例：刪除 5"
            line_reply(reply_token, help_msg)
            continue

        if text.lower() in ("list", "列表"):
            _handle_list_tasks(user_id, reply_token)
            continue

        if text.startswith("新增"):
            _handle_create_task(user_id, reply_token, text)
            continue

        # 🚀 這裡新增了刪除指令的判斷
        if text.startswith("刪除"):
            _handle_delete_task(user_id, reply_token, text)
            continue

def _handle_create_task(user_id, reply_token, text: str):
    if not user_id: return
    try:
        parts = text.split()
        if len(parts) != 8: raise ValueError
        _, ride_date, start_station, end_station, train_kw, min_seats, start_time, end_time = parts

        # 10 小時防呆
        if (int(end_time.split(':')[0]) - int(start_time.split(':')[0])) > 10:
            line_reply(reply_token, "⚠️ 台鐵官網限制時段不能超過 10 小時喔！請改短一點。")
            return

        profile = get_line_profile(user_id)
        display_name = profile.get("displayName") if profile else None
        description = f"{start_station} → {end_station} {ride_date} {start_time}-{end_time} {train_kw}"

        task = create_task(user_id, display_name, description, ride_date, start_station, end_station, start_time, end_time, train_keyword=train_kw, min_seats=min_seats)
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

# 🚀 這裡新增了刪除邏輯的函式
def _handle_delete_task(user_id, reply_token, text: str):
    if not user_id: return
    try:
        parts = text.split()
        if len(parts) < 2: raise ValueError
        task_id = int(parts[1])
        # 調用 db_tasks.py 裡的 delete_task
        if delete_task(task_id, user_id):
            line_reply(reply_token, f"🗑️ 任務 #{task_id} 已成功刪除！列表清爽多了。")
        else:
            line_reply(reply_token, f"❌ 找不到任務 #{task_id}，或者是那不是妳建立的喔。")
    except:
        line_reply(reply_token, "❌ 格式錯誤！範例：刪除 5")

# ========= Webhook =========
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET": return "ok", 200
    body = request.get_json(silent=True) or {}
    process_line_events(body)
    return jsonify({"ok": True})

@app.route("/")
def index(): return f"🚀 {TARGET_DESC} 運行中！", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))