# app.py - 女朋友對話機器人（LINE webhook + SQLite）
import os
import datetime
import requests
from flask import Flask, jsonify, request

from zoneinfo import ZoneInfo

from db_love import (
    seed_defaults,
    random_love_line,
    add_love_line,
    delete_love_line,
    list_love_lines,
    random_date_idea,
    add_date_idea,
    add_wish,
    list_wishes,
    add_mood,
    list_moods,
    set_setting,
    get_setting,
)

app = Flask(__name__)

# ====== Env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # optional fallback push target
BOT_NAME = os.getenv("BOT_NAME", "啊晡對話機器人")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")
GIRLFRIEND_NICKNAME = os.getenv("GIRLFRIEND_NICKNAME", "啊晡")

# 管理員（允許 /新增情話 /刪除情話 /列表情話 /新增約會）
# 例如：ADMIN_LINE_USER_IDS=Uxxxx,Uyyyy
ADMIN_LINE_USER_IDS = {
    x.strip() for x in os.getenv("ADMIN_LINE_USER_IDS", "").split(",") if x.strip()
}

print("=== BOT BOOT ===", flush=True)
print("BOT_NAME:", BOT_NAME, flush=True)
print("TIMEZONE:", TIMEZONE, flush=True)
print("GF_NICKNAME:", GIRLFRIEND_NICKNAME, flush=True)
print("ADMIN_LINE_USER_IDS:", ",".join(list(ADMIN_LINE_USER_IDS)) or "(none)", flush=True)
print("LINE_TOKEN set?:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("================", flush=True)

# Seed DB on boot
try:
    seed_defaults()
except Exception as e:
    print("[DB] seed_defaults error:", e, flush=True)


# ====== LINE helpers ======
def _line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def line_reply(reply_token: str, message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，無法回覆", flush=True)
        return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("Reply status:", resp.status_code, flush=True)
    except Exception as e:
        print("Reply error:", e, flush=True)


def line_push(message: str, to_user_id: str | None = None):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    target = to_user_id or LINE_TARGET_USER_ID
    if not target:
        return
    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": target, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("Push status:", resp.status_code, flush=True)
    except Exception as e:
        print("Push error:", e, flush=True)


def get_line_profile(user_id: str) -> dict | None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return None
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


# ====== bot logic ======
def _is_admin(user_id: str) -> bool:
    # 若你沒設 ADMIN_LINE_USER_IDS，就視為不限制（方便你跟女友都能加）
    if not ADMIN_LINE_USER_IDS:
        return True
    return user_id in ADMIN_LINE_USER_IDS


def _tz_now() -> datetime.datetime:
    try:
        return datetime.datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.datetime.now()


def infer_activity_message(nickname: str) -> str:
    now = _tz_now()
    h = now.hour
    wd = now.weekday()  # 0=Mon ... 6=Sun
    is_weekend = wd >= 5

    # 時段推估（你可以自己改成更貼近她的作息）
    if 0 <= h < 6:
        base = f"{nickname} 應該在睡覺或半夢半醒。"
        tip = "你可以傳：「睡醒跟我說，我在。」"
    elif 6 <= h < 9:
        base = f"{nickname} 可能剛起床 / 準備出門 / 吃早餐。"
        tip = "你可以傳：「早安～今天也要順順的。」"
    elif 9 <= h < 12:
        base = f"{nickname} 大概率在忙工作/上課中。"
        tip = "你可以傳：「忙也要記得喝水，我在想你。」"
    elif 12 <= h < 13:
        base = f"{nickname} 可能在吃午餐或放空一下。"
        tip = "你可以傳：「午餐吃什麼～我想聽。」"
    elif 13 <= h < 18:
        base = f"{nickname} 應該在下午忙碌模式。"
        tip = "你可以傳：「辛苦了，等你下班我抱抱。」"
    elif 18 <= h < 20:
        base = f"{nickname} 可能在下班/下課路上或吃晚餐。"
        tip = "你可以傳：「晚餐想吃什麼～我陪你選。」"
    elif 20 <= h < 23:
        base = f"{nickname} 可能在放鬆、追劇、滑手機、或準備休息。"
        tip = "你可以傳：「我想你～今天過得怎樣？」"
    else:
        base = f"{nickname} 應該準備睡覺或已經躺平。"
        tip = "你可以傳：「晚安～做個好夢，明天也一起加油。」"

    if is_weekend:
        base += "（週末版：比較像在耍廢或出去玩）"

    return f"⏰ {now.strftime('%H:%M')} 推測：{base}\n💬 建議你說：{tip}"


def help_text() -> str:
    return (
        f"【{BOT_NAME} 指令】\n"
        "\n"
        "基本：\n"
        "  /情話                - 隨機一句情話\n"
        "  /啊晡在幹嘛           - 依照現在時間推測她在做什麼\n"
        "  /早安                - 早安 + 情話\n"
        "  /晚安                - 晚安 + 情話\n"
        "  /約會                - 隨機約會靈感\n"
        "\n"
        "記錄：\n"
        "  /許願 <內容>          - 新增願望\n"
        "  /願望                - 顯示你的願望清單（最近 10 筆）\n"
        "  /心情 <內容>          - 記錄今天心情\n"
        "  /回顧心情            - 顯示最近心情（最近 10 筆）\n"
        "\n"
        "紀念日：\n"
        "  /設定紀念日 YYYY-MM-DD - 設定紀念日（每個 user 一份）\n"
        "  /紀念日               - 顯示交往第幾天\n"
        "\n"
        "管理（可選）：\n"
        "  /新增情話 <內容>\n"
        "  /列表情話\n"
        "  /刪除情話 <id>\n"
        "  /新增約會 <內容>\n"
        "\n"
        "提示：直接打 /help 或 /說明 也可以看到這份清單。"
    )


def _cmd(text: str) -> tuple[str, str]:
    """
    解析 '/命令 參數...'，回傳 (命令, 參數字串)
    """
    t = text.strip()
    if not t.startswith("/"):
        return "", t
    # split once
    parts = t.split(" ", 1)
    cmd = parts[0].strip()
    arg = parts[1].strip() if len(parts) == 2 else ""
    return cmd, arg


def handle_command(user_id: str, text: str) -> str:
    cmd, arg = _cmd(text)

    # aliases
    if cmd in ("/help", "/說明", "/幫助"):
        return help_text()

    if cmd in ("/情話",):
        row = random_love_line()
        if not row:
            return "資料庫目前沒有情話。你可以用：/新增情話 你最可愛"
        return f"💌 情話 #{row['id']}\n{row['text']}"

    if cmd in ("/啊晡在幹嘛", "/阿晡在幹嘛", "/在幹嘛"):
        nickname = get_setting(user_id, "gf_nickname") or GIRLFRIEND_NICKNAME
        return infer_activity_message(nickname)

    if cmd in ("/早安",):
        row = random_love_line()
        extra = row["text"] if row else "今天也要順順的。"
        return f"早安。\n{extra}"

    if cmd in ("/晚安",):
        row = random_love_line()
        extra = row["text"] if row else "做個好夢。"
        return f"晚安。\n{extra}"

    if cmd in ("/約會",):
        idea = random_date_idea()
        if not idea:
            return "目前沒有約會靈感。你可以用：/新增約會 去河堤散步"
        return f"🎡 約會靈感\n{idea['text']}"

    if cmd in ("/新增約會",):
        if not arg:
            return "用法：/新增約會 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        idea_id = add_date_idea(arg)
        return f"✅ 已新增約會靈感 #{idea_id}"

    if cmd in ("/許願",):
        if not arg:
            return "用法：/許願 <內容>"
        wid = add_wish(user_id, arg)
        return f"✅ 願望已記下來了（#{wid}）"

    if cmd in ("/願望",):
        ws = list_wishes(user_id, limit=10)
        if not ws:
            return "你目前沒有願望清單。用：/許願 <內容> 來新增"
        lines = [f"#{w['id']} {w['text']} ({w['created_at']})" for w in ws]
        return "📝 願望清單（最近 10 筆）\n" + "\n".join(lines)

    if cmd in ("/心情",):
        if not arg:
            return "用法：/心情 <內容>（例如：/心情 今天有點累但很想你）"
        mid = add_mood(user_id, arg)
        return f"✅ 心情已記錄（#{mid}）"

    if cmd in ("/回顧心情",):
        ms = list_moods(user_id, limit=10)
        if not ms:
            return "目前還沒有心情記錄。用：/心情 <內容> 來新增"
        lines = [f"#{m['id']} {m['text']} ({m['created_at']})" for m in ms]
        return "📒 最近心情（最近 10 筆）\n" + "\n".join(lines)

    if cmd in ("/設定紀念日",):
        if not arg:
            return "用法：/設定紀念日 YYYY-MM-DD（例如：/設定紀念日 2024-06-01）"
        # allow YYYY/MM/DD
        v = arg.replace("/", "-")
        try:
            datetime.date.fromisoformat(v)
        except Exception:
            return "日期格式不對。請用 YYYY-MM-DD（例如 2024-06-01）"
        set_setting(user_id, "anniversary", v)
        return f"✅ 紀念日已設定為 {v}"

    if cmd in ("/紀念日",):
        v = get_setting(user_id, "anniversary") or os.getenv("RELATION_START_DATE", "")
        if not v:
            return "你還沒設定紀念日。用：/設定紀念日 YYYY-MM-DD"
        try:
            start = datetime.date.fromisoformat(v)
        except Exception:
            return "紀念日資料格式不正確，請重新設定：/設定紀念日 YYYY-MM-DD"
        today = _tz_now().date()
        days = (today - start).days + 1
        return f"📅 我們在一起第 {days} 天\n（從 {start.isoformat()} 算起）"

    if cmd in ("/設定啊晡", "/設定暱稱"):
        if not arg:
            return "用法：/設定暱稱 <名字>（例如：/設定暱稱 啊晡）"
        set_setting(user_id, "gf_nickname", arg)
        return f"✅ 暱稱已設定為：{arg}"

    # 管理情話
    if cmd in ("/新增情話",):
        if not arg:
            return "用法：/新增情話 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        lid = add_love_line(arg)
        return f"✅ 情話已新增 #{lid}"

    if cmd in ("/列表情話",):
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        rows = list_love_lines(limit=20)
        if not rows:
            return "目前沒有情話。"
        lines = [f"#{r['id']} {r['text']}" for r in rows]
        return "📚 情話清單（最新 20 筆）\n" + "\n".join(lines)

    if cmd in ("/刪除情話",):
        if not arg:
            return "用法：/刪除情話 <id>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        try:
            lid = int(arg)
        except Exception:
            return "id 必須是數字。用法：/刪除情話 12"
        ok = delete_love_line(lid)
        return f"🗑️ 已刪除 #{lid}" if ok else "❌ 找不到這筆 id"

    # 非指令：給一個很溫柔的 fallback（避免已讀不回）
    if not cmd:
        # 你也可以改成：看到「想你/抱抱」就回固定句
        row = random_love_line()
        if row:
            return f"我在。\n{row['text']}\n\n（想看指令打 /help）"
        return "我在～（想看指令打 /help）"

    return "我看不懂這個指令耶，打 /help 我給你清單。"


def process_line_events(body):
    if not body or "events" not in body:
        return
    for ev in body.get("events", []):
        if ev.get("type") != "message":
            continue
        msg = ev.get("message", {})
        if msg.get("type") != "text":
            continue

        text = (msg.get("text") or "").strip()
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId") or "unknown"

        # optional: 顯示名稱
        profile = get_line_profile(user_id) or {}
        display_name = profile.get("displayName") or ""

        print(f"[IN] {display_name}({user_id}): {text}", flush=True)

        out = handle_command(user_id, text)
        if reply_token:
            line_reply(reply_token, out)


# ====== routes ======
@app.route("/")
def index():
    return f"✅ {BOT_NAME} running", 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "ok", 200
    process_line_events(request.get_json(silent=True) or {})
    return jsonify({"ok": True})


@app.route("/webhook-debug", methods=["POST", "GET"])
def webhook_debug():
    if request.method == "GET":
        return "ok", 200
    body = request.get_json(silent=True) or {}
    print("=== DEBUG WEBHOOK ===", body, flush=True)
    process_line_events(body)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
