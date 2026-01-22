# app.py - 女朋友對話機器人（LINE webhook + SQLite + Weather + Photo tasks + Photo Forwarding + Conversation Bridge）
import os
import sqlite3
import datetime
import mimetypes
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file, abort
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from db_love import (
    seed_defaults,
    # love lines
    random_love_line,
    add_love_line,
    delete_love_line,
    list_love_lines,
    # date ideas
    random_date_idea,
    add_date_idea,
    # wishes/moods/settings
    add_wish,
    list_wishes,
    add_mood,
    list_moods,
    set_setting,
    get_setting,
    # subscribers / roles
    upsert_subscriber,
    set_role,
    set_active,
    get_couple_user_ids,
)

from weather_client import fetch_today_weather_metrics

app = Flask(__name__)

# ====== Env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # fallback push target
BOT_NAME = os.getenv("BOT_NAME", "臭寶對話機器人")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")

# 你說：臭寶=女友、臭晡晡=你
DEFAULT_GIRLFRIEND_NICKNAME = os.getenv("GIRLFRIEND_NICKNAME", "臭寶")
DEFAULT_SELF_NICKNAME = os.getenv("SELF_NICKNAME", "臭晡晡")

# DB & media
LOVE_DB_PATH = os.getenv("LOVE_DB_PATH", "/data/love.db")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/data/media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# 你部署在 Zeabur 後一定要設：PUBLIC_BASE_URL=https://xxxx.zeabur.app
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or "").rstrip("/")
# 可選：避免外人猜到 /media/<id> 取走照片
MEDIA_ACCESS_TOKEN = os.getenv("MEDIA_ACCESS_TOKEN", "").strip()

# Weather（預設新竹）
WEATHER_CITY = os.getenv("WEATHER_CITY", "新竹")
WEATHER_LAT = float(os.getenv("WEATHER_LAT", "24.8138"))
WEATHER_LON = float(os.getenv("WEATHER_LON", "120.9675"))

# Weather 門檻
UV_HIGH_THRESHOLD = float(os.getenv("UV_HIGH_THRESHOLD", "8"))
HUMIDITY_RANGE_THRESHOLD = float(os.getenv("HUMIDITY_RANGE_THRESHOLD", "25"))
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "0") == "1"

# 對話/在不在 判斷門檻（分鐘）
ACTIVE_MINUTES = int(os.getenv("ACTIVE_MINUTES", "30"))
REPLIED_WINDOW_MINUTES = int(os.getenv("REPLIED_WINDOW_MINUTES", "180"))

# 管理員（允許：新增情話/刪除情話/列表情話/新增約會）
ADMIN_LINE_USER_IDS = {
    x.strip() for x in os.getenv("ADMIN_LINE_USER_IDS", "").split(",") if x.strip()
}

print("=== BOT BOOT ===", flush=True)
print("BOT_NAME:", BOT_NAME, flush=True)
print("TIMEZONE:", TIMEZONE, flush=True)
print("DEFAULT_GF:", DEFAULT_GIRLFRIEND_NICKNAME, flush=True)
print("DEFAULT_SELF:", DEFAULT_SELF_NICKNAME, flush=True)
print("LOVE_DB_PATH:", LOVE_DB_PATH, flush=True)
print("MEDIA_DIR:", str(MEDIA_DIR), flush=True)
print("PUBLIC_BASE_URL:", PUBLIC_BASE_URL or "(not set)", flush=True)
print("MEDIA_ACCESS_TOKEN set?:", bool(MEDIA_ACCESS_TOKEN), flush=True)
print("WEATHER_CITY:", WEATHER_CITY, "lat/lon:", WEATHER_LAT, WEATHER_LON, flush=True)
print("ENABLE_SCHEDULER:", ENABLE_SCHEDULER, flush=True)
print("ACTIVE_MINUTES:", ACTIVE_MINUTES, "REPLIED_WINDOW_MINUTES:", REPLIED_WINDOW_MINUTES, flush=True)
print("ADMIN_LINE_USER_IDS:", ",".join(list(ADMIN_LINE_USER_IDS)) or "(none)", flush=True)
print("LINE_TOKEN set?:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("================", flush=True)

# Seed DB on boot
try:
    seed_defaults()
except Exception as e:
    print("[DB] seed_defaults error:", e, flush=True)


# ====== DB direct helpers (for media table & role map/display name) ======
def _db_conn():
    conn = sqlite3.connect(LOVE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_media_table():
    try:
        conn = _db_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                message_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content_type TEXT,
                from_user_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("[DB] ensure media table error:", e, flush=True)


_ensure_media_table()


def save_media_record(message_id: str, filename: str, content_type: str | None, from_user_id: str):
    try:
        conn = _db_conn()
        conn.execute(
            """
            INSERT INTO media(message_id, filename, content_type, from_user_id, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                filename=excluded.filename,
                content_type=excluded.content_type,
                from_user_id=excluded.from_user_id
            """,
            (message_id, filename, content_type, from_user_id, _iso_now()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("[DB] save_media_record error:", e, flush=True)


def get_media_record(message_id: str):
    try:
        conn = _db_conn()
        row = conn.execute(
            "SELECT message_id, filename, content_type, from_user_id, created_at FROM media WHERE message_id=?",
            (message_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print("[DB] get_media_record error:", e, flush=True)
        return None


def get_role_map_active() -> dict:
    """
    回傳 {'girlfriend': user_id, 'boyfriend': user_id}（若已設定）
    需要 db_love.py 已建立 subscriber table。
    """
    try:
        conn = _db_conn()
        rows = conn.execute(
            """
            SELECT line_user_id, role
            FROM subscriber
            WHERE is_active=1 AND role IN ('girlfriend','boyfriend')
            """
        ).fetchall()
        conn.close()
        m = {}
        for r in rows:
            m[r["role"]] = r["line_user_id"]
        return m
    except Exception as e:
        print("[DB] get_role_map_active error:", e, flush=True)
        return {}


def get_display_name_from_db(user_id: str) -> str:
    try:
        conn = _db_conn()
        row = conn.execute(
            "SELECT display_name FROM subscriber WHERE line_user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row and row["display_name"]:
            return row["display_name"]
    except Exception:
        pass
    return ""


# ====== Time helpers ======
def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _tz_now() -> datetime.datetime:
    return datetime.datetime.now(_tz())


def _iso_now() -> str:
    return _tz_now().isoformat(timespec="seconds")


def _parse_dt(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz())
        return dt
    except Exception:
        return None


def _mins_ago(dt: datetime.datetime | None) -> int | None:
    if not dt:
        return None
    delta = _tz_now() - dt
    return int(delta.total_seconds() // 60)


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
        if resp.status_code != 200:
            print("Reply body:", resp.text[:500], flush=True)
    except Exception as e:
        print("Reply error:", e, flush=True)


def line_push_messages(to_user_id: str, messages: list[dict]):
    if not LINE_CHANNEL_ACCESS_TOKEN or not to_user_id:
        return
    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": messages}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        print("Push status:", resp.status_code, flush=True)
        if resp.status_code != 200:
            print("Push body:", resp.text[:500], flush=True)
    except Exception as e:
        print("Push error:", e, flush=True)


def line_push_text(to_user_id: str, text: str):
    line_push_messages(to_user_id, [{"type": "text", "text": text}])


def line_push_many_text(text: str, user_ids: list[str]):
    for uid in user_ids:
        line_push_text(uid, text)


def get_line_profile(user_id: str) -> dict | None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return None
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    try:
        resp = requests.get(url, headers=_line_headers(), timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def line_get_message_content(message_id: str) -> tuple[bytes, str | None]:
    """
    下載 LINE 影像二進位內容（需要 Token）
    回傳 (bytes, content_type)
    """
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE token missing")

    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"LINE content fetch failed: {resp.status_code} {resp.text[:200]}")
    ctype = resp.headers.get("Content-Type")
    return resp.content, ctype


def _ext_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".bin"
    content_type = content_type.split(";")[0].strip().lower()
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/gif":
        return ".gif"
    # fallback
    ext = mimetypes.guess_extension(content_type) or ".bin"
    return ext


def build_media_url(message_id: str) -> str | None:
    """
    回傳可被 LINE 讀取的 https URL：
      {PUBLIC_BASE_URL}/media/{message_id}[?k=token]
    """
    if not PUBLIC_BASE_URL:
        return None
    url = f"{PUBLIC_BASE_URL}/media/{message_id}"
    if MEDIA_ACCESS_TOKEN:
        url += f"?k={MEDIA_ACCESS_TOKEN}"
    return url


def forward_image_to_other_party(sender_user_id: str, sender_name: str, message_id: str):
    """
    將收到的圖片轉送給另一方（需要雙方角色都已設定）
    """
    role_map = get_role_map_active()
    gf_id = role_map.get("girlfriend")
    bf_id = role_map.get("boyfriend")

    if not gf_id or not bf_id:
        # 尚未完成身份設定，不轉送（避免誤送）
        print("[FORWARD] roles not ready, skip", flush=True)
        return

    if sender_user_id == gf_id:
        target_id = bf_id
        target_label = DEFAULT_SELF_NICKNAME
    elif sender_user_id == bf_id:
        target_id = gf_id
        target_label = DEFAULT_GIRLFRIEND_NICKNAME
    else:
        # 不是這對情侶之一，忽略
        print("[FORWARD] sender not in couple, skip", flush=True)
        return

    media_url = build_media_url(message_id)
    if not media_url:
        print("[FORWARD] PUBLIC_BASE_URL not set, cannot forward image", flush=True)
        return

    now = _tz_now().strftime("%m/%d %H:%M")
    caption = f"📷 {sender_name}（{now}）傳來一張照片給你（{target_label}）"

    # 先 text 再 image（一次 push 兩則）
    line_push_messages(
        target_id,
        [
            {"type": "text", "text": caption},
            {"type": "image", "originalContentUrl": media_url, "previewImageUrl": media_url},
        ],
    )


# ====== core: activity tracking ======
def upsert_activity(user_id: str, display_name: str, preview: str):
    try:
        upsert_subscriber(user_id=user_id, display_name=display_name)
    except Exception as e:
        print("[DB] upsert_subscriber error:", e, flush=True)

    now_iso = _iso_now()
    try:
        set_setting(user_id, "last_seen_ts", now_iso)
        set_setting(user_id, "last_msg_ts", now_iso)
        set_setting(user_id, "last_msg_preview", preview[:60])
    except Exception as e:
        print("[DB] set_setting activity error:", e, flush=True)


# ====== misc helpers ======
def _is_admin(user_id: str) -> bool:
    if not ADMIN_LINE_USER_IDS:
        return True
    return user_id in ADMIN_LINE_USER_IDS


def push_to_couple_text(message: str, fallback_user_id: str | None = None):
    ids = get_couple_user_ids()
    ids = [x for x in ids if x]

    if len(ids) >= 2:
        line_push_many_text(message, ids)
        return

    fallback = []
    if fallback_user_id:
        fallback.append(fallback_user_id)
    if LINE_TARGET_USER_ID and LINE_TARGET_USER_ID not in fallback:
        fallback.append(LINE_TARGET_USER_ID)
    if fallback:
        line_push_many_text(message, fallback)


def infer_activity_message(nickname: str) -> str:
    now = _tz_now()
    h = now.hour
    wd = now.weekday()
    is_weekend = wd >= 5

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
        f"【{BOT_NAME} 指令（不用 /）】\n"
        "\n"
        "身份/推播：\n"
        "  我是臭寶 / 我是臭晡晡\n"
        "  加入推播 / 退出推播\n"
        "\n"
        "對話橋樑：\n"
        "  對話狀態\n"
        "  跟臭寶說 <內容>\n"
        "  跟臭晡晡說 <內容>\n"
        "\n"
        "基本：\n"
        "  情話 / 臭寶在幹嘛 / 早安 / 晚安 / 約會\n"
        "\n"
        "天氣：\n"
        "  天氣 / 天氣提醒\n"
        "\n"
        "攝影任務（會推播給雙方）：\n"
        "  攝影任務\n"
        "  攝影任務 給臭寶\n"
        "  攝影任務 給臭晡晡\n"
        "\n"
        "照片轉送：\n"
        "  你或臭寶把照片傳給 bot，我會自動轉送給另一方。\n"
        "\n"
        "記錄：\n"
        "  許願 <內容> / 願望\n"
        "  心情 <內容> / 回顧心情\n"
        "\n"
        "紀念日：\n"
        "  設定紀念日 YYYY-MM-DD / 紀念日\n"
        "\n"
        "管理：\n"
        "  新增情話 <內容> / 列表情話 / 刪除情話 <id>\n"
        "  新增約會 <內容>\n"
    )


def _cmd(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    if not t:
        return "", ""
    while t.startswith(("/", "／")):
        t = t[1:].lstrip()

    cmds_with_arg = [
        "新增情話",
        "刪除情話",
        "新增約會",
        "許願",
        "心情",
        "設定紀念日",
        "攝影任務",
        "跟臭寶說",
        "跟臭晡晡說",
    ]
    for c in cmds_with_arg:
        if t.startswith(c) and len(t) > len(c):
            return c, t[len(c):].strip()

    parts = t.split(None, 1)
    cmd = parts[0].strip()
    arg = parts[1].strip() if len(parts) == 2 else ""
    return cmd, arg


def build_weather_alert_message(metrics: dict) -> str | None:
    max_uv = metrics.get("max_uv")
    min_h = metrics.get("min_humidity")
    max_h = metrics.get("max_humidity")
    h_range = metrics.get("humidity_range")

    triggers = []
    if max_uv is not None and max_uv >= UV_HIGH_THRESHOLD:
        triggers.append("uv")
    if h_range is not None and h_range >= HUMIDITY_RANGE_THRESHOLD:
        triggers.append("humidity")
    if not triggers:
        return None

    gf = DEFAULT_GIRLFRIEND_NICKNAME
    city = WEATHER_CITY
    uv_part = f"今天{city}紫外線偏高（UV最高約 {max_uv:.1f}）" if max_uv is not None else f"今天{city}紫外線偏高"
    hum_part = ""
    if min_h is not None and max_h is not None and h_range is not None:
        hum_part = f"，濕度約 {min_h:.0f}%～{max_h:.0f}%（波動 {h_range:.0f}%）"

    return "\n".join(
        [
            f"☀️ 天氣提醒：{uv_part}{hum_part}",
            f"{gf} 出門記得擦防曬（記得用你那罐 Allie！）",
            "回家也要注意退紅保養：Curél / 理膚寶水先厚敷保濕、避免太刺激的酸類。",
        ]
    )


def build_weather_summary(metrics: dict) -> str:
    max_uv = metrics.get("max_uv")
    min_h = metrics.get("min_humidity")
    max_h = metrics.get("max_humidity")
    h_range = metrics.get("humidity_range")

    parts = [f"🌦️ {WEATHER_CITY} 今日摘要"]
    if max_uv is not None:
        parts.append(f"UV 最高：約 {max_uv:.1f}")
    if min_h is not None and max_h is not None and h_range is not None:
        parts.append(f"濕度：約 {min_h:.0f}%～{max_h:.0f}%（波動 {h_range:.0f}%）")
    parts.append("（用：天氣提醒 會在符合門檻時推播給雙方）")
    return "\n".join(parts)


PHOTO_TASKS_FOR_GIRLFRIEND = [
    "拍一張你現在看到的天空（不要濾鏡）",
    "拍你今天吃的東西（要有近照）",
    "拍一張你現在的鞋子/腳步（像日常紀錄）",
    "拍一張路上看到可愛的小東西（招牌/娃娃/貓都行）",
    "拍一張你最喜歡的角度自拍（不用完美，真實就好）",
]

PHOTO_TASKS_FOR_SELF = [
    "拍一張『你想給臭寶看的今天』：桌面/天空/路邊都可以",
    "拍你今天的晚餐，然後寫 10 字心得",
    "拍一張你今天最開心的一瞬間（畫面即可）",
    "拍一張你要跟臭寶分享的小細節（例如：咖啡、車窗、夕陽）",
    "拍一張你今天的穿搭（全身或局部都可）",
]


def build_photo_task(assign_to: str) -> str:
    gf = DEFAULT_GIRLFRIEND_NICKNAME
    me = DEFAULT_SELF_NICKNAME
    now = _tz_now().strftime("%m/%d %H:%M")

    import random
    if assign_to == "給臭寶":
        task = random.choice(PHOTO_TASKS_FOR_GIRLFRIEND)
        return f"📸 攝影任務（{now}）\n{gf}：請幫 {me} {task}\n完成後把照片傳給我（bot），我會自動轉送給 {me}。"
    if assign_to == "給臭晡晡":
        task = random.choice(PHOTO_TASKS_FOR_SELF)
        return f"📸 攝影任務（{now}）\n{me}：請幫 {gf} {task}\n完成後把照片傳給我（bot），我會自動轉送給 {gf}。"

    task_gf = random.choice(PHOTO_TASKS_FOR_GIRLFRIEND)
    task_me = random.choice(PHOTO_TASKS_FOR_SELF)
    return (
        f"📸 互拍任務（{now}）\n"
        f"{gf}：請幫 {me} {task_gf}\n"
        f"{me}：請幫 {gf} {task_me}\n"
        "完成後把照片傳給我（bot），我會自動轉送給另一方。"
    )


def build_conversation_status_text() -> str:
    role_map = get_role_map_active()
    gf_id = role_map.get("girlfriend")
    bf_id = role_map.get("boyfriend")

    if not gf_id or not bf_id:
        return (
            "⚠️ 目前還不知道誰是臭寶/臭晡晡。\n"
            "請你先說：我是臭晡晡\n"
            "請臭寶說：我是臭寶"
        )

    gf_last_ts = _parse_dt(get_setting(gf_id, "last_msg_ts"))
    bf_last_ts = _parse_dt(get_setting(bf_id, "last_msg_ts"))
    gf_preview = get_setting(gf_id, "last_msg_preview") or ""
    bf_preview = get_setting(bf_id, "last_msg_preview") or ""

    gf_name = get_display_name_from_db(gf_id) or DEFAULT_GIRLFRIEND_NICKNAME
    bf_name = get_display_name_from_db(bf_id) or DEFAULT_SELF_NICKNAME

    now = _tz_now()

    def fmt(dt: datetime.datetime | None) -> str:
        return dt.astimezone(_tz()).strftime("%m/%d %H:%M") if dt else "未知"

    gf_mins = _mins_ago(gf_last_ts)
    bf_mins = _mins_ago(bf_last_ts)

    gf_active = (gf_mins is not None and gf_mins <= ACTIVE_MINUTES)
    bf_active = (bf_mins is not None and bf_mins <= ACTIVE_MINUTES)

    is_replying = False
    waiting_reply = False
    if gf_last_ts and bf_last_ts:
        if gf_last_ts > bf_last_ts and (now - gf_last_ts).total_seconds() <= REPLIED_WINDOW_MINUTES * 60:
            is_replying = True
        if bf_last_ts > gf_last_ts and (now - bf_last_ts).total_seconds() <= REPLIED_WINDOW_MINUTES * 60:
            waiting_reply = True

    lines = []
    lines.append("🧩 對話狀態（推估）")
    lines.append(f"{gf_name}：最後訊息 {fmt(gf_last_ts)}（{gf_mins} 分鐘前）")
    if gf_preview:
        lines.append(f"  └ 最近內容：{gf_preview}")
    lines.append(f"{bf_name}：最後訊息 {fmt(bf_last_ts)}（{bf_mins} 分鐘前）")
    if bf_preview:
        lines.append(f"  └ 最近內容：{bf_preview}")
    lines.append("")
    if is_replying:
        lines.append("✅ 結論：臭寶最近有回你（看起來正在跟你對話）。")
    elif waiting_reply:
        lines.append("⏳ 結論：你講完之後臭寶還沒回（目前像是在等她回覆）。")
    else:
        lines.append("ℹ️ 結論：目前沒有明顯的『你講→她回』節奏（可能各忙各的）。")

    active_note = []
    if gf_active:
        active_note.append("臭寶剛剛有出現")
    if bf_active:
        active_note.append("你剛剛有出現")
    if active_note:
        lines.append("（" + "、".join(active_note) + "）")

    return "\n".join(lines)


def relay_message(from_user_id: str, to_role: str, content: str) -> str:
    role_map = get_role_map_active()
    target_id = role_map.get(to_role)
    if not target_id:
        return "⚠️ 目標尚未設定角色。請雙方先說：我是臭寶 / 我是臭晡晡"
    if not content.strip():
        return "用法：跟臭寶說 <內容> 或 跟臭晡晡說 <內容>"

    sender_name = get_display_name_from_db(from_user_id) or "對方"
    now = _tz_now().strftime("%m/%d %H:%M")
    msg = f"💬 {sender_name}（{now}）想跟你說：\n{content.strip()}"

    line_push_text(target_id, msg)
    line_push_text(from_user_id, "✅ 已幫你送出。")
    return "✅ 已轉送（我也推播確認給你了）。"


def handle_command(user_id: str, text: str) -> str:
    cmd, arg = _cmd(text)

    if cmd in ("help", "說明", "幫助"):
        return help_text()

    # ===== identity / push =====
    if cmd == "我是臭寶":
        set_role(user_id, "girlfriend")
        set_active(user_id, True)
        return "✅ 已設定你是：臭寶（女友）。之後天氣提醒/任務/照片轉送/轉話都會用到你。"

    if cmd == "我是臭晡晡":
        set_role(user_id, "boyfriend")
        set_active(user_id, True)
        return "✅ 已設定你是：臭晡晡。之後天氣提醒/任務/照片轉送/轉話都會用到你。"

    if cmd == "加入推播":
        set_active(user_id, True)
        return "✅ 已加入推播。"

    if cmd == "退出推播":
        set_active(user_id, False)
        return "✅ 已退出推播。"

    # ===== bridge =====
    if cmd in ("對話狀態", "狀態", "臭寶有沒有在跟我對話", "她在嗎", "有在嗎"):
        return build_conversation_status_text()

    if cmd == "跟臭寶說":
        return relay_message(user_id, "girlfriend", arg)

    if cmd == "跟臭晡晡說":
        return relay_message(user_id, "boyfriend", arg)

    # ===== basic =====
    if cmd == "情話":
        row = random_love_line()
        if not row:
            return "資料庫目前沒有情話。你可以用：新增情話 你最可愛"
        return f"💌 情話 #{row['id']}\n{row['text']}"

    if cmd in ("臭寶在幹嘛", "啊晡在幹嘛", "阿晡在幹嘛"):
        nickname = get_setting(user_id, "gf_nickname") or DEFAULT_GIRLFRIEND_NICKNAME
        return infer_activity_message(nickname)

    if cmd == "早安":
        row = random_love_line()
        extra = row["text"] if row else "今天也要順順的。"
        return f"早安。\n{extra}"

    if cmd == "晚安":
        row = random_love_line()
        extra = row["text"] if row else "做個好夢。"
        return f"晚安。\n{extra}"

    if cmd == "約會":
        idea = random_date_idea()
        if not idea:
            return "目前沒有約會靈感。你可以用：新增約會 去河堤散步"
        return f"🎡 約會靈感\n{idea['text']}"

    if cmd == "新增約會":
        if not arg:
            return "用法：新增約會 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        idea_id = add_date_idea(arg)
        return f"✅ 已新增約會靈感 #{idea_id}"

    # ===== weather =====
    if cmd == "天氣":
        metrics = fetch_today_weather_metrics(lat=WEATHER_LAT, lon=WEATHER_LON, timezone=TIMEZONE)
        return build_weather_summary(metrics)

    if cmd == "天氣提醒":
        metrics = fetch_today_weather_metrics(lat=WEATHER_LAT, lon=WEATHER_LON, timezone=TIMEZONE)
        msg = build_weather_alert_message(metrics)
        if not msg:
            return "✅ 目前天氣條件未達提醒門檻（UV/濕度波動都還 OK）。"
        push_to_couple_text(msg, fallback_user_id=user_id)
        return "✅ 已推播天氣提醒給雙方。"

    # ===== photo task =====
    if cmd == "攝影任務":
        mode = "互拍"
        a = arg.strip()
        if a in ("給臭寶", "給女友", "給臭寶寶"):
            mode = "給臭寶"
        elif a in ("給臭晡晡", "給我", "給男友"):
            mode = "給臭晡晡"
        task_msg = build_photo_task(mode)
        push_to_couple_text(task_msg, fallback_user_id=user_id)
        return "✅ 已派發攝影任務給雙方。（交作業：把照片傳給 bot，我會自動轉送）"

    # ===== logs =====
    if cmd == "許願":
        if not arg:
            return "用法：許願 <內容>"
        wid = add_wish(user_id, arg)
        return f"✅ 願望已記下來了（#{wid}）"

    if cmd == "願望":
        ws = list_wishes(user_id, limit=10)
        if not ws:
            return "你目前沒有願望清單。用：許願 <內容> 來新增"
        lines = [f"#{w['id']} {w['text']} ({w['created_at']})" for w in ws]
        return "📝 願望清單（最近 10 筆）\n" + "\n".join(lines)

    if cmd == "心情":
        if not arg:
            return "用法：心情 <內容>（例如：心情 今天有點累但很想你）"
        mid = add_mood(user_id, arg)
        return f"✅ 心情已記錄（#{mid}）"

    if cmd == "回顧心情":
        ms = list_moods(user_id, limit=10)
        if not ms:
            return "目前還沒有心情記錄。用：心情 <內容> 來新增"
        lines = [f"#{m['id']} {m['text']} ({m['created_at']})" for m in ms]
        return "📒 最近心情（最近 10 筆）\n" + "\n".join(lines)

    if cmd == "設定紀念日":
        if not arg:
            return "用法：設定紀念日 YYYY-MM-DD（例如：設定紀念日 2024-06-01）"
        v = arg.replace("/", "-")
        try:
            datetime.date.fromisoformat(v)
        except Exception:
            return "日期格式不對。請用 YYYY-MM-DD（例如 2024-06-01）"
        set_setting(user_id, "anniversary", v)
        return f"✅ 紀念日已設定為 {v}"

    if cmd == "紀念日":
        v = get_setting(user_id, "anniversary") or os.getenv("RELATION_START_DATE", "")
        if not v:
            return "你還沒設定紀念日。用：設定紀念日 YYYY-MM-DD"
        try:
            start = datetime.date.fromisoformat(v)
        except Exception:
            return "紀念日資料格式不正確，請重新設定：設定紀念日 YYYY-MM-DD"
        today = _tz_now().date()
        days = (today - start).days + 1
        return f"📅 我們在一起第 {days} 天\n（從 {start.isoformat()} 算起）"

    # ===== admin =====
    if cmd == "新增情話":
        if not arg:
            return "用法：新增情話 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        lid = add_love_line(arg)
        return f"✅ 情話已新增 #{lid}"

    if cmd == "列表情話":
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        rows = list_love_lines(limit=20)
        if not rows:
            return "目前沒有情話。"
        lines = [f"#{r['id']} {r['text']}" for r in rows]
        return "📚 情話清單（最新 20 筆）\n" + "\n".join(lines)

    if cmd == "刪除情話":
        if not arg:
            return "用法：刪除情話 <id>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        try:
            lid = int(arg)
        except Exception:
            return "id 必須是數字。用法：刪除情話 12"
        ok = delete_love_line(lid)
        return f"🗑️ 已刪除 #{lid}" if ok else "❌ 找不到這筆 id"

    if not cmd:
        row = random_love_line()
        if row:
            return f"我在。\n{row['text']}\n\n（想看指令打 help）"
        return "我在～（想看指令打 help）"

    return "我看不懂這個指令耶，打 help 我給你清單。"


def process_line_events(body):
    if not body or "events" not in body:
        return

    for ev in body.get("events", []):
        if ev.get("type") != "message":
            continue

        msg = ev.get("message", {})
        msg_type = msg.get("type")
        reply_token = ev.get("replyToken")
        source = ev.get("source") or {}
        user_id = source.get("userId") or "unknown"

        profile = get_line_profile(user_id) or {}
        display_name = profile.get("displayName") or ""

        # 先確保 subscriber 有寫入（雙方推播/角色/顯示名）
        try:
            upsert_subscriber(user_id=user_id, display_name=display_name)
        except Exception as e:
            print("[DB] upsert_subscriber error:", e, flush=True)

        if msg_type == "text":
            text = (msg.get("text") or "").strip()
            print(f"[IN] {display_name}({user_id}): {text}", flush=True)
            upsert_activity(user_id, display_name, preview=text)

            out = handle_command(user_id, text)
            if reply_token:
                line_reply(reply_token, out)
            continue

        if msg_type == "image":
            message_id = msg.get("id")
            print(f"[IN] {display_name}({user_id}): <image> id={message_id}", flush=True)
            upsert_activity(user_id, display_name, preview="[image]")

            # 下載並存檔
            try:
                content, ctype = line_get_message_content(message_id)
                ext = _ext_from_content_type(ctype)
                filename = f"{message_id}{ext}"
                filepath = MEDIA_DIR / filename
                filepath.write_bytes(content)

                save_media_record(message_id=message_id, filename=filename, content_type=ctype, from_user_id=user_id)
                print(f"[MEDIA] saved {filepath} ({ctype})", flush=True)

                # 自動轉送給另一方
                forward_image_to_other_party(sender_user_id=user_id, sender_name=display_name or "對方", message_id=message_id)

                if reply_token:
                    line_reply(reply_token, "✅ 收到照片了，我已經幫你轉送給對方。")
            except Exception as e:
                print("[MEDIA] image handling error:", e, flush=True)
                if reply_token:
                    line_reply(reply_token, "✅ 我收到照片了，但轉送失敗（可能 PUBLIC_BASE_URL 未設定或下載失敗）。")

            continue

        # 其他類型：也算活動，但不做轉送
        if msg_type in ("video", "audio", "sticker", "file"):
            preview = f"[{msg_type}]"
            print(f"[IN] {display_name}({user_id}): {preview} id={msg.get('id')}", flush=True)
            upsert_activity(user_id, display_name, preview=preview)
            if reply_token:
                line_reply(reply_token, "✅ 收到～")
            continue

        if reply_token:
            line_reply(reply_token, "✅ 收到～")


# ====== scheduler (weather proactive push) ======
_scheduler: BackgroundScheduler | None = None


def scheduled_weather_check():
    try:
        metrics = fetch_today_weather_metrics(lat=WEATHER_LAT, lon=WEATHER_LON, timezone=TIMEZONE)
        msg = build_weather_alert_message(metrics)
        if not msg:
            print("[SCHED] Weather ok; no push.", flush=True)
            return
        push_to_couple_text(msg, fallback_user_id=None)
        print("[SCHED] Weather alert pushed.", flush=True)
    except Exception as e:
        print("[SCHED] scheduled_weather_check error:", e, flush=True)


def start_scheduler():
    global _scheduler
    if _scheduler:
        return

    sched = BackgroundScheduler(timezone=_tz())
    sched.add_job(scheduled_weather_check, "cron", hour=8, minute=30, id="weather_0830", replace_existing=True)
    sched.add_job(scheduled_weather_check, "cron", hour=12, minute=30, id="weather_1230", replace_existing=True)
    sched.add_job(scheduled_weather_check, "cron", hour=17, minute=30, id="weather_1730", replace_existing=True)

    sched.start()
    _scheduler = sched
    print("[SCHED] started.", flush=True)


if ENABLE_SCHEDULER:
    # 若用 gunicorn，務必 workers=1，避免多份 scheduler 重複推播
    try:
        start_scheduler()
    except Exception as e:
        print("[SCHED] start_scheduler error:", e, flush=True)


# ====== routes ======
@app.route("/")
def index():
    return f"✅ {BOT_NAME} running", 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/media/<message_id>")
def media(message_id: str):
    # 可選保護：若設定 MEDIA_ACCESS_TOKEN，必須帶 k=
    if MEDIA_ACCESS_TOKEN:
        k = request.args.get("k", "")
        if k != MEDIA_ACCESS_TOKEN:
            abort(403)

    rec = get_media_record(message_id)
    if not rec:
        abort(404)

    filepath = MEDIA_DIR / rec["filename"]
    if not filepath.exists():
        abort(404)

    ctype = rec.get("content_type") or mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
    return send_file(filepath, mimetype=ctype, as_attachment=False)


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
