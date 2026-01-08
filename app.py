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
from checker import check_task_has_ticket, check_ticket_available

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # 預設推播對象（可選）
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


def line_push(message: str, to_user_id: str | None = None):
    """
    推播訊息給指定 user_id。
    如果沒給，就用環境變數的 LINE_TARGET_USER_ID（方便你自己測試用）。
    """
    print(">>> line_push() called", flush=True)

    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過推播", flush=True)
        return

    if not to_user_id:
        if not LINE_TARGET_USER_ID:
            print("⚠️ 沒有指定 userId，也沒設定 LINE_TARGET_USER_ID，略過推播", flush=True)
            return
        to_user_id = LINE_TARGET_USER_ID

    url = "https://api.line.me/v2/bot/message/push"
    body = {
        "to": to_user_id,
        "messages": [{"type": "text", "text": message}],
    }

    print(f">>> SENDING PUSH TO {to_user_id} ...", flush=True)
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("LINE push status:", resp.status_code, resp.text, flush=True)
    except Exception as e:
        print("LINE push 發送失敗：", e, flush=True)


def line_reply(reply_token: str, text: str):
    """回覆使用者訊息"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過 reply", flush=True)
        return

    url = "https://api.line.me/v2/bot/message/reply"
    body = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("LINE reply status:", resp.status_code, resp.text, flush=True)
    except Exception as e:
        print("LINE reply 失敗：", e, flush=True)


def get_line_profile(user_id: str) -> dict | None:
    """拿使用者 profile（主要是 displayName），失敗就回 None"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return None
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
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
        # 1) 先把搭車日已過的任務關閉
        expire_past_tasks(today)

        # 2) 取出所有尚未過期 & is_active=1 的任務
        tasks = get_active_future_tasks(today)
        print(f"[job] active future tasks = {len(tasks)}", flush=True)

        for task in tasks:
            try:
                has_ticket = check_task_has_ticket(task)
            except Exception as e:
                print(f"[job] task #{task['id']} error:", e, flush=True)
                continue

            if has_ticket:
                msg = (
                    f"🔥 台鐵有票啦！\n"
                    f"{task['description']}\n"
                    f"日期：{task['ride_date']}\n"
                    f"時間：{task['start_time']}~{task['end_time']}\n"
                    f"車次關鍵字：{task['train_keyword']}  "
                    f"(餘座 ≥ {task['min_seats']})"
                )
                line_push(msg, to_user_id=task["line_user_id"])
                mark_notified(task["id"])
            else:
                print(f"[job] task #{task['id']} 目前沒有符合票", flush=True)

    except Exception as e:
        print("[job] 發生錯誤：", e, flush=True)


scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()


# ========= Routes =========
@app.get("/")
def index():
    return "ok", 200


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/manual-check")
def manual_check():
    """
    手動觸發一次 job（方便你在瀏覽器按一下就跑一次排程邏輯）。
    """
    job_check_and_notify()
    return jsonify({"status": "triggered"})


@app.get("/test-push")
def test_push():
    """
    強制測試推播（用 LINE_TARGET_USER_ID 或指定 userId）。
    """
    line_push("🔔 測試訊息：來自 railway 搶票機器人 test-push")
    return jsonify({"status": "test-push-called"})


# webhook-debug：純 debug 用，會把 body 印到 log
@app.route("/webhook-debug", methods=["GET", "POST"])
def webhook_debug():
    try:
        data = request.get_json(force=True, silent=True)
        print("=== webhook-debug body ===", flush=True)
        print(data, flush=True)
    except Exception as e:
        print("webhook_debug error:", e, flush=True)
    return "ok", 200


# ========= LINE Bot Webhook =========
@app.route("/webhook", methods=["POST"])
def webhook():
    """
    給 LINE Messaging API 用的 webhook。
    目前支援：
      - 「新增 任務」指令：
        格式：
          新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00

        對應：
          搭車日   出發站     抵達站     車次關鍵字 最少座位 起始時間 結束時間
    """
    body = request.get_json(force=True, silent=True)
    print("=== /webhook body ===", flush=True)
    print(body, flush=True)

    if not body or "events" not in body:
        return "ok", 200

    for event in body["events"]:
        event_type = event.get("type")
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId")

        # 只處理 message -> text
        if event_type == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"]["text"].strip()

            # 指令：新增任務
            if text.startswith("新增") or text.lower().startswith("add "):
                _handle_add_task_command(text, user_id, reply_token)
                continue

            # 其他訊息回個簡單提示
            help_msg = (
                "嗨～我是台鐵搶票機器人 🤖\n\n"
                "建立新任務格式：\n"
                "新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00\n\n"
                "說明：\n"
                "  日期 出發站 抵達站 車次關鍵字 最少座位 起始時間 結束時間\n"
            )
            line_reply(reply_token, help_msg)

    return "ok", 200


def _handle_add_task_command(text: str, user_id: str | None, reply_token: str | None):
    """
    解析「新增」指令，寫入 SQLite。
    指令範例：
      新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00
    """
    if not user_id:
        if reply_token:
            line_reply(reply_token, "找不到 userId，無法建立任務 QQ")
        return

    try:
        # 把「新增」/「add」切掉
        parts = text.split()
        if len(parts) != 8:
            raise ValueError("parts length mismatch")

        _, ride_date, start_station, end_station, train_kw, min_seats, start_time, end_time = parts

        # 拿一下 displayName（失敗就算了）
        profile = get_line_profile(user_id)
        display_name = None
        if profile and isinstance(profile, dict):
            display_name = profile.get("displayName")

        description = (
            f"{start_station} → {end_station} {ride_date} "
            f"{start_time}~{end_time} {train_kw} {min_seats}張以上"
        )

        task_id = create_task(
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

        msg = (
            f"✅ 已建立任務 #{task_id}\n"
            f"{description}\n\n"
            f"之後每 {os.getenv('CHECK_INTERVAL_MINUTES', '5')} 分鐘會幫你查票，"
            f"有符合條件就會通知你喔～"
        )
        if reply_token:
            line_reply(reply_token, msg)
        else:
            line_push(msg, to_user_id=user_id)

    except Exception as e:
        print("handle_add_task error:", e, flush=True)
        if reply_token:
            help_msg = (
                "建立任務失敗，格式可能錯誤 QQ\n\n"
                "請用下面格式再試一次：\n"
                "新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00\n"
                "（中間用空白分開）"
            )
            line_reply(reply_token, help_msg)


# ========= main =========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
