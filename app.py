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
from checker import check_task_has_ticket  # ✅ 修補：移除 check_ticket_available

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


def line_push(user_id, text):
    """推播訊息給指定 user_id"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，略過 push", flush=True)
        return

    url = "https://api.line.me/v2/bot/message/push"
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("LINE push status:", resp.status_code, resp.text[:200], flush=True)
    except Exception as e:
        print("LINE push error:", e, flush=True)


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
        print("LINE reply status:", resp.status_code, resp.text[:200], flush=True)
    except Exception as e:
        print("LINE reply error:", e, flush=True)


# ========= 核心：排程檢票 =========
def _fmt_task(task: dict) -> str:
    return (
        f"#{task['id']} | {task['from_station']}→{task['to_station']} | "
        f"{task['date']} {task.get('time_range', '')} | "
        f"keyword={task.get('train_keyword','*')} min={task.get('min_seats',1)}"
    ).strip()


def check_all_tasks_job():
    """定期檢查所有未過期的任務，有票就通知"""
    try:
        expire_past_tasks()

        tasks = get_active_future_tasks()
        if not tasks:
            print("No active tasks.", flush=True)
            return

        print(f"Checking {len(tasks)} active tasks...", flush=True)

        for task in tasks:
            task_id = task["id"]
            if task.get("notified"):
                continue

            ok = False
            try:
                ok = check_task_has_ticket(task)
            except Exception as e:
                print("check_task_has_ticket error:", e, flush=True)
                ok = False

            if ok:
                msg = (
                    f"✅ 可能有票！\n{TARGET_DESC}\n"
                    f"任務 {_fmt_task(task)}\n"
                    f"（建議立即手動去台鐵查詢/購買）"
                )
                # 1) push 到指定 user（如果有設定）
                if LINE_TARGET_USER_ID:
                    line_push(LINE_TARGET_USER_ID, msg)

                # 2) 任務標記已通知，避免狂推
                mark_notified(task_id)
            else:
                print("No ticket for task:", _fmt_task(task), flush=True)

    except Exception as e:
        print("check_all_tasks_job fatal error:", e, flush=True)


# ========= HTTP API =========
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/tasks")
def list_tasks():
    tasks = get_active_future_tasks(include_notified=True)
    return jsonify({"ok": True, "tasks": tasks})


@app.post("/tasks")
def api_create_task():
    """
    body JSON:
    {
      "from_station": "台北",
      "to_station": "高雄",
      "date": "2026-01-27",
      "time_range": "06:00-12:00",
      "train_keyword": "*",
      "min_seats": 1
    }
    """
    body = request.get_json(force=True, silent=True) or {}

    from_station = body.get("from_station")
    to_station = body.get("to_station")
    date = body.get("date")

    if not from_station or not to_station or not date:
        return jsonify({"ok": False, "error": "from_station/to_station/date required"}), 400

    task = create_task(
        from_station=from_station,
        to_station=to_station,
        date=date,
        time_range=body.get("time_range", ""),
        train_keyword=body.get("train_keyword", "*"),
        min_seats=int(body.get("min_seats", 1)),
    )
    return jsonify({"ok": True, "task": task})


@app.post("/line/webhook")
def line_webhook():
    """
    LINE webhook 入口
    - 收到 "新增 台北 高雄 2026-01-27 06:00-12:00" 之類，就建立任務
    - 收到 "列表" 就回目前任務
    """
    payload = request.get_json(force=True, silent=True) or {}
    events = payload.get("events", [])
    for ev in events:
        if ev.get("type") != "message":
            continue
        msg = ev.get("message", {})
        if msg.get("type") != "text":
            continue

        text = (msg.get("text") or "").strip()
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")

        if not reply_token:
            continue

        if text in ("help", "幫助", "說明"):
            line_reply(
                reply_token,
                "指令：\n"
                "1) 新增 台北 高雄 2026-01-27 06:00-12:00\n"
                "2) 列表\n",
            )
            continue

        if text in ("列表", "list"):
            tasks = get_active_future_tasks(include_notified=True)
            if not tasks:
                line_reply(reply_token, "目前沒有任務。")
            else:
                lines = ["目前任務："]
                for t in tasks:
                    status = "✅已通知" if t.get("notified") else "⏳未通知"
                    lines.append(f"{status} {_fmt_task(t)}")
                line_reply(reply_token, "\n".join(lines))
            continue

        # 嘗試 parse: 新增 <from> <to> <date> [time_range]
        parts = text.split()
        if len(parts) >= 4 and parts[0] in ("新增", "add"):
            _, from_station, to_station, date = parts[:4]
            time_range = parts[4] if len(parts) >= 5 else ""
            try:
                task = create_task(
                    from_station=from_station,
                    to_station=to_station,
                    date=date,
                    time_range=time_range,
                    train_keyword="*",
                    min_seats=1,
                )
                line_reply(reply_token, f"✅ 已新增任務：{_fmt_task(task)}")
            except Exception as e:
                line_reply(reply_token, f"❌ 新增失敗：{e}")
            continue

        # fallback
        line_reply(reply_token, "看不懂指令，輸入「help」看用法。")

        # 如果你希望自動把對話 user_id 記起來，你可以在這裡把 user_id 存到 DB
        # （目前程式沒有做）

    return jsonify({"ok": True})


# ========= 啟動排程 =========
def start_scheduler():
    interval_minutes = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(check_all_tasks_job, "interval", minutes=interval_minutes, id="check_job")
    scheduler.start()
    print(f"Scheduler started: every {interval_minutes} minutes.", flush=True)


start_scheduler()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
