# app.py
import os
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

from checker import check_ticket_available

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")
TARGET_DESC = os.getenv("TARGET_DESC", "春節返鄉車票")

print("=== DEBUG ENV AT STARTUP ===", flush=True)
print("LINE_CHANNEL_ACCESS_TOKEN set?:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("LINE_TARGET_USER_ID:", LINE_TARGET_USER_ID, flush=True)
print("TARGET_DESC:", TARGET_DESC, flush=True)
print("============================", flush=True)


# ========= LINE Messaging API PUSH =========
def line_push(message: str):
    print(">>> line_push() called", flush=True)

    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過推播", flush=True)
        return
    if not LINE_TARGET_USER_ID:
        print("⚠️ LINE_TARGET_USER_ID 未設定，略過推播", flush=True)
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "to": LINE_TARGET_USER_ID,
        "messages": [{"type": "text", "text": message}],
    }

    print(">>> SENDING REQUEST TO LINE...", flush=True)
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        print("LINE push status:", resp.status_code, resp.text, flush=True)
    except Exception as e:
        print("LINE push 發送失敗：", e, flush=True)


# ========= 排程工作 =========
def job_check_and_notify():
    print("[job] 開始檢查票況...", flush=True)
    try:
        if check_ticket_available():
            line_push(f"🔥 搶票機器人：偵測到有票，可以上官網確認！")
        else:
            print("[job] 目前沒有票", flush=True)
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
    # 這個還是照原本流程：先檢查有沒有票，再通知
    job_check_and_notify()
    return jsonify({"status": "triggered"})


# ✅ 強制推播測試：一定會叫 line_push()
@app.get("/test-push")
def test_push():
    line_push("🔔 測試訊息：來自 railway 搶票機器人 test-push")
    return jsonify({"status": "test-push-called"})


# webhook-debug：拿 userId 用的
@app.route("/webhook-debug", methods=["GET", "POST"])
def webhook_debug():
    try:
        data = request.get_json(force=True, silent=True)
        print("=== webhook body ===", flush=True)
        print(data, flush=True)
    except Exception as e:
        print("webhook_debug error:", e, flush=True)
    return "ok", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
