# app.py
import os
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

from checker import check_ticket_available

app = Flask(__name__)

# 用官方機器人的 Messaging API
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")
TARGET_DESC = os.getenv("TARGET_DESC", "春節返鄉車票")


# ========= LINE Messaging API PUSH =========
def line_push(message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過推播")
        return
    if not LINE_TARGET_USER_ID:
        print("⚠️ LINE_TARGET_USER_ID 未設定，略過推播")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "to": LINE_TARGET_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message,
            }
        ],
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        print("LINE push status:", resp.status_code, resp.text)
    except Exception as e:
        print("LINE push 發送失敗：", e)


# ========= 排程工作 =========
def job_check_and_notify():
    print("[job] 開始檢查票況...")
    try:
        if check_ticket_available():
            line_push(f"🔥 搶票機器人：偵測到有票，可以上官網確認！")
        else:
            print("[job] 目前沒有票")
    except Exception as e:
        print("[job] 發生錯誤：", e)


scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()


# ========= 基本 Route =========
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/manual-check")
def manual_check():
    job_check_and_notify()
    return jsonify({"status": "triggered"})


# （可選）用來抓 userId 的 webhook debug
@app.post("/webhook-debug")
def webhook_debug():
    data = request.get_json(force=True, silent=True) or {}
    print("=== webhook body ===")
    print(data)
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
