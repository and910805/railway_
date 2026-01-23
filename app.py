# app.py - 臭寶對話機器人（LINE webhook + SQLite + Multi-Weather + Photo tasks + Photo Forwarding + Conversation Bridge）
import os
import hmac
import base64
import hashlib
import datetime
import mimetypes
from pathlib import Path
from typing import Optional

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
    # subscribers/roles
    upsert_subscriber,
    set_role,
    set_active,
    get_role_map_active,
    get_couple_user_ids,
    get_display_name,
    get_user_role,
    # photo tasks
    create_photo_task,
    list_open_photo_tasks,
    claim_latest_open_task_for_role,
    # media
    save_media_record,
    get_media_record,
    # NEW
    mark_message_processed,
)


from weather_client import fetch_today_weather_metrics

app = Flask(__name__)

# ====== Env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()  # optional but recommended
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # fallback push target

BOT_NAME = os.getenv("BOT_NAME", "臭寶對話機器人")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")

DEFAULT_GIRLFRIEND_NICKNAME = os.getenv("GIRLFRIEND_NICKNAME", "臭寶")
DEFAULT_SELF_NICKNAME = os.getenv("SELF_NICKNAME", "臭晡晡")

# DB & media
LOVE_DB_PATH = os.getenv("LOVE_DB_PATH", "/data/love.db")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/data/media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or "").rstrip("/")  # e.g. https://xxx.zeabur.app
MEDIA_ACCESS_TOKEN = os.getenv("MEDIA_ACCESS_TOKEN", "").strip()

# Scheduler
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "0") == "1"

# Photo task expiry minutes (for auto "交作業" matching)
PHOTO_TASK_EXPIRE_MIN = int(os.getenv("PHOTO_TASK_EXPIRE_MIN", "180"))

# Conversation status tuning
ACTIVE_MINUTES = int(os.getenv("ACTIVE_MINUTES", "30"))
REPLIED_WINDOW_MINUTES = int(os.getenv("REPLIED_WINDOW_MINUTES", "180"))

# Weather thresholds
UV_HIGH_THRESHOLD = float(os.getenv("UV_HIGH_THRESHOLD", "8"))
HUMIDITY_RANGE_THRESHOLD = float(os.getenv("HUMIDITY_RANGE_THRESHOLD", "25"))

# Multi weather locations
# WEATHER_LOCATIONS=新竹,24.8138,120.9675;台南市,22.99,120.185;鹽水,23.31,120.24;嘉義市,23.48,120.449722
# app.py (near thresholds)
TEMP_LOW_THRESHOLD = float(os.getenv("TEMP_LOW_THRESHOLD", "14"))
APP_TEMP_LOW_THRESHOLD = float(os.getenv("APP_TEMP_LOW_THRESHOLD", "14"))

SETTINGS_GLOBAL_USER_ID = "__global__"
def _parse_task_list_env(key: str, fallback: list[str]) -> list[str]:
    """
    Env 格式建議用 | 分隔，例如：
    PHOTO_TASKS_FOR_GIRLFRIEND=自拍自己給我看|拍今日穿搭|拍你正在做的事
    """
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return fallback
    items = [x.strip() for x in raw.split("|") if x.strip()]
    return items or fallback

PHOTO_TASKS_FOR_GIRLFRIEND = _parse_task_list_env(
    "PHOTO_TASKS_FOR_GIRLFRIEND",
    [
        "自拍自己給我看",
        "拍你今天的穿搭",
        "拍你正在做的事（工作/上課/耍廢都可以）",
        "拍你手上的飲料或食物",
        "拍你現在看到的風景",
        "拍你笑一下（要露出可愛表情）",
    ],
)

PHOTO_TASKS_FOR_SELF = _parse_task_list_env(
    "PHOTO_TASKS_FOR_SELF",
    [
        "自拍給臭寶看",
        "拍你今天的穿搭",
        "拍你現在手邊在忙什麼",
        "拍你附近的風景",
    ],
)

def _get_threshold_float(key: str, default: float) -> float:
    v = get_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key=key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def parse_weather_locations() -> list[dict]:
    raw = (os.getenv("WEATHER_LOCATIONS") or "").strip()
    out: list[dict] = []
    if raw:
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            parts = [p.strip() for p in item.split(",")]
            if len(parts) != 3:
                continue
            name, lat_s, lon_s = parts
            try:
                out.append({"name": name, "lat": float(lat_s), "lon": float(lon_s)})
            except ValueError:
                continue

    # fallback single point (optional)
    if not out:
        city = os.getenv("WEATHER_CITY", "新竹")
        lat = os.getenv("WEATHER_LAT")
        lon = os.getenv("WEATHER_LON")
        if lat and lon:
            try:
                out.append({"name": city, "lat": float(lat), "lon": float(lon)})
            except ValueError:
                pass
    return out


WEATHER_LOCATIONS = parse_weather_locations()
WEATHER_LOC_MAP = {loc["name"]: loc for loc in WEATHER_LOCATIONS}

# Admins
ADMIN_LINE_USER_IDS = {x.strip() for x in os.getenv("ADMIN_LINE_USER_IDS", "").split(",") if x.strip()}

print("=== BOT BOOT ===", flush=True)
print("BOT_NAME:", BOT_NAME, flush=True)
print("TIMEZONE:", TIMEZONE, flush=True)
print("LOVE_DB_PATH:", LOVE_DB_PATH, flush=True)
print("PUBLIC_BASE_URL:", PUBLIC_BASE_URL or "(not set)", flush=True)
print("MEDIA_DIR:", str(MEDIA_DIR), flush=True)
print("MEDIA_ACCESS_TOKEN set?:", bool(MEDIA_ACCESS_TOKEN), flush=True)
print("WEATHER_LOCATIONS:", WEATHER_LOCATIONS, flush=True)
print("ENABLE_SCHEDULER:", ENABLE_SCHEDULER, flush=True)
print("PHOTO_TASK_EXPIRE_MIN:", PHOTO_TASK_EXPIRE_MIN, flush=True)
print("ADMIN_LINE_USER_IDS:", ",".join(sorted(ADMIN_LINE_USER_IDS)) or "(none)", flush=True)
print("LINE_TOKEN set?:", bool(LINE_CHANNEL_ACCESS_TOKEN), flush=True)
print("LINE_CHANNEL_SECRET set?:", bool(LINE_CHANNEL_SECRET), flush=True)
print("================", flush=True)

# Seed DB on boot
seed_defaults(db_path=LOVE_DB_PATH)


# ====== time helpers ======
def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _tz_now() -> datetime.datetime:
    return datetime.datetime.now(_tz())


def _iso_now() -> str:
    return _tz_now().isoformat(timespec="seconds")


def _parse_dt(s: str | None) -> Optional[datetime.datetime]:
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz())
        return dt
    except Exception:
        return None


def _mins_ago(dt: Optional[datetime.datetime]) -> Optional[int]:
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


def verify_line_signature(raw_body: bytes) -> bool:
    """
    If LINE_CHANNEL_SECRET is set, verify X-Line-Signature.
    """
    if not LINE_CHANNEL_SECRET:
        return True
    sig = request.headers.get("X-Line-Signature", "")
    mac = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, sig)


def line_reply(reply_token: str, message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        if resp.status_code != 200:
            print("Reply status:", resp.status_code, "body:", resp.text[:300], flush=True)
    except Exception as e:
        print("Reply error:", e, flush=True)


def line_push_messages(to_user_id: str, messages: list[dict]):
    if not LINE_CHANNEL_ACCESS_TOKEN or not to_user_id:
        return
    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": messages}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        if resp.status_code != 200:
            print("Push status:", resp.status_code, "body:", resp.text[:300], flush=True)
    except Exception as e:
        print("Push error:", e, flush=True)


def line_push_text(to_user_id: str, text: str):
    line_push_messages(to_user_id, [{"type": "text", "text": text}])


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
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE token missing")
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=25)
    if resp.status_code != 200:
        raise RuntimeError(f"LINE content fetch failed: {resp.status_code} {resp.text[:200]}")
    return resp.content, resp.headers.get("Content-Type")


def _ext_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".bin"
    c = content_type.split(";")[0].strip().lower()
    if c == "image/jpeg":
        return ".jpg"
    if c == "image/png":
        return ".png"
    if c == "image/gif":
        return ".gif"
    ext = mimetypes.guess_extension(c) or ".bin"
    return ext


def build_media_url(message_id: str) -> str | None:
    if not PUBLIC_BASE_URL:
        return None
    url = f"{PUBLIC_BASE_URL}/media/{message_id}"
    if MEDIA_ACCESS_TOKEN:
        url += f"?k={MEDIA_ACCESS_TOKEN}"
    return url


def push_to_couple_text(message: str, fallback_user_id: str | None = None):
    ids = [x for x in get_couple_user_ids(db_path=LOVE_DB_PATH) if x]
    if len(ids) >= 2:
        for uid in ids:
            line_push_text(uid, message)
        return

    fb: list[str] = []
    if fallback_user_id:
        fb.append(fallback_user_id)
    if LINE_TARGET_USER_ID and LINE_TARGET_USER_ID not in fb:
        fb.append(LINE_TARGET_USER_ID)

    for uid in fb:
        line_push_text(uid, message)


def _is_admin(user_id: str) -> bool:
    return (not ADMIN_LINE_USER_IDS) or (user_id in ADMIN_LINE_USER_IDS)


# ====== activity tracking ======
def upsert_activity(user_id: str, display_name: str, preview: str):
    upsert_subscriber(db_path=LOVE_DB_PATH, user_id=user_id, display_name=display_name)
    now_iso = _iso_now()
    set_setting(db_path=LOVE_DB_PATH, user_id=user_id, key="last_msg_ts", value=now_iso)
    set_setting(db_path=LOVE_DB_PATH, user_id=user_id, key="last_msg_preview", value=preview[:80])


# ====== chatbot: basic inference ======
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


# ====== weather text builders (per city) ======
def build_weather_alert_message(city_name: str, metrics: dict) -> str | None:
    # existing thresholds
    uv_th = _get_threshold_float("uv_high_threshold", UV_HIGH_THRESHOLD)
    hum_th = _get_threshold_float("humidity_range_threshold", HUMIDITY_RANGE_THRESHOLD)

    # new thresholds
    temp_low_th = _get_threshold_float("temp_low_threshold", TEMP_LOW_THRESHOLD)
    app_low_th = _get_threshold_float("app_temp_low_threshold", APP_TEMP_LOW_THRESHOLD)

    max_uv = metrics.get("max_uv")
    h_range = metrics.get("humidity_range")
    tmin = metrics.get("min_temp")
    amin = metrics.get("min_app_temp")

    triggers = []
    if max_uv is not None and max_uv >= uv_th:
        triggers.append("uv")
    if h_range is not None and h_range >= hum_th:
        triggers.append("humidity")
    if tmin is not None and tmin <= temp_low_th:
        triggers.append("temp_low")
    if amin is not None and amin <= app_low_th:
        triggers.append("app_temp_low")

    if not triggers:
        return None

    lines = []
    # compose message parts
    if "temp_low" in triggers or "app_temp_low" in triggers:
        t_part = f"溫度最低約 {tmin:.1f}°C" if tmin is not None else "溫度偏低"
        a_part = f"體感最低約 {amin:.1f}°C" if amin is not None else ""
        lines.append(f"🧥 天氣提醒：今天 {city_name} 偏冷（{t_part}" + (f"，{a_part}" if a_part else "") + "）")
        lines.append("出門記得加件外套、圍巾/帽子視情況。")

    if "uv" in triggers or "humidity" in triggers:
        uv_part = f"UV 最高約 {max_uv:.1f}" if max_uv is not None else "UV 偏高"
        hum_part = f"濕度波動約 {h_range:.0f}%" if h_range is not None else ""
        lines.append(f"☀️ 另：{city_name} {uv_part}" + (f"，{hum_part}" if hum_part else ""))

    return "\n".join(lines)



def build_weather_summary(city_name: str, metrics: dict) -> str:
    lines = [f"🌦️ {city_name} 今日摘要"]

    tn = metrics.get("temp_now")
    tmin = metrics.get("min_temp")
    tmax = metrics.get("max_temp")
    if tn is not None or (tmin is not None and tmax is not None):
        parts = []
        if tn is not None:
            parts.append(f"現在 {tn:.1f}°C")
        if tmin is not None and tmax is not None:
            parts.append(f"最低 {tmin:.1f}°C / 最高 {tmax:.1f}°C")
        lines.append("溫度：" + "，".join(parts))

    an = metrics.get("app_temp_now")
    amin = metrics.get("min_app_temp")
    amax = metrics.get("max_app_temp")
    if an is not None or (amin is not None and amax is not None):
        parts = []
        if an is not None:
            parts.append(f"現在 {an:.1f}°C")
        if amin is not None and amax is not None:
            parts.append(f"最低 {amin:.1f}°C / 最高 {amax:.1f}°C")
        lines.append("體感：" + "，".join(parts))

    max_uv = metrics.get("max_uv")
    if max_uv is not None:
        lines.append(f"UV 最高：約 {max_uv:.1f}")

    min_h = metrics.get("min_humidity")
    max_h = metrics.get("max_humidity")
    h_range = metrics.get("humidity_range")
    if min_h is not None and max_h is not None and h_range is not None:
        lines.append(f"濕度：約 {min_h:.0f}%～{max_h:.0f}%（波動 {h_range:.0f}%）")

    return "\n".join(lines)



def photo_options_text(target: str) -> str:
    gf = DEFAULT_GIRLFRIEND_NICKNAME
    me = DEFAULT_SELF_NICKNAME

    if target == "給臭寶":
        lines = [f"📋 攝影選項（{gf}）", "用法：攝影任務 給臭寶 #編號 或 攝影任務 給臭寶 <自訂內容>", ""]
        for i, t in enumerate(PHOTO_TASKS_FOR_GIRLFRIEND, 1):
            lines.append(f"#{i} {t}")
        return "\n".join(lines)

    if target == "給臭晡晡":
        lines = [f"📋 攝影選項（{me}）", "用法：攝影任務 給臭晡晡 #編號 或 攝影任務 給臭晡晡 <自訂內容>", ""]
        for i, t in enumerate(PHOTO_TASKS_FOR_SELF, 1):
            lines.append(f"#{i} {t}")
        return "\n".join(lines)

    # both
    lines = [
        "📋 攝影選項（雙方）",
        "用法：",
        "  攝影任務            -> 隨機互拍",
        "  攝影任務 給臭寶 #1  -> 指派臭寶做第 1 個選項",
        "  攝影任務 給臭寶 自拍自己給我看 -> 自訂內容",
        "  攝影任務 給臭晡晡 #2 -> 指派你做第 2 個選項",
        "",
        f"",
    ]
    for i, t in enumerate(PHOTO_TASKS_FOR_GIRLFRIEND, 1):
        lines.append(f"  #{i} {t}")
    lines.append("")
    lines.append(f"")
    for i, t in enumerate(PHOTO_TASKS_FOR_SELF, 1):
        lines.append(f"  #{i} {t}")
    return "\n".join(lines)


def pick_task_from_list(role: str, token: str) -> Optional[str]:
    """
    token can be "#3" or any custom text
    """
    token = (token or "").strip()
    if not token:
        return None

    if token.startswith("#"):
        try:
            idx = int(token[1:])
        except Exception:
            return None
        if role == "girlfriend":
            if 1 <= idx <= len(PHOTO_TASKS_FOR_GIRLFRIEND):
                return PHOTO_TASKS_FOR_GIRLFRIEND[idx - 1]
        if role == "boyfriend":
            if 1 <= idx <= len(PHOTO_TASKS_FOR_SELF):
                return PHOTO_TASKS_FOR_SELF[idx - 1]
        return None

    # custom
    return token


def build_photo_task_message(assign_role: str, task_text: str, task_id: int) -> str:
    gf = DEFAULT_GIRLFRIEND_NICKNAME
    me = DEFAULT_SELF_NICKNAME
    now = _tz_now().strftime("%m/%d %H:%M")

    if assign_role == "girlfriend":
        return (
            f"📸 攝影任務 #{task_id}（{now}）\n"
            f"{gf}：請 {task_text}\n"
            f"完成後把照片傳給 bot，我會自動轉送給 {me}。"
        )

    if assign_role == "boyfriend":
        return (
            f"📸 攝影任務 #{task_id}（{now}）\n"
            f"{me}：請 {task_text}\n"
            f"完成後把照片傳給 bot，我會自動轉送給 {gf}。"
        )

    # both (not used for single tasks; kept for completeness)
    return f"📸 攝影任務（{now}）\n{task_text}"


# ====== conversation status ======
def build_conversation_status_text() -> str:
    role_map = get_role_map_active(db_path=LOVE_DB_PATH)
    gf_id = role_map.get("girlfriend")
    bf_id = role_map.get("boyfriend")

    if not gf_id or not bf_id:
        return (
            "⚠️ 目前還不知道誰是臭寶/臭晡晡。\n"
            "請你先說：我是臭晡晡\n"
            "請臭寶說：我是臭寶"
        )

    gf_last_ts = _parse_dt(get_setting(db_path=LOVE_DB_PATH, user_id=gf_id, key="last_msg_ts"))
    bf_last_ts = _parse_dt(get_setting(db_path=LOVE_DB_PATH, user_id=bf_id, key="last_msg_ts"))
    gf_preview = get_setting(db_path=LOVE_DB_PATH, user_id=gf_id, key="last_msg_preview") or ""
    bf_preview = get_setting(db_path=LOVE_DB_PATH, user_id=bf_id, key="last_msg_preview") or ""

    gf_name = get_display_name(db_path=LOVE_DB_PATH, user_id=gf_id) or DEFAULT_GIRLFRIEND_NICKNAME
    bf_name = get_display_name(db_path=LOVE_DB_PATH, user_id=bf_id) or DEFAULT_SELF_NICKNAME

    now = _tz_now()

    def fmt(dt: Optional[datetime.datetime]) -> str:
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
    role_map = get_role_map_active(db_path=LOVE_DB_PATH)
    target_id = role_map.get(to_role)
    if not target_id:
        return "⚠️ 目標尚未設定角色。請雙方先說：我是臭寶 / 我是臭晡晡"
    if not content.strip():
        return "用法：跟臭寶說 <內容> 或 跟臭晡晡說 <內容>"

    sender_name = get_display_name(db_path=LOVE_DB_PATH, user_id=from_user_id) or "對方"
    now = _tz_now().strftime("%m/%d %H:%M")
    msg = f"💬 {sender_name}（{now}）想跟你說：\n{content.strip()}"

    line_push_text(target_id, msg)
    line_push_text(from_user_id, "✅ 已幫你送出。")
    return "✅ 已轉送（我也推播確認給你了）。"


# ====== help / command parsing ======
def help_text() -> str:
    cities = ", ".join(WEATHER_LOC_MAP.keys()) if WEATHER_LOC_MAP else "（未設定）"
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
        f"天氣（多地點：{cities}）：\n"
        "  天氣               - 全部地點摘要\n"
        "  天氣 <地點>         - 指定地點\n"
        "  天氣提醒            - 檢查全部地點，符合門檻才推播給雙方\n"
        "  天氣提醒 <地點>      - 只檢查指定地點並推播\n"
        "\n"
        "攝影任務（會推播給雙方）：\n"
        "  攝影選項            - 顯示選項清單\n"
        "  攝影選項 給臭寶      - 只看臭寶選項\n"
        "  攝影任務            - 隨機互拍（各一個）\n"
        "  攝影任務 給臭寶 #1   - 指派臭寶做第 1 個選項\n"
        "  攝影任務 給臭寶 自拍自己給我看  - 自訂內容\n"
        "  任務狀態            - 查看目前未完成任務\n"
        "\n"
        "照片轉送：\n"
        "  你或臭寶把照片傳給 bot，我會自動轉送給另一方。\n"
        "  若剛好在任務有效期內，會自動判定交作業並標記完成。\n"
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
    """
    支援：
      - 不用 /（也兼容 /）
      - 指令後可不打空白（例如：許願我想吃拉麵）
    """
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
        "攝影選項",
        "天氣",
        "天氣提醒",
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


# ====== photo forwarding (with task matching) ======
def forward_image_to_other_party(sender_user_id: str, sender_name: str, message_id: str) -> tuple[bool, str]:
    """
    Returns (forwarded_ok, note_text)
    note_text can include matched task info.
    """
    role_map = get_role_map_active(db_path=LOVE_DB_PATH)
    gf_id = role_map.get("girlfriend")
    bf_id = role_map.get("boyfriend")

    if not gf_id or not bf_id:
        return False, "roles not ready"

    if sender_user_id == gf_id:
        target_id = bf_id
        sender_role = "girlfriend"
        target_label = DEFAULT_SELF_NICKNAME
    elif sender_user_id == bf_id:
        target_id = gf_id
        sender_role = "boyfriend"
        target_label = DEFAULT_GIRLFRIEND_NICKNAME
    else:
        return False, "sender not in couple"

    media_url = build_media_url(message_id)
    if not media_url:
        return False, "PUBLIC_BASE_URL not set"

    # match latest open task for sender_role
    matched = claim_latest_open_task_for_role(
        db_path=LOVE_DB_PATH, role=sender_role, expire_minutes=PHOTO_TASK_EXPIRE_MIN
    )
    task_note = ""
    if matched:
        task_note = f"\n🎯 交作業：任務 #{matched['id']} - {matched['text']}"

    now = _tz_now().strftime("%m/%d %H:%M")
    caption = f"📷 {sender_name}（{now}）傳來一張照片給你（{target_label}）{task_note}"

    line_push_messages(
        target_id,
        [
            {"type": "text", "text": caption},
            {"type": "image", "originalContentUrl": media_url, "previewImageUrl": media_url},
        ],
    )
    return True, "forwarded"


# ====== command handler ======
def handle_command(user_id: str, text: str) -> str:
    cmd, arg = _cmd(text)

    if cmd in ("help", "說明", "幫助"):
        return help_text()

    # identity / push
    if cmd == "我是臭寶":
        set_role(db_path=LOVE_DB_PATH, user_id=user_id, role="girlfriend")
        set_active(db_path=LOVE_DB_PATH, user_id=user_id, is_active=True)
        return "✅ 已設定你是：臭寶（女友）。"

    if cmd == "我是臭晡晡":
        set_role(db_path=LOVE_DB_PATH, user_id=user_id, role="boyfriend")
        set_active(db_path=LOVE_DB_PATH, user_id=user_id, is_active=True)
        return "✅ 已設定你是：臭晡晡。"

    if cmd == "加入推播":
        set_active(db_path=LOVE_DB_PATH, user_id=user_id, is_active=True)
        return "✅ 已加入推播。"

    if cmd == "退出推播":
        set_active(db_path=LOVE_DB_PATH, user_id=user_id, is_active=False)
        return "✅ 已退出推播。"

    # conversation bridge
    if cmd in ("對話狀態", "狀態", "臭寶有沒有在跟我對話", "她在嗎", "有在嗎"):
        return build_conversation_status_text()

    if cmd == "跟臭寶說":
        return relay_message(user_id, "girlfriend", arg)

    if cmd == "跟臭晡晡說":
        return relay_message(user_id, "boyfriend", arg)

    # basic
    if cmd == "情話":
        row = random_love_line(db_path=LOVE_DB_PATH)
        if not row:
            return "資料庫目前沒有情話。你可以用：新增情話 你最可愛"
        return f"💌 情話 #{row['id']}\n{row['text']}"

    if cmd in ("臭寶在幹嘛", "啊晡在幹嘛", "阿晡在幹嘛"):
        nickname = get_setting(db_path=LOVE_DB_PATH, user_id=user_id, key="gf_nickname") or DEFAULT_GIRLFRIEND_NICKNAME
        return infer_activity_message(nickname)

    if cmd == "早安":
        row = random_love_line(db_path=LOVE_DB_PATH)
        extra = row["text"] if row else "今天也要順順的。"
        return f"早安。\n{extra}"

    if cmd == "晚安":
        row = random_love_line(db_path=LOVE_DB_PATH)
        extra = row["text"] if row else "做個好夢。"
        return f"晚安。\n{extra}"

    if cmd == "約會":
        idea = random_date_idea(db_path=LOVE_DB_PATH)
        if not idea:
            return "目前沒有約會靈感。你可以用：新增約會 去河堤散步"
        return f"🎡 約會靈感\n{idea['text']}"

    if cmd == "新增約會":
        if not arg:
            return "用法：新增約會 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        idea_id = add_date_idea(db_path=LOVE_DB_PATH, text=arg)
        return f"✅ 已新增約會靈感 #{idea_id}"

    # wishes/moods
    if cmd == "許願":
        if not arg:
            return "用法：許願 <內容>"
        wid = add_wish(db_path=LOVE_DB_PATH, user_id=user_id, text=arg)
        return f"✅ 願望已記下來了（#{wid}）"

    if cmd == "願望":
        ws = list_wishes(db_path=LOVE_DB_PATH, user_id=user_id, limit=10)
        if not ws:
            return "你目前沒有願望清單。用：許願 <內容> 來新增"
        lines = [f"#{w['id']} {w['text']} ({w['created_at']})" for w in ws]
        return "📝 願望清單（最近 10 筆）\n" + "\n".join(lines)

    if cmd == "心情":
        if not arg:
            return "用法：心情 <內容>（例如：心情 今天有點累但很想你）"
        mid = add_mood(db_path=LOVE_DB_PATH, user_id=user_id, text=arg)
        return f"✅ 心情已記錄（#{mid}）"

    if cmd == "回顧心情":
        ms = list_moods(db_path=LOVE_DB_PATH, user_id=user_id, limit=10)
        if not ms:
            return "目前還沒有心情記錄。用：心情 <內容> 來新增"
        lines = [f"#{m['id']} {m['text']} ({m['created_at']})" for m in ms]
        return "📒 最近心情（最近 10 筆）\n" + "\n".join(lines)

    # anniversary
    if cmd == "設定紀念日":
        if not arg:
            return "用法：設定紀念日 YYYY-MM-DD（例如：設定紀念日 2024-06-01）"
        v = arg.replace("/", "-")
        try:
            datetime.date.fromisoformat(v)
        except Exception:
            return "日期格式不對。請用 YYYY-MM-DD（例如 2024-06-01）"
        set_setting(db_path=LOVE_DB_PATH, user_id=user_id, key="anniversary", value=v)
        return f"✅ 紀念日已設定為 {v}"

    if cmd == "紀念日":
        v = get_setting(db_path=LOVE_DB_PATH, user_id=user_id, key="anniversary") or os.getenv("RELATION_START_DATE", "")
        if not v:
            return "你還沒設定紀念日。用：設定紀念日 YYYY-MM-DD"
        try:
            start = datetime.date.fromisoformat(v)
        except Exception:
            return "紀念日資料格式不正確，請重新設定：設定紀念日 YYYY-MM-DD"
        today = _tz_now().date()
        days = (today - start).days + 1
        return f"📅 我們在一起第 {days} 天\n（從 {start.isoformat()} 算起）"

    # ===== weather (multi-locations) =====
    if cmd == "天氣":
        # 天氣 / 天氣 台南市
        if arg:
            name = arg.strip()
            loc = WEATHER_LOC_MAP.get(name)
            if not loc:
                return f"找不到地點：{name}\n可用：{', '.join(WEATHER_LOC_MAP.keys())}"
            metrics = fetch_today_weather_metrics(lat=loc["lat"], lon=loc["lon"], timezone=TIMEZONE)
            return build_weather_summary(name, metrics)

        # all
        blocks = []
        for name, loc in WEATHER_LOC_MAP.items():
            metrics = fetch_today_weather_metrics(lat=loc["lat"], lon=loc["lon"], timezone=TIMEZONE)
            blocks.append(build_weather_summary(name, metrics))
        return "\n\n".join(blocks) if blocks else "⚠️ 尚未設定 WEATHER_LOCATIONS"
    if cmd in ("設定低溫", "設定體感低溫", "設定UV", "設定濕度波動"):
        if ADMIN_LINE_USER_IDS and user_id not in ADMIN_LINE_USER_IDS:
            return "⚠️ 只有管理者可以調整天氣門檻。"

        if not arg:
            return (
                "用法：\n"
                "• 設定低溫 14\n"
                "• 設定體感低溫 13\n"
                "• 設定UV 8\n"
                "• 設定濕度波動 25"
            )

        try:
            val = float(arg.strip())
        except Exception:
            return "數值格式錯誤，請輸入數字（例如：設定低溫 14）"

        key_map = {
            "設定低溫": "temp_low_threshold",
            "設定體感低溫": "app_temp_low_threshold",
            "設定UV": "uv_high_threshold",
            "設定濕度波動": "humidity_range_threshold",
        }
        k = key_map[cmd]
        set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key=k, value=str(val))
        return f"✅ 已更新門檻：{cmd} = {val}"

    if cmd in ("查看天氣門檻", "天氣門檻"):
        uv_th = _get_threshold_float("uv_high_threshold", UV_HIGH_THRESHOLD)
        hum_th = _get_threshold_float("humidity_range_threshold", HUMIDITY_RANGE_THRESHOLD)
        t_th = _get_threshold_float("temp_low_threshold", TEMP_LOW_THRESHOLD)
        a_th = _get_threshold_float("app_temp_low_threshold", APP_TEMP_LOW_THRESHOLD)
        return (
            "📌 目前天氣提醒門檻\n"
            f"• UV ≥ {uv_th}\n"
            f"• 濕度波動 ≥ {hum_th}%\n"
            f"• 最低溫 ≤ {t_th}°C\n"
            f"• 最低體感 ≤ {a_th}°C"
        )

    if cmd == "天氣提醒":
        # 天氣提醒 / 天氣提醒 鹽水
        if not WEATHER_LOC_MAP:
            return "⚠️ 尚未設定 WEATHER_LOCATIONS"

        targets: list[tuple[str, dict]] = []
        if arg:
            name = arg.strip()
            loc = WEATHER_LOC_MAP.get(name)
            if not loc:
                return f"找不到地點：{name}\n可用：{', '.join(WEATHER_LOC_MAP.keys())}"
            targets = [(name, loc)]
        else:
            targets = [(n, l) for n, l in WEATHER_LOC_MAP.items()]

        alerts = []
        for name, loc in targets:
            metrics = fetch_today_weather_metrics(lat=loc["lat"], lon=loc["lon"], timezone=TIMEZONE)
            msg = build_weather_alert_message(name, metrics)
            if msg:
                alerts.append(msg)

        if not alerts:
            return "✅ 目前天氣條件未達提醒門檻（UV/濕度波動都還 OK）。"

        push_to_couple_text("\n\n".join(alerts), fallback_user_id=user_id)
        return "✅ 已推播天氣提醒給雙方。"

    # ===== photo tasks =====
    if cmd == "攝影選項":
        # 攝影選項 / 攝影選項 給臭寶 / 攝影選項 給臭晡晡
        a = arg.strip()
        if a.startswith("給臭寶"):
            return photo_options_text("給臭寶")
        if a.startswith("給臭晡晡"):
            return photo_options_text("給臭晡晡")
        return photo_options_text("")

    if cmd == "任務狀態":
        open_tasks = list_open_photo_tasks(db_path=LOVE_DB_PATH, limit=10)
        if not open_tasks:
            return "✅ 目前沒有未完成的攝影任務。"
        lines = ["📌 未完成攝影任務（最近 10 筆）"]
        for t in open_tasks:
            lines.append(f"#{t['id']} [{t['assign_role']}] {t['text']}（到期：{t['expires_at']}）")
        return "\n".join(lines)

    if cmd == "攝影任務":
        # 支援：
        # 1) 攝影任務 -> 隨機互拍（各一個）
        # 2) 攝影任務 給臭寶 -> 隨機給臭寶
        # 3) 攝影任務 給臭寶 #1 -> 選項
        # 4) 攝影任務 給臭寶 自拍自己給我看 -> 自訂
        # 5) 攝影任務 選項 -> 等同攝影選項
        a = arg.strip()

        if a.startswith("選項"):
            return photo_options_text("")

        now = _tz_now()
        expires_at = (now + datetime.timedelta(minutes=PHOTO_TASK_EXPIRE_MIN)).isoformat(timespec="seconds")

        # detect target
        target = ""
        rest = ""
        if a.startswith("給臭寶"):
            target = "girlfriend"
            rest = a[len("給臭寶"):].strip()
        elif a.startswith("給臭晡晡"):
            target = "boyfriend"
            rest = a[len("給臭晡晡"):].strip()

        role_map = get_role_map_active(db_path=LOVE_DB_PATH)
        gf_id = role_map.get("girlfriend")
        bf_id = role_map.get("boyfriend")
        if not gf_id or not bf_id:
            # still allow creating tasks, but pushing might fallback
            pass

        if target in ("girlfriend", "boyfriend"):
            # choose or custom
            if not rest:
                # random
                import random

                if target == "girlfriend":
                    task_text = random.choice(PHOTO_TASKS_FOR_GIRLFRIEND)
                else:
                    task_text = random.choice(PHOTO_TASKS_FOR_SELF)
            else:
                t = pick_task_from_list(target, rest)
                if not t:
                    return "⚠️ 選項格式不對。用：攝影任務 給臭寶 #1 或 攝影任務 給臭寶 <自訂內容>"
                task_text = t

            task_id = create_photo_task(
                db_path=LOVE_DB_PATH,
                assign_role=target,
                text=task_text,
                created_by=user_id,
                expires_at=expires_at,
            )
            msg = build_photo_task_message(target, task_text, task_id)
            push_to_couple_text(msg, fallback_user_id=user_id)
            return "✅ 已派發攝影任務給雙方。（交作業：把照片傳給 bot，我會自動轉送）"

        # else: mutual tasks (two tasks)
        import random
        gf_task = random.choice(PHOTO_TASKS_FOR_GIRLFRIEND)
        me_task = random.choice(PHOTO_TASKS_FOR_SELF)

        gf_task_id = create_photo_task(
            db_path=LOVE_DB_PATH, assign_role="girlfriend", text=gf_task, created_by=user_id, expires_at=expires_at
        )
        me_task_id = create_photo_task(
            db_path=LOVE_DB_PATH, assign_role="boyfriend", text=me_task, created_by=user_id, expires_at=expires_at
        )

        gf_msg = build_photo_task_message("girlfriend", gf_task, gf_task_id)
        me_msg = build_photo_task_message("boyfriend", me_task, me_task_id)

        push_to_couple_text(f"{gf_msg}\n\n{me_msg}", fallback_user_id=user_id)
        return "✅ 已派發互拍任務給雙方。（交作業：把照片傳給 bot，我會自動轉送）"

    # ===== admin =====
    if cmd == "新增情話":
        if not arg:
            return "用法：新增情話 <內容>"
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        lid = add_love_line(db_path=LOVE_DB_PATH, text=arg)
        return f"✅ 情話已新增 #{lid}"

    if cmd == "列表情話":
        if not _is_admin(user_id):
            return "這個指令目前只開給管理員使用。"
        rows = list_love_lines(db_path=LOVE_DB_PATH, limit=20)
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
        ok = delete_love_line(db_path=LOVE_DB_PATH, love_id=lid)
        return f"🗑️ 已刪除 #{lid}" if ok else "❌ 找不到這筆 id"

    # fallback
    if not cmd:
        row = random_love_line(db_path=LOVE_DB_PATH)
        if row:
            return f"我在。\n{row['text']}\n\n（想看指令打 help）"
        return "我在～（想看指令打 help）"

    return "我看不懂這個指令耶，打 help 我給你清單。"


# ====== webhook processing ======
def process_line_events(body):
    if not body or "events" not in body:
        return

    for ev in body.get("events", []):
        if ev.get("type") != "message":
            continue

        msg = ev.get("message", {})
        msg_type = msg.get("type")
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId") or "unknown"
        message_id = msg.get("id")  # IMPORTANT: use for dedupe

        # --- DEDUPE: avoid LINE retries causing duplicate side-effects ---
        if message_id:
            try:
                is_new = mark_message_processed(
                    db_path=LOVE_DB_PATH,
                    message_id=message_id,
                    user_id=user_id,
                    msg_type=msg_type or "",
                )
                if not is_new:
                    print(f"[DEDUPE] skip {msg_type} id={message_id}", flush=True)
                    continue
            except Exception as e:
                # If dedupe fails, keep processing (do not drop events)
                print("[DEDUPE] mark_message_processed error:", e, flush=True)

        # Now do the slower profile call
        profile = get_line_profile(user_id) or {}
        display_name = profile.get("displayName") or ""

        if msg_type == "text":
            text = (msg.get("text") or "").strip()
            print(f"[IN] {display_name}({user_id}): {text}", flush=True)
            upsert_activity(user_id, display_name, preview=text)

            out = handle_command(user_id, text)
            if reply_token:
                line_reply(reply_token, out)
            continue

        if msg_type == "image":
            # (keep your existing logic, but now message_id already exists)
            print(f"[IN] {display_name}({user_id}): <image> id={message_id}", flush=True)
            upsert_activity(user_id, display_name, preview="[image]")

            try:
                content, ctype = line_get_message_content(message_id)
                ext = _ext_from_content_type(ctype)
                filename = f"{message_id}{ext}"
                filepath = MEDIA_DIR / filename
                filepath.write_bytes(content)

                save_media_record(
                    db_path=LOVE_DB_PATH,
                    message_id=message_id,
                    filename=filename,
                    content_type=ctype,
                    from_user_id=user_id,
                    created_at=_iso_now(),
                )
                print(f"[MEDIA] saved {filepath} ({ctype})", flush=True)

                ok, note = forward_image_to_other_party(
                    sender_user_id=user_id,
                    sender_name=display_name or "對方",
                    message_id=message_id,
                )

                if reply_token:
                    if ok:
                        line_reply(reply_token, "✅ 收到照片了，我已經幫你轉送給對方。")
                    else:
                        line_reply(reply_token, f"✅ 收到照片了，但目前無法轉送（{note}）。")

            except Exception as e:
                print("[MEDIA] image handling error:", e, flush=True)
                if reply_token:
                    line_reply(reply_token, f"✅ 我收到照片了，但處理/轉送失敗：{e}")
            continue

        preview = f"[{msg_type}]"
        print(f"[IN] {display_name}({user_id}): {preview}", flush=True)
        upsert_activity(user_id, display_name, preview=preview)
        if reply_token:
            line_reply(reply_token, "✅ 收到～")



# ====== scheduler: proactive weather push ======
_scheduler: BackgroundScheduler | None = None


def scheduled_weather_check():
    if not WEATHER_LOC_MAP:
        print("[SCHED] WEATHER_LOCATIONS empty; skip.", flush=True)
        return

    try:
        alerts = []
        for name, loc in WEATHER_LOC_MAP.items():
            metrics = fetch_today_weather_metrics(lat=loc["lat"], lon=loc["lon"], timezone=TIMEZONE)
            msg = build_weather_alert_message(name, metrics)
            if msg:
                alerts.append(msg)

        if not alerts:
            print("[SCHED] Weather ok; no push.", flush=True)
            return

        push_to_couple_text("\n\n".join(alerts), fallback_user_id=None)
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
    # 用 gunicorn 時務必 workers=1，避免多份 scheduler 重複推播
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
    if MEDIA_ACCESS_TOKEN:
        if request.args.get("k", "") != MEDIA_ACCESS_TOKEN:
            abort(403)

    rec = get_media_record(db_path=LOVE_DB_PATH, message_id=message_id)
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

    raw = request.get_data()  # bytes
    if not verify_line_signature(raw):
        return jsonify({"ok": False, "error": "bad signature"}), 403

    body = request.get_json(silent=True) or {}
    process_line_events(body)
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
