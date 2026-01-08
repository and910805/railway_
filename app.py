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
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # 預設測試用
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


def line_push(message, to_user_id=None):
    """
    推播訊息給指定 user_id。
    如果沒指定，就用 LINE_TARGET_USER_ID（方便你自己測試）。
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

    print(">>> SENDING PUSH TO", to_user_id, flush=True)
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("LINE push status:", resp.status_code, resp.text, flush=True)
    except Exception as e:
        print("LINE push 發送失敗：", e, flush=True)


def line_reply(reply_token, text):
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


def get_line_profile(user_id):
    """拿使用者 profile（displayName 用），失敗就回 None"""
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
        # 1) 先把搭車日已過的任務關閉
        expire_past_tasks(today)

        # 2) 取出所有尚未過期 & is_active=1 的任務
        tasks = get_active_future_tasks(today)
        print("[job] active future tasks =", len(tasks), flush=True)

        for task in tasks:
            try:
                has_ticket = check_task_has_ticket(task)
            except Exception as e:
                print("[job] task #%s error: %s" % (task["id"], e), flush=True)
                continue

            if has_ticket:
                msg = (
                    "🔥 台鐵有票啦！\n"
                    f"{task['description']}\n"
                    f"日期：{task['ride_date']}\n"
                    f"時間：{task['start_time']}~{task['end_time']}\n"
                    f"車次關鍵字：{task['train_keyword']}  "
                    f"(餘座 ≥ {task['min_seats']})"
                )
                line_push(msg, to_user_id=task["line_user_id"])
                mark_notified(task["id"])
            else:
                print("[job] task #%s 目前沒有符合票" % task["id"], flush=True)

    except Exception as e:
        print("[job] 發生錯誤：", e, flush=True)


scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()


# ========= 一般 Routes =========
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


# ========= 共用的 LINE events 處理 =========
def process_line_events(body):
    """
    共用的事件處理邏輯：
      - 新增 任務
      - 列出 / list 任務
      - 其他文字 → 傳回說明
    """
    if not body or "events" not in body:
        return

    for event in body["events"]:
        event_type = event.get("type")
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId")

        if event_type == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"]["text"].strip()

            # 1) 新增任務指令
            if text.startswith("新增") or text.lower().startswith("add "):
                _handle_add_task_command(text, user_id, reply_token)
                continue

            # 2) 列出任務
            if (
                text.lower() == "list"
                or text.startswith("列表")
                or text.startswith("查詢任務")
                or text.startswith("任務列表")
            ):
                _handle_list_tasks(user_id, reply_token)
                continue

            # 3) 其他訊息 → 回說明
            help_msg = (
                "嗨～我是台鐵搶票機器人 🤖\n\n"
                "✅ 建立新任務格式：\n"
                "新增 2026/01/27 1008-台北 4220-高雄 自強135 2 06:00 12:00\n"
                "  日期 出發站 抵達站 車次關鍵字 最少座位 起始時間 結束時間\n\n"
                "✅ 查看目前監控任務：\n"
                "  傳送：list\n"
                "  或：列表 / 查詢任務 / 任務列表\n"
            )
            if reply_token:
                line_reply(reply_token, help_msg)


def _handle_add_task_command(text, user_id, reply_token):
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
        parts = text.split()
        if len(parts) != 8:
            raise ValueError("parts length mismatch")

        _, ride_date, start_station, end_station, train_kw, min_seats, start_time, end_time = parts

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


def _handle_list_tasks(user_id, reply_token):
    """列出目前這個 user 的監控任務"""
    if not user_id:
        if reply_token:
            line_reply(reply_token, "找不到 userId，無法查詢任務 QQ")
        return

    today = datetime.date.today().strftime("%Y-%m-%d")
    tasks = get_active_future_tasks(today)

    # 只顯示自己的任務
    my_tasks = [t for t in tasks if t["line_user_id"] == user_id]

    if not my_tasks:
        msg = "目前沒有為你監控中的任務喔～\n可以用「新增 ...」來建立一個。"
        if reply_token:
            line_reply(reply_token, msg)
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
    if reply_token:
        line_reply(reply_token, msg)


# ========= Webhook Routes =========
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    """
    給 LINE 官方設定的 webhook。
    GET 的時候只回 ok（有些工具會用 GET 測試）。
    """
    if request.method == "GET":
        return "ok", 200

    body = request.get_json(force=True, silent=True)
    print("=== /webhook body ===", flush=True)
    print(body, flush=True)

    process_line_events(body)
    return "ok", 200


@app.route("/webhook-debug", methods=["POST", "GET"])
def webhook_debug():
    """
    你如果 LINE 後台現在是指到 /webhook-debug，
    也會走同一套處理（順便印 log）。
    """
    if request.method == "GET":
        return "ok", 200

    body = request.get_json(force=True, silent=True)
    print("=== /webhook-debug body ===", flush=True)
    print(body, flush=True)

    process_line_events(body)
    return "ok", 200


# ========= main =========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
