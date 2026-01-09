# app.py - 臭寶（吳芃秀）專屬：1/27 台北高雄保衛戰終極版
import os
import datetime
import requests
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

# 從妳設計的 db_tasks 匯入所有功能
from db_tasks import (
    create_task,
    expire_past_tasks,
    get_active_future_tasks,
    mark_notified,
    delete_task,  # 🚀 新增功能：刪除任務
)
from checker import check_task_has_ticket # 🚀 核心引擎

app = Flask(__name__)

# 讀取環境變數 (Zeabur 設定)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # 推播備用 ID
TARGET_DESC = os.getenv("TARGET_DESC", "台鐵搶票機器人")

# 啟動時的偵錯資訊，確保臭咘咘沒設定錯
print("=== 🚀 臭寶專屬系統啟動中 ===", flush=True)
print("LINE_TOKEN 設定狀態:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("LINE_TARGET_USER_ID:", LINE_TARGET_USER_ID, flush=True)
print("目標名稱:", TARGET_DESC, flush=True)
print("============================", flush=True)

# ========= LINE API 基礎零件 (完全還原) =========
def _line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def line_reply(reply_token: str, message: str):
    """回覆使用者訊息"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ 未設定 TOKEN，無法回覆", flush=True)
        return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("Reply 狀態:", resp.status_code, flush=True)
    except Exception as e:
        print("Reply 錯誤:", e, flush=True)

def line_push(message: str, to_user_id: str | None = None):
    """主動推播訊息給臭寶"""
    if not LINE_CHANNEL_ACCESS_TOKEN: return
    target = to_user_id or LINE_TARGET_USER_ID
    if not target: return

    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": target, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print(f">>> 推播成功至 {target}: {resp.status_code}", flush=True)
    except Exception as e:
        print("Push 錯誤:", e, flush=True)

def get_line_profile(user_id: str) -> dict | None:
    """獲取臭寶的 LINE 顯示名稱"""
    if not LINE_CHANNEL_ACCESS_TOKEN: return None
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except: return None

# ========= 排程核心 (完全還原並強化) =========
def job_check_and_notify():
    """自動巡邏任務"""
    print(f"⏰ {datetime.datetime.now()} [巡邏] 開始檢查任務...", flush=True)
    today = datetime.date.today().strftime("%Y-%m-%d")

    try:
        expire_past_tasks(today) # 過期清理
        tasks = get_active_future_tasks(today) # 獲取未來任務
        print(f"[巡邏] 目前共有 {len(tasks)} 個任務待檢查", flush=True)

        cooldown_minutes = int(os.getenv("NOTIFY_COOLDOWN_MINUTES", "10"))
        now_dt = datetime.datetime.now()

        for task in tasks:
            # 檢查冷卻，避免狂叮咚
            last = task.get("last_notify_at")
            if last and cooldown_minutes > 0:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    if (now_dt - last_dt).total_seconds() < cooldown_minutes * 60:
                        continue
                except: pass

            # 執行查票引擎
            if check_task_has_ticket(task):
                msg = (
                    "🔥 臭寶（吳芃秀）快訂票！\n"
                    f"發現有符合條件的票：\n{task['description']}\n"
                    f"日期：{task['ride_date']}\n"
                    f"時段：{task['start_time']}~{task['end_time']}\n"
                    f"餘座 ≥ {task['min_seats']} 張"
                )
                line_push(msg, to_user_id=task.get("line_user_id"))
                mark_notified(task["id"])
            else:
                print(f"[巡邏] 任務 #{task['id']} 目前無票", flush=True)
    except Exception as e:
        print(f"巡邏出錯: {e}", flush=True)

# 排程器設定 (5~10分鐘一次)
scheduler = BackgroundScheduler(daemon=True)
interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "10"))
scheduler.add_job(job_check_and_notify, "interval", minutes=interval_minutes)
scheduler.start()

# ========= 所有的 API Routes (完全還原) =========
@app.route("/")
def index(): return f"🚀 {TARGET_DESC} 運行中！", 200

@app.route("/health")
def health(): return jsonify({"status": "ok"})

@app.route("/manual-check")
def manual_check():
    """手動觸發一輪巡邏"""
    job_check_and_notify()
    return jsonify({"ok": True})

@app.route("/test-push")
def test_push():
    """測試 LINE 推播是否正常"""
    line_push("🔔 測試：臭寶妳好！這是來自雲端查票機的問候。")
    return jsonify({"status": "test-push-called"})

# ========= LINE 指令處理中心 (完全體) =========
def process_line_events(body):
    """處理 LINE 傳來的指令"""
    if not body or "events" not in body: return
    for ev in body.get("events", []):
        if ev.get("type") != "message" or ev["message"].get("type") != "text": continue
        
        text = ev["message"]["text"].strip()
        reply_token = ev["replyToken"]
        user_id = ev["source"].get("userId")

        # 1. 說明
        if text.lower() in ("help", "幫助", "說明"):
            msg = "📌 臭寶（吳芃秀）指令集：\n\n1) 新增 日期 起點 終點 車次 數量 開始 結束\n2) 列表 (看任務)\n3) 刪除 ID (砍任務)\n4) 查票 (立刻抓票)"
            line_reply(reply_token, msg)
        # 2. 列表
        elif text.lower() in ("list", "列表"):
            _handle_list_tasks(user_id, reply_token)
        # 3. 新增 (包含 10 小時限制)
        elif text.startswith("新增"):
            _handle_create_task(user_id, reply_token, text)
        # 4. 刪除 (新功能)
        elif text.startswith("刪除"):
            _handle_delete_task(user_id, reply_token, text)
        # 5. 強制查票 (新功能)
        elif text in ("查票", "立即檢查"):
            line_reply(reply_token, "🫡 收到！正在為臭寶進行手動巡邏，請稍候約 20 秒...")
            job_check_and_notify()

def _handle_create_task(user_id, reply_token, text):
    """建立監控任務"""
    try:
        parts = text.split()
        if len(parts) != 8: raise ValueError
        _, date, start, end, kw, seats, s_time, e_time = parts

        # 🚀 臭寶要求的防呆：10 小時限制
        if (int(e_time.split(':')[0]) - int(s_time.split(':')[0])) > 10:
            line_reply(reply_token, "⚠️ 台鐵官網限制時段不能超過 10 小時喔！請調整開始與結束時間。")
            return

        profile = get_line_profile(user_id)
        name = profile.get("displayName") if profile else None
        desc = f"{start}→{end} {date} {s_time}-{e_time} {kw}"
        
        task = create_task(user_id, name, desc, date, start, end, s_time, e_time, kw, seats)
        line_reply(reply_token, f"✅ 任務已建立！[#{task['id']}] {desc}")
    except:
        line_reply(reply_token, "❌ 格式錯了啦！範例：\n新增 2026/01/27 1000-臺北 4400-高雄 * 1 06:00 12:00")

def _handle_list_tasks(user_id, reply_token):
    """查詢任務清單"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    tasks = [t for t in get_active_future_tasks(today) if t["line_user_id"] == user_id]
    if not tasks: return line_reply(reply_token, "目前沒任務喔。")
    msg = "📋 監控清單：\n" + "\n".join([f"[#{t['id']}] {t['description']}" for t in tasks])
    line_reply(reply_token, msg)

def _handle_delete_task(user_id, reply_token, text):
    """刪除指定任務"""
    try:
        task_id = int(text.split()[1])
        if delete_task(task_id, user_id):
            line_reply(reply_token, f"🗑️ 任務 #{task_id} 已移除！")
        else:
            line_reply(reply_token, f"❌ 找不到該任務或非本人。")
    except: line_reply(reply_token, "❌ 範例：刪除 5")

# ========= Webhooks (完全還原) =========
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET": return "ok", 200
    process_line_events(request.get_json(silent=True) or {})
    return jsonify({"ok": True})

@app.route("/webhook-debug", methods=["POST", "GET"])
def webhook_debug():
    """妳最愛的 Debug Webhook"""
    if request.method == "GET": return "ok", 200
    body = request.get_json(silent=True) or {}
    print("=== DEBUG WEBHOOK ===", body, flush=True)
    process_line_events(body)
    return jsonify({"ok": True})

if __name__ == '__main__':
    # PORT 由 Zeabur 環境變數提供
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))