# app.py
import os
import datetime
import requests
from flask import Flask, jsonify, request
from zoneinfo import ZoneInfo

from db_love import (
    seed_defaults, random_love_line, add_love_line, delete_love_line, list_love_lines,
    random_date_idea, add_date_idea, add_wish, list_wishes, add_mood, list_moods,
    set_setting, get_setting, get_random_photo_task, complete_photo_task
)

app = Flask(__name__)

# ====== Env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
# 建議將雙方的 LINE ID 存入環境變數以便 Push 通知
CHOU_BU_ID = os.getenv("CHOU_BU_ID", "U_ZHUANG_GUAN_LIN_ID") # 莊冠霖
CHOU_BAO_ID = os.getenv("CHOU_BAO_ID", "U_WU_PENG_XIU_ID")   # 吳芃秀
BOT_NAME = os.getenv("BOT_NAME", "啊晡對話機器人")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")
GIRLFRIEND_NICKNAME = os.getenv("GIRLFRIEND_NICKNAME", "啊晡")
ADMIN_LINE_USER_IDS = {x.strip() for x in os.getenv("ADMIN_LINE_USER_IDS", "").split(",") if x.strip()}

# Seed DB
try: seed_defaults()
except Exception as e: print("[DB] seed_defaults error:", e, flush=True)

# ====== Helpers ======
def _line_headers():
    return {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

def line_reply(reply_token: str, message: str):
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=_line_headers(), json=body, timeout=10)

def line_push(to_user_id: str, message: str):
    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=_line_headers(), json=body, timeout=10)

def _tz_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(TIMEZONE))

def get_skin_care_advice():
    """根據時間給予臭寶保養提醒"""
    now = _tz_now()
    h = now.hour
    advice = "\n\n🧴 **臭寶保養小提醒**："
    if 6 <= h < 11:
        advice += "\n記得擦 Allie 防曬，如果臉泛紅記得噴理膚寶水或雅漾喔！"
    elif 20 <= h < 24:
        advice += "\n洗臉後記得補上 Curél 乳霜，保護敏感肌。"
    else:
        advice += "\n多喝水對皮膚好，加油！"
    return advice

def infer_activity_message(nickname: str) -> str:
    now = _tz_now()
    h, wd = now.hour, now.weekday()
    is_weekend = wd >= 5
    if 0 <= h < 6: base = f"{nickname} 應該在睡覺。"
    elif 6 <= h < 9: base = f"{nickname} 可能剛起床。"
    elif 9 <= h < 18: base = f"{nickname} 大概率在忙碌中。"
    elif 18 <= h < 22: base = f"{nickname} 可能在吃晚餐或放鬆。"
    else: base = f"{nickname} 應該準備休息了。"
    
    msg = f"⏰ {now.strftime('%H:%M')} 推測：{base}"
    if is_weekend: msg += "（週末版）"
    return msg + get_skin_care_advice()

def _cmd(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    while t.startswith(("/", "／")): t = t[1:].lstrip()
    cmds_with_arg = ["新增情話", "刪除情話", "新增約會", "許願", "心情", "設定紀念日", "設定暱稱", "設定啊晡", "完成任務"]
    for c in cmds_with_arg:
        if t.startswith(c) and len(t) > len(c):
            return c, t[len(c):].strip()
    parts = t.split(None, 1)
    return (parts[0].strip(), parts[1].strip() if len(parts) == 2 else "")

def handle_command(user_id: str, text: str) -> str:
    cmd, arg = _cmd(text)
    
    # 攝影任務與 Push 功能
    if cmd == "抽任務":
        task = get_random_photo_task()
        return f"📸 **今日攝影挑戰**：\n{task['task_text']}\n\n(完成後請打：完成任務 {task['id']})" if task else "目前沒任務，快去加幾個！"

    if cmd == "完成任務":
        if not arg: return "用法：完成任務 <ID>"
        if complete_photo_task(arg, user_id):
            partner = CHOU_BAO_ID if user_id == CHOU_BU_ID else CHOU_BU_ID
            name = "臭咘咘" if user_id == CHOU_BU_ID else "臭寶"
            line_push(partner, f"🎉 報喜！{name} 剛剛完成了一個攝影任務！快去要照片看 ❤️")
            return "收到！已幫你通知對方囉，快去分享照片吧！"
        return "找不到該任務 ID。"

    # 原有指令全數保留
    if cmd in ("help", "說明", "幫助"):
        return f"【{BOT_NAME} 指令】\n互動：抽任務、在幹嘛、情話、早安/晚安、約會\n記錄：許願 <內容>、心情 <內容>、願望、回顧心情\n設定：設定紀念日、設定暱稱\n管理：新增情話、列表情話"

    if cmd == "情話":
        row = random_love_line()
        return f"💌 情話 #{row['id']}\n{row['text']}" if row else "你在我心裡就是最好的情話。"

    if cmd in ("啊晡在幹嘛", "在幹嘛"):
        nickname = get_setting(user_id, "gf_nickname") or GIRLFRIEND_NICKNAME
        return infer_activity_message(nickname)

    if cmd == "早安":
        row = random_love_line()
        return f"早安。\n{row['text'] if row else '今天也要順順的。'}"

    if cmd == "晚安":
        row = random_love_line()
        return f"晚安。\n{row['text'] if row else '做個好夢。'}"

    if cmd == "約會":
        idea = random_date_idea()
        return f"🎡 約會靈感\n{idea['text']}" if idea else "目前沒靈感。"

    if cmd == "許願":
        if not arg: return "用法：許願 <內容>"
        wid = add_wish(user_id, arg)
        return f"✅ 願望已記下 (#{wid})"

    if cmd == "願望":
        ws = list_wishes(user_id)
        if not ws: return "清單是空的。"
        return "📝 願望清單\n" + "\n".join([f"#{w['id']} {w['text']}" for w in ws])

    if cmd == "心情":
        if not arg: return "用法：心情 <內容>"
        mid = add_mood(user_id, arg)
        return f"✅ 心情已記錄 (#{mid})"

    if cmd == "回顧心情":
        ms = list_moods(user_id)
        if not ms: return "還沒記錄過心情。"
        return "📒 最近心情\n" + "\n".join([f"#{m['id']} {m['text']} ({m['created_at']})" for m in ms])

    if cmd == "設定紀念日":
        if not arg: return "用法：設定紀念日 YYYY-MM-DD"
        v = arg.replace("/", "-")
        try: datetime.date.fromisoformat(v)
        except: return "日期格式不對。"
        set_setting(user_id, "anniversary", v)
        return f"✅ 紀念日已設為 {v}"

    if cmd == "紀念日":
        v = get_setting(user_id, "anniversary") or os.getenv("RELATION_START_DATE", "")
        if not v: return "還沒設定紀念日。"
        start = datetime.date.fromisoformat(v)
        days = (_tz_now().date() - start).days + 1
        return f"📅 在一起第 {days} 天"

    if cmd in ("設定啊晡", "設定暱稱"):
        if not arg: return "用法：設定暱稱 <名字>"
        set_setting(user_id, "gf_nickname", arg)
        return f"✅ 暱稱已設定：{arg}"

    # 管理指令
    if cmd == "新增情話":
        if not arg or not ADMIN_LINE_USER_IDS: return "無權限或用法錯誤。"
        lid = add_love_line(arg)
        return f"✅ 情話已新增 #{lid}"

    if cmd == "列表情話":
        rows = list_love_lines()
        return "📚 情話清單\n" + "\n".join([f"#{r['id']} {r['text']}" for r in rows]) if rows else "空。"

    if cmd == "刪除情話":
        try: return f"🗑️ 已刪除 #{arg}" if delete_love_line(arg) else "找不到 ID。"
        except: return "用法：刪除情話 <ID>"

    if not cmd:
        row = random_love_line()
        return f"我在。\n{row['text']}\n\n(打 help 看指令)" if row else "我在～"

    return "我看不懂耶，打 help 我給你清單。"

@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET": return "ok", 200
    body = request.get_json(silent=True) or {}
    for ev in body.get("events", []):
        if ev.get("type") == "message" and ev["message"].get("type") == "text":
            uid = ev["source"].get("userId", "unknown")
            reply_token = ev.get("replyToken")
            out = handle_command(uid, ev["message"]["text"])
            if reply_token: line_reply(reply_token, out)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))