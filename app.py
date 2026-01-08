# app.py
import os
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from checker import check_ticket_available

app = Flask(__name__)

LINE_NOTIFY_TOKEN = os.getenv("LINE_NOTIFY_TOKEN")
TARGET_DESC = os.getenv("TARGET_DESC", "春節返鄉車票")


# ========= LINE Notify =========
def line_notify(message: str):
    if not LINE_NOTIFY_TOKEN:
        print("⚠️ LINE_NOTIFY_TOKEN 未設定，略過通知")
        return

    url = "https://notify-api.line.me/api/notify"
    headers = {
        "Authorization": f"Bearer {LINE_NOTIFY_TOKEN}",
    }
    data = {"message": message}

    try:
        resp = requests.post(url, headers=headers, data=data, timeout=10)
        print("LINE Notify status:", resp.status_code, resp.text)
    except Exception as e:
        print("LINE Notify 發送失敗：", e)


# ========= 排程工作 =========
def job_check_and_notify():
    print("[job] 開始檢查票況...")
    try:
        if check_ticket_available():
            line_notify(f"🔥 搶票機器人：偵測到有票，可以上官網確認！")
        else:
            print("[job] 目前沒有票")
    except Exception as e:
        print("[job] 發生錯誤：", e)


scheduler = BackgroundScheduler(daemon=True)
# 每 5 分鐘跑一次；要改頻率在這裡調整
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()


# ========= Flask Routes =========
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/manual-check")
def manual_check():
    """
    用瀏覽器 / curl 打這個路徑，可以手動觸發一次檢查
    """
    job_check_and_notify()
    return jsonify({"status": "triggered"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
