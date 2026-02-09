# app.py - 臭寶對話機器人（LINE webhook + SQLite + Multi-Weather + Photo tasks + Photo Forwarding + Conversation Bridge）
import os
import hmac
import base64
import hashlib
import datetime
import time
import asyncio
import threading
import mimetypes
import json
from PIL import Image, ImageOps
from pathlib import Path
from typing import Optional
import re

import requests
from flask import Flask, jsonify, request, send_file, abort, render_template_string, redirect, session, g
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler

from db_love import (
    ensure_med_pill_row,
    get_med_pill_row,
    list_med_pill_rows_between,
    set_med_pill_taken,
    mark_med_pill_reminded,
    seed_defaults,
    clear_med_pill_taken,
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
    get_settings_map,
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
    expire_open_photo_tasks,
    list_photo_tasks,
    list_task_media_items,
    list_media_records,
    list_media_records_with_task,
    claim_latest_open_task_for_role,
    # media
    save_media_record,
    get_media_record,
    # NEW
    mark_message_processed,
    create_dashboard_magic_token,
    consume_dashboard_magic_token,
    # conflict repair workflow
    create_conflict_event,
    get_conflict_event,
    list_conflict_events,
    list_open_conflict_events,
    list_conflict_triggers_for_event,
    set_conflict_event_triggers,
    list_conflict_confirmed_users,
    confirm_conflict_event,
    list_conflict_trigger_top,
    get_no_blowup_streak_days,
    list_conflict_triggers_map,
    list_conflict_confirms_map,
    list_conflict_events_ready_for_cooldown_notify,
    mark_conflict_cooldown_notified,
    create_game_session,
    get_game_week_stats,
    list_game_sessions_since,
    get_repair_week_stats,
)


from weather_client import fetch_today_weather_metrics
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("love-bot")

app = Flask(__name__)

# ====== Request timing (debug perf) ======
@app.before_request
def _timing_start():
    try:
        g._t0 = time.time()
    except Exception:
        pass

@app.after_request
def _timing_end(resp):
    try:
        t0 = getattr(g, '_t0', None)
        if t0:
            resp.headers['X-Server-Duration-ms'] = f"{(time.time()-t0)*1000:.1f}"
    except Exception:
        pass
    return resp


# ====== Env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()  # optional but recommended


# ====== Discord (optional) ======
# - ENABLE_DISCORD_BOT=1 to enable bot runtime (or provide DISCORD_BOT_TOKEN)
# - DISCORD_DM_ONLY=1 to only react in DMs
# - DISCORD_ALLOWED_USER_IDS / DISCORD_ALLOWED_CHANNEL_IDS: comma-separated allowlists (optional)
DISCORD_BOT_TOKEN = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
ENABLE_DISCORD_BOT = os.getenv("ENABLE_DISCORD_BOT", "0") == "1" or bool(DISCORD_BOT_TOKEN)
DISCORD_DM_ONLY = os.getenv("DISCORD_DM_ONLY", "0") == "1"
DISCORD_ALLOWED_USER_IDS = set(
    [s.strip() for s in (os.getenv("DISCORD_ALLOWED_USER_IDS") or "").split(",") if s.strip()]
)
DISCORD_ALLOWED_CHANNEL_IDS = set(
    [s.strip() for s in (os.getenv("DISCORD_ALLOWED_CHANNEL_IDS") or "").split(",") if s.strip()]
)

DISCORD_DEBUG = os.getenv("DISCORD_DEBUG", "0") == "1"


# Discord push (broadcast) targets for scheduled notifications
# - DISCORD_PUSH_CHANNEL_IDS / DISCORD_PUSH_CHANNEL_ID: comma-separated channel IDs for broadcast pushes
# - DISCORD_PUSH_DM=1 to also DM role-bound users on Discord (default: auto; DM only if no push channels set)
_raw_push_channels = (os.getenv("DISCORD_PUSH_CHANNEL_IDS") or os.getenv("DISCORD_PUSH_CHANNEL_ID") or "").strip()
DISCORD_PUSH_CHANNEL_IDS = set([s.strip() for s in _raw_push_channels.split(",") if s.strip()])

if os.getenv("DISCORD_PUSH_DM") is None:
    # If you configured broadcast channels, default to NOT DM (avoid double notifications).
    DISCORD_PUSH_DM = not bool(DISCORD_PUSH_CHANNEL_IDS)
else:
    DISCORD_PUSH_DM = os.getenv("DISCORD_PUSH_DM", "0") == "1"




# ====== Flask session (for dashboard login) ======
# 建議在 Zeabur 設定：
#   FLASK_SECRET_KEY=一段夠長的隨機字串（或用 SECRET_KEY）
# 若沒設定，會用隨機值，缺點是重啟後已登入的 dashboard 會失效（需從 LINE 再拿一次登入連結）
FLASK_SECRET_KEY = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or os.getenv("LINE_CHANNEL_SECRET") or "").strip()
if not FLASK_SECRET_KEY:
    FLASK_SECRET_KEY = os.urandom(32).hex()
    print("[WARN] FLASK_SECRET_KEY/SECRET_KEY not set; using random key (sessions reset on restart).", flush=True)
app.secret_key = FLASK_SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Zeabur 對外通常是 HTTPS；若 PUBLIC_BASE_URL 是 https:// 開頭，就把 cookie 設為 Secure
try:
    if (os.getenv("PUBLIC_BASE_URL") or "").lower().startswith("https://"):
        app.config["SESSION_COOKIE_SECURE"] = True
except Exception:
    pass
LINE_TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID")  # fallback push target

BOT_NAME = os.getenv("BOT_NAME", "臭寶對話機器人")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

def get_public_base_url() -> str:
    """Best-effort public base URL for links in LINE & docs page."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    # Zeabur/Reverse proxy headers
    proto = request.headers.get("X-Forwarded-Proto") or "https"
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
    return f"{proto}://{host}".rstrip("/")

DOC_SECTIONS = [
    {
        "id": "quickstart",
        "title": "快速開始（3 步）",
        "items": [
            {"cmd": "我是臭寶", "desc": "設定身份（女友）"},
            {"cmd": "我是臭晡晡", "desc": "設定身份（男友）"},
            {"cmd": "加入推播", "desc": "讓你收到提醒/通知"},
        ],
        "note": "建議兩個人都先設定身份 + 加入推播，後續提醒與照片轉送才會完整運作。",
    },
    {
        "id": "pill",
        "title": "事前藥（吃藥提醒/紀錄）",
        "items": [
            {"cmd": "吃了", "desc": "回報今天已吃（也可用：吃完 / 吃了 21:30）"},
            {"cmd": "吃藥狀態", "desc": "查看今天是否已回報"},
            {"cmd": "吃藥紀錄 14", "desc": "列出最近 N 天吃藥時間（最多 30 天）"},
            {"cmd": "取消吃藥", "desc": "撤銷今天的回報（誤傳可用）"},
        ],
        "note": "你只要正常回：『吃了』或『吃完 21:30』，bot 會記錄並通知另一方。",
    },
    {
        "id": "duo",
        "title": "Duolingo（連勝提醒）",
        "items": [
            {"cmd": "Duolingo狀態", "desc": "查看提醒是否開啟、今天是否已玩"},
            {"cmd": "Duolingo提醒開", "desc": "每天 22:00 起每 10 分鐘提醒一次"},
            {"cmd": "Duolingo提醒關", "desc": "關閉提醒"},
            {"cmd": "Duolingo已玩", "desc": "今天已完成，今晚不再提醒"},
        ],
        "note": "提醒預設 22:00～23:50；想延長到半夜或改頻率我也可以幫你改成可設定的時間窗。",
    },
    {
        "id": "bridge",
        "title": "對話橋樑（代傳訊息）",
        "items": [
            {"cmd": "跟臭寶說 今天要不要吃壽司", "desc": "轉達訊息給對方"},
            {"cmd": "跟臭晡晡說 我下班了", "desc": "轉達訊息給對方"},
            {"cmd": "對話狀態", "desc": "查看橋樑模式/互動狀態摘要"},
        ],
    },
    {
        "id": "photo",
        "title": "攝影任務 & 照片轉送",
        "items": [
            {"cmd": "攝影選項", "desc": "列出可用任務選項"},
            {"cmd": "攝影任務", "desc": "隨機互拍（雙方各一個）"},
            {"cmd": "攝影任務 給臭寶 自拍自己給我看", "desc": "指定任務內容"},
            {"cmd": "任務狀態", "desc": "查看未完成任務"},
        ],
        "note": "把照片傳給 bot 會自動轉送給對方；若在任務期間會自動判定交作業並標記完成。",
    },
    {
        "id": "weather",
        "title": "天氣",
        "items": [
            {"cmd": "天氣", "desc": "看全部地點天氣摘要"},
            {"cmd": "天氣 台北", "desc": "指定地點天氣"},
            {"cmd": "天氣提醒", "desc": "符合門檻才推播提醒"},
        ],
    },
]

DOC_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }}｜使用說明</title>
    <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --sky-50:#f0f9ff; --sky-700:#0369a1;
      --shadow: 0 10px 30px rgba(15, 23, 42, .08);
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
      --radius: 18px;
    }
    html,body{height:100%;}
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    a{color:inherit;}
    /* layout */
    .wrap{max-width:1100px; margin:0 auto; padding:18px 16px 44px;}
    .max-w-3xl{max-width:768px;}
    .max-w-5xl{max-width:1024px;}
    .max-w-6xl{max-width:1152px;}
    .mx-auto{margin-left:auto; margin-right:auto;}
    .ml-auto{margin-left:auto;}
    .block{display:block;}
    .w-full{width:100%;}
    .min-w-full{min-width:100%;}
    .overflow-hidden{overflow:hidden;}
    .overflow-x-auto{overflow-x:auto;}
    .break-all{word-break:break-all;}
    .flex{display:flex;}
    .inline-flex{display:inline-flex;}
    .flex-col{flex-direction:column;}
    .flex-wrap{flex-wrap:wrap;}
    .items-center{align-items:center;}
    .items-end{align-items:flex-end;}
    .items-start{align-items:flex-start;}
    .justify-between{justify-content:space-between;}
    .grid{display:grid;}
    .grid-cols-1{grid-template-columns:1fr;}
    .gap-1{gap:4px;}
    .gap-2{gap:8px;}
    .gap-3{gap:12px;}
    .gap-4{gap:16px;}
    .space-y-2 > * + *{margin-top:8px;}
    .space-y-3 > * + *{margin-top:12px;}
    .space-y-6 > * + *{margin-top:24px;}
    .divide-y > * + *{border-top:1px solid var(--slate-200);}
    /* responsive (subset) */
    @media (min-width:768px){
      .md\:flex-row{flex-direction:row;}
      .md\:items-end{align-items:flex-end;}
      .md\:items-start{align-items:flex-start;}
      .md\:justify-between{justify-content:space-between;}
      .md\:grid-cols-2{grid-template-columns:repeat(2, minmax(0,1fr));}
      .md\:col-span-2{grid-column:span 2 / span 2;}
      .md\:mt-0{margin-top:0;}
      .md\:text-3xl{font-size:30px;}
    }
    @media (min-width:1024px){
      .lg\:grid-cols-3{grid-template-columns:repeat(3, minmax(0,1fr));}
    }
    /* spacing */
    .p-3{padding:12px;}
    .p-4{padding:16px;}
    .p-5{padding:20px;}
    .p-6{padding:24px;}
    .px-2{padding-left:8px; padding-right:8px;}
    .px-3{padding-left:12px; padding-right:12px;}
    .px-4{padding-left:16px; padding-right:16px;}
    .px-5{padding-left:20px; padding-right:20px;}
    .py-1{padding-top:4px; padding-bottom:4px;}
    .py-2{padding-top:8px; padding-bottom:8px;}
    .py-10{padding-top:40px; padding-bottom:40px;}
    .mt-1{margin-top:4px;}
    .mt-2{margin-top:8px;}
    .mt-3{margin-top:12px;}
    .mt-4{margin-top:16px;}
    .mt-5{margin-top:20px;}
    .mt-6{margin-top:24px;}
    .mt-8{margin-top:32px;}
    .mt-10{margin-top:40px;}
    .pr-4{padding-right:16px;}
    /* typography */
    .font-bold{font-weight:800;}
    .font-semibold{font-weight:700;}
    .font-mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .tracking-tight{letter-spacing:-0.02em;}
    .text-left{text-align:left;}
    .text-xs{font-size:12px;}
    .text-sm{font-size:13px;}
    .text-lg{font-size:18px;}
    .text-xl{font-size:20px;}
    .text-2xl{font-size:26px;}
    .text-3xl{font-size:30px;}
    .text-white{color:#fff;}
    .text-slate-500{color:var(--slate-500);}
    .text-slate-600{color:var(--slate-600);}
    .text-slate-700{color:var(--slate-700);}
    .text-slate-800{color:var(--slate-800);}
    .text-slate-900{color:var(--slate-900);}
    .text-emerald-700{color:var(--emerald-700);}
    .text-emerald-900{color:var(--emerald-900);}
    .text-amber-700{color:var(--amber-700);}
    .text-amber-800{color:var(--amber-800);}
    .text-amber-900{color:var(--amber-900);}
    .text-rose-900{color:var(--rose-900);}
    .text-sky-700{color:var(--sky-700);}
    .underline{text-decoration:underline;}
    /* surfaces */
    .bg-white{background:#fff;}
    .bg-slate-50{background:var(--slate-50);}
    .bg-slate-100{background:var(--slate-100);}
    .bg-slate-900{background:var(--slate-900);}
    .bg-emerald-50{background:var(--emerald-50);}
    .bg-amber-50{background:var(--amber-50);}
    .bg-rose-50{background:var(--rose-50);}
    .bg-sky-50{background:var(--sky-50);}
    .border{border-width:1px; border-style:solid;}
    .border-slate-200{border-color:var(--slate-200);}
    .border-slate-300{border-color:var(--slate-300);}
    .border-emerald-200{border-color:var(--emerald-200);}
    .border-amber-200{border-color:var(--amber-200);}
    .border-rose-200{border-color:var(--rose-200);}
    .rounded{border-radius:10px;}
    .rounded-lg{border-radius:12px;}
    .rounded-xl{border-radius:16px;}
    .rounded-2xl{border-radius:20px;}
    .rounded-full{border-radius:999px;}
    .shadow{box-shadow:var(--shadow);}
    .shadow-sm{box-shadow:var(--shadow-sm);}
    .h-auto{height:auto;}
    /* hover (subset) */
    .hover\:bg-slate-50:hover{background:var(--slate-50);}
    .hover\:text-slate-800:hover{color:var(--slate-800);}
    /* buttons (for login templates) */
    .btn{display:inline-block; padding:10px 14px; border-radius:14px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:800;}
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:16px; box-shadow:var(--shadow-sm);}
    /* Gallery helpers */
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="mx-auto max-w-3xl px-4 py-10">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h1 class="text-3xl font-bold tracking-tight">{{ bot_name }} 使用說明</h1>
        <p class="mt-2 text-sm text-slate-600">
          這頁是給人看的完整版教學；LINE 內的 help 會只給「精簡版 + 連結」，避免字數/排版限制。
        </p>
        <p class="mt-1 text-xs text-slate-500">更新時間：{{ updated_at }}</p>
      </div>
      <div class="rounded-xl bg-white shadow p-4 text-xs text-slate-600">
        <div class="font-semibold text-slate-800">入口連結</div>
        <div class="mt-1 break-all">{{ base_url }}/docs</div>
      </div>
    </div>

    <div class="mt-8 rounded-2xl bg-white shadow p-6">
      <h2 class="text-xl font-semibold">目錄</h2>
      <div class="mt-3 flex flex-wrap gap-2">
        {% for s in sections %}
          <a href="#{{ s.id }}" class="rounded-full border border-slate-200 px-3 py-1 text-sm hover:bg-slate-50">{{ s.title }}</a>
        {% endfor %}
      </div>
    </div>

    {% for s in sections %}
      <section id="{{ s.id }}" class="mt-8 rounded-2xl bg-white shadow p-6">
        <div class="flex items-center justify-between">
          <h2 class="text-xl font-semibold">{{ s.title }}</h2>
          <a href="#top" class="text-sm text-slate-500 hover:text-slate-800">回到頂部</a>
        </div>

        {% if s.note %}
          <p class="mt-2 text-sm text-slate-600">{{ s.note }}</p>
        {% endif %}

        <div class="mt-4 space-y-3">
          {% for it in s.get('items', []) %}
            <div class="rounded-xl border border-slate-200 p-4">
              <div class="flex items-center justify-between gap-3">
                <code class="text-sm font-semibold text-slate-900 break-all">{{ it.cmd }}</code>
                <button class="text-xs rounded-lg border border-slate-200 px-2 py-1 hover:bg-slate-50"
                        onclick="navigator.clipboard.writeText('{{ it.cmd }}')">複製</button>
              </div>
              <p class="mt-2 text-sm text-slate-600">{{ it.desc }}</p>
            </div>
          {% endfor %}
        </div>
      </section>
    {% endfor %}

    <div class="mt-10 text-xs text-slate-500">
      <p>想把這頁做得更像「產品官網」：我可以幫你加 FAQ、示意圖、QR code、甚至做成獨立前端（Vite/Next.js）在 Zeabur 另一個 service。</p>
    </div>
  </div>

  <a id="top"></a>
</body>
</html>"""

DEFAULT_GIRLFRIEND_NICKNAME = os.getenv("GIRLFRIEND_NICKNAME", "臭寶")
DEFAULT_SELF_NICKNAME = os.getenv("SELF_NICKNAME", "臭晡晡")

# DB & media
LOVE_DB_PATH = os.getenv("LOVE_DB_PATH", "/data/love.db")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/data/media"))
THUMB_DIR = Path(os.getenv("THUMB_DIR", str(MEDIA_DIR / "_thumbs")))

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or "").rstrip("/")  # e.g. https://xxx.zeabur.app
MEDIA_ACCESS_TOKEN = os.getenv("MEDIA_ACCESS_TOKEN", "").strip()

# Scheduler
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "0") == "1"
# Medication (pill) reminder
MED_PILL_ENABLED = os.getenv("MED_PILL_ENABLED", "0") == "1"
MED_PILL_REMIND_TIME = os.getenv("MED_PILL_REMIND_TIME", "21:30")          # HH:MM
MED_PILL_NUDGE_MINUTES = int(os.getenv("MED_PILL_NUDGE_MINUTES", "30"))    # every 30 min if no reply
MED_PILL_QUIET_HOURS = os.getenv("MED_PILL_QUIET_HOURS", "00:00-07:00")    # set "" to disable


MED_PILL_MAX_REMIND_COUNT = int(os.getenv("MED_PILL_MAX_REMIND_COUNT", "0"))  # 0 = unlimited

# Duolingo streak reminder
DUO_REMIND_ENABLED = os.getenv("DUO_REMIND_ENABLED", "1") == "1"
DUO_REMIND_EVERY_MINUTES = int(os.getenv("DUO_REMIND_EVERY_MINUTES", "10"))
DUO_REMIND_START_HOUR = int(os.getenv("DUO_REMIND_START_HOUR", "22"))   # 22 = 10pm
DUO_REMIND_END_HOUR = int(os.getenv("DUO_REMIND_END_HOUR", "23"))       # 23 = 11pm

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
TEMP_HIGH_THRESHOLD = float(os.getenv("TEMP_HIGH_THRESHOLD", "32"))
APP_TEMP_HIGH_THRESHOLD = float(os.getenv("APP_TEMP_HIGH_THRESHOLD", "34"))

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

def _parse_hhmm(s: str, default=(21, 30)) -> tuple[int, int]:
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*[:：]\s*(\d{2})", s)
    if not m:
        return default
    h = int(m.group(1))
    mm = int(m.group(2))
    if 0 <= h <= 23 and 0 <= mm <= 59:
        return (h, mm)
    return default

# ===== global (DB) settings helpers =====
def _global_settings_map() -> dict:
    """Request-scoped cache of global settings to avoid opening SQLite connections repeatedly."""
    try:
        cached = getattr(g, "_global_settings_map", None)
    except Exception:
        cached = None
    if cached is None:
        try:
            cached = get_settings_map(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID) or {}
        except Exception:
            cached = {}
        try:
            g._global_settings_map = cached
        except Exception:
            pass
    return cached

def _get_setting_global(key: str) -> str | None:
    try:
        return _global_settings_map().get(key)
    except Exception:
        return None

def _get_str_setting_global(key: str, default: str, allow_empty: bool = False) -> str:
    v = _get_setting_global(key)
    if v is None:
        return default
    s = str(v)
    if s == "" and not allow_empty:
        return default
    return s

def _get_bool_setting_global(key: str, default: bool) -> bool:
    v = _get_setting_global(key)
    if v is None:
        return default
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")

def _get_int_setting_global(key: str, default: int, min_v: int | None = None, max_v: int | None = None) -> int:
    v = _get_setting_global(key)
    if v is None or str(v).strip() == "":
        out = int(default)
    else:
        try:
            out = int(float(str(v).strip()))
        except Exception:
            out = int(default)
    if min_v is not None:
        out = max(min_v, out)
    if max_v is not None:
        out = min(max_v, out)
    return out

def _get_float_setting_global(key: str, default: float, min_v: float | None = None, max_v: float | None = None) -> float:
    v = _get_setting_global(key)
    if v is None or str(v).strip() == "":
        out = float(default)
    else:
        try:
            out = float(str(v).strip())
        except Exception:
            out = float(default)
    if min_v is not None:
        out = max(min_v, out)
    if max_v is not None:
        out = min(max_v, out)
    return out

def _get_hhmm_setting_global(key: str, default_hhmm: str) -> tuple[int, int]:
    raw = _get_str_setting_global(key, default_hhmm)
    return _parse_hhmm(raw, default=_parse_hhmm(default_hhmm))

def _parse_times_csv(raw: str) -> list[tuple[int, int]]:
    # "08:30,12:30,17:30"
    out: list[tuple[int, int]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        h, m = _parse_hhmm(part, default=(-1, -1))
        if 0 <= h <= 23 and 0 <= m <= 59:
            out.append((h, m))
    # de-dup
    uniq = []
    seen = set()
    for h, m in out:
        key = (h, m)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((h, m))
    return uniq

def _format_times_csv(times: list[tuple[int, int]]) -> str:
    return ",".join([f"{h:02d}:{m:02d}" for h, m in times])

# ===== Weather reminder settings =====
def weather_remind_enabled() -> bool:
    # default: enabled
    return _get_bool_setting_global("weather_remind_enabled", True)

def weather_remind_times() -> list[tuple[int, int]]:
    default_times = _parse_times_csv(_get_str_setting_global("weather_remind_times", "08:30,12:30,17:30"))
    return default_times or [(8, 30), (12, 30), (17, 30)]

# ===== Medication reminder settings =====
def med_pill_enabled() -> bool:
    return _get_bool_setting_global("med_pill_enabled", MED_PILL_ENABLED)

def med_pill_remind_hm() -> tuple[int, int]:
    # HH:MM
    return _get_hhmm_setting_global("med_pill_remind_time", MED_PILL_REMIND_TIME)

def med_pill_nudge_minutes() -> int:
    return _get_int_setting_global("med_pill_nudge_minutes", MED_PILL_NUDGE_MINUTES, 1, 12 * 60)

def med_pill_max_remind_count() -> int:
    # 0 = unlimited
    return _get_int_setting_global("med_pill_max_remind_count", MED_PILL_MAX_REMIND_COUNT, 0, 99)

def med_pill_quiet_hours() -> str:
    # format: "00:00-07:00", set "" to disable
    return _get_str_setting_global("med_pill_quiet_hours", MED_PILL_QUIET_HOURS, allow_empty=True)

def _in_quiet_hours(now: datetime.datetime) -> bool:
    raw = (med_pill_quiet_hours() or "").strip()
    if not raw:
        return False
    m = re.fullmatch(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", raw)
    if not m:
        return False
    sh, sm = _parse_hhmm(m.group(1), default=(0, 0))
    eh, em = _parse_hhmm(m.group(2), default=(7, 0))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        # crosses midnight
        return now >= start or now <= end
    return start <= now <= end

# ===== Duolingo reminder helpers =====
def duo_remind_enabled() -> bool:
    # settings override env default
    return _get_bool_setting_global("duo_remind_enabled", DUO_REMIND_ENABLED)

def duo_remind_every_minutes() -> int:
    return _get_int_setting_global("duo_remind_every_minutes", DUO_REMIND_EVERY_MINUTES, 1, 59)

def duo_remind_start_hour() -> int:
    return _get_int_setting_global("duo_remind_start_hour", DUO_REMIND_START_HOUR, 0, 23)

def duo_remind_end_hour() -> int:
    return _get_int_setting_global("duo_remind_end_hour", DUO_REMIND_END_HOUR, 0, 23)

def duo_done_today(now: datetime.datetime) -> bool:
    d = (_get_setting_global("duo_done_day") or "").strip()
    return d == now.date().isoformat()

def _is_pill_confirm_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False

    # If user explicitly mentions pill/medicine, be more flexible
    if any(k in t for k in ("藥", "事前", "避孕")):
        return any(k in t for k in ("吃了", "吃完", "已吃", "吃藥", "服用", "已服用"))

    # Otherwise, only accept short/strict confirmations to avoid false positives (e.g., "吃了拉麵")
    patterns = [
        r"^(?:我)?(?:已)?吃(?:了|完)$",
        r"^(?:我)?(?:已)?吃(?:了|完)\s*\d{1,2}\s*[:：]\s*\d{2}$",
        r"^(?:我)?(?:已)?吃(?:了|完)\s*\d{1,2}\s*(?:點|時|时)\s*(?:\d{1,2}\s*分?)?$",
        r"^(?:我)?(?:已)?吃(?:了|完)\s*\d{1,2}\s*(?:點|時|时)\s*半$",
        r"^(?:我)?吃藥(?:了)?$",
        r"^(?:我)?吃藥(?:了)?\s*\d{1,2}\s*[:：]\s*\d{2}$",
    ]
    return any(re.fullmatch(p, t) for p in patterns)

def _extract_taken_dt_and_label(text: str, now: datetime.datetime) -> tuple[datetime.datetime, str]:
    t = (text or "").strip()

    # HH:MM
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", t)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mm <= 59:
            dt = now.replace(hour=h, minute=mm, second=0, microsecond=0)
            return dt, f"{h:02d}:{mm:02d}"

    # H點半
    m = re.search(r"(\d{1,2})\s*(?:點|時|时)\s*半", t)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            dt = now.replace(hour=h, minute=30, second=0, microsecond=0)
            return dt, f"{h:02d}:30"

    # H點(分)
    m = re.search(r"(\d{1,2})\s*(?:點|時|时)\s*(\d{1,2})?\s*(?:分)?", t)
    if m:
        h = int(m.group(1))
        mm = int(m.group(2)) if (m.group(2) is not None and m.group(2) != "") else 0
        if 0 <= h <= 23 and 0 <= mm <= 59:
            dt = now.replace(hour=h, minute=mm, second=0, microsecond=0)
            return dt, f"{h:02d}:{mm:02d}"

    # fallback: reported now
    return now, now.strftime("%H:%M")

def push_and_log(
    to_user_id: str,
    message: str,
    *,
    reason: str,
    target_role: str | None = None,
):
    """
    Unified push (LINE + Discord) with logging
    """
    preview = message.replace("\n", " ")[:80]

    logger.info(
        "[PUSH][%s] to=%s role=%s msg=\"%s\"",
        reason,
        to_user_id,
        target_role,
        preview,
    )

    try:
        unified_push_text(to_user_id, message)
    except Exception as e:
        logger.error(
            "[PUSH][%s][FAILED] to=%s role=%s err=%s",
            reason,
            to_user_id,
            target_role,
            str(e),
        )
        raise
def build_med_pill_message(remind_count: int) -> str:
    # 第 1 次（23:00）
    if remind_count <= 0:
        return (
            "吃藥提醒：\n"
            "事前藥不能中斷，請記得服用。\n"
            "吃完回我：吃完 23:05（或直接回：吃了）"
        )

    # 第 2 次
    if remind_count == 1:
        return (
            "再提醒一次：\n"
            "今天的事前藥還沒回報。\n"
            "請吃完後立刻回覆時間。"
        )

    # 第 3 次
    if remind_count == 2:
        return (
            "重要提醒：\n"
            "事前藥必須每天準時服用。\n"
            "請立即處理並回覆我。"
        )

    # 第 4 次以上（嚴肅）
    return (
        "⚠️ 警告提醒：\n"
        "你今天尚未回報服用事前藥。\n"
        "請現在立刻服用並回覆「吃了」。"
    )

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


def line_reply_messages(reply_token: str, messages: list[dict]):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    url = "https://api.line.me/v2/bot/message/reply"
    body = {"replyToken": reply_token, "messages": messages}
    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)
        if resp.status_code != 200:
            print("Reply status:", resp.status_code, "body:", resp.text[:300], flush=True)
    except Exception as e:
        print("Reply error:", e, flush=True)


def line_reply(reply_token: str, message: str):
    # Backward-compatible text-only reply
    return line_reply_messages(reply_token, [{"type": "text", "text": message}])




# ====== Discord bot bridge (optional) ======
_discord_client = None
_discord_loop = None
_discord_ready = threading.Event()

def _discord_enabled() -> bool:
    return bool(ENABLE_DISCORD_BOT and DISCORD_BOT_TOKEN)

def _is_discord_id(user_id: str) -> bool:
    return bool(user_id) and str(user_id).isdigit()

def _chunk_text(text: str, limit: int = 1800) -> list[str]:
    # Discord hard limit is 2000 chars; keep some buffer
    text = text or ""
    if len(text) <= limit:
        return [text]
    out = []
    s = text
    while s:
        out.append(s[:limit])
        s = s[limit:]
    return out


def _find_first_uri(obj):
    try:
        if isinstance(obj, dict):
            uri = obj.get("uri")
            if isinstance(uri, str) and uri.startswith("http"):
                return uri
            for v in obj.values():
                u = _find_first_uri(v)
                if u:
                    return u
        elif isinstance(obj, list):
            for v in obj:
                u = _find_first_uri(v)
                if u:
                    return u
    except Exception:
        return None
    return None

def _discord_plain_text(out) -> str:
    # Convert LINE-style reply payload to text for Discord
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        if out.get("type") == "text":
            return out.get("text") or ""
        if out.get("type") == "flex":
            uri = _find_first_uri(out)
            if uri:
                return (out.get("altText") or "(flex)") + "\n" + uri
            return out.get("altText") or ""
        return out.get("altText") or ""
    if isinstance(out, list):
        parts = []
        for m in out:
            if isinstance(m, str):
                parts.append(m)
            elif isinstance(m, dict):
                mtype = m.get("type")
                if mtype == "text":
                    parts.append(m.get("text") or "")
                elif mtype == "image":
                    parts.append(m.get("originalContentUrl") or m.get("previewImageUrl") or "")
                elif mtype == "flex":
                    uri = _find_first_uri(m)
                    if uri:
                        parts.append((m.get("altText") or "(flex)") + "\n" + uri)
                    else:
                        parts.append(m.get("altText") or "(flex)")
                else:
                    # fallback
                    parts.append(m.get("altText") or m.get("text") or f"({mtype})")
            else:
                parts.append(str(m))
        return "\n\n".join([p for p in parts if p])
    return str(out)

async def _discord_send_text_async(user_id_int: int, text: str):
    import discord  # local import to keep optional dependency
    global _discord_client
    if not _discord_client:
        raise RuntimeError("Discord client not ready")
    user = await _discord_client.fetch_user(user_id_int)
    for chunk in _chunk_text(text):
        if chunk.strip() == "":
            continue
        await user.send(chunk)

async def _discord_send_messages_async(user_id_int: int, messages: list[dict]):
    # Best-effort conversion of LINE push payloads to Discord DM
    txt = _discord_plain_text(messages)
    if txt:
        await _discord_send_text_async(user_id_int, txt)

def discord_send_messages(to_user_id: str, messages: list[dict]):
    global _discord_loop
    if not _discord_enabled():
        raise RuntimeError("Discord bridge disabled")
    if not _discord_loop or not _discord_ready.is_set():
        raise RuntimeError("Discord loop not ready")
    uid = int(str(to_user_id))
    fut = asyncio.run_coroutine_threadsafe(_discord_send_messages_async(uid, messages), _discord_loop)
    return fut.result(timeout=15)

def discord_send_text(to_user_id: str, text: str):
    global _discord_loop
    if not _discord_enabled():
        raise RuntimeError("Discord bridge disabled")
    if not _discord_loop or not _discord_ready.is_set():
        raise RuntimeError("Discord loop not ready")
    uid = int(str(to_user_id))
    fut = asyncio.run_coroutine_threadsafe(_discord_send_text_async(uid, text), _discord_loop)
    return fut.result(timeout=15)


async def _discord_send_channel_text_async(channel_id_int: int, text: str):
    import discord  # local import
    global _discord_client
    if not _discord_client:
        raise RuntimeError("Discord client not ready")

    ch = _discord_client.get_channel(channel_id_int)
    if ch is None:
        ch = await _discord_client.fetch_channel(channel_id_int)
    for chunk in _chunk_text(text):
        if chunk.strip() == "":
            continue
        await ch.send(chunk)

def discord_send_channel_text(channel_id: str, text: str):
    global _discord_loop
    if not _discord_enabled():
        raise RuntimeError("Discord bridge disabled")
    if not _discord_loop or not _discord_ready.is_set():
        raise RuntimeError("Discord loop not ready")
    cid = int(str(channel_id))
    fut = asyncio.run_coroutine_threadsafe(_discord_send_channel_text_async(cid, text), _discord_loop)
    return fut.result(timeout=15)

def start_discord_bot():
    """Start Discord bot in a background thread (non-blocking).

    Notes:
    - Requires ENABLE_DISCORD_BOT=1 or DISCORD_BOT_TOKEN set.
    - If you run gunicorn with multiple workers, each worker would start a bot.
      Keep workers=1 for single-instance deployments.
    """
    global _discord_client, _discord_loop

    if not _discord_enabled():
        logger.info("[DISCORD] disabled (no token / ENABLE_DISCORD_BOT=0)")
        return

    if _discord_client is not None:
        return  # already started

    import discord  # local import
    intents = discord.Intents.default()
    intents.message_content = True  # requires enabling Message Content Intent in Developer Portal
    intents.messages = True
    intents.dm_messages = True

    client = discord.Client(intents=intents)
    _discord_client = client

    @client.event
    async def on_ready():
        _discord_ready.set()
        logger.info("[DISCORD] logged in as %s", getattr(client.user, "name", "unknown"))
        try:
            gcount = len(getattr(client, "guilds", []) or [])
            logger.info(
                "[DISCORD] ready guilds=%d dm_only=%s allowed_users=%s allowed_channels=%s push_channels=%s",
                gcount,
                DISCORD_DM_ONLY,
                ",".join(sorted(DISCORD_ALLOWED_USER_IDS)) if DISCORD_ALLOWED_USER_IDS else "(all)",
                ",".join(sorted(DISCORD_ALLOWED_CHANNEL_IDS)) if DISCORD_ALLOWED_CHANNEL_IDS else "(all)",
                ",".join(sorted(DISCORD_PUSH_CHANNEL_IDS)) if DISCORD_PUSH_CHANNEL_IDS else "(none)",
            )
        except Exception:
            pass

    @client.event
    async def on_message(message: "discord.Message"):

        try:
            uid = str(message.author.id)
            guild_id = str(message.guild.id) if message.guild is not None else "-"
            channel_id = str(getattr(message.channel, "id", "")) if getattr(message, "channel", None) is not None else "-"
            author_name = getattr(message.author, "display_name", None) or getattr(message.author, "name", "") or ""
            raw_content = message.content or ""
            if DISCORD_DEBUG:
                preview = raw_content.replace("\n", "\\n")[:200]
                logger.info(
                    '[DISCORD][IN] uid=%s name="%s" guild=%s channel=%s content_len=%d att=%d content="%s"',
                    uid,
                    author_name,
                    guild_id,
                    channel_id,
                    len(raw_content),
                    len(message.attachments or []),
                    preview,
                )

            if message.author.bot:
                if DISCORD_DEBUG:
                    logger.info("[DISCORD][SKIP] bot_message uid=%s", uid)
                return

            if DISCORD_ALLOWED_USER_IDS and uid not in DISCORD_ALLOWED_USER_IDS:
                if DISCORD_DEBUG:
                    logger.info("[DISCORD][SKIP] uid_not_allowed uid=%s", uid)
                return

            # DM-only unless explicitly disabled
            if message.guild is not None:
                if DISCORD_DM_ONLY:
                    if DISCORD_DEBUG:
                        logger.info("[DISCORD][SKIP] guild_message_blocked dm_only=1 guild=%s channel=%s", guild_id, channel_id)
                    return
                if DISCORD_ALLOWED_CHANNEL_IDS and str(message.channel.id) not in DISCORD_ALLOWED_CHANNEL_IDS:
                    if DISCORD_DEBUG:
                        logger.info("[DISCORD][SKIP] channel_not_allowed channel=%s", channel_id)
                    return

            display_name = author_name

            # handle image attachments
            if message.attachments:
                for i, att in enumerate(message.attachments):
                    ctype = (getattr(att, "content_type", None) or "").lower()
                    if not ctype.startswith("image/"):
                        continue
                    data = await att.read()
                    ext = _ext_from_content_type(ctype or None)
                    message_id = f"discord_{message.id}_{i}"
                    filename = f"{message_id}{ext}"
                    filepath = MEDIA_DIR / filename
                    filepath.write_bytes(data)

                    save_media_record(
                        db_path=LOVE_DB_PATH,
                        message_id=message_id,
                        filename=filename,
                        content_type=ctype or "image/*",
                        from_user_id=uid,
                        created_at=_iso_now(),
                    )
                    ok, note = forward_image_to_other_party(
                        sender_user_id=uid,
                        sender_name=display_name or "對方",
                        message_id=message_id,
                    )
                    await message.channel.send(
                        "✅ 收到照片了，我已經幫你轉送給對方。" if ok else f"✅ 收到照片了，但目前無法轉送（{note}）。"
                    )

            content = (raw_content or "").strip()
            if not content:
                if DISCORD_DEBUG:
                    logger.info("[DISCORD][SKIP] empty_content uid=%s guild=%s channel=%s", uid, guild_id, channel_id)
                    logger.info("[DISCORD][HINT] If you're testing in a server and always see empty content, enable Message Content Intent in Developer Portal.")
                return

            upsert_activity(uid, display_name, preview=content)
            out = handle_command(uid, content)
            txt = _discord_plain_text(out)
            if txt:
                for chunk in _chunk_text(txt):
                    await message.channel.send(chunk)
        except Exception as e:
            logger.exception("[DISCORD] on_message error: %s", str(e))


    def _runner():
        global _discord_loop
        _discord_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_discord_loop)
        try:
            _discord_loop.run_until_complete(client.start(DISCORD_BOT_TOKEN))
        finally:
            try:
                _discord_loop.stop()
            except Exception:
                pass

    t = threading.Thread(target=_runner, name="discord-bot", daemon=True)
    t.start()
    logger.info("[DISCORD] starting thread...")



def line_push_messages(to_user_id: str, messages: list[dict]):
    # Discord dispatch: if user_id is numeric, treat it as a Discord user ID and DM it.
    if _discord_enabled() and _is_discord_id(to_user_id):
        try:
            discord_send_messages(to_user_id, messages)
        except Exception as e:
            logger.error("[DISCORD_PUSH][FAILED] to=%s err=%s", to_user_id, str(e))
        return

    if not LINE_CHANNEL_ACCESS_TOKEN or not to_user_id:
        logger.warning("[LINE_PUSH][SKIP] token_missing=%s to_empty=%s", not bool(LINE_CHANNEL_ACCESS_TOKEN), not bool(to_user_id))
        return

    url = "https://api.line.me/v2/bot/message/push"
    body = {"to": to_user_id, "messages": messages}

    # 你要的：log 出「送了什麼」
    # 把 messages 裡每個 message 的 type 與文字摘要印出來（圖片/貼圖也看得到 type）
    parts = []
    for m in messages or []:
        mtype = (m or {}).get("type")
        if mtype == "text":
            txt = (m.get("text") or "").replace("\n", "\\n")
            parts.append(f"text:{txt[:500]}")
        else:
            parts.append(f"{mtype}")
    logger.info('[LINE_PUSH][REQ] to=%s messages=%s', to_user_id, " | ".join(parts)[:2000])

    try:
        resp = requests.post(url, headers=_line_headers(), json=body, timeout=10)

        # 你要的：log 出「已送出 OK」（LINE 回 2xx 才算）
        if 200 <= resp.status_code < 300:
            logger.info("[LINE_PUSH][OK] to=%s status=%s", to_user_id, resp.status_code)
            return

        # 失敗：印出回應內容
        logger.error(
            '[LINE_PUSH][FAIL] to=%s status=%s body="%s"',
            to_user_id,
            resp.status_code,
            (resp.text or "")[:800],
        )
    except Exception as e:
        logger.exception("[LINE_PUSH][EXCEPTION] to=%s err=%s", to_user_id, str(e))
        raise



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


def dash_media_src(message_id: str) -> str:
    """
    Browser-side media URL (relative), includes MEDIA_ACCESS_TOKEN if enabled.
    """
    url = f"/media/{message_id}"
    if MEDIA_ACCESS_TOKEN:
        url += f"?k={MEDIA_ACCESS_TOKEN}"
    return url


def dash_thumb_src(message_id: str, w: int = 480) -> str:
    """Dashboard thumbnails (smaller payload than /media)."""
    w = max(160, min(1024, int(w)))
    url = f"/thumb/{message_id}?w={w}"
    if MEDIA_ACCESS_TOKEN:
        url += f"&k={MEDIA_ACCESS_TOKEN}"
    return url





def _get_active_role_ids() -> dict[str, list[str]]:
    """Return active user_ids for girlfriend/boyfriend. Supports multiple IDs per role (LINE + Discord)."""
    import sqlite3
    out: dict[str, list[str]] = {"girlfriend": [], "boyfriend": []}
    try:
        conn = sqlite3.connect(str(LOVE_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT line_user_id, role FROM subscriber WHERE is_active=1 AND role IN ('girlfriend','boyfriend')"
        ).fetchall()
        for r in rows:
            uid = (r["line_user_id"] or "").strip()
            role = (r["role"] or "").strip()
            if uid and role in out and uid not in out[role]:
                out[role].append(uid)
    except Exception as e:
        logger.error("[ROLE] _get_active_role_ids failed: %s", str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _get_role_ids(role: str) -> list[str]:
    return _get_active_role_ids().get(role, []) or []


def _pick_primary_uid(uids: list[str]) -> str | None:
    """Prefer LINE user id (starts with 'U') for DB-bound features; otherwise first."""
    if not uids:
        return None
    for u in uids:
        if (u or "").startswith("U"):
            return u
    return uids[0]


def _push_to_discord_channels_text(text: str):
    if not (_discord_enabled() and DISCORD_PUSH_CHANNEL_IDS):
        return
    for cid in sorted(DISCORD_PUSH_CHANNEL_IDS):
        try:
            logger.info('[DISCORD_PUSH][CHANNEL] to=%s msg="%s"', cid, text.replace("\n", " ")[:80])
            discord_send_channel_text(cid, text)
        except Exception as e:
            logger.error("[DISCORD_PUSH][CHANNEL][FAILED] to=%s err=%s", cid, str(e))


def unified_push_messages(to_user_id: str, messages: list[dict]):
    """Push LINE payload to LINE user OR Discord user (best-effort conversion)."""
    if _is_discord_id(to_user_id):
        return discord_send_messages(to_user_id, messages)
    return line_push_messages(to_user_id, messages)


def unified_push_text(to_user_id: str, text: str):
    if _is_discord_id(to_user_id):
        return discord_send_text(to_user_id, text)
    return line_push_text(to_user_id, text)


def push_to_role_text(role: str, message: str, *, reason: str):
    _push_to_discord_channels_text(message)
    for uid in _get_role_ids(role):
        if _is_discord_id(uid) and not DISCORD_PUSH_DM:
            continue
        push_and_log(uid, message, reason=reason, target_role=role)


def push_to_roles_text(roles: tuple[str, ...], message: str, *, reason: str):
    _push_to_discord_channels_text(message)
    for role in roles:
        for uid in _get_role_ids(role):
            if _is_discord_id(uid) and not DISCORD_PUSH_DM:
                continue
            push_and_log(uid, message, reason=reason, target_role=role)


def push_to_couple_text(message: str, fallback_user_id: str | None = None):
    """Push to both roles (LINE + optional Discord DM) + optional Discord broadcast channels."""
    gf_ids = _get_role_ids("girlfriend")
    bf_ids = _get_role_ids("boyfriend")
    if gf_ids or bf_ids:
        push_to_roles_text(("girlfriend", "boyfriend"), message, reason="COUPLE_PUSH")
        return

    # fallback (legacy)
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
    # thresholds (DB overrides env defaults)
    uv_th = _get_threshold_float("uv_high_threshold", UV_HIGH_THRESHOLD)
    hum_th = _get_threshold_float("humidity_range_threshold", HUMIDITY_RANGE_THRESHOLD)

    temp_low_th = _get_threshold_float("temp_low_threshold", TEMP_LOW_THRESHOLD)
    app_low_th = _get_threshold_float("app_temp_low_threshold", APP_TEMP_LOW_THRESHOLD)

    temp_high_th = _get_threshold_float("temp_high_threshold", TEMP_HIGH_THRESHOLD)
    app_high_th = _get_threshold_float("app_temp_high_threshold", APP_TEMP_HIGH_THRESHOLD)

    max_uv = metrics.get("max_uv")
    h_range = metrics.get("humidity_range")

    tmin = metrics.get("min_temp")
    tmax = metrics.get("max_temp")
    amin = metrics.get("min_app_temp")
    amax = metrics.get("max_app_temp")

    triggers = []
    if max_uv is not None and max_uv >= uv_th:
        triggers.append("uv")
    if h_range is not None and h_range >= hum_th:
        triggers.append("humidity")

    if tmin is not None and tmin <= temp_low_th:
        triggers.append("temp_low")
    if amin is not None and amin <= app_low_th:
        triggers.append("app_temp_low")

    if tmax is not None and tmax >= temp_high_th:
        triggers.append("temp_high")
    if amax is not None and amax >= app_high_th:
        triggers.append("app_temp_high")

    if not triggers:
        return None

    lines = []

    # cold message
    if ("temp_low" in triggers) or ("app_temp_low" in triggers):
        t_part = f"最低約 {tmin:.1f}°C" if tmin is not None else "低溫"
        a_part = f"體感最低約 {amin:.1f}°C" if amin is not None else ""
        extra = f"，{a_part}" if a_part else ""
        lines.append(f"🧥 天氣提醒：今天 {city_name} 偏冷（{t_part}{extra}）")
        lines.append("出門記得加件外套，必要時圍巾/帽子。")

    # hot message
    if ("temp_high" in triggers) or ("app_temp_high" in triggers):
        t_part = f"最高約 {tmax:.1f}°C" if tmax is not None else "高溫"
        a_part = f"體感最高約 {amax:.1f}°C" if amax is not None else ""
        extra = f"，{a_part}" if a_part else ""
        lines.append(f"🔥 天氣提醒：今天 {city_name} 偏熱（{t_part}{extra}）")
        lines.append("注意補水、防曬，避免正中午長時間曝曬。")

    # UV / humidity
    if ("uv" in triggers) or ("humidity" in triggers):
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
    role_ids = _get_active_role_ids()
    gf_id = _pick_primary_uid(role_ids.get("girlfriend") or [])
    bf_id = _pick_primary_uid(role_ids.get("boyfriend") or [])

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
    target_ids = [uid for uid in _get_role_ids(to_role) if uid and uid != from_user_id]
    if not target_ids:
        return "⚠️ 目標尚未設定角色或未加入推播。請雙方先說：我是臭寶 / 我是臭晡晡（並加入推播）"
    if not content.strip():
        return "用法：跟臭寶說 <內容> 或 跟臭晡晡說 <內容>"

    sender_name = get_display_name(db_path=LOVE_DB_PATH, user_id=from_user_id) or "對方"
    now = _tz_now().strftime("%m/%d %H:%M")
    msg = f"💬 {sender_name}（{now}）想跟你說：\n{content.strip()}"

    for tid in target_ids:
        try:
            unified_push_text(tid, msg)
        except Exception as e:
            logger.error("[RELAY][FAILED] to=%s err=%s", tid, str(e))

    return "✅ 已轉達給對方。"



# ====== help / command parsing ======
def help_text() -> str:
    cities = ", ".join(WEATHER_LOC_MAP.keys()) if WEATHER_LOC_MAP else "（未設定）"
    return (
        f"【{BOT_NAME} 指令一覽】（不用 /）\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "一、身份與推播設定\n"
        "━━━━━━━━━━━━━━━━\n"
        "  我是臭寶\n"
        "  我是臭晡晡\n"
        "    - 設定你的身份（用於推播與對話橋樑）\n"
        "\n"
        "  加入推播\n"
        "  退出推播\n"
        "    - 是否接收系統推播通知\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "二、吃藥提醒（事前藥）\n"
        "━━━━━━━━━━━━━━━━\n"
        "  吃藥狀態 / 藥狀態\n"
        "    - 查詢今天是否已回報吃藥（雙方可查）\n"
        "\n"
        "  取消吃藥 / 重置吃藥\n"
        "    - 取消今天的「已吃藥」紀錄（誤傳可用）\n"
        "\n"
        "  吃藥紀錄 <天數>\n"
        "    - 列出最近 N 天的吃藥時間（最多 30 天；例：吃藥紀錄 14）\n"
        "\n"
        "  （自動功能）\n"
        "    - 每天固定時間提醒吃藥\n"
        "    - 未回覆會定期再次提醒\n"
        "    - 回覆「吃了 / 吃完 21:30」會自動記錄並通知另一方\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "三、Duolingo 提醒\n"
        "━━━━━━━━━━━━━━━━\n"
        "  Duolingo提醒開 / Duolingo提醒關\n"
        "    - 每晚 22:00 起每 10 分鐘提醒一次\n"
        "\n"
        "  Duolingo已玩\n"
        "    - 暫停今天提醒（明天 22:00 會再開始）\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "四、對話橋樑（代傳訊息）\n"
        "━━━━━━━━━━━━━━━━\n"
        "  對話狀態\n"
        "    - 查看目前是否開啟橋樑模式\n"
        "\n"
        "  跟臭寶說 <內容>\n"
        "  跟臭晡晡說 <內容>\n"
        "    - 透過 bot 轉達訊息給對方\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "四、基本互動\n"
        "━━━━━━━━━━━━━━━━\n"
        "  情話\n"
        "  臭寶在幹嘛\n"
        "  早安\n"
        "  晚安\n"
        "  約會\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        f"五、天氣查詢與提醒（地點：{cities}）\n"
        "━━━━━━━━━━━━━━━━\n"
        "  天氣\n"
        "    - 全部地點天氣摘要\n"
        "\n"
        "  天氣 <地點>\n"
        "    - 指定地點天氣\n"
        "\n"
        "  天氣提醒\n"
        "    - 檢查全部地點，符合門檻才推播給雙方\n"
        "\n"
        "  天氣提醒 <地點>\n"
        "    - 只檢查指定地點並推播\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "六、攝影任務（會推播給雙方）\n"
        "━━━━━━━━━━━━━━━━\n"
        "  攝影選項\n"
        "    - 顯示所有任務選項\n"
        "\n"
        "  攝影選項 給臭寶\n"
        "    - 只顯示臭寶的任務選項\n"
        "\n"
        "  攝影任務\n"
        "    - 隨機互拍（雙方各一個）\n"
        "\n"
        "  攝影任務 給臭寶 #1\n"
        "    - 指派臭寶執行第 1 個選項\n"
        "\n"
        "  攝影任務 給臭寶 <自訂內容>\n"
        "    - 例：攝影任務 給臭寶 自拍自己給我看\n"
        "\n"
        "  任務狀態\n"
        "    - 查看目前尚未完成的任務\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "七、照片轉送（自動）\n"
        "━━━━━━━━━━━━━━━━\n"
        "  - 你或臭寶把照片傳給 bot\n"
        "  - 會自動轉送給另一方\n"
        "  - 若在任務有效期間，會自動判定交作業並標記完成\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "八、生活記錄\n"
        "━━━━━━━━━━━━━━━━\n"
        "  許願 <內容> / 願望\n"
        "    - 記錄願望清單\n"
        "\n"
        "  心情 <內容>\n"
        "  回顧心情\n"
        "    - 記錄並回顧心情狀態\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "九、紀念日\n"
        "━━━━━━━━━━━━━━━━\n"
        "  設定紀念日 YYYY-MM-DD\n"
        "  紀念日\n"
        "    - 查看已設定的紀念日\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        "十、管理功能\n"
        "━━━━━━━━━━━━━━━━\n"
        "  新增情話 <內容>\n"
        "  列表情話\n"
        "  刪除情話 <id>\n"
        "\n"
        "  新增約會 <內容>\n"
    )





def help_quick_text(base_url: str) -> str:
    return (
        f"📘 使用說明（完整版）：{base_url}/docs\n"
        "\n"
        "常用：\n"
        "  我是臭寶 / 我是臭晡晡\n"
        "  加入推播\n"
        "  吃藥狀態 / 吃藥紀錄 14\n"
        "  Duolingo狀態 / Duolingo已玩\n"
        "  攝影任務 / 任務狀態\n"
        "\n"
        "（LINE 內 help 會精簡，完整排版請看網站）"
    )


def build_help_flex(base_url: str, dash_login_url: str | None = None) -> dict:
    # Flex Message bubble

    def _msg_btn(label: str, text: str, style: str = "secondary") -> dict:
        return {
            "type": "button",
            "style": style,
            "height": "sm",
            "action": {"type": "message", "label": label, "text": text},
        }

    def _uri_btn(label: str, uri: str, style: str = "secondary") -> dict:
        return {
            "type": "button",
            "style": style,
            "height": "sm",
            "action": {"type": "uri", "label": label, "uri": uri},
        }

    def _row(btn_left: dict, btn_right: dict | None = None) -> dict:
        contents = [dict(btn_left)]
        if btn_right:
            contents.append(dict(btn_right))
        else:
            # placeholder to keep alignment
            contents.append({"type": "box", "layout": "vertical", "contents": []})
        # make 2-column grid
        contents[0]["flex"] = 1
        contents[1]["flex"] = 1
        return {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": contents}

    # Footer actions (keep your existing links)
    footer_contents: list[dict] = []
    if dash_login_url:
        footer_contents.append(
            {
                "type": "button",
                "style": "primary",
                "action": {"type": "uri", "label": "開啟儀表板", "uri": dash_login_url},
            }
        )

    else:
        # Always show dashboard entry; if user not authorized, bot will explain how to unlock.
        footer_contents.append(
            {
                "type": "button",
                "style": "primary",
                "height": "sm",
                "action": {"type": "message", "label": "儀表板", "text": "儀表板"},
            }
        )

        footer_contents.append(
        {
            "type": "button",
            "style": "secondary" if dash_login_url else "primary",
            "action": {"type": "uri", "label": "開啟使用說明", "uri": f"{base_url}/docs"},
        }
    )
        footer_contents.append(
        {
            "type": "button",
            "style": "secondary",
            "action": {"type": "message", "label": "貼我精簡版", "text": "help2"},
        }
    )

    # Quick command buttons (what you asked for)
    quick_btn_rows = [
        _row(_msg_btn("天氣", "天氣", style="primary"), _msg_btn("天氣門檻", "天氣門檻")),
        _row(_msg_btn("吃藥狀態", "吃藥狀態", style="primary"), _msg_btn("我吃了", "我吃了")),
        _row(_msg_btn("吃藥紀錄", "吃藥紀錄 14"), _msg_btn("取消吃藥", "取消吃藥")),
        _row(_msg_btn("Duolingo狀態", "Duolingo狀態", style="primary"), _msg_btn("Duolingo已玩", "Duolingo已玩")),
        _row(_msg_btn("Duo提醒開", "Duolingo提醒開"), _msg_btn("Duo提醒關", "Duolingo提醒關")),
        _row(_msg_btn("攝影任務", "攝影任務", style="primary"), _msg_btn("任務狀態", "任務狀態")),
    ]

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": f"{BOT_NAME} 使用說明", "weight": "bold", "size": "xl"},
                {
                    "type": "text",
                    "text": "點按鈕就會送出指令（等同你手打）。完整版排版在網站。",
                    "wrap": True,
                    "size": "sm",
                    "color": "#666666",
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": "常用功能按鈕", "size": "sm", "weight": "bold"},
                        *quick_btn_rows,
                    ],
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents,
        },
    }





def build_dashboard_login_flex(base_url: str, login_url: str) -> dict:
    """A simple Flex card that opens the dashboard via one-time magic link."""
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": f"{BOT_NAME}｜私人儀表板", "weight": "bold", "size": "xl"},
                {"type": "text", "text": "這是一個一次性/短效登入連結，點開即可登入（網址不需要手打 token）。", "wrap": True, "size": "sm", "color": "#666666"},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "button", "style": "primary", "action": {"type": "uri", "label": "開啟儀表板", "uri": login_url}},
                {"type": "button", "style": "secondary", "action": {"type": "uri", "label": "使用說明", "uri": f"{base_url}/docs"}},
            ],
        },
    }

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
        "吃藥紀錄",
        "吃藥記錄",
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
    Supports multiple IDs per role (LINE + Discord).
    """
    role_ids = _get_active_role_ids()
    gf_ids = role_ids.get("girlfriend") or []
    bf_ids = role_ids.get("boyfriend") or []

    if not gf_ids or not bf_ids:
        return False, "roles not ready"

    if sender_user_id in gf_ids:
        sender_role = "girlfriend"
        target_role = "boyfriend"
        target_label = DEFAULT_SELF_NICKNAME
    elif sender_user_id in bf_ids:
        sender_role = "boyfriend"
        target_role = "girlfriend"
        target_label = DEFAULT_GIRLFRIEND_NICKNAME
    else:
        return False, "sender not in couple"

    target_ids = [uid for uid in _get_role_ids(target_role) if uid and uid != sender_user_id]
    if not target_ids:
        return False, "target not set"

    media_url = build_media_url(message_id)
    if not media_url:
        return False, "PUBLIC_BASE_URL not set"

    matched = claim_latest_open_task_for_role(
        db_path=LOVE_DB_PATH, role=sender_role, expire_minutes=PHOTO_TASK_EXPIRE_MIN, message_id=message_id
    )
    task_note = ""
    if matched:
        task_note = f"\n🎯 交作業：任務 #{matched['id']} - {matched['text']}"

    now = _tz_now().strftime("%m/%d %H:%M")
    caption = f"📷 {sender_name}（{now}）傳來一張照片給你（{target_label}）{task_note}"

    ok_any = False
    for target_id in target_ids:
        try:
            if _is_discord_id(target_id):
                unified_push_text(target_id, caption + f"\n{media_url}")
            else:
                unified_push_messages(
                    target_id,
                    [
                        {"type": "text", "text": caption},
                        {"type": "image", "originalContentUrl": media_url, "previewImageUrl": media_url},
                    ],
                )
            ok_any = True
        except Exception as e:
            logger.error("[PHOTO_FWD][FAILED] to=%s err=%s", target_id, str(e))
    return (ok_any, "forwarded" if ok_any else "failed")



# ====== command handler ======
def handle_command(user_id: str, text: str) -> str:
    # ===== medication: pill confirmation from girlfriend =====
    try:
        role = get_user_role(db_path=LOVE_DB_PATH, user_id=user_id)
        if role == "girlfriend" and _is_pill_confirm_text(text):
            now = _tz_now()
            taken_dt, label = _extract_taken_dt_and_label(text, now)
            day = now.date().isoformat()

            set_med_pill_taken(
                db_path=LOVE_DB_PATH,
                user_id=user_id,
                day=day,
                taken_at_iso=taken_dt.isoformat(timespec="seconds"),
                taken_time_text=label,
                reported_text=text,
            )

            # notify boyfriend (臭晡晡)


            push_to_roles_text(("boyfriend",), f"{DEFAULT_GIRLFRIEND_NICKNAME} 今天已吃事前藥（{label}）。", reason="MED_CONFIRM_NOTIFY")


            return f"收到～我記錄你今天 {label} 吃藥，並已通知 {DEFAULT_SELF_NICKNAME}。"
    except Exception as e:
        print("[MED] pill confirm error:", e, flush=True)

    cmd, arg = _cmd(text)
    cmd_l = (cmd or "").strip().lower()

    if cmd in ("help", "說明", "幫助"):
        base_url = get_public_base_url()
        dash_login_url = None
        try:
            if _dash_user_allowed(user_id):
                dash_login_url = _dash_make_login_url(user_id)
        except Exception:
            dash_login_url = None
        return [
            {
                "type": "flex",
                "altText": f"{BOT_NAME} 使用說明",
                "contents": build_help_flex(base_url, dash_login_url=dash_login_url),
            }
        ]

    if cmd in ("help2", "指令", "常用指令"):
        base_url = get_public_base_url()
        return help_quick_text(base_url)

    if cmd in ("儀表板", "面板") or cmd_l in ("dashboard", "dash"):
        base_url = get_public_base_url()
        if not _dash_user_allowed(user_id):
            return "🔒 儀表板是私人資料。請先在 LINE 設定角色：我是臭寶 / 我是臭晡晡（且兩人都加入推播）。"
        login_url = _dash_make_login_url(user_id)

        # Discord 無法直接「點 Flex 卡片」；改回傳純文字一次性登入連結。
        if _is_discord_id(user_id):
            ttl_min = max(1, int(DASH_MAGIC_TOKEN_TTL_SECONDS // 60))
            return f"🔐 儀表板一次性登入連結（{ttl_min} 分鐘有效）：\n{login_url}"

        return [
            {
                "type": "flex",
                "altText": f"{BOT_NAME} 儀表板",
                "contents": build_dashboard_login_flex(base_url, login_url),
            }
        ]


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
    if cmd in ("吃藥狀態", "藥狀態"):
        gf_id = _pick_primary_uid(_get_role_ids("girlfriend"))
        if not gf_id:
            return "目前沒有設定 girlfriend/boyfriend 角色，先用「設定角色」把雙方設好。"
        now = _tz_now()
        day = now.date().isoformat()
        row = get_med_pill_row(db_path=LOVE_DB_PATH, user_id=gf_id, day=day) or {}
        if row.get("taken_at"):
            return f"{DEFAULT_GIRLFRIEND_NICKNAME} 今天已回報吃藥（{row.get('taken_time_text') or '已記錄'}）。"
        return f"{DEFAULT_GIRLFRIEND_NICKNAME} 今天尚未回報吃藥。已提醒 {int(row.get('remind_count') or 0)} 次。"
    if cmd in ("取消吃藥", "重置吃藥", "撤銷吃藥"):
        gf_id = _pick_primary_uid(_get_role_ids("girlfriend"))
        if not gf_id:
            return "目前沒有設定 girlfriend/boyfriend 角色，先用「我是臭寶 / 我是臭晡晡」設好。"

        # 允許男方或女方都能清（避免女方誤傳時男方不在也能自救）
        role = get_user_role(db_path=LOVE_DB_PATH, user_id=user_id)
        if role not in ("boyfriend", "girlfriend"):
            return "只有臭寶或臭晡晡可以使用這個指令。"

        day = _tz_now().date().isoformat()

        row = get_med_pill_row(db_path=LOVE_DB_PATH, user_id=gf_id, day=day) or {}
        if not row.get("taken_at"):
            return f"{DEFAULT_GIRLFRIEND_NICKNAME} 今天本來就沒有『已吃藥』紀錄，不需要取消。"

        clear_med_pill_taken(db_path=LOVE_DB_PATH, user_id=gf_id, day=day)

        # 同步通知另一方，避免資訊不一致
        other_id = rm.get("boyfriend" if role == "girlfriend" else "girlfriend")
        if other_id:
            line_push_text(other_id, f"已撤銷今天的吃藥回報紀錄（{day}）。")

        return f"已撤銷 {DEFAULT_GIRLFRIEND_NICKNAME} 今天的吃藥回報紀錄（{day}）。"


    # ===== medication: pill history (max 30 days) =====
    if cmd in ("吃藥紀錄", "吃藥記錄", "藥紀錄", "藥記錄"):
        gf_id = _pick_primary_uid(_get_role_ids("girlfriend"))
        if not gf_id:
            return "目前沒有設定 girlfriend/boyfriend 角色，先用「我是臭寶 / 我是臭晡晡」設好。"

        days = 30
        if arg:
            mm = re.search(r"(\d{1,3})", arg)
            if mm:
                days = int(mm.group(1))
        days = max(1, min(30, days))

        now = _tz_now()
        end_day = now.date()
        start_day = end_day - datetime.timedelta(days=days - 1)

        rows = list_med_pill_rows_between(
            db_path=LOVE_DB_PATH,
            user_id=gf_id,
            start_day=start_day.isoformat(),
            end_day=end_day.isoformat(),
        )
        by_day = {r.get("day"): r for r in rows if r.get("day")}

        lines = [f"📋 {DEFAULT_GIRLFRIEND_NICKNAME} 事前藥紀錄（最近 {days} 天）"]
        for i in range(days):
            d = end_day - datetime.timedelta(days=i)
            ds = d.isoformat()
            r = by_day.get(ds)
            if r and r.get("taken_at"):
                label = r.get("taken_time_text") or "已記錄"
                lines.append(f"{ds} ✅ {label}")
            else:
                rc = int(r.get("remind_count") or 0) if r else 0
                if rc:
                    lines.append(f"{ds} ❌ 未回報（提醒 {rc} 次）")
                else:
                    lines.append(f"{ds} ❌ 未回報")
        return "\n".join(lines)

    # ===== Duolingo reminder commands =====
    if cmd_l in ("duolingo已玩", "已玩duolingo", "duolingo完成", "多零果已玩", "已玩多零果", "多零果完成"):
        today = _tz_now().date().isoformat()
        set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key="duo_done_day", value=today)
        return "👌 收到～今天就不再提醒 Duolingo 了（明天 22:00 會再開始）。"

    if cmd_l in ("duolingo提醒開", "開duolingo提醒", "duolingo開", "多零果提醒開", "開多零果提醒", "多零果開"):
        set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key="duo_remind_enabled", value="1")
        set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key="duo_done_day", value="")
        return "✅ 已開啟 Duolingo 提醒（每天 22:00 起每 10 分鐘提醒一次）。"

    if cmd_l in ("duolingo提醒關", "關duolingo提醒", "duolingo關", "多零果提醒關", "關多零果提醒", "多零果關"):
        set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key="duo_remind_enabled", value="0")
        return "✅ 已關閉 Duolingo 提醒。"

    if cmd_l in ("duolingo狀態", "duolingo設定", "多零果狀態", "多零果設定"):
        enabled = duo_remind_enabled()
        today = _tz_now().date().isoformat()
        done = (get_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key="duo_done_day") or "").strip()
        return (
            f"Duolingo 提醒：{'開' if enabled else '關'}\n"
            f"今日已玩：{'是' if done == today else '否'}\n"
            "（可用：Duolingo提醒開 / Duolingo提醒關 / Duolingo已玩）"
        )

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

        role_ids = _get_active_role_ids()
        gf_id = _pick_primary_uid(role_ids.get("girlfriend") or [])
        bf_id = _pick_primary_uid(role_ids.get("boyfriend") or [])
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
                if isinstance(out, list):
                    line_reply_messages(reply_token, out)
                else:
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
    if not weather_remind_enabled():
        print("[SCHED] Weather reminders disabled; skip.", flush=True)
        return
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
def scheduled_med_pill_daily():
    if not med_pill_enabled():
        return

    gf_id = _pick_primary_uid(_get_role_ids("girlfriend"))
    if not gf_id:
        return

    now = _tz_now()
    if _in_quiet_hours(now):
        return

    day = now.date().isoformat()
    row = get_med_pill_row(db_path=LOVE_DB_PATH, user_id=gf_id, day=day) or {}
    if row.get("taken_at"):
        return

    max_cnt = med_pill_max_remind_count()
    cur_cnt = int(row.get("remind_count") or 0)
    if max_cnt > 0 and cur_cnt >= max_cnt:
        return

    msg = build_med_pill_message(cur_cnt)

    # Push to girlfriend role recipients (LINE + optional Discord DM) + optional Discord broadcast channels
    push_to_roles_text(("girlfriend",), msg, reason="MED_DAILY")

    mark_med_pill_reminded(db_path=LOVE_DB_PATH, user_id=gf_id, day=day, remind_at_iso=now.isoformat(timespec="seconds"))
    print("[SCHED][MED] daily pill reminder pushed.", flush=True)



def scheduled_med_pill_nudge():
    if not med_pill_enabled():
        return

    gf_id = _pick_primary_uid(_get_role_ids("girlfriend"))
    if not gf_id:
        return

    now = _tz_now()
    if _in_quiet_hours(now):
        return

    # only start nudging after remind time
    rh, rm_ = med_pill_remind_hm()
    remind_dt = now.replace(hour=rh, minute=rm_, second=0, microsecond=0)
    if now < remind_dt:
        return

    day = now.date().isoformat()
    row = get_med_pill_row(db_path=LOVE_DB_PATH, user_id=gf_id, day=day) or {}
    if row.get("taken_at"):
        return

    max_cnt = med_pill_max_remind_count()
    cur_cnt = int(row.get("remind_count") or 0)
    if max_cnt > 0 and cur_cnt >= max_cnt:
        return

    last = _parse_dt(row.get("last_remind_at"))
    nudge_min = med_pill_nudge_minutes()
    if last and (now - last).total_seconds() < nudge_min * 60:
        return

    msg = build_med_pill_message(cur_cnt)
    push_to_roles_text(("girlfriend",), msg, reason="MED_NUDGE")

    mark_med_pill_reminded(db_path=LOVE_DB_PATH, user_id=gf_id, day=day, remind_at_iso=now.isoformat(timespec="seconds"))
    print("[SCHED][MED] nudge pushed.", flush=True)


def scheduled_duo_remind():
    """
    Duolingo streak reminder.
    Runs on cron; also gated by DB setting so you can enable/disable by command.
    """
    if not duo_remind_enabled():
        return

    now = _tz_now()
    if duo_done_today(now):
        return

    msg = (
        "Duolingo 時間！\n"
        f"{duo_remind_every_minutes()} 分鐘一次提醒：記得去玩一下，別斷連勝。\n"
        "（回「Duolingo已玩」可暫停今天提醒）"
    )
    push_to_couple_text(msg, fallback_user_id=None)
    print("[SCHED][DUO] reminder pushed.", flush=True)


def scheduled_repair_cooldown_scan():
    try:
        sent = _repair_process_cooldown_end_notifications(limit=50)
        if sent:
            print(f"[SCHED][REPAIR] cooldown-end notifications sent={sent}", flush=True)
    except Exception as e:
        print("[SCHED][REPAIR] scan error:", e, flush=True)

def _scheduler_remove_if_exists(sched: BackgroundScheduler, job_id: str):
    try:
        sched.remove_job(job_id)
    except Exception:
        return

def _scheduler_apply_settings(sched: BackgroundScheduler):
    # ===== weather jobs =====
    for j in list(sched.get_jobs()):
        if (j.id or "").startswith("weather_"):
            _scheduler_remove_if_exists(sched, j.id)

    if weather_remind_enabled():
        times = weather_remind_times()
        for i, (h, m) in enumerate(times):
            sched.add_job(
                scheduled_weather_check,
                "cron",
                hour=h,
                minute=m,
                id=f"weather_{h:02d}{m:02d}_{i}",
                replace_existing=True,
            )
        print(f"[SCHED] Weather jobs applied: {_format_times_csv(times)}", flush=True)
    else:
        print("[SCHED] Weather reminders disabled (no weather jobs).", flush=True)

    # ===== medication jobs =====
    _scheduler_remove_if_exists(sched, "med_pill_daily")
    _scheduler_remove_if_exists(sched, "med_pill_nudge")

    if med_pill_enabled():
        rh, rm_ = med_pill_remind_hm()
        sched.add_job(
            scheduled_med_pill_daily,
            "cron",
            hour=rh,
            minute=rm_,
            id="med_pill_daily",
            replace_existing=True,
        )
        # run frequently, but only push if >= nudge_minutes since last reminder
        poll = max(1, min(5, med_pill_nudge_minutes()))
        sched.add_job(
            scheduled_med_pill_nudge,
            "interval",
            minutes=poll,
            id="med_pill_nudge",
            replace_existing=True,
        )
        print(f"[SCHED] Med jobs applied: remind={rh:02d}:{rm_:02d} nudge={med_pill_nudge_minutes()}m poll={poll}m max={med_pill_max_remind_count()}", flush=True)
    else:
        print("[SCHED] Med reminders disabled (no med jobs).", flush=True)

    # ===== duolingo job (always scheduled; gated by duo_remind_enabled in function) =====
    _scheduler_remove_if_exists(sched, "duo_remind")
    try:
        every = duo_remind_every_minutes()
        sh = duo_remind_start_hour()
        eh = duo_remind_end_hour()
        if eh < sh:
            eh = sh
        sched.add_job(
            scheduled_duo_remind,
            "cron",
            hour=f"{sh}-{eh}",
            minute=f"*/{every}",
            id="duo_remind",
            replace_existing=True,
        )
        print(f"[SCHED] Duo job applied: {sh:02d}:00-{eh:02d}:59 every {every}m", flush=True)
    except Exception as e:
        print("[SCHED][DUO] add_job error:", e, flush=True)

    # ===== repair cooldown scan (every minute) =====
    _scheduler_remove_if_exists(sched, "repair_cooldown_scan")
    try:
        sched.add_job(
            scheduled_repair_cooldown_scan,
            "interval",
            minutes=1,
            id="repair_cooldown_scan",
            replace_existing=True,
        )
    except Exception as e:
        print("[SCHED][REPAIR] add_job error:", e, flush=True)


def refresh_scheduler_jobs():
    if not _scheduler:
        return
    try:
        _scheduler_apply_settings(_scheduler)
    except Exception as e:
        print("[SCHED] refresh_scheduler_jobs error:", e, flush=True)


def start_scheduler():
    global _scheduler
    if _scheduler:
        return
    sched = BackgroundScheduler(timezone=_tz())
    _scheduler_apply_settings(sched)
    sched.start()
    _scheduler = sched
    print("[SCHED] started.", flush=True)




if ENABLE_SCHEDULER:
    # 用 gunicorn 時務必 workers=1，避免多份 scheduler 重複推播
    try:
        start_scheduler()
    except Exception as e:
        print("[SCHED] start_scheduler error:", e, flush=True)


# ====== Discord autostart (optional) ======
if ENABLE_DISCORD_BOT:
    try:
        start_discord_bot()
    except Exception as e:
        print("[DISCORD] start_discord_bot error:", e, flush=True)



# ====== routes ======
@app.route("/")
def index():
    return redirect("/docs")


@app.route("/docs")
def docs():
    base_url = get_public_base_url()
    updated_at = _tz_now().strftime("%Y-%m-%d %H:%M")
    return render_template_string(
        DOC_TEMPLATE,
        bot_name=BOT_NAME,
        base_url=base_url,
        updated_at=updated_at,
        sections=DOC_SECTIONS,
    )


@app.route("/docs/plain")
def docs_plain():
    # 方便你 debug（純文字完整版）
    return (help_text(), 200, {"Content-Type": "text/plain; charset=utf-8"})




# ===== Dashboard (private) =====
# 目的：把「LINE 可以問到的資料」用更漂亮的方式呈現在網站上（避免公開洩漏，所以必須登入）。
#
# 登入方式：由 LINE 發放一次性短效 magic link（/dash/login?t=...）
# - 使用者在 LINE 打：儀表板（或 help 內點「開啟儀表板」）
# - Bot 會回一個登入連結（短效、一次性）
# - 網站用該 token 換取 session cookie，並 redirect 到 /dash（URL 不會留下 token）
#
# Zeabur 建議環境變數：
#   PUBLIC_BASE_URL=https://<your-domain>
#   FLASK_SECRET_KEY=一段夠長的隨機字串（或 SECRET_KEY）
# 可調整：
#   DASH_MAGIC_TOKEN_TTL_SECONDS=600
#   DASH_SESSION_TTL_SECONDS=43200

DASH_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }}｜儀表板</title>
    <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --sky-50:#f0f9ff; --sky-700:#0369a1;
      --shadow: 0 10px 30px rgba(15, 23, 42, .08);
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
      --radius: 18px;
    }
    html,body{height:100%;}
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    a{color:inherit;}
    /* layout */
    .wrap{max-width:1100px; margin:0 auto; padding:18px 16px 44px;}
    .max-w-3xl{max-width:768px;}
    .max-w-5xl{max-width:1024px;}
    .max-w-6xl{max-width:1152px;}
    .mx-auto{margin-left:auto; margin-right:auto;}
    .ml-auto{margin-left:auto;}
    .block{display:block;}
    .w-full{width:100%;}
    .min-w-full{min-width:100%;}
    .overflow-hidden{overflow:hidden;}
    .overflow-x-auto{overflow-x:auto;}
    .break-all{word-break:break-all;}
    .flex{display:flex;}
    .inline-flex{display:inline-flex;}
    .flex-col{flex-direction:column;}
    .flex-wrap{flex-wrap:wrap;}
    .items-center{align-items:center;}
    .items-end{align-items:flex-end;}
    .items-start{align-items:flex-start;}
    .justify-between{justify-content:space-between;}
    .grid{display:grid;}
    .grid-cols-1{grid-template-columns:1fr;}
    .gap-1{gap:4px;}
    .gap-2{gap:8px;}
    .gap-3{gap:12px;}
    .gap-4{gap:16px;}
    .space-y-2 > * + *{margin-top:8px;}
    .space-y-3 > * + *{margin-top:12px;}
    .space-y-6 > * + *{margin-top:24px;}
    .divide-y > * + *{border-top:1px solid var(--slate-200);}
    /* responsive (subset) */
    @media (min-width:768px){
      .md\:flex-row{flex-direction:row;}
      .md\:items-end{align-items:flex-end;}
      .md\:items-start{align-items:flex-start;}
      .md\:justify-between{justify-content:space-between;}
      .md\:grid-cols-2{grid-template-columns:repeat(2, minmax(0,1fr));}
      .md\:col-span-2{grid-column:span 2 / span 2;}
      .md\:mt-0{margin-top:0;}
      .md\:text-3xl{font-size:30px;}
    }
    @media (min-width:1024px){
      .lg\:grid-cols-3{grid-template-columns:repeat(3, minmax(0,1fr));}
    }
    /* spacing */
    .p-3{padding:12px;}
    .p-4{padding:16px;}
    .p-5{padding:20px;}
    .p-6{padding:24px;}
    .px-2{padding-left:8px; padding-right:8px;}
    .px-3{padding-left:12px; padding-right:12px;}
    .px-4{padding-left:16px; padding-right:16px;}
    .px-5{padding-left:20px; padding-right:20px;}
    .py-1{padding-top:4px; padding-bottom:4px;}
    .py-2{padding-top:8px; padding-bottom:8px;}
    .py-10{padding-top:40px; padding-bottom:40px;}
    .mt-1{margin-top:4px;}
    .mt-2{margin-top:8px;}
    .mt-3{margin-top:12px;}
    .mt-4{margin-top:16px;}
    .mt-5{margin-top:20px;}
    .mt-6{margin-top:24px;}
    .mt-8{margin-top:32px;}
    .mt-10{margin-top:40px;}
    .pr-4{padding-right:16px;}
    /* typography */
    .font-bold{font-weight:800;}
    .font-semibold{font-weight:700;}
    .font-mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .tracking-tight{letter-spacing:-0.02em;}
    .text-left{text-align:left;}
    .text-xs{font-size:12px;}
    .text-sm{font-size:13px;}
    .text-lg{font-size:18px;}
    .text-xl{font-size:20px;}
    .text-2xl{font-size:26px;}
    .text-3xl{font-size:30px;}
    .text-white{color:#fff;}
    .text-slate-500{color:var(--slate-500);}
    .text-slate-600{color:var(--slate-600);}
    .text-slate-700{color:var(--slate-700);}
    .text-slate-800{color:var(--slate-800);}
    .text-slate-900{color:var(--slate-900);}
    .text-emerald-700{color:var(--emerald-700);}
    .text-emerald-900{color:var(--emerald-900);}
    .text-amber-700{color:var(--amber-700);}
    .text-amber-800{color:var(--amber-800);}
    .text-amber-900{color:var(--amber-900);}
    .text-rose-900{color:var(--rose-900);}
    .text-sky-700{color:var(--sky-700);}
    .underline{text-decoration:underline;}
    /* surfaces */
    .bg-white{background:#fff;}
    .bg-slate-50{background:var(--slate-50);}
    .bg-slate-100{background:var(--slate-100);}
    .bg-slate-900{background:var(--slate-900);}
    .bg-emerald-50{background:var(--emerald-50);}
    .bg-amber-50{background:var(--amber-50);}
    .bg-rose-50{background:var(--rose-50);}
    .bg-sky-50{background:var(--sky-50);}
    .border{border-width:1px; border-style:solid;}
    .border-slate-200{border-color:var(--slate-200);}
    .border-slate-300{border-color:var(--slate-300);}
    .border-emerald-200{border-color:var(--emerald-200);}
    .border-amber-200{border-color:var(--amber-200);}
    .border-rose-200{border-color:var(--rose-200);}
    .rounded{border-radius:10px;}
    .rounded-lg{border-radius:12px;}
    .rounded-xl{border-radius:16px;}
    .rounded-2xl{border-radius:20px;}
    .rounded-full{border-radius:999px;}
    .shadow{box-shadow:var(--shadow);}
    .shadow-sm{box-shadow:var(--shadow-sm);}
    .h-auto{height:auto;}
    /* hover (subset) */
    .hover\:bg-slate-50:hover{background:var(--slate-50);}
    .hover\:text-slate-800:hover{color:var(--slate-800);}
    /* buttons (for login templates) */
    .btn{display:inline-block; padding:10px 14px; border-radius:14px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:800;}
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:16px; box-shadow:var(--shadow-sm);}
    /* Gallery helpers */
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="max-w-5xl mx-auto px-4 py-10">
    <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold">{{ bot_name }}｜私人儀表板</h1>
        <div class="mt-1 text-sm text-slate-600">更新時間：{{ updated_at }}　·　<a class="underline" href="{{ base_url }}/docs">使用說明</a>　·　<a class="underline" href="/dash/settings">設定中心</a>　·　<a class="underline" href="/dash/tasks">任務牆</a>　·　<a class="underline" href="/dash/gallery">相簿</a>　·　<a class="underline" href="/dash/repair">修復中心</a>　·　<a class="underline" href="/dash/game">紓壓遊戲</a></div>
      </div>
      <div class="text-sm text-slate-600">
        <div>{{ gf_label }}：{{ gf_name or "未設定" }}　·　{{ bf_label }}：{{ bf_name or "未設定" }}</div>
      </div>
    </div>

    {% if warning %}
    <div class="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-amber-900">
      {{ warning }}
    </div>
    {% endif %}

    <div class="mt-8 grid gap-4 md:grid-cols-2">
      <div class="rounded-2xl bg-white shadow p-6">
        <div class="text-lg font-semibold">📅 紀念日</div>
        {% if anniversary_date %}
          <div class="mt-3 text-3xl font-bold">{{ anniversary_days }}</div>
          <div class="mt-1 text-sm text-slate-600">從 {{ anniversary_date }} 算起（含當天）</div>
        {% else %}
          <div class="mt-3 text-slate-700">尚未設定。到 LINE 輸入：<span class="font-mono bg-slate-100 px-2 py-1 rounded">設定紀念日 YYYY-MM-DD</span></div>
        {% endif %}
      </div>

      <div class="rounded-2xl bg-white shadow p-6">
        <div class="text-lg font-semibold">💊 今天吃藥狀態</div>
        <div class="mt-3 text-slate-800">{{ pill_today_text }}</div>
        <div class="mt-3 text-sm text-slate-600">
          指令：<span class="font-mono bg-slate-100 px-2 py-1 rounded">吃了</span> /
          <span class="font-mono bg-slate-100 px-2 py-1 rounded">吃藥狀態</span> /
          <span class="font-mono bg-slate-100 px-2 py-1 rounded">吃藥紀錄 14</span>
        </div>
      </div>
    </div>

    <div class="mt-4 rounded-2xl bg-white shadow p-6">
      <div class="flex items-center justify-between gap-3">
        <div class="text-lg font-semibold">📋 事前藥紀錄（最近 {{ pills_days }} 天）</div>
        <div class="text-sm text-slate-600">最多顯示 30 天</div>
      </div>
      <div class="mt-4 overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="text-left text-slate-500">
            <tr>
              <th class="py-2 pr-4">日期</th>
              <th class="py-2 pr-4">狀態</th>
              <th class="py-2 pr-4">時間</th>
              <th class="py-2 pr-4">提醒次數</th>
            </tr>
          </thead>
          <tbody class="divide-y">
            {% for r in pills %}
            <tr>
              <td class="py-2 pr-4 font-mono">{{ r.day }}</td>
              <td class="py-2 pr-4">
                {% if r.taken %}
                  <span class="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">✅ 已回報</span>
                {% else %}
                  <span class="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-slate-700">❌ 未回報</span>
                {% endif %}
              </td>
              <td class="py-2 pr-4">{{ r.taken_time_text or "-" }}</td>
              <td class="py-2 pr-4">{{ r.remind_count }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="mt-4 grid gap-4 md:grid-cols-2">
      <div class="rounded-2xl bg-white shadow p-6">
        <div class="text-lg font-semibold">🟩 Duolingo（連勝）</div>
        <div class="mt-3 flex flex-wrap gap-2">
          {% if duo_enabled %}
            <span class="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">提醒：開</span>
          {% else %}
            <span class="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-slate-700">提醒：關</span>
          {% endif %}

          {% if duo_done_today %}
            <span class="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">今天：已玩</span>
          {% else %}
            <span class="inline-flex items-center rounded-full bg-amber-50 px-3 py-1 text-amber-800">今天：未回報</span>
          {% endif %}
        </div>
        <div class="mt-3 text-sm text-slate-600">
          時間窗：{{ duo_window }}　·　頻率：每 {{ duo_every }} 分鐘
        </div>
        <div class="mt-3 text-sm text-slate-600">
          指令：<span class="font-mono bg-slate-100 px-2 py-1 rounded">Duolingo狀態</span> /
          <span class="font-mono bg-slate-100 px-2 py-1 rounded">Duolingo已玩</span> /
          <span class="font-mono bg-slate-100 px-2 py-1 rounded">Duolingo提醒開/關</span>
        </div>
      </div>

      <div class="rounded-2xl bg-white shadow p-6">
        <div class="text-lg font-semibold">🧠 最近心情</div>
        {% if moods %}
          <ul class="mt-3 space-y-2">
            {% for m in moods %}
              <li class="rounded-xl bg-slate-50 p-3">
                <div class="text-slate-800">{{ m.text }}</div>
                <div class="mt-1 text-xs text-slate-500">#{{ m.id }} · {{ m.who }} · {{ m.created_at }}</div>
              </li>
            {% endfor %}
          </ul>
        {% else %}
          <div class="mt-3 text-slate-700">目前還沒有心情記錄。LINE 指令：<span class="font-mono bg-slate-100 px-2 py-1 rounded">心情 &lt;內容&gt;</span></div>
        {% endif %}
      </div>
    </div>

    <div class="mt-4 rounded-2xl bg-white shadow p-6">
      <div class="text-lg font-semibold">✨ 願望清單（最近 {{ wishes_limit }} 筆）</div>
      {% if wishes %}
        <div class="mt-3 overflow-x-auto">
          <table class="min-w-full text-sm">
            <thead class="text-left text-slate-500">
              <tr>
                <th class="py-2 pr-4">#</th>
                <th class="py-2 pr-4">內容</th>
                <th class="py-2 pr-4">誰</th>
                <th class="py-2 pr-4">時間</th>
              </tr>
            </thead>
            <tbody class="divide-y">
              {% for w in wishes %}
              <tr>
                <td class="py-2 pr-4 font-mono">{{ w.id }}</td>
                <td class="py-2 pr-4">{{ w.text }}</td>
                <td class="py-2 pr-4">{{ w.who }}</td>
                <td class="py-2 pr-4 text-slate-600">{{ w.created_at }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="mt-3 text-slate-700">目前還沒有願望。LINE 指令：<span class="font-mono bg-slate-100 px-2 py-1 rounded">許願 &lt;內容&gt;</span></div>
      {% endif %}
    </div>

    <div class="mt-6 text-xs text-slate-500">
      <div>🔒 這是私人頁面：需要從 LINE 取得一次性登入連結。</div>
      <div class="mt-1">若你登入失效，回到 LINE 輸入「儀表板」即可重新取得登入連結。</div>
    </div>
  </div>
</body>
</html>
"""


DASH_TASKS_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }}｜任務牆</title>
    <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --sky-50:#f0f9ff; --sky-700:#0369a1;
      --shadow: 0 10px 30px rgba(15, 23, 42, .08);
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
      --radius: 18px;
    }
    html,body{height:100%;}
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    a{color:inherit;}
    /* layout */
    .wrap{max-width:1100px; margin:0 auto; padding:18px 16px 44px;}
    .max-w-3xl{max-width:768px;}
    .max-w-5xl{max-width:1024px;}
    .max-w-6xl{max-width:1152px;}
    .mx-auto{margin-left:auto; margin-right:auto;}
    .ml-auto{margin-left:auto;}
    .block{display:block;}
    .w-full{width:100%;}
    .min-w-full{min-width:100%;}
    .overflow-hidden{overflow:hidden;}
    .overflow-x-auto{overflow-x:auto;}
    .break-all{word-break:break-all;}
    .flex{display:flex;}
    .inline-flex{display:inline-flex;}
    .flex-col{flex-direction:column;}
    .flex-wrap{flex-wrap:wrap;}
    .items-center{align-items:center;}
    .items-end{align-items:flex-end;}
    .items-start{align-items:flex-start;}
    .justify-between{justify-content:space-between;}
    .grid{display:grid;}
    .grid-cols-1{grid-template-columns:1fr;}
    .gap-1{gap:4px;}
    .gap-2{gap:8px;}
    .gap-3{gap:12px;}
    .gap-4{gap:16px;}
    .space-y-2 > * + *{margin-top:8px;}
    .space-y-3 > * + *{margin-top:12px;}
    .space-y-6 > * + *{margin-top:24px;}
    .divide-y > * + *{border-top:1px solid var(--slate-200);}
    /* responsive (subset) */
    @media (min-width:768px){
      .md\:flex-row{flex-direction:row;}
      .md\:items-end{align-items:flex-end;}
      .md\:items-start{align-items:flex-start;}
      .md\:justify-between{justify-content:space-between;}
      .md\:grid-cols-2{grid-template-columns:repeat(2, minmax(0,1fr));}
      .md\:col-span-2{grid-column:span 2 / span 2;}
      .md\:mt-0{margin-top:0;}
      .md\:text-3xl{font-size:30px;}
    }
    @media (min-width:1024px){
      .lg\:grid-cols-3{grid-template-columns:repeat(3, minmax(0,1fr));}
    }
    /* spacing */
    .p-3{padding:12px;}
    .p-4{padding:16px;}
    .p-5{padding:20px;}
    .p-6{padding:24px;}
    .px-2{padding-left:8px; padding-right:8px;}
    .px-3{padding-left:12px; padding-right:12px;}
    .px-4{padding-left:16px; padding-right:16px;}
    .px-5{padding-left:20px; padding-right:20px;}
    .py-1{padding-top:4px; padding-bottom:4px;}
    .py-2{padding-top:8px; padding-bottom:8px;}
    .py-10{padding-top:40px; padding-bottom:40px;}
    .mt-1{margin-top:4px;}
    .mt-2{margin-top:8px;}
    .mt-3{margin-top:12px;}
    .mt-4{margin-top:16px;}
    .mt-5{margin-top:20px;}
    .mt-6{margin-top:24px;}
    .mt-8{margin-top:32px;}
    .mt-10{margin-top:40px;}
    .pr-4{padding-right:16px;}
    /* typography */
    .font-bold{font-weight:800;}
    .font-semibold{font-weight:700;}
    .font-mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .tracking-tight{letter-spacing:-0.02em;}
    .text-left{text-align:left;}
    .text-xs{font-size:12px;}
    .text-sm{font-size:13px;}
    .text-lg{font-size:18px;}
    .text-xl{font-size:20px;}
    .text-2xl{font-size:26px;}
    .text-3xl{font-size:30px;}
    .text-white{color:#fff;}
    .text-slate-500{color:var(--slate-500);}
    .text-slate-600{color:var(--slate-600);}
    .text-slate-700{color:var(--slate-700);}
    .text-slate-800{color:var(--slate-800);}
    .text-slate-900{color:var(--slate-900);}
    .text-emerald-700{color:var(--emerald-700);}
    .text-emerald-900{color:var(--emerald-900);}
    .text-amber-700{color:var(--amber-700);}
    .text-amber-800{color:var(--amber-800);}
    .text-amber-900{color:var(--amber-900);}
    .text-rose-900{color:var(--rose-900);}
    .text-sky-700{color:var(--sky-700);}
    .underline{text-decoration:underline;}
    /* surfaces */
    .bg-white{background:#fff;}
    .bg-slate-50{background:var(--slate-50);}
    .bg-slate-100{background:var(--slate-100);}
    .bg-slate-900{background:var(--slate-900);}
    .bg-emerald-50{background:var(--emerald-50);}
    .bg-amber-50{background:var(--amber-50);}
    .bg-rose-50{background:var(--rose-50);}
    .bg-sky-50{background:var(--sky-50);}
    .border{border-width:1px; border-style:solid;}
    .border-slate-200{border-color:var(--slate-200);}
    .border-slate-300{border-color:var(--slate-300);}
    .border-emerald-200{border-color:var(--emerald-200);}
    .border-amber-200{border-color:var(--amber-200);}
    .border-rose-200{border-color:var(--rose-200);}
    .rounded{border-radius:10px;}
    .rounded-lg{border-radius:12px;}
    .rounded-xl{border-radius:16px;}
    .rounded-2xl{border-radius:20px;}
    .rounded-full{border-radius:999px;}
    .shadow{box-shadow:var(--shadow);}
    .shadow-sm{box-shadow:var(--shadow-sm);}
    .h-auto{height:auto;}
    /* hover (subset) */
    .hover\:bg-slate-50:hover{background:var(--slate-50);}
    .hover\:text-slate-800:hover{color:var(--slate-800);}
    /* buttons (for login templates) */
    .btn{display:inline-block; padding:10px 14px; border-radius:14px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:800;}
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:16px; box-shadow:var(--shadow-sm);}
    /* Gallery helpers */
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="max-w-6xl mx-auto px-4 py-10">
    <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold">📌 任務牆</h1>
        <div class="mt-1 text-sm text-slate-600">
          更新時間：{{ updated_at }}
          · <a class="underline" href="/dash">回儀表板</a>
          · <a class="underline" href="/dash/settings">設定中心</a>
          · <a class="underline" href="/dash/gallery">相簿</a>
          · <a class="underline" href="/dash/repair">修復中心</a>
          · <a class="underline" href="/dash/game">紓壓遊戲</a>
        </div>
      </div>
      <div class="text-sm text-slate-600">
        <div>{{ gf_label }}：{{ gf_name or "未設定" }}　·　{{ bf_label }}：{{ bf_name or "未設定" }}</div>
      </div>
    </div>

    <div class="mt-6 rounded-2xl bg-white shadow p-6">
      <div class="flex flex-wrap items-center gap-3">
        <div class="text-sm text-slate-600">Open：<span class="font-semibold text-slate-900">{{ open_tasks|length }}</span></div>
        <div class="text-sm text-slate-600">Expired：<span class="font-semibold text-slate-900">{{ expired_tasks|length }}</span></div>
        <div class="text-sm text-slate-600">Done：<span class="font-semibold text-slate-900">{{ done_tasks|length }}</span></div>
        {% if expired_sweep_count %}
        <div class="ml-auto text-xs text-slate-500">（本次自動標記過期：{{ expired_sweep_count }}）</div>
        {% endif %}
      </div>
    </div>

    <div class="mt-6 grid gap-4 lg:grid-cols-3">
      <!-- OPEN -->
      <div class="rounded-2xl bg-white shadow p-5">
        <div class="text-lg font-semibold">🟢 Open</div>
        <div class="mt-3 space-y-3">
          {% if not open_tasks %}
            <div class="text-sm text-slate-600">目前沒有 open 任務。</div>
          {% endif %}
          {% for t in open_tasks %}
            <div class="rounded-xl border border-slate-200 p-4">
              <div class="flex items-start justify-between gap-3">
                <div class="font-semibold">#{{ t.id }} · 派給 {{ t.assign_label }}</div>
                <span class="text-xs rounded-full bg-emerald-50 text-emerald-700 px-2 py-1">open</span>
              </div>
              <div class="mt-2 text-slate-800">{{ t.text }}</div>
              <div class="mt-2 text-xs text-slate-600">
                派發：{{ t.created_by_name }} · 建立：{{ t.created_at }} · 到期：{{ t.expires_at }}
              </div>
            </div>
          {% endfor %}
        </div>
      </div>

      <!-- EXPIRED -->
      <div class="rounded-2xl bg-white shadow p-5">
        <div class="text-lg font-semibold">🟠 Expired</div>
        <div class="mt-3 space-y-3">
          {% if not expired_tasks %}
            <div class="text-sm text-slate-600">目前沒有 expired 任務。</div>
          {% endif %}
          {% for t in expired_tasks %}
            <div class="rounded-xl border border-slate-200 p-4">
              <div class="flex items-start justify-between gap-3">
                <div class="font-semibold">#{{ t.id }} · 派給 {{ t.assign_label }}</div>
                <span class="text-xs rounded-full bg-amber-50 text-amber-700 px-2 py-1">expired</span>
              </div>
              <div class="mt-2 text-slate-800">{{ t.text }}</div>
              <div class="mt-2 text-xs text-slate-600">
                派發：{{ t.created_by_name }} · 建立：{{ t.created_at }} · 到期：{{ t.expires_at }}
              </div>
            </div>
          {% endfor %}
        </div>
      </div>

      <!-- DONE -->
      <div class="rounded-2xl bg-white shadow p-5">
        <div class="text-lg font-semibold">✅ Done</div>
        <div class="mt-3 space-y-3">
          {% if not done_tasks %}
            <div class="text-sm text-slate-600">目前沒有 done 任務。</div>
          {% endif %}
          {% for t in done_tasks %}
            <div class="rounded-xl border border-slate-200 p-4">
              <div class="flex items-start justify-between gap-3">
                <div class="font-semibold">#{{ t.id }} · 派給 {{ t.assign_label }}</div>
                <span class="text-xs rounded-full bg-sky-50 text-sky-700 px-2 py-1">done</span>
              </div>
              <div class="mt-2 text-slate-800">{{ t.text }}</div>
              <div class="mt-2 text-xs text-slate-600">
                派發：{{ t.created_by_name }} · 建立：{{ t.created_at }} · 完成：{{ t.done_at or "-" }}
              </div>
              <div class="mt-3">
                <a class="text-sm underline" href="/dash/gallery?group=task&task_id={{ t.id }}">看這個任務的照片</a>
              </div>
            </div>
          {% endfor %}
        </div>
      </div>
    </div>

    <div class="mt-10 text-xs text-slate-500">
      註：任務在到期前上傳照片，會自動「交作業」並標記 done；超過到期會自動標記 expired。
    </div>
  </div>
</body>
</html>
"""


DASH_GALLERY_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }}｜相簿</title>
    <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --sky-50:#f0f9ff; --sky-700:#0369a1;
      --shadow: 0 10px 30px rgba(15, 23, 42, .08);
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
      --radius: 18px;
    }
    html,body{height:100%;}
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    a{color:inherit;}
    /* layout */
    .wrap{max-width:1100px; margin:0 auto; padding:18px 16px 44px;}
    .max-w-3xl{max-width:768px;}
    .max-w-5xl{max-width:1024px;}
    .max-w-6xl{max-width:1152px;}
    .mx-auto{margin-left:auto; margin-right:auto;}
    .ml-auto{margin-left:auto;}
    .block{display:block;}
    .w-full{width:100%;}
    .min-w-full{min-width:100%;}
    .overflow-hidden{overflow:hidden;}
    .overflow-x-auto{overflow-x:auto;}
    .break-all{word-break:break-all;}
    .flex{display:flex;}
    .inline-flex{display:inline-flex;}
    .flex-col{flex-direction:column;}
    .flex-wrap{flex-wrap:wrap;}
    .items-center{align-items:center;}
    .items-end{align-items:flex-end;}
    .items-start{align-items:flex-start;}
    .justify-between{justify-content:space-between;}
    .grid{display:grid;}
    .grid-cols-1{grid-template-columns:1fr;}
    .gap-1{gap:4px;}
    .gap-2{gap:8px;}
    .gap-3{gap:12px;}
    .gap-4{gap:16px;}
    .space-y-2 > * + *{margin-top:8px;}
    .space-y-3 > * + *{margin-top:12px;}
    .space-y-6 > * + *{margin-top:24px;}
    .divide-y > * + *{border-top:1px solid var(--slate-200);}
    /* responsive (subset) */
    @media (min-width:768px){
      .md\:flex-row{flex-direction:row;}
      .md\:items-end{align-items:flex-end;}
      .md\:items-start{align-items:flex-start;}
      .md\:justify-between{justify-content:space-between;}
      .md\:grid-cols-2{grid-template-columns:repeat(2, minmax(0,1fr));}
      .md\:col-span-2{grid-column:span 2 / span 2;}
      .md\:mt-0{margin-top:0;}
      .md\:text-3xl{font-size:30px;}
    }
    @media (min-width:1024px){
      .lg\:grid-cols-3{grid-template-columns:repeat(3, minmax(0,1fr));}
    }
    /* spacing */
    .p-3{padding:12px;}
    .p-4{padding:16px;}
    .p-5{padding:20px;}
    .p-6{padding:24px;}
    .px-2{padding-left:8px; padding-right:8px;}
    .px-3{padding-left:12px; padding-right:12px;}
    .px-4{padding-left:16px; padding-right:16px;}
    .px-5{padding-left:20px; padding-right:20px;}
    .py-1{padding-top:4px; padding-bottom:4px;}
    .py-2{padding-top:8px; padding-bottom:8px;}
    .py-10{padding-top:40px; padding-bottom:40px;}
    .mt-1{margin-top:4px;}
    .mt-2{margin-top:8px;}
    .mt-3{margin-top:12px;}
    .mt-4{margin-top:16px;}
    .mt-5{margin-top:20px;}
    .mt-6{margin-top:24px;}
    .mt-8{margin-top:32px;}
    .mt-10{margin-top:40px;}
    .pr-4{padding-right:16px;}
    /* typography */
    .font-bold{font-weight:800;}
    .font-semibold{font-weight:700;}
    .font-mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .tracking-tight{letter-spacing:-0.02em;}
    .text-left{text-align:left;}
    .text-xs{font-size:12px;}
    .text-sm{font-size:13px;}
    .text-lg{font-size:18px;}
    .text-xl{font-size:20px;}
    .text-2xl{font-size:26px;}
    .text-3xl{font-size:30px;}
    .text-white{color:#fff;}
    .text-slate-500{color:var(--slate-500);}
    .text-slate-600{color:var(--slate-600);}
    .text-slate-700{color:var(--slate-700);}
    .text-slate-800{color:var(--slate-800);}
    .text-slate-900{color:var(--slate-900);}
    .text-emerald-700{color:var(--emerald-700);}
    .text-emerald-900{color:var(--emerald-900);}
    .text-amber-700{color:var(--amber-700);}
    .text-amber-800{color:var(--amber-800);}
    .text-amber-900{color:var(--amber-900);}
    .text-rose-900{color:var(--rose-900);}
    .text-sky-700{color:var(--sky-700);}
    .underline{text-decoration:underline;}
    /* surfaces */
    .bg-white{background:#fff;}
    .bg-slate-50{background:var(--slate-50);}
    .bg-slate-100{background:var(--slate-100);}
    .bg-slate-900{background:var(--slate-900);}
    .bg-emerald-50{background:var(--emerald-50);}
    .bg-amber-50{background:var(--amber-50);}
    .bg-rose-50{background:var(--rose-50);}
    .bg-sky-50{background:var(--sky-50);}
    .border{border-width:1px; border-style:solid;}
    .border-slate-200{border-color:var(--slate-200);}
    .border-slate-300{border-color:var(--slate-300);}
    .border-emerald-200{border-color:var(--emerald-200);}
    .border-amber-200{border-color:var(--amber-200);}
    .border-rose-200{border-color:var(--rose-200);}
    .rounded{border-radius:10px;}
    .rounded-lg{border-radius:12px;}
    .rounded-xl{border-radius:16px;}
    .rounded-2xl{border-radius:20px;}
    .rounded-full{border-radius:999px;}
    .shadow{box-shadow:var(--shadow);}
    .shadow-sm{box-shadow:var(--shadow-sm);}
    .h-auto{height:auto;}
    /* hover (subset) */
    .hover\:bg-slate-50:hover{background:var(--slate-50);}
    .hover\:text-slate-800:hover{color:var(--slate-800);}
    /* buttons (for login templates) */
    .btn{display:inline-block; padding:10px 14px; border-radius:14px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:800;}
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:16px; box-shadow:var(--shadow-sm);}
    /* Gallery helpers */
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
  <style>
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="max-w-6xl mx-auto px-4 py-10">
    <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold">🖼️ 相簿</h1>
        <div class="mt-1 text-sm text-slate-600">
          更新時間：{{ updated_at }}
          · <a class="underline" href="/dash">回儀表板</a>
          · <a class="underline" href="/dash/settings">設定中心</a>
          · <a class="underline" href="/dash/tasks">任務牆</a>
          · <a class="underline" href="/dash/repair">修復中心</a>
          · <a class="underline" href="/dash/game">紓壓遊戲</a>
        </div>
      </div>
      <div class="text-sm text-slate-600">
        <div>{{ gf_label }}：{{ gf_name or "未設定" }}　·　{{ bf_label }}：{{ bf_name or "未設定" }}</div>
      </div>
    </div>

    <div class="mt-6 rounded-2xl bg-white shadow p-6">
      <div class="flex flex-wrap items-center gap-3">
        <div class="text-sm text-slate-600">顯示筆數（最近）：</div>
        <div class="text-xs text-slate-500">（使用縮圖加速，點照片可開原圖）</div>

        {% for n in [60, 120, 240, 400] %}
          <a class="text-sm underline {% if limit == n %}font-semibold{% endif %}" href="/dash/gallery?group={{ group }}&limit={{ n }}{% if task_id %}&task_id={{ task_id }}{% endif %}">{{ n }}</a>
        {% endfor %}
        <div class="ml-auto flex items-center gap-3">
          <a class="text-sm underline {% if group == 'date' %}font-semibold{% endif %}" href="/dash/gallery?group=date&limit={{ limit }}">依日期</a>
          <a class="text-sm underline {% if group == 'task' %}font-semibold{% endif %}" href="/dash/gallery?group=task&limit={{ limit }}">依任務</a>
        </div>
      </div>

    </div>
      {% set prev_offset = offset - limit if offset - limit > 0 else 0 %}
      <div class="mt-3 flex items-center gap-4 text-sm text-slate-600">
        <div>Offset：{{ offset }}</div>
        <a class="underline" href="/dash/gallery?group={{ group }}&limit={{ limit }}&offset={{ prev_offset }}{% if task_id %}&task_id={{ task_id }}{% endif %}">上一頁</a>
        <a class="underline" href="/dash/gallery?group={{ group }}&limit={{ limit }}&offset={{ offset + limit }}{% if task_id %}&task_id={{ task_id }}{% endif %}">下一頁</a>
      </div>
    </div>

    {% if group == 'date' %}
      {% if not date_groups %}
        <div class="mt-6 text-sm text-slate-600">目前沒有照片。</div>
      {% endif %}

      {% for day, items in date_groups %}
        <div class="mt-8 flex items-end justify-between">
          <div class="text-lg font-semibold">{{ day }}</div>
          <div class="text-sm text-slate-600">{{ items|length }} 張</div>
        </div>
        <div class="mt-4 masonry">
          {% for it in items %}
            <div class="masonry-item">
              <div class="rounded-2xl bg-white shadow overflow-hidden">
                <a href="{{ it.full }}" target="_blank" rel="noopener"><img class="w-full h-auto" loading="lazy" src="{{ it.thumb }}" /></a>
                <div class="p-3 text-xs text-slate-600">
                  <div class="flex items-center justify-between gap-2">
                    <div>{{ it.who }} · {{ it.time }}</div>
                    {% if it.task_id %}
                      <a class="underline" href="/dash/gallery?group=task&task_id={{ it.task_id }}">任務 #{{ it.task_id }}</a>
                    {% endif %}
                  </div>
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      {% endfor %}
    {% else %}
      {% if task_id %}
        <div class="mt-6 text-sm text-slate-600">已篩選任務：<span class="font-semibold">#{{ task_id }}</span>（<a class="underline" href="/dash/gallery?group=task&limit={{ limit }}">清除篩選</a>）</div>
      {% endif %}

      {% if not task_groups %}
        <div class="mt-6 text-sm text-slate-600">目前沒有「任務→照片」關聯資料。派一個攝影任務後，在到期內上傳照片，就會自動掛到任務底下。</div>
      {% endif %}

      {% for g in task_groups %}
        <div id="task-{{ g.task.id }}" class="mt-10 rounded-2xl bg-white shadow p-6">
          <div class="flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
            <div>
              <div class="text-lg font-semibold">#{{ g.task.id }} · {{ g.task.status }} · 派給 {{ g.task.assign_label }}</div>
              <div class="mt-1 text-slate-800">{{ g.task.text }}</div>
              <div class="mt-2 text-xs text-slate-600">
                派發：{{ g.task.created_by_name }} · 建立：{{ g.task.created_at }} · 到期：{{ g.task.expires_at }} · 完成：{{ g.task.done_at or "-" }}
              </div>
            </div>
            <div class="mt-3 md:mt-0 text-sm">
              <a class="underline" href="/dash/tasks">回任務牆</a>
            </div>
          </div>

          {% if g.items %}
            <div class="mt-5 masonry">
              {% for it in g.items %}
                <div class="masonry-item">
                  <div class="rounded-2xl border border-slate-200 overflow-hidden">
                    <a href="{{ it.full }}" target="_blank" rel="noopener"><img class="w-full h-auto" loading="lazy" src="{{ it.thumb }}" /></a>
                    <div class="p-3 text-xs text-slate-600">{{ it.who }} · {{ it.time }}</div>
                  </div>
                </div>
              {% endfor %}
            </div>
          {% else %}
            <div class="mt-4 text-sm text-slate-600">這個任務目前沒有掛到照片（可能是舊資料、或交作業時尚未啟用關聯）。</div>
          {% endif %}
        </div>
      {% endfor %}
    {% endif %}
  </div>
</body>
</html>
"""
DASH_SETTINGS_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --sky-50:#f0f9ff; --sky-700:#0369a1;
      --shadow: 0 10px 30px rgba(15, 23, 42, .08);
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
      --radius: 18px;
    }
    html,body{height:100%;}
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    a{color:inherit;}
    /* layout */
    .wrap{max-width:1100px; margin:0 auto; padding:18px 16px 44px;}
    .max-w-3xl{max-width:768px;}
    .max-w-5xl{max-width:1024px;}
    .max-w-6xl{max-width:1152px;}
    .mx-auto{margin-left:auto; margin-right:auto;}
    .ml-auto{margin-left:auto;}
    .block{display:block;}
    .w-full{width:100%;}
    .min-w-full{min-width:100%;}
    .overflow-hidden{overflow:hidden;}
    .overflow-x-auto{overflow-x:auto;}
    .break-all{word-break:break-all;}
    .flex{display:flex;}
    .inline-flex{display:inline-flex;}
    .flex-col{flex-direction:column;}
    .flex-wrap{flex-wrap:wrap;}
    .items-center{align-items:center;}
    .items-end{align-items:flex-end;}
    .items-start{align-items:flex-start;}
    .justify-between{justify-content:space-between;}
    .grid{display:grid;}
    .grid-cols-1{grid-template-columns:1fr;}
    .gap-1{gap:4px;}
    .gap-2{gap:8px;}
    .gap-3{gap:12px;}
    .gap-4{gap:16px;}
    .space-y-2 > * + *{margin-top:8px;}
    .space-y-3 > * + *{margin-top:12px;}
    .space-y-6 > * + *{margin-top:24px;}
    .divide-y > * + *{border-top:1px solid var(--slate-200);}
    /* responsive (subset) */
    @media (min-width:768px){
      .md\:flex-row{flex-direction:row;}
      .md\:items-end{align-items:flex-end;}
      .md\:items-start{align-items:flex-start;}
      .md\:justify-between{justify-content:space-between;}
      .md\:grid-cols-2{grid-template-columns:repeat(2, minmax(0,1fr));}
      .md\:col-span-2{grid-column:span 2 / span 2;}
      .md\:mt-0{margin-top:0;}
      .md\:text-3xl{font-size:30px;}
    }
    @media (min-width:1024px){
      .lg\:grid-cols-3{grid-template-columns:repeat(3, minmax(0,1fr));}
    }
    /* spacing */
    .p-3{padding:12px;}
    .p-4{padding:16px;}
    .p-5{padding:20px;}
    .p-6{padding:24px;}
    .px-2{padding-left:8px; padding-right:8px;}
    .px-3{padding-left:12px; padding-right:12px;}
    .px-4{padding-left:16px; padding-right:16px;}
    .px-5{padding-left:20px; padding-right:20px;}
    .py-1{padding-top:4px; padding-bottom:4px;}
    .py-2{padding-top:8px; padding-bottom:8px;}
    .py-10{padding-top:40px; padding-bottom:40px;}
    .mt-1{margin-top:4px;}
    .mt-2{margin-top:8px;}
    .mt-3{margin-top:12px;}
    .mt-4{margin-top:16px;}
    .mt-5{margin-top:20px;}
    .mt-6{margin-top:24px;}
    .mt-8{margin-top:32px;}
    .mt-10{margin-top:40px;}
    .pr-4{padding-right:16px;}
    /* typography */
    .font-bold{font-weight:800;}
    .font-semibold{font-weight:700;}
    .font-mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .tracking-tight{letter-spacing:-0.02em;}
    .text-left{text-align:left;}
    .text-xs{font-size:12px;}
    .text-sm{font-size:13px;}
    .text-lg{font-size:18px;}
    .text-xl{font-size:20px;}
    .text-2xl{font-size:26px;}
    .text-3xl{font-size:30px;}
    .text-white{color:#fff;}
    .text-slate-500{color:var(--slate-500);}
    .text-slate-600{color:var(--slate-600);}
    .text-slate-700{color:var(--slate-700);}
    .text-slate-800{color:var(--slate-800);}
    .text-slate-900{color:var(--slate-900);}
    .text-emerald-700{color:var(--emerald-700);}
    .text-emerald-900{color:var(--emerald-900);}
    .text-amber-700{color:var(--amber-700);}
    .text-amber-800{color:var(--amber-800);}
    .text-amber-900{color:var(--amber-900);}
    .text-rose-900{color:var(--rose-900);}
    .text-sky-700{color:var(--sky-700);}
    .underline{text-decoration:underline;}
    /* surfaces */
    .bg-white{background:#fff;}
    .bg-slate-50{background:var(--slate-50);}
    .bg-slate-100{background:var(--slate-100);}
    .bg-slate-900{background:var(--slate-900);}
    .bg-emerald-50{background:var(--emerald-50);}
    .bg-amber-50{background:var(--amber-50);}
    .bg-rose-50{background:var(--rose-50);}
    .bg-sky-50{background:var(--sky-50);}
    .border{border-width:1px; border-style:solid;}
    .border-slate-200{border-color:var(--slate-200);}
    .border-slate-300{border-color:var(--slate-300);}
    .border-emerald-200{border-color:var(--emerald-200);}
    .border-amber-200{border-color:var(--amber-200);}
    .border-rose-200{border-color:var(--rose-200);}
    .rounded{border-radius:10px;}
    .rounded-lg{border-radius:12px;}
    .rounded-xl{border-radius:16px;}
    .rounded-2xl{border-radius:20px;}
    .rounded-full{border-radius:999px;}
    .shadow{box-shadow:var(--shadow);}
    .shadow-sm{box-shadow:var(--shadow-sm);}
    .h-auto{height:auto;}
    /* hover (subset) */
    .hover\:bg-slate-50:hover{background:var(--slate-50);}
    .hover\:text-slate-800:hover{color:var(--slate-800);}
    /* buttons (for login templates) */
    .btn{display:inline-block; padding:10px 14px; border-radius:14px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:800;}
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:16px; box-shadow:var(--shadow-sm);}
    /* Gallery helpers */
    .masonry { column-gap: 1rem; column-count: 2; }
    @media (min-width: 768px) { .masonry { column-count: 3; } }
    @media (min-width: 1024px) { .masonry { column-count: 4; } }
    .masonry-item { break-inside: avoid; margin-bottom: 1rem; }
  </style>
  <title>{{ bot_name }} · 設定中心</title>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="mx-auto max-w-5xl p-6">
    <div class="flex items-center justify-between">
      <div>
        <div class="text-2xl font-bold">{{ bot_name }} · 設定中心</div>
        <div class="mt-1 text-sm text-slate-600">更新時間：{{ updated_at }}　·　<a class="underline" href="/dash">回儀表板</a>　·　<a class="underline" href="/dash/tasks">任務牆</a>　·　<a class="underline" href="/dash/gallery">相簿</a>　·　<a class="underline" href="/dash/repair">修復中心</a>　·　<a class="underline" href="/dash/game">紓壓遊戲</a></div>
      </div>
      <div class="text-sm text-slate-600">
        <div>{{ gf_label }}：{{ gf_name or "未設定" }}　·　{{ bf_label }}：{{ bf_name or "未設定" }}</div>
      </div>
    </div>

    {% if status %}
      <div class="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-900">{{ status }}</div>
    {% endif %}
    {% if error %}
      <div class="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-900">{{ error }}</div>
    {% endif %}

    <form class="mt-6 space-y-6" method="post">
      <div class="rounded-2xl bg-white shadow-sm border border-slate-200 p-5">
        <div class="text-lg font-semibold">天氣提醒</div>
        <div class="mt-1 text-sm text-slate-600">門檻與提醒時段（不用改 env / 重啟）。提醒時段格式：<span class="font-mono">08:30,12:30,17:30</span></div>
        <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="flex items-center gap-2">
            <input type="checkbox" name="weather_remind_enabled" value="1" {% if weather_remind_enabled %}checked{% endif %} />
            <span>啟用天氣提醒推播</span>
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">提醒時段（CSV）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="weather_remind_times" value="{{ weather_remind_times }}" placeholder="08:30,12:30,17:30" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">低溫門檻（°C）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="temp_low_threshold" value="{{ temp_low_threshold }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">高溫門檻（°C）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="temp_high_threshold" value="{{ temp_high_threshold }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">體感低溫門檻（°C）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="app_temp_low_threshold" value="{{ app_temp_low_threshold }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">體感高溫門檻（°C）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="app_temp_high_threshold" value="{{ app_temp_high_threshold }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">UV 門檻</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="uv_high_threshold" value="{{ uv_high_threshold }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">濕度波動門檻（%）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="humidity_range_threshold" value="{{ humidity_range_threshold }}" />
          </label>
        </div>
      </div>

      <div class="rounded-2xl bg-white shadow-sm border border-slate-200 p-5">
        <div class="text-lg font-semibold">Duolingo 提醒</div>
        <div class="mt-1 text-sm text-slate-600">時段/頻率（提醒開關仍可用 LINE 指令）。</div>
        <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="flex items-center gap-2">
            <input type="checkbox" name="duo_remind_enabled" value="1" {% if duo_remind_enabled %}checked{% endif %} />
            <span>啟用 Duolingo 提醒</span>
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">提醒頻率（分鐘）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="duo_remind_every_minutes" value="{{ duo_remind_every_minutes }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">開始小時（0-23）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="duo_remind_start_hour" value="{{ duo_remind_start_hour }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">結束小時（0-23）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="duo_remind_end_hour" value="{{ duo_remind_end_hour }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">今天已玩（顯示用）</div>
            <div class="mt-2 text-sm">{{ "是" if duo_done_today else "否" }}</div>
          </label>
        </div>
      </div>

      <div class="rounded-2xl bg-white shadow-sm border border-slate-200 p-5">
        <div class="text-lg font-semibold">吃藥提醒</div>
        <div class="mt-1 text-sm text-slate-600">提醒時間/間隔/最多提醒幾次。</div>
        <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="flex items-center gap-2">
            <input type="checkbox" name="med_pill_enabled" value="1" {% if med_pill_enabled %}checked{% endif %} />
            <span>啟用吃藥提醒</span>
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">每日提醒時間（HH:MM）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="med_pill_remind_time" value="{{ med_pill_remind_time }}" placeholder="23:00" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">提醒間隔（分鐘）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="med_pill_nudge_minutes" value="{{ med_pill_nudge_minutes }}" />
          </label>

          <label class="block">
            <div class="text-sm text-slate-600">最多提醒幾次（0=不限）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="med_pill_max_remind_count" value="{{ med_pill_max_remind_count }}" />
          </label>

          <label class="block md:col-span-2">
            <div class="text-sm text-slate-600">安靜時段（HH:MM-HH:MM，留空=不啟用）</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2 font-mono" name="med_pill_quiet_hours" value="{{ med_pill_quiet_hours }}" placeholder="00:00-07:00" />
          </label>
        </div>
      </div>

      <div class="rounded-2xl bg-white shadow-sm border border-slate-200 p-5">
        <div class="text-lg font-semibold">角色顯示名稱</div>
        <div class="mt-1 text-sm text-slate-600">儀表板顯示用（不影響 LINE 顯示名稱）。</div>
        <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <label class="block">
            <div class="text-sm text-slate-600">Girlfriend 標籤</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2" name="role_label_girlfriend" value="{{ role_label_girlfriend }}" placeholder="Girlfriend" />
          </label>
          <label class="block">
            <div class="text-sm text-slate-600">Boyfriend 標籤</div>
            <input class="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2" name="role_label_boyfriend" value="{{ role_label_boyfriend }}" placeholder="Boyfriend" />
          </label>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <button class="rounded-2xl bg-slate-900 text-white px-5 py-2" type="submit">儲存並套用</button>
        <a class="rounded-2xl border border-slate-300 px-5 py-2" href="/dash">取消</a>
      </div>

      <div class="text-xs text-slate-500">
        <div>備註：儲存後會即時套用排程（APScheduler）。若你目前關掉 ENABLE_SCHEDULER，設定會保存但不會推播。</div>
      </div>
    </form>
  </div>
</body>
</html>
"""






DASH_REPAIR_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} · 修復中心</title>
  <style>
    :root{
      --slate-50:#f8fafc; --slate-100:#f1f5f9; --slate-200:#e2e8f0; --slate-300:#cbd5e1;
      --slate-500:#64748b; --slate-600:#475569; --slate-700:#334155; --slate-800:#1f2937; --slate-900:#0f172a;
      --emerald-50:#ecfdf5; --emerald-200:#a7f3d0; --emerald-700:#047857; --emerald-900:#064e3b;
      --amber-50:#fffbeb; --amber-200:#fde68a; --amber-700:#b45309; --amber-800:#92400e; --amber-900:#78350f;
      --rose-50:#fff1f2; --rose-200:#fecdd3; --rose-900:#881337;
      --shadow-sm: 0 6px 18px rgba(15, 23, 42, .06);
    }
    body{
      margin:0;
      background:var(--slate-50);
      color:var(--slate-900);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial;
    }
    .wrap{max-width:1080px; margin:0 auto; padding:24px 16px 40px;}
    .grid{display:grid; gap:16px;}
    .grid-2{grid-template-columns:1fr;}
    @media (min-width: 900px){ .grid-2{grid-template-columns:1fr 1fr;} }
    .card{background:#fff; border:1px solid var(--slate-200); border-radius:18px; padding:18px; box-shadow:var(--shadow-sm);}
    .muted{color:var(--slate-600);}
    .tiny{font-size:12px; color:var(--slate-500);}
    .row{display:flex; flex-wrap:wrap; gap:10px; align-items:center;}
    .pill{display:inline-flex; align-items:center; border-radius:999px; padding:4px 10px; font-size:12px;}
    .pill-open{background:var(--amber-50); color:var(--amber-800);}
    .pill-closed{background:var(--emerald-50); color:var(--emerald-700);}
    .pill-cool{background:var(--rose-50); color:var(--rose-900);}
    .input, textarea, select{
      width:100%;
      border:1px solid var(--slate-300);
      border-radius:12px;
      padding:10px 12px;
      box-sizing:border-box;
      font:inherit;
      background:#fff;
    }
    textarea{min-height:110px; resize:vertical;}
    .btn{
      border:0; border-radius:12px; padding:10px 14px; cursor:pointer;
      font-weight:700; background:#111827; color:#fff;
    }
    .btn-light{
      border:1px solid var(--slate-300); background:#fff; color:var(--slate-800);
    }
    .line{height:1px; background:var(--slate-200); margin:14px 0;}
    .event{border:1px solid var(--slate-200); border-radius:14px; padding:14px;}
    .event + .event{margin-top:12px;}
    .mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    .warn{border:1px solid var(--rose-200); background:var(--rose-50); color:var(--rose-900); padding:10px 12px; border-radius:12px;}
    .ok{border:1px solid var(--emerald-200); background:var(--emerald-50); color:var(--emerald-900); padding:10px 12px; border-radius:12px;}
    .list{margin:8px 0 0 20px; padding:0;}
    .check-grid{display:grid; grid-template-columns:1fr; gap:8px; margin-top:8px;}
    @media (min-width: 600px){ .check-grid{grid-template-columns:1fr 1fr;} }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="row" style="justify-content:space-between;">
      <div>
        <div style="font-size:28px; font-weight:800;">修復中心</div>
        <div class="muted">更新時間：{{ updated_at }}　·　<a href="/dash">回儀表板</a>　·　<a href="/dash/settings">設定中心</a>　·　<a href="/dash/tasks">任務牆</a>　·　<a href="/dash/gallery">相簿</a>　·　<a href="/dash/game">紓壓遊戲</a></div>
      </div>
      <div class="muted">{{ me_name }}（你） · {{ other_name }}（對方）</div>
    </div>

    {% if status %}
      <div class="ok" style="margin-top:16px;">{{ status }}</div>
    {% endif %}
    {% if error %}
      <div class="warn" style="margin-top:16px;">{{ error }}</div>
    {% endif %}

    <div class="grid grid-2" style="margin-top:16px;">
      <div class="card">
        <div class="muted">連續不吵爆天數</div>
        <div style="font-size:36px; font-weight:800; margin-top:6px;">{{ streak_days }}</div>
        <div class="tiny">徽章：{{ streak_badge }}</div>
      </div>
      <div class="card">
        <div style="font-weight:700;">本週 Top 雷點</div>
        {% if trigger_top %}
          <ul class="list">
            {% for t in trigger_top %}
              <li>{{ t.label }}（{{ t.count }} 次）</li>
            {% endfor %}
          </ul>
        {% else %}
          <div class="tiny" style="margin-top:8px;">本週尚無資料</div>
        {% endif %}
      </div>
    </div>

    <div class="card" style="margin-top:16px;">
      <div style="font-size:20px; font-weight:800;">1) 情緒投遞箱</div>
      <div class="tiny">先抒發，再修復。系統會把內容整理成可執行任務。</div>
      <form method="post" style="margin-top:12px;">
        <input type="hidden" name="action" value="create" />

        <label>今天不爽什麼</label>
        <textarea name="vent_text" required placeholder="例如：你剛剛回我很快，但語氣讓我覺得被敷衍。"></textarea>

        <div class="grid grid-2" style="margin-top:10px;">
          <div>
            <label>情緒類型</label>
            <select name="emotion_type">
              {% for key, label in emotion_options %}
                <option value="{{ key }}">{{ label }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label>強度（1~5）</label>
            <select name="intensity">
              {% for n in [1,2,3,4,5] %}
                <option value="{{ n }}" {% if n == 3 %}selected{% endif %}>{{ n }}</option>
              {% endfor %}
            </select>
          </div>
        </div>

        <div style="margin-top:10px;">
          <label>希望對方現在回覆嗎</label>
          <div class="row" style="margin-top:6px;">
            <label><input type="radio" name="wants_reply_now" value="1" checked /> 是</label>
            <label><input type="radio" name="wants_reply_now" value="0" /> 否</label>
          </div>
        </div>

        <div style="margin-top:10px;">
          <label>2) 我現在需要</label>
          <div class="check-grid">
            {% for key, label in need_options %}
              <label><input type="radio" name="need_type" value="{{ key }}" {% if loop.first %}checked{% endif %} /> {{ label }}</label>
            {% endfor %}
          </div>
        </div>

        <div style="margin-top:14px;">
          <label style="display:inline-flex; align-items:center; gap:8px; margin-bottom:10px;">
            <input type="checkbox" name="notify_other" value="1" checked />
            <span>送出後通知對方</span>
          </label>
        </div>

        <div style="margin-top:4px;">
          <button class="btn" type="submit">送出（先抒發）</button>
        </div>
      </form>
    </div>

    <div class="card" style="margin-top:16px;">
      <div style="font-size:20px; font-weight:800;">事件回顧與修復</div>
      <div class="tiny">包含：冷卻計時、修復任務卡、觸發點記錄、雙方已修復。</div>

      <div style="margin-top:12px;">
        {% if not events %}
          <div class="tiny">目前沒有事件，先從上面的情緒投遞箱開始。</div>
        {% endif %}
        {% for ev in events %}
          <div class="event">
            <div class="row" style="justify-content:space-between;">
              <div>
                <strong>#{{ ev.id }}</strong> · {{ ev.owner_label }} · {{ ev.created_at }}
              </div>
              <div class="row">
                {% if ev.closed %}
                  <span class="pill pill-closed">已修復</span>
                {% else %}
                  <span class="pill pill-open">處理中</span>
                {% endif %}
                {% if ev.cooldown_left > 0 %}
                  <span class="pill pill-cool">冷卻 {{ ev.cooldown_left }} 分鐘</span>
                {% endif %}
              </div>
            </div>

            <div class="tiny" style="margin-top:4px;">
              情緒：{{ ev.emotion_label }} · 強度：{{ ev.intensity }} · 需要：{{ ev.need_label }} · 立即回覆：{{ "是" if ev.wants_reply_now else "否" }}
            </div>
            <div class="line"></div>
            <div>{{ ev.vent_text }}</div>

            {% if ev.cooldown_active_for_me %}
              <div class="warn" style="margin-top:12px;">
                3) 冷卻計時器啟動中：先不要解釋，先安撫。剩餘 {{ ev.cooldown_left }} 分鐘。
              </div>
            {% endif %}

            {% if ev.show_task_cards %}
              <div style="margin-top:12px;">
                <strong>4) 修復任務卡</strong>
                <ul class="list">
                  {% for t in ev.task_cards %}
                    <li>{{ t }}</li>
                  {% endfor %}
                </ul>
              </div>
            {% endif %}

            {% if ev.can_edit_triggers %}
              <form method="post" style="margin-top:12px;">
                <input type="hidden" name="action" value="save_triggers" />
                <input type="hidden" name="event_id" value="{{ ev.id }}" />
                <div><strong>5) 觸發點記錄</strong></div>
                <div class="check-grid">
                  {% for tk, tl in trigger_options %}
                    <label><input type="checkbox" name="trigger_keys" value="{{ tk }}" {% if tk in ev.triggers %}checked{% endif %} /> {{ tl }}</label>
                  {% endfor %}
                </div>
                <div style="margin-top:8px;"><button type="submit" class="btn btn-light">儲存觸發點</button></div>
              </form>
            {% endif %}

            {% if ev.can_confirm %}
              <form method="post" style="margin-top:12px;">
                <input type="hidden" name="action" value="confirm" />
                <input type="hidden" name="event_id" value="{{ ev.id }}" />
                <div class="row">
                  <div class="tiny mono">6) 和好儀式：{{ ev.confirmed_count }}/{{ ev.participant_count }} 已確認</div>
                  {% if ev.i_confirmed %}
                    <span class="pill pill-closed">你已按「已修復」</span>
                  {% elif not ev.closed %}
                    <button class="btn" type="submit">我這邊已修復</button>
                  {% endif %}
                </div>
              </form>
            {% endif %}
          </div>
        {% endfor %}
      </div>
    </div>
  </div>
</body>
</html>
"""

DASH_GAME_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} · 紓壓遊戲</title>
  <style>
    :root{
      --bg:#f8fafc; --card:#ffffff; --line:#dbe3ef; --text:#0f172a; --muted:#475569;
      --pink:#ff4d8d; --blue:#2f6bff; --orange:#ff8a4c;
    }
    body{margin:0; background:var(--bg); color:var(--text); font-family: ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans TC","Helvetica Neue",Arial;}
    .wrap{max-width:1080px; margin:0 auto; padding:20px 16px 32px;}
    .top{display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; align-items:flex-end;}
    .muted{color:var(--muted);}
    .card{background:var(--card); border:1px solid var(--line); border-radius:20px; padding:16px; box-shadow:0 8px 24px rgba(15,23,42,.06);}
    .grid{display:grid; grid-template-columns:1fr; gap:16px; margin-top:16px;}
    @media (min-width: 920px){ .grid{grid-template-columns:1.2fr 1fr;} }

    .arena{
      position:relative; min-height:430px; overflow:hidden; border-radius:18px; border:1px solid #e5e7eb;
      background:
        radial-gradient(circle at 22% 16%, #ffe0f0 0, #ffe0f0 11%, transparent 12%),
        radial-gradient(circle at 78% 20%, #d7e7ff 0, #d7e7ff 12%, transparent 13%),
        linear-gradient(180deg, #fff7fc 0%, #f7fbff 55%, #ecf4ff 100%);
      cursor:pointer;
    }
    .arena .hitflash{
      position:absolute; inset:0; pointer-events:none; opacity:0;
      background:radial-gradient(circle, rgba(255,62,62,.35) 0%, rgba(255,62,62,.12) 32%, transparent 60%);
      transition:opacity .09s linear;
    }
    .arena .vignette{
      position:absolute; inset:0; pointer-events:none;
      background:radial-gradient(ellipse at center, transparent 52%, rgba(15,23,42,.12) 100%);
    }

    .dummy{
      position:absolute; left:50%; top:54%; transform:translate(-50%,-50%);
      width:190px; text-align:center; user-select:none; transition:filter .16s linear;
    }
    .dummy.downed{filter:grayscale(.45) brightness(.9);}
    .head{
      width:100px; height:100px; background:#ffd7bf; border:3px solid #334155;
      border-radius:50%; margin:0 auto; position:relative; overflow:hidden;
      box-shadow:inset 0 -8px 0 rgba(0,0,0,.06);
    }
    .eye{
      position:absolute; top:36px; width:12px; height:12px; border-radius:50%;
      background:#111827; transition:all .12s linear;
    }
    .eye.l{left:25px;} .eye.r{right:25px;}
    .mouth{
      position:absolute; left:50%; top:60px; width:34px; height:12px;
      border-bottom:4px solid #111827; border-radius:0 0 30px 30px;
      transform:translateX(-50%); transition:all .12s linear;
    }
    .wound{position:absolute; opacity:0; transition:opacity .12s linear;}
    .bruise{width:28px; height:18px; border-radius:50%; background:rgba(90,41,132,.45);}
    .bruise.b1{left:12px; top:48px;}
    .bruise.b2{right:10px; top:44px;}
    .bump{left:46px; top:6px; width:18px; height:12px; border-radius:50%; background:#d45b73;}
    .bandage{
      right:8px; top:20px; width:22px; height:10px; border-radius:3px;
      background:#f8e7bd; border:1px solid #e6d39b; transform:rotate(-20deg);
    }
    .sweat{
      left:8px; top:24px; width:10px; height:18px; border-radius:10px;
      background:rgba(80,160,255,.55); transform:rotate(8deg);
    }
    .body{
      width:136px; height:156px; background:#76a9ff; border:3px solid #334155;
      border-radius:22px; margin:8px auto 0; position:relative;
      box-shadow:inset 0 -10px 0 rgba(0,0,0,.08);
      transition:all .12s linear;
    }
    .body::after{
      content:""; position:absolute; left:20px; top:36px; width:96px; height:2px;
      background:rgba(255,255,255,.4); box-shadow:0 20px 0 rgba(255,255,255,.28), 0 40px 0 rgba(255,255,255,.2);
    }
    .name{margin-top:8px; font-weight:800;}
    .state-label{
      margin-top:4px; display:inline-block; font-size:12px; padding:2px 10px;
      border-radius:999px; background:#e2e8f0; color:#334155; border:1px solid #cbd5e1;
    }

    .dummy.stage1 .sweat{opacity:1;}
    .dummy.stage2 .sweat,.dummy.stage2 .bruise.b1{opacity:1;}
    .dummy.stage3 .sweat,.dummy.stage3 .bruise{opacity:1;}
    .dummy.stage3 .bandage{opacity:1;}
    .dummy.stage3 .mouth{
      border-bottom:0; border-top:4px solid #111827; border-radius:30px 30px 0 0; top:66px;
    }
    .dummy.stage4 .sweat,.dummy.stage4 .bruise,.dummy.stage4 .bump,.dummy.stage4 .bandage{opacity:1;}
    .dummy.stage4 .head{background:#eec2ac;}
    .dummy.stage4 .eye{
      width:15px; height:4px; border-radius:4px; top:39px; background:#111827;
    }
    .dummy.stage4 .eye.l{transform:rotate(18deg);}
    .dummy.stage4 .eye.r{transform:rotate(-18deg);}
    .dummy.stage4 .body{
      background:#6f97df;
      background-image:linear-gradient(135deg, rgba(255,255,255,.15) 25%, transparent 25%, transparent 50%, rgba(255,255,255,.15) 50%, rgba(255,255,255,.15) 75%, transparent 75%, transparent);
      background-size:16px 16px;
    }

    .fx{
      position:absolute; left:50%; top:33%; transform:translate(-50%,-50%);
      font-size:46px; font-weight:900; color:#be123c; opacity:0; pointer-events:none;
      text-shadow:0 8px 16px rgba(0,0,0,.16);
    }
    .float{
      position:absolute; font-weight:900; color:#be123c; pointer-events:none; animation:rise .72s ease-out forwards;
      text-shadow:0 2px 4px rgba(0,0,0,.1);
    }
    @keyframes rise { from{transform:translateY(0) scale(1); opacity:1;} to{transform:translateY(-56px) scale(1.12); opacity:0;} }

    .hp-wrap{margin:10px auto 0; max-width:420px;}
    .hp-row{display:flex; justify-content:space-between; gap:10px; margin-bottom:6px;}
    .hp-bg{height:18px; border-radius:999px; background:#e2e8f0; overflow:hidden; border:1px solid #cbd5e1;}
    .hp-bar{height:100%; width:100%; background:linear-gradient(90deg, #22c55e, #86efac); transition:width .12s linear, background .12s linear;}

    .tools{display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px;}
    .tool{border:1px solid #cbd5e1; background:#fff; padding:10px; border-radius:12px; cursor:pointer; text-align:left;}
    .tool.active{border-color:var(--blue); box-shadow:inset 0 0 0 1px var(--blue); background:#eff6ff;}

    .ctl{display:flex; gap:8px; margin-top:10px; flex-wrap:wrap;}
    .btn{border:0; border-radius:12px; padding:10px 14px; font-weight:800; cursor:pointer;}
    .btn-hit{background:var(--pink); color:#fff;}
    .btn-reset{background:#111827; color:#fff;}
    .btn-soft{background:#e2e8f0; color:#1f2937;}

    .stats{display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:10px;}
    .stat{background:#f8fafc; border:1px solid #dbe3ef; border-radius:12px; padding:8px;}
    .note{margin-top:12px; font-size:12px; color:#475569;}
    .gate{padding:22px; background:#fff7ed; border:1px solid #fed7aa; color:#7c2d12; border-radius:16px;}
    .section-title{font-size:16px; font-weight:800; margin-top:14px;}
    .input,.select,.textarea{
      width:100%; border:1px solid #cbd5e1; border-radius:10px; padding:8px 10px;
      box-sizing:border-box; font:inherit; background:#fff;
    }
    .textarea{min-height:78px; resize:vertical;}
    .btn-mini{
      border:1px solid #cbd5e1; border-radius:10px; background:#fff; cursor:pointer;
      padding:6px 10px; font-weight:700; color:#0f172a;
    }
    .pill{display:inline-block; border-radius:999px; padding:2px 8px; font-size:12px; font-weight:700;}
    .pill-boss{background:#ffe4e6; color:#9f1239; border:1px solid #fecdd3;}
    .pill-skill{background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;}
    .boss-panel{
      position:absolute; left:12px; top:12px; right:12px; display:flex; justify-content:space-between; gap:10px; align-items:flex-start;
      pointer-events:none;
    }
    .boss-left{background:rgba(255,255,255,.88); border:1px solid #dbe3ef; border-radius:12px; padding:8px 10px; max-width:72%;}
    .cool-card{margin-top:10px; background:#fff7ed; border:1px solid #fed7aa; border-radius:12px; padding:10px;}
    .progress{height:10px; background:#fed7aa; border-radius:999px; overflow:hidden;}
    .progress > div{height:100%; width:0%; background:linear-gradient(90deg,#fb923c,#f97316);}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <div style="font-size:30px; font-weight:900;">紓壓遊戲：打爆臭晡晡（虛擬）</div>
        <div class="muted">更新時間：{{ updated_at }}　·　<a href="/dash">回儀表板</a>　·　<a href="/dash/repair">修復中心</a>　·　<a href="/dash/tasks">任務牆</a></div>
      </div>
      <div class="muted">{{ me_name }}（你） · {{ other_name }}（對手）</div>
    </div>

    {% if not allow_play %}
      <div class="gate" style="margin-top:16px;">
        目前無法確認你的 couple 身分，暫時不能進入遊戲。你可以先回儀表板看其他功能。
      </div>
    {% else %}
      <div class="grid">
        <div class="card">
          <div class="arena" id="arena" title="點這裡也能攻擊">
            <div class="hitflash" id="hitFlash"></div>
            <div class="vignette"></div>
            <div class="boss-panel">
              <div class="boss-left">
                <div><span class="pill pill-boss" id="bossName">Boss</span> <span class="pill pill-skill" id="bossSkill">技能</span></div>
                <div class="muted" style="font-size:12px; margin-top:4px;" id="bossDesc">每日 Boss 描述</div>
              </div>
            </div>
            <div class="dummy stage0" id="dummy">
              <div class="head">
                <div class="eye l"></div>
                <div class="eye r"></div>
                <div class="mouth"></div>
                <div class="wound bruise b1"></div>
                <div class="wound bruise b2"></div>
                <div class="wound bump"></div>
                <div class="wound bandage"></div>
                <div class="wound sweat"></div>
              </div>
              <div class="body"></div>
              <div class="name" id="dummyName">{{ other_name }}</div>
              <div class="state-label" id="stateLabel">狀態：笑嘻嘻</div>
            </div>
            <div class="fx" id="fx">BAM!</div>
          </div>
          <div class="hp-wrap">
            <div class="hp-row">
              <div class="muted" style="font-size:13px;">HP: <span id="hpText">100 / 100</span></div>
              <div class="muted" style="font-size:13px;">回合 <span id="roundText">1</span></div>
            </div>
            <div class="hp-bg"><div class="hp-bar" id="hpBar"></div></div>
          </div>
          <div class="ctl">
            <button class="btn btn-hit" id="hitBtn">重擊！</button>
            <button class="btn btn-soft" id="healBtn">喝奶茶冷靜一下</button>
            <button class="btn btn-reset" id="resetBtn">重開一局</button>
          </div>
        </div>

        <div class="card">
          <div style="font-size:22px; font-weight:900;">工具箱</div>
          <div class="tools" id="tools"></div>
          <div class="stats" style="margin-top:12px;">
            <div class="stat"><div class="muted" style="font-size:12px;">本週場次</div><div id="weekPlays" style="font-size:26px; font-weight:900;">{{ week_stats.plays }}</div></div>
            <div class="stat"><div class="muted" style="font-size:12px;">本週 KO</div><div id="weekKo" style="font-size:26px; font-weight:900;">{{ week_stats.sum_ko }}</div></div>
            <div class="stat"><div class="muted" style="font-size:12px;">本週最高連擊</div><div id="weekMaxCombo" style="font-size:26px; font-weight:900;">{{ week_stats.max_combo }}</div></div>
          </div>
          <div class="stats">
            <div class="stat"><div class="muted" style="font-size:12px;">總傷害</div><div id="totalDmg" style="font-size:26px; font-weight:900;">0</div></div>
            <div class="stat"><div class="muted" style="font-size:12px;">連擊</div><div id="combo" style="font-size:26px; font-weight:900;">x1</div></div>
            <div class="stat"><div class="muted" style="font-size:12px;">KO 次數</div><div id="koCount" style="font-size:26px; font-weight:900;">0</div></div>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">效果設定</div>
            <label style="display:block; margin-top:8px;"><input id="soundToggle" type="checkbox" checked /> 音效</label>
            <label style="display:block; margin-top:4px;"><input id="vibeToggle" type="checkbox" checked /> 震動（手機）</label>
            <label style="display:block; margin-top:8px; font-size:12px;">音效包
              <select class="select" id="soundPack" style="margin-top:4px;">
                <option value="arcade">街機包</option>
                <option value="comic">漫畫包</option>
                <option value="soft">柔和包</option>
              </select>
            </label>
            <label style="display:block; margin-top:8px; font-size:12px;">震動包
              <select class="select" id="vibePack" style="margin-top:4px;">
                <option value="normal">標準</option>
                <option value="heavy">重擊</option>
                <option value="light">輕量</option>
              </select>
            </label>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">KO 任務獎勵（導向修復）</div>
            <div id="rewardList" style="margin-top:8px; font-size:13px;"></div>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">6) 對方視角卡</div>
            <div id="perspectiveCard" style="margin-top:8px; font-size:13px;">點按鈕抽一張卡</div>
            <button class="btn-mini" id="perspectiveBtn" style="margin-top:8px;">抽卡</button>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">7) 道具收集（Skin）</div>
            <div id="skinList" style="margin-top:8px; font-size:13px;"></div>
            <div style="margin-top:8px;">
              <select class="select" id="skinSelect"></select>
            </div>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">8) 每週紓壓報告</div>
            <div id="weeklyReport" style="margin-top:8px; font-size:13px;"></div>
          </div>
          <div class="stat" style="margin-top:10px;">
            <div class="muted" style="font-size:12px;">9) 雙人和解挑戰（本週）</div>
            <div id="repairChallenge" style="margin-top:8px; font-size:13px;"></div>
          </div>
          <div class="note">
            說明：純虛擬紓壓遊戲，不鼓勵現實暴力。<br/>
            你現在看到的是「受傷分段」版本：黑眼圈、腫包、OK 繃都會隨血量出現。
          </div>

          <div class="section-title">3) 情緒轉譯</div>
          <div class="muted" style="font-size:12px;">把原始怒氣轉成可以說出口的請求句。</div>
          <textarea class="textarea" id="rawEmotionInput" placeholder="例如：你每次都不回我，我真的超火。"></textarea>
          <button class="btn-mini" id="translateBtn" style="margin-top:8px;">轉譯成需求</button>
          <div class="stat" id="translateOut" style="margin-top:8px; font-size:13px;">尚未轉譯</div>

          <div class="section-title">4) 語氣練習場</div>
          <div class="muted" style="font-size:12px;">同一句話三種語氣，練習修復版。</div>
          <input class="input" id="toneInput" placeholder="輸入一句你想說的話" />
          <button class="btn-mini" id="toneBtn" style="margin-top:8px;">生成三種語氣</button>
          <div class="stat" id="toneOut" style="margin-top:8px; font-size:13px;">尚未生成</div>

          <div class="section-title">5) 冷卻挑戰（30 秒）</div>
          <div class="cool-card">
            <div class="muted" style="font-size:12px;">跟著節奏呼吸 30 秒，完成後解鎖修復語句。</div>
            <div id="coolHint" style="margin-top:6px; font-weight:800;">尚未開始</div>
            <div class="progress" style="margin-top:8px;"><div id="coolProgress"></div></div>
            <button class="btn-mini" id="coolStartBtn" style="margin-top:8px;">開始冷卻挑戰</button>
            <div class="stat" id="coolOut" style="margin-top:8px; font-size:13px;">未解鎖</div>
          </div>
        </div>
      </div>
    {% endif %}
  </div>

  {% if allow_play %}
  <script>
    (() => {
      const STORAGE_KEY = "dash_stress_game_v2";
      const rewardDefs = {{ game_rewards_json|safe }};
      const weeklyReportSeed = {{ weekly_report_json|safe }};
      const repairChallengeSeed = {{ repair_challenge_json|safe }};
      const perspectiveCards = {{ perspective_cards_json|safe }};
      const skins = [
        { id: "classic", name: "經典藍", needKo: 0, body: "#76a9ff" },
        { id: "mint", name: "薄荷綠", needKo: 2, body: "#5fd0b7" },
        { id: "sunset", name: "夕陽橘", needKo: 4, body: "#ff9b6b" },
        { id: "neon", name: "霓虹粉", needKo: 7, body: "#f472b6" },
        { id: "boss", name: "魔王黑", needKo: 10, body: "#64748b" }
      ];
      const bosses = [
        { id: "read_ghost", name: "已讀不回魔王", skill: "冷場護盾", desc: "前 3 秒攻擊減傷 25%", armor: 0.25, rageAt: 0.35 },
        { id: "schedule_chaos", name: "臨改行程魔王", skill: "混亂步伐", desc: "每第 4 下傷害波動", armor: 0.10, rageAt: 0.4 },
        { id: "tone_sting", name: "口氣刺刺魔王", skill: "反彈情緒", desc: "高連擊時更容易觸發爆擊", armor: 0.12, rageAt: 0.28 },
        { id: "joke_over", name: "玩笑過頭魔王", skill: "厚臉皮", desc: "血量越低越耐打", armor: 0.08, rageAt: 0.22 }
      ];
      const tools = [
        { id: "pillow", name: "軟枕頭", min: 6, max: 12, fx: "啪！", shake: 1 },
        { id: "slipper", name: "拖鞋", min: 10, max: 18, fx: "咻啪！", shake: 1.25 },
        { id: "bubble_hammer", name: "泡泡槌", min: 14, max: 24, fx: "BAM!", shake: 1.45 },
        { id: "mega_keyboard", name: "巨型鍵盤", min: 18, max: 30, fx: "轟！", shake: 1.7 },
        { id: "laser_cat", name: "雷射貓掌", min: 22, max: 36, fx: "喵砰！", shake: 2.0 }
      ];
      const stageNames = ["笑嘻嘻", "冒冷汗", "黑眼圈", "鼻青臉腫", "懷疑人生"];

      const hpBar = document.getElementById("hpBar");
      const hpText = document.getElementById("hpText");
      const roundText = document.getElementById("roundText");
      const totalDmgEl = document.getElementById("totalDmg");
      const comboEl = document.getElementById("combo");
      const koCountEl = document.getElementById("koCount");
      const weekPlaysEl = document.getElementById("weekPlays");
      const weekKoEl = document.getElementById("weekKo");
      const weekMaxComboEl = document.getElementById("weekMaxCombo");
      const hitBtn = document.getElementById("hitBtn");
      const healBtn = document.getElementById("healBtn");
      const resetBtn = document.getElementById("resetBtn");
      const soundToggle = document.getElementById("soundToggle");
      const vibeToggle = document.getElementById("vibeToggle");
      const soundPackEl = document.getElementById("soundPack");
      const vibePackEl = document.getElementById("vibePack");
      const rewardList = document.getElementById("rewardList");
      const perspectiveCard = document.getElementById("perspectiveCard");
      const perspectiveBtn = document.getElementById("perspectiveBtn");
      const skinList = document.getElementById("skinList");
      const skinSelect = document.getElementById("skinSelect");
      const weeklyReport = document.getElementById("weeklyReport");
      const repairChallenge = document.getElementById("repairChallenge");
      const arena = document.getElementById("arena");
      const bossNameEl = document.getElementById("bossName");
      const bossSkillEl = document.getElementById("bossSkill");
      const bossDescEl = document.getElementById("bossDesc");
      const hitFlash = document.getElementById("hitFlash");
      const dummy = document.getElementById("dummy");
      const fx = document.getElementById("fx");
      const stateLabel = document.getElementById("stateLabel");
      const toolsWrap = document.getElementById("tools");
      const rawEmotionInput = document.getElementById("rawEmotionInput");
      const translateBtn = document.getElementById("translateBtn");
      const translateOut = document.getElementById("translateOut");
      const toneInput = document.getElementById("toneInput");
      const toneBtn = document.getElementById("toneBtn");
      const toneOut = document.getElementById("toneOut");
      const coolHint = document.getElementById("coolHint");
      const coolProgress = document.getElementById("coolProgress");
      const coolStartBtn = document.getElementById("coolStartBtn");
      const coolOut = document.getElementById("coolOut");

      let state = {
        round: 1,
        hpMax: 100,
        hp: 100,
        totalDamage: 0,
        combo: 1,
        maxCombo: 1,
        koCount: 0,
        selectedTool: "pillow",
        lastHitTs: 0,
        soundOn: true,
        vibeOn: true,
        toolHits: {},
        activeBossId: "",
        selectedSkin: "classic",
        soundPack: "arcade",
        vibePack: "normal"
      };
      let coolTimer = null;
      let coolEndsAt = 0;

      function pickTool() { return tools.find(t => t.id === state.selectedTool) || tools[0]; }
      function randInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
      function hpRatio() { return state.hpMax <= 0 ? 0 : state.hp / state.hpMax; }

      function stageFromRatio(r) {
        if (r > 0.75) return 0;
        if (r > 0.55) return 1;
        if (r > 0.35) return 2;
        if (r > 0.15) return 3;
        return 4;
      }

      function pickDailyBoss() {
        const d = new Date();
        const key = d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate();
        return bosses[key % bosses.length];
      }

      function getBoss() {
        const b = bosses.find(x => x.id === state.activeBossId);
        return b || bosses[0];
      }

      function renderBoss() {
        const b = getBoss();
        bossNameEl.textContent = "Boss：" + b.name;
        bossSkillEl.textContent = b.skill;
        bossDescEl.textContent = b.desc;
      }

      function renderRewards() {
        rewardList.innerHTML = "";
        for (const r of rewardDefs) {
          const unlocked = state.koCount >= Number(r.ko || 0);
          const row = document.createElement("div");
          row.style.marginBottom = "6px";
          row.innerHTML =
            (unlocked ? "✅ " : "🔒 ") +
            "KO " + r.ko + " · " + r.title + "：" +
            "<span style='color:" + (unlocked ? "#0f766e" : "#64748b") + ";'>" + r.task + "</span>";
          rewardList.appendChild(row);
        }
      }

      function renderPerspectiveCard(text) {
        perspectiveCard.innerHTML = text || "點按鈕抽一張卡";
      }

      function renderSkins() {
        skinSelect.innerHTML = "";
        const unlocked = skins.filter(s => state.koCount >= s.needKo);
        skinList.innerHTML = skins.map(s => {
          const ok = state.koCount >= s.needKo;
          return (ok ? "✅ " : "🔒 ") + s.name + "（KO " + s.needKo + "）";
        }).join("<br/>");

        for (const s of unlocked) {
          const opt = document.createElement("option");
          opt.value = s.id;
          opt.textContent = s.name;
          skinSelect.appendChild(opt);
        }
        if (!unlocked.some(s => s.id === state.selectedSkin)) {
          state.selectedSkin = unlocked.length ? unlocked[0].id : "classic";
        }
        skinSelect.value = state.selectedSkin;
        const cur = skins.find(s => s.id === state.selectedSkin) || skins[0];
        const bodyEl = dummy.querySelector(".body");
        if (bodyEl) bodyEl.style.backgroundColor = cur.body;
      }

      function renderWeeklyReport() {
        weeklyReport.innerHTML =
          "摘要：" + (weeklyReportSeed.summary || "無") + "<br/>" +
          "平均傷害：" + Number(weeklyReportSeed.avg_damage || 0) + "<br/>" +
          "最常用道具：" + (weeklyReportSeed.top_tool || "無") + "<br/>" +
          "最高回合：" + Number(weeklyReportSeed.max_round || 0);
      }

      function renderRepairChallenge() {
        const goal = Number(repairChallengeSeed.goal || 3);
        const p = Number(repairChallengeSeed.progress || 0);
        const done = !!repairChallengeSeed.done;
        repairChallenge.innerHTML =
          (done ? "🏁 已完成！" : "進度中") + "<br/>" +
          "本週已完成修復：" + p + " / " + goal + "<br/>" +
          (done ? "可解鎖雙人徽章。"
                : "目標：本週完成 3 次『有吵但有修復』");
      }

      function beep(freq, duration, type) {
        if (!state.soundOn) return;
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (!Ctx) return;
          const ctx = new Ctx();
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          let f = freq;
          let wave = type || "square";
          if (state.soundPack === "comic") {
            f = Math.floor(freq * 1.2);
            wave = "triangle";
          } else if (state.soundPack === "soft") {
            f = Math.floor(freq * 0.85);
            wave = "sine";
          }
          osc.type = wave;
          osc.frequency.value = f;
          gain.gain.value = 0.0001;
          osc.connect(gain);
          gain.connect(ctx.destination);
          const now = ctx.currentTime;
          gain.gain.exponentialRampToValueAtTime(0.11, now + 0.01);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + duration / 1000.0);
          osc.start(now);
          osc.stop(now + duration / 1000.0 + 0.02);
          setTimeout(() => { try { ctx.close(); } catch (_) {} }, duration + 90);
        } catch (_) {}
      }

      function pulseVibe(ms) {
        if (!state.vibeOn) return;
        try {
          if (!navigator.vibrate) return;
          if (state.vibePack === "heavy") {
            if (Array.isArray(ms)) navigator.vibrate(ms.map(v => Math.round(v * 1.5)));
            else navigator.vibrate(Math.round(Number(ms || 0) * 1.5));
          } else if (state.vibePack === "light") {
            if (Array.isArray(ms)) navigator.vibrate(ms.map(v => Math.max(5, Math.round(v * 0.6))));
            else navigator.vibrate(Math.max(5, Math.round(Number(ms || 0) * 0.6)));
          } else {
            navigator.vibrate(ms);
          }
        } catch (_) {}
      }

      async function saveReport(reason) {
        try {
          const payload = {
            reason: reason || "manual",
            total_damage: state.totalDamage,
            max_combo: state.maxCombo,
            ko_count: state.koCount,
            round_reached: state.round,
            tool_breakdown: state.toolHits || {}
          };
          const resp = await fetch("/dash/game/report", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          if (!resp.ok) return;
          const out = await resp.json();
          if (!out || !out.ok) return;
          if (out.week_stats) {
            weekPlaysEl.textContent = String(out.week_stats.plays || 0);
            weekKoEl.textContent = String(out.week_stats.sum_ko || 0);
            weekMaxComboEl.textContent = String(out.week_stats.max_combo || 0);
          }
          if (out.challenge) {
            repairChallengeSeed.goal = Number(out.challenge.goal || 3);
            repairChallengeSeed.progress = Number(out.challenge.progress || 0);
            repairChallengeSeed.done = !!out.challenge.done;
            renderRepairChallenge();
          }
        } catch (_) {}
      }

      function buildTranslation(raw) {
        const t = (raw || "").trim();
        if (!t) return "請先輸入你原本想說的話。";
        const feeling = "我現在感到委屈 / 生氣，心裡有壓力。";
        const need = "我需要先被理解，再一起討論怎麼改善。";
        const request = "你可以先回我一句『我有在聽』，然後我們 10 分鐘後再聊嗎？";
        return "感受：" + feeling + "<br/>需求：" + need + "<br/>請求：" + request + "<br/><span class='muted'>原句摘要：" + t.slice(0, 40) + (t.length > 40 ? "…" : "") + "</span>";
      }

      function buildToneVariants(raw) {
        const t = (raw || "").trim();
        if (!t) return "請先輸入一句話。";
        const sharp = "刺傷版：你這樣真的很誇張，我受夠了。";
        const neutral = "中性版：這件事讓我不太舒服，我想確認一下。";
        const repair = "修復版：剛剛那段我有被刺到，可以先聽我 1 分鐘嗎？我希望我們一起把它講好。";
        return sharp + "<br/>" + neutral + "<br/><strong>" + repair + "</strong>";
      }

      function startCooldownChallenge() {
        if (coolTimer) return;
        const durationMs = 30000;
        coolEndsAt = Date.now() + durationMs;
        coolHint.textContent = "吸氣 4 秒 / 吐氣 4 秒，跟著節奏。";
        coolOut.innerHTML = "進行中…";
        coolProgress.style.width = "0%";
        let tick = 0;
        coolTimer = setInterval(() => {
          const left = Math.max(0, coolEndsAt - Date.now());
          const done = Math.min(100, Math.round((1 - left / durationMs) * 100));
          coolProgress.style.width = done + "%";
          tick += 1;
          const phase = Math.floor((durationMs - left) / 4000) % 2;
          coolHint.textContent = phase === 0 ? "吸氣 4 秒" : "吐氣 4 秒";
          if (left <= 0) {
            clearInterval(coolTimer);
            coolTimer = null;
            coolHint.textContent = "完成！現在適合進入修復對話。";
            coolOut.innerHTML = "已解鎖修復語句：<strong>我先不爭輸贏，我想把我們修好。</strong><br/><a href='/dash/repair'>前往修復中心</a>";
            beep(260, 120, "sine");
            pulseVibe([25, 20, 30]);
          }
        }, 200);
      }

      function renderTools() {
        toolsWrap.innerHTML = "";
        for (const t of tools) {
          const btn = document.createElement("button");
          btn.className = "tool" + (t.id === state.selectedTool ? " active" : "");
          btn.innerHTML = "<strong>" + t.name + "</strong><div class='muted' style='font-size:12px;'>傷害 " + t.min + " ~ " + t.max + "</div>";
          btn.onclick = () => { state.selectedTool = t.id; renderTools(); save(); };
          toolsWrap.appendChild(btn);
        }
      }

      function render() {
        const ratio = hpRatio();
        const stage = stageFromRatio(ratio);
        hpText.textContent = state.hp + " / " + state.hpMax;
        roundText.textContent = state.round;
        hpBar.style.width = Math.max(0, ratio * 100) + "%";
        totalDmgEl.textContent = state.totalDamage;
        comboEl.textContent = "x" + state.combo;
        koCountEl.textContent = state.koCount;
        stateLabel.textContent = "狀態：" + stageNames[stage];
        dummy.className = "dummy stage" + stage + (dummy.classList.contains("downed") ? " downed" : "");
        renderBoss();
        renderRewards();
        renderSkins();
        renderWeeklyReport();
        renderRepairChallenge();

        if (ratio > 0.6) hpBar.style.background = "linear-gradient(90deg, #22c55e, #86efac)";
        else if (ratio > 0.3) hpBar.style.background = "linear-gradient(90deg, #f59e0b, #fcd34d)";
        else hpBar.style.background = "linear-gradient(90deg, #ef4444, #fb7185)";
      }

      function flash(strength) {
        hitFlash.style.opacity = String(Math.min(0.52, 0.14 + strength * 0.09));
        setTimeout(() => { hitFlash.style.opacity = "0"; }, 80);
      }

      function showFx(text, strong) {
        fx.textContent = text;
        fx.style.opacity = "1";
        fx.style.transform = "translate(-50%,-50%) scale(" + (strong ? "1.15" : "1") + ")";
        setTimeout(() => {
          fx.style.opacity = "0";
          fx.style.transform = "translate(-50%,-50%) scale(1)";
        }, 190);
      }

      function floatDamage(dmg, crit) {
        const span = document.createElement("div");
        span.className = "float";
        span.textContent = (crit ? "爆擊 -" : "-") + dmg;
        span.style.left = (38 + Math.random() * 24) + "%";
        span.style.top = (28 + Math.random() * 22) + "%";
        span.style.color = crit ? "#b91c1c" : "#be123c";
        span.style.fontSize = (crit ? 28 : 22) + "px";
        arena.appendChild(span);
        setTimeout(() => span.remove(), 760);
      }

      function shake(power) {
        const px = Math.round(8 * power);
        dummy.animate([
          { transform: "translate(-50%,-50%) translateX(0) rotate(0deg)" },
          { transform: "translate(-50%,-50%) translateX(" + (-px) + "px) rotate(-4deg)" },
          { transform: "translate(-50%,-50%) translateX(" + (px) + "px) rotate(4deg)" },
          { transform: "translate(-50%,-50%) translateX(" + (-px * 0.4) + "px) rotate(-2deg)" },
          { transform: "translate(-50%,-50%) translateX(0) rotate(0deg)" }
        ], { duration: Math.round(130 + power * 40), iterations: 1 });
      }

      function ko() {
        state.koCount += 1;
        showFx("K.O!", true);
        beep(160, 180, "sawtooth");
        pulseVibe([70, 35, 90]);
        dummy.classList.add("downed");
        saveReport("ko");
        setTimeout(() => {
          state.round += 1;
          state.hpMax = 100 + (state.round - 1) * 18;
          state.hp = state.hpMax;
          state.combo = 1;
          state.activeBossId = pickDailyBoss().id;
          dummy.classList.remove("downed");
          render();
          save();
        }, 360);
      }

      function hit() {
        const now = Date.now();
        const prevHitTs = state.lastHitTs || 0;
        if (now - prevHitTs <= 1300) state.combo = Math.min(15, state.combo + 1);
        else state.combo = 1;
        if (state.combo > state.maxCombo) state.maxCombo = state.combo;
        state.lastHitTs = now;

        const tool = pickTool();
        const boss = getBoss();
        let critChance = 0.2;
        if (boss.id === "tone_sting" && state.combo >= 6) critChance += 0.12;
        const crit = Math.random() < critChance;
        const base = randInt(tool.min, tool.max);
        const comboMul = 1 + (state.combo - 1) * 0.09;
        const critMul = crit ? 1.6 : 1.0;
        let dmg = Math.max(1, Math.floor(base * comboMul * critMul));

        // 2) combo special moves
        let comboFx = "";
        if (state.combo === 5) {
          dmg += 10;
          comboFx = "連擊技：直球重擊";
        } else if (state.combo === 10) {
          dmg += 18;
          comboFx = "連擊技：颶風連環";
        } else if (state.combo >= 15) {
          dmg += 28;
          comboFx = "連擊技：終結爆發";
        }

        // 1) boss mode resist/skill
        if (boss.id === "read_ghost" && now - prevHitTs < 3000) {
          dmg = Math.max(1, Math.floor(dmg * (1 - boss.armor)));
        } else if (boss.id === "schedule_chaos" && (state.combo % 4 === 0)) {
          dmg = Math.max(1, Math.floor(dmg * 0.7));
        } else if (boss.id === "joke_over") {
          const r = hpRatio();
          if (r <= boss.rageAt) dmg = Math.max(1, Math.floor(dmg * 0.75));
        } else {
          dmg = Math.max(1, Math.floor(dmg * (1 - boss.armor * 0.4)));
        }
        state.toolHits[tool.id] = Number(state.toolHits[tool.id] || 0) + 1;

        state.hp = Math.max(0, state.hp - dmg);
        state.totalDamage += dmg;
        showFx(comboFx || (crit ? "爆擊!" : tool.fx), crit || !!comboFx);
        beep(crit ? 520 : 360, crit ? 130 : 90, crit ? "square" : "triangle");
        pulseVibe(crit ? [24, 16, 24] : 18);
        floatDamage(dmg, crit);
        flash(tool.shake + (crit ? 0.4 : 0));
        shake(tool.shake + (crit ? 0.4 : 0));
        render();
        save();

        if (state.hp <= 0) ko();
      }

      function heal() {
        state.combo = 1;
        const healPts = 10;
        state.hp = Math.min(state.hpMax, state.hp + healPts);
        showFx("呼...", false);
        beep(240, 100, "sine");
        pulseVibe(10);
        render();
        save();
      }

      function reset() {
        if (state.totalDamage > 0 || state.koCount > 0) {
          saveReport("reset");
        }
        state = {
          round: 1, hpMax: 100, hp: 100, totalDamage: 0, combo: 1, maxCombo: 1, koCount: 0,
          selectedTool: "pillow", lastHitTs: 0, soundOn: true, vibeOn: true, toolHits: {}, activeBossId: pickDailyBoss().id,
          selectedSkin: "classic", soundPack: "arcade", vibePack: "normal"
        };
        renderTools();
        render();
        save();
      }

      function save() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) {}
      }

      function load() {
        try {
          const raw = localStorage.getItem(STORAGE_KEY);
          if (!raw) return;
          const x = JSON.parse(raw);
          state = Object.assign(state, x || {});
          if (!tools.some(t => t.id === state.selectedTool)) state.selectedTool = "pillow";
          state.maxCombo = Math.max(1, Number(state.maxCombo || 1));
          if (typeof state.soundOn !== "boolean") state.soundOn = true;
          if (typeof state.vibeOn !== "boolean") state.vibeOn = true;
          if (!state.toolHits || typeof state.toolHits !== "object") state.toolHits = {};
          if (!state.activeBossId || !bosses.some(b => b.id === state.activeBossId)) {
            state.activeBossId = pickDailyBoss().id;
          }
          if (!state.selectedSkin) state.selectedSkin = "classic";
          if (!state.soundPack) state.soundPack = "arcade";
          if (!state.vibePack) state.vibePack = "normal";
        } catch (_) {}
      }

      hitBtn.addEventListener("click", hit);
      healBtn.addEventListener("click", heal);
      resetBtn.addEventListener("click", reset);
      translateBtn.addEventListener("click", () => {
        translateOut.innerHTML = buildTranslation(rawEmotionInput.value);
      });
      toneBtn.addEventListener("click", () => {
        toneOut.innerHTML = buildToneVariants(toneInput.value);
      });
      coolStartBtn.addEventListener("click", startCooldownChallenge);
      perspectiveBtn.addEventListener("click", () => {
        const idx = Math.floor(Math.random() * perspectiveCards.length);
        renderPerspectiveCard(perspectiveCards[idx] || "先安撫，再溝通。");
      });
      skinSelect.addEventListener("change", () => {
        state.selectedSkin = skinSelect.value || "classic";
        renderSkins();
        save();
      });
      soundToggle.addEventListener("change", () => {
        state.soundOn = !!soundToggle.checked;
        save();
      });
      vibeToggle.addEventListener("change", () => {
        state.vibeOn = !!vibeToggle.checked;
        save();
      });
      soundPackEl.addEventListener("change", () => {
        state.soundPack = soundPackEl.value || "arcade";
        save();
      });
      vibePackEl.addEventListener("change", () => {
        state.vibePack = vibePackEl.value || "normal";
        save();
      });
      arena.addEventListener("click", (e) => {
        if (e.target && e.target.id !== "healBtn" && e.target.id !== "resetBtn") hit();
      });
      document.addEventListener("keydown", (e) => {
        if (e.code === "Space") { e.preventDefault(); hit(); }
      });

      load();
      if (!state.activeBossId) state.activeBossId = pickDailyBoss().id;
      soundToggle.checked = !!state.soundOn;
      vibeToggle.checked = !!state.vibeOn;
      soundPackEl.value = state.soundPack || "arcade";
      vibePackEl.value = state.vibePack || "normal";
      renderTools();
      render();
      renderPerspectiveCard("抽一張卡，看看對方可能在想什麼。");
    })();
  </script>
  {% endif %}
</body>
</html>
"""

# ===== Dashboard (private, LINE-issued magic link) =====
# 目的：把「LINE 可以問到的資料」用更漂亮的方式呈現在網站上（避免公開洩漏，所以必須登入）。
#
# 使用方式：
# - 在 LINE 輸入：儀表板（或 help 內點「開啟儀表板」）
# - Bot 會回一個「一次性 / 短效」登入連結：/dash/login?t=...
# - 網站用該 token 換取 session cookie，並 redirect 到 /dash（URL 不會留下 token）
#
# Zeabur 建議環境變數：
#   PUBLIC_BASE_URL=https://<your-domain>
#   FLASK_SECRET_KEY=一段夠長的隨機字串（或 SECRET_KEY）
#
DASH_MAGIC_TOKEN_TTL_SECONDS = int(os.getenv("DASH_MAGIC_TOKEN_TTL_SECONDS", "600"))   # magic link 有效秒數（預設 10 分鐘）
DASH_SESSION_TTL_SECONDS = int(os.getenv("DASH_SESSION_TTL_SECONDS", "43200"))        # session 有效秒數（預設 12 小時）

_LAST_TASK_EXPIRE_SWEEP_TS = 0.0  # throttle expire_open_photo_tasks on /dash/tasks

REPAIR_EMOTION_OPTIONS = [
    ("hurt", "委屈"),
    ("angry", "生氣"),
    ("disappointed", "失望"),
    ("anxious", "焦慮"),
]

REPAIR_NEED_OPTIONS = [
    ("companionship", "先陪我，不要講道理"),
    ("comfort", "給我抱抱/安慰"),
    ("solve", "幫我一起想解法"),
    ("cooldown", "我想自己冷靜 30 分鐘"),
]

REPAIR_TRIGGER_OPTIONS = [
    ("tone", "口氣"),
    ("no_reply", "已讀不回"),
    ("schedule_change", "臨時改行程"),
    ("joke_too_far", "開玩笑過頭"),
]

GAME_REWARD_TASKS = [
    {"ko": 1, "title": "先安撫一句", "task": "傳一句：我先抱抱你，我在。"},
    {"ko": 3, "title": "重述在意點", "task": "用 1 句話重述對方在意的點，不反駁。"},
    {"ko": 5, "title": "行動承諾", "task": "問：你希望我現在怎麼做？並承諾 1 個可執行行動。"},
    {"ko": 8, "title": "和好儀式", "task": "一起到修復中心按『已修復』，結束這回合。"},
]

DASH_LOGIN_REQUIRED_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} - 需要登入</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans TC","Helvetica Neue",Arial; margin:0; background:#0b1020; color:#e6e8ef;}
    .wrap{max-width:820px; margin:0 auto; padding:40px 18px;}
    .card{background:#131a33; border:1px solid rgba(255,255,255,.08); border-radius:18px; padding:22px;}
    h1{margin:0 0 10px 0; font-size:22px;}
    p{margin:8px 0; color:#b8bfd8; line-height:1.6;}
    .btn{display:inline-block; padding:10px 14px; border-radius:12px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:700;}
    .muted{font-size:13px; color:#93a0c7;}
    code{background:rgba(255,255,255,.08); padding:2px 6px; border-radius:8px;}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>🔒 需要登入才能看私人資料</h1>
      <p>請回到 LINE 或 Discord，輸入 <code>儀表板</code>，或打 <code>help</code> 點「開啟儀表板」取得一次性登入連結。</p>
      <p class="muted">（這樣網址不需要手打 token，也不會長期暴露在瀏覽器紀錄裡）</p>
      <p style="margin-top:16px;">
        <a class="btn" href="{{ docs_url }}">回到使用說明</a>
      </p>
    </div>
  </div>
</body>
</html>"""

DASH_LOGIN_FAIL_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} - 登入失敗</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans TC","Helvetica Neue",Arial; margin:0; background:#0b1020; color:#e6e8ef;}
    .wrap{max-width:820px; margin:0 auto; padding:40px 18px;}
    .card{background:#131a33; border:1px solid rgba(255,255,255,.08); border-radius:18px; padding:22px;}
    h1{margin:0 0 10px 0; font-size:22px;}
    p{margin:8px 0; color:#b8bfd8; line-height:1.6;}
    .btn{display:inline-block; padding:10px 14px; border-radius:12px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:700;}
    code{background:rgba(255,255,255,.08); padding:2px 6px; border-radius:8px;}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>⚠️ 登入連結無效或已過期</h1>
      <p>請回到 LINE 或 Discord，輸入 <code>儀表板</code> 重新取得一次性登入連結。</p>
      <p style="margin-top:16px;">
        <a class="btn" href="{{ docs_url }}">回到使用說明</a>
      </p>
    </div>
  </div>
</body>
</html>"""

DASH_LOGIN_FORBIDDEN_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} - 未授權</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans TC","Helvetica Neue",Arial; margin:0; background:#0b1020; color:#e6e8ef;}
    .wrap{max-width:820px; margin:0 auto; padding:40px 18px;}
    .card{background:#131a33; border:1px solid rgba(255,255,255,.08); border-radius:18px; padding:22px;}
    h1{margin:0 0 10px 0; font-size:22px;}
    p{margin:8px 0; color:#b8bfd8; line-height:1.6;}
    .btn{display:inline-block; padding:10px 14px; border-radius:12px; text-decoration:none; background:#2f6bff; color:#fff; font-weight:700;}
    code{background:rgba(255,255,255,.08); padding:2px 6px; border-radius:8px;}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>⛔ 未授權</h1>
      <p>這個儀表板只提供給已設定的 couple 成員（在 LINE 先設定 <code>我是臭寶</code> / <code>我是臭晡晡</code>，並加入推播）。</p>
      <p style="margin-top:16px;">
        <a class="btn" href="{{ docs_url }}">回到使用說明</a>
      </p>
    </div>
  </div>
</body>
</html>"""



def _dashboard_session_valid() -> bool:
    try:
        exp = int(session.get("dash_exp") or 0)
    except Exception:
        exp = 0
    uid = (session.get("dash_uid") or "").strip()
    return bool(uid) and int(time.time()) < exp



def _dash_allowed_user_ids() -> set[str]:
    role_ids = _get_active_role_ids()
    allowed: set[str] = set()
    for role in ("girlfriend", "boyfriend"):
        for uid in role_ids.get(role, []) or []:
            uid = (uid or "").strip()
            if uid:
                allowed.add(uid)
    return allowed


def _dash_user_allowed(user_id: str) -> bool:
    user_id = (user_id or "").strip()
    if not user_id:
        return False
    return user_id in _dash_allowed_user_ids()


def _dash_make_login_url(for_user_id: str) -> str:
    base_url = get_public_base_url()
    token = create_dashboard_magic_token(
        db_path=LOVE_DB_PATH,
        user_id=for_user_id,
        ttl_seconds=DASH_MAGIC_TOKEN_TTL_SECONDS,
    )
    return f"{base_url}/dash/login?t={token}"


def _dash_require_page():
    if _dashboard_session_valid():
        return None
    return render_template_string(
        DASH_LOGIN_REQUIRED_TEMPLATE,
        bot_name=BOT_NAME,
        docs_url=f"{get_public_base_url()}/docs",
    )


def _dash_require_api():
    if _dashboard_session_valid():
        return
    abort(401)


def _dash_current_uid() -> str:
    return (session.get("dash_uid") or "").strip()


def _dash_pair_for_uid(base: dict, uid: str) -> tuple[str | None, str, str | None, str]:
    uid = (uid or "").strip()
    gf_id = (base.get("gf_id") or "").strip()
    bf_id = (base.get("bf_id") or "").strip()
    gf_name = base.get("gf_name") or "女方"
    bf_name = base.get("bf_name") or "男方"

    if uid and uid == gf_id:
        return gf_id or None, gf_name, bf_id or None, bf_name
    if uid and uid == bf_id:
        return bf_id or None, bf_name, gf_id or None, gf_name

    # fallback: unknown uid (rare), still return a stable pair
    me_name = _safe_name(uid) or "你"
    if gf_id:
        return uid or None, me_name, gf_id, gf_name
    if bf_id:
        return uid or None, me_name, bf_id, bf_name
    return uid or None, me_name, None, "對方"


def _repair_label_from_options(key: str, options: list[tuple[str, str]], default: str = "未設定") -> str:
    k = (key or "").strip()
    for opt_key, opt_label in options:
        if k == opt_key:
            return opt_label
    return default


def _repair_emotion_label(key: str) -> str:
    return _repair_label_from_options(key, REPAIR_EMOTION_OPTIONS, default="其他")


def _repair_need_label(key: str) -> str:
    return _repair_label_from_options(key, REPAIR_NEED_OPTIONS, default="先被理解")


def _repair_trigger_label(key: str) -> str:
    return _repair_label_from_options(key, REPAIR_TRIGGER_OPTIONS, default=key or "其他")


def _repair_cooldown_minutes_remaining(cooldown_until: str | None) -> int:
    dt = _parse_dt(cooldown_until)
    if not dt:
        return 0
    left = int((dt - _tz_now()).total_seconds() // 60)
    return max(0, left)


def _repair_task_cards(ev: dict) -> list[str]:
    needs = _repair_need_label(ev.get("need_type") or "")
    return [
        "先道歉一句，不反駁。",
        "重述她在意點（1 句，不下判斷）。",
        f"問：你希望我現在怎麼做？（可參考：{needs}）",
    ]


def _repair_badge(streak_days: int) -> str:
    d = int(max(0, streak_days))
    if d >= 30:
        return "穩定維護 30 天"
    if d >= 14:
        return "修復默契 14 天"
    if d >= 7:
        return "冷靜回合 7 天"
    if d >= 3:
        return "先安撫再溝通 3 天"
    return "今天也在練習"


def _game_rewards_for_ko(ko_count: int) -> list[dict]:
    k = max(0, int(ko_count))
    out: list[dict] = []
    for r in GAME_REWARD_TASKS:
        item = dict(r)
        item["unlocked"] = bool(k >= int(r["ko"]))
        out.append(item)
    return out


def _repair_process_cooldown_end_notifications(limit: int = 30) -> int:
    now_iso = _iso_now()
    rows = list_conflict_events_ready_for_cooldown_notify(
        db_path=LOVE_DB_PATH,
        now_iso=now_iso,
        limit=limit,
    )
    sent = 0
    for ev in rows:
        event_id = int(ev.get("id") or 0)
        if event_id <= 0:
            continue
        target_uid = (ev.get("target_user_id") or "").strip()
        if not target_uid:
            mark_conflict_cooldown_notified(db_path=LOVE_DB_PATH, event_id=event_id, notified_at_iso=now_iso)
            continue
        try:
            msg = (
                "⏰ 修復中心提醒：冷卻時間已結束\n"
                f"事件 #{event_id} 現在可以回來處理。\n"
                "建議順序：先安撫，再看任務卡。"
            )
            push_and_log(target_uid, msg, reason="REPAIR_COOLDOWN_END", target_role=None)
            mark_conflict_cooldown_notified(db_path=LOVE_DB_PATH, event_id=event_id, notified_at_iso=now_iso)
            sent += 1
        except Exception as e:
            logger.error("[REPAIR_COOLDOWN_NOTIFY][FAILED] event=%s to=%s err=%s", event_id, target_uid, str(e))
    return sent


def _get_couple_setting(key: str, gf_id: str | None, bf_id: str | None) -> str:
    for uid in (gf_id, bf_id):
        if not uid:
            continue
        v = get_setting(db_path=LOVE_DB_PATH, user_id=uid, key=key)
        if v:
            return v
    return ""


def _safe_name(uid: str | None) -> str:
    if not uid:
        return ""
    try:
        return get_display_name(db_path=LOVE_DB_PATH, user_id=uid) or ""
    except Exception:
        return ""


def _build_pill_history(days: int, gf_id: str | None) -> list[dict]:
    days = max(1, min(30, int(days)))
    if not gf_id:
        return []
    now = _tz_now()
    end_day = now.date()
    start_day = end_day - datetime.timedelta(days=days - 1)

    rows = list_med_pill_rows_between(
        db_path=LOVE_DB_PATH,
        user_id=gf_id,
        start_day=start_day.isoformat(),
        end_day=end_day.isoformat(),
    )
    by_day = {r.get("day"): r for r in rows if r.get("day")}
    out = []
    for i in range(days):
        d = end_day - datetime.timedelta(days=i)
        ds = d.isoformat()
        r = by_day.get(ds) or {}
        taken = bool(r.get("taken_at"))
        out.append(
            {
                "day": ds,
                "taken": taken,
                "taken_time_text": (r.get("taken_time_text") or "") if taken else "",
                "remind_count": int(r.get("remind_count") or 0),
            }
        )
    return out



def _build_dash_base() -> dict:
    """Lightweight base context for /dash/* pages (no pill history, no mood/wish lists)."""
    base_url = get_public_base_url()
    updated_at = _tz_now().strftime("%Y-%m-%d %H:%M:%S")

    role_ids = _get_active_role_ids()
    gf_id = _pick_primary_uid(role_ids.get("girlfriend") or [])
    bf_id = _pick_primary_uid(role_ids.get("boyfriend") or [])

    gf_name = _safe_name(gf_id) or DEFAULT_GIRLFRIEND_NICKNAME
    bf_name = _safe_name(bf_id) or DEFAULT_BOYFRIEND_NICKNAME

    gf_label = _get_str_setting_global("role_label_girlfriend", "Girlfriend")
    bf_label = _get_str_setting_global("role_label_boyfriend", "Boyfriend")

    return {
        "bot_name": BOT_NAME,
        "base_url": base_url,
        "updated_at": updated_at,
        "gf_id": gf_id,
        "bf_id": bf_id,
        "gf_name": gf_name,
        "bf_name": bf_name,
        "gf_label": gf_label,
        "bf_label": bf_label,
    }

def _build_dashboard_data() -> dict:
    base_url = get_public_base_url()
    updated_at = _tz_now().strftime("%Y-%m-%d %H:%M:%S")
    role_ids = _get_active_role_ids()
    gf_id = _pick_primary_uid(role_ids.get("girlfriend") or [])
    bf_id = _pick_primary_uid(role_ids.get("boyfriend") or [])

    gf_name = _safe_name(gf_id) or DEFAULT_GIRLFRIEND_NICKNAME
    bf_name = _safe_name(bf_id) or DEFAULT_BOYFRIEND_NICKNAME

    gf_label = _get_str_setting_global("role_label_girlfriend", "Girlfriend")
    bf_label = _get_str_setting_global("role_label_boyfriend", "Boyfriend")

    # anniversary: prefer stored setting, fallback to env
    anniversary_date = _get_couple_setting("anniversary", gf_id, bf_id) or (os.getenv("RELATION_START_DATE") or "").strip()
    anniversary_days = ""
    warning = ""

    if anniversary_date:
        try:
            start = datetime.date.fromisoformat(anniversary_date)
            today = _tz_now().date()
            anniversary_days = str((today - start).days + 1)
        except Exception:
            warning = "⚠️ 紀念日格式不正確（請在 LINE 重新設定：設定紀念日 YYYY-MM-DD）"
            anniversary_date = ""

    # pill: today's status (girlfriend as source of truth)
    pill_today_text = "尚未設定 girlfriend/boyfriend 角色。先在 LINE 打：我是臭寶 / 我是臭晡晡"
    if gf_id:
        now = _tz_now()
        day = now.date().isoformat()
        row = get_med_pill_row(db_path=LOVE_DB_PATH, user_id=gf_id, day=day) or {}
        if row.get("taken_at"):
            pill_today_text = f"{gf_name} 今天已回報（{row.get('taken_time_text') or '已記錄'}）"
        else:
            pill_today_text = f"{gf_name} 今天尚未回報（已提醒 {int(row.get('remind_count') or 0)} 次）"

    pills_days = 30
    pills = _build_pill_history(pills_days, gf_id)


    # duolingo: enabled + done today
    duo_enabled = duo_remind_enabled()
    now = _tz_now()
    duo_done_today_flag = duo_done_today(now)
    duo_every = duo_remind_every_minutes()
    duo_start = duo_remind_start_hour()
    duo_end = duo_remind_end_hour()
    duo_window = f"{duo_start:02d}:00～{duo_end:02d}:59"

    # moods (combine gf+bf)
    moods_out = []
    try:
        if gf_id:
            for m in list_moods(db_path=LOVE_DB_PATH, user_id=gf_id, limit=5):
                moods_out.append({"id": m["id"], "text": m["text"], "created_at": m["created_at"], "who": gf_name})
        if bf_id:
            for m in list_moods(db_path=LOVE_DB_PATH, user_id=bf_id, limit=5):
                moods_out.append({"id": m["id"], "text": m["text"], "created_at": m["created_at"], "who": bf_name})
        # sort by created_at (string iso)
        moods_out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        moods_out = moods_out[:8]
    except Exception:
        moods_out = []

    # wishes (combine gf+bf)
    wishes_limit = 12
    wishes_out = []
    try:
        if gf_id:
            for w in list_wishes(db_path=LOVE_DB_PATH, user_id=gf_id, limit=wishes_limit):
                wishes_out.append({"id": w["id"], "text": w["text"], "created_at": w["created_at"], "who": gf_name})
        if bf_id:
            for w in list_wishes(db_path=LOVE_DB_PATH, user_id=bf_id, limit=wishes_limit):
                wishes_out.append({"id": w["id"], "text": w["text"], "created_at": w["created_at"], "who": bf_name})
        wishes_out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        wishes_out = wishes_out[:wishes_limit]
    except Exception:
        wishes_out = []

    return {
        "bot_name": BOT_NAME,
        "base_url": base_url,
        "updated_at": updated_at,
        "gf_name": gf_name,
        "gf_label": gf_label,
        "bf_name": bf_name,
        "bf_label": bf_label,
        "anniversary_date": anniversary_date,
        "anniversary_days": anniversary_days,
        "pill_today_text": pill_today_text,
        "pills_days": pills_days,
        "pills": pills,
        "duo_enabled": duo_enabled,
        "duo_done_today": duo_done_today_flag,
        "duo_every": duo_every,
        "duo_window": duo_window,
        "moods": moods_out,
        "wishes": wishes_out,
        "wishes_limit": wishes_limit,
        "warning": warning,
    }



@app.route("/dash/login")
def dash_login():
    """Exchange a one-time magic token for a session cookie, then redirect to /dash."""
    token = (request.args.get("t") or "").strip()
    uid = consume_dashboard_magic_token(db_path=LOVE_DB_PATH, token=token)
    if not uid:
        return render_template_string(
            DASH_LOGIN_FAIL_TEMPLATE,
            bot_name=BOT_NAME,
            docs_url=f"{get_public_base_url()}/docs",
        ), 401

    # hard gate: only active couple members can view the dashboard
    if not _dash_user_allowed(uid):
        return render_template_string(
            DASH_LOGIN_FORBIDDEN_TEMPLATE,
            bot_name=BOT_NAME,
            docs_url=f"{get_public_base_url()}/docs",
        ), 403

    session["dash_uid"] = uid
    session["dash_exp"] = int(time.time()) + DASH_SESSION_TTL_SECONDS
    session["dash_at"] = int(time.time())
    return redirect("/dash")


@app.route("/dash/logout")
def dash_logout():
    session.pop("dash_uid", None)
    session.pop("dash_exp", None)
    session.pop("dash_at", None)
    return redirect("/dash")

@app.route("/dash/settings", methods=["GET", "POST"])
def dash_settings():
    resp = _dash_require_page()
    if resp is not None:
        return resp

    status = ""
    error = ""

    if request.method == "POST":
        try:
            def _set(key: str, val: str):
                set_setting(db_path=LOVE_DB_PATH, user_id=SETTINGS_GLOBAL_USER_ID, key=key, value=val)

            # weather
            _set("weather_remind_enabled", "1" if request.form.get("weather_remind_enabled") else "0")
            _set("weather_remind_times", (request.form.get("weather_remind_times") or "").strip())
            for k in ("temp_low_threshold", "temp_high_threshold", "app_temp_low_threshold", "app_temp_high_threshold", "uv_high_threshold", "humidity_range_threshold"):
                _set(k, (request.form.get(k) or "").strip())

            # duolingo
            _set("duo_remind_enabled", "1" if request.form.get("duo_remind_enabled") else "0")
            for k in ("duo_remind_every_minutes", "duo_remind_start_hour", "duo_remind_end_hour"):
                _set(k, (request.form.get(k) or "").strip())

            # medication
            _set("med_pill_enabled", "1" if request.form.get("med_pill_enabled") else "0")
            for k in ("med_pill_remind_time", "med_pill_nudge_minutes", "med_pill_max_remind_count"):
                _set(k, (request.form.get(k) or "").strip())
            # allow empty to disable quiet hours
            _set("med_pill_quiet_hours", (request.form.get("med_pill_quiet_hours") or ""))

            # role labels (dashboard only)
            _set("role_label_girlfriend", (request.form.get("role_label_girlfriend") or "").strip())
            _set("role_label_boyfriend", (request.form.get("role_label_boyfriend") or "").strip())

            refresh_scheduler_jobs()
            status = "已儲存並套用。"
        except Exception as e:
            error = f"儲存失敗：{e}"

    base = _build_dash_base()
    base_url = base["base_url"]
    updated_at = base["updated_at"]
    gf_id = base.get("gf_id")
    bf_id = base.get("bf_id")
    gf_name = base["gf_name"]
    bf_name = base["bf_name"]
    gf_label = base["gf_label"]
    bf_label = base["bf_label"]

    # weather view values
    v_times = _get_setting_global("weather_remind_times")
    weather_times = (v_times if (v_times is not None and v_times.strip() != "") else "08:30,12:30,17:30")
    data = {
        "bot_name": BOT_NAME,
        "base_url": base_url,
        "updated_at": updated_at,
        "status": status,
        "error": error,
        "gf_name": gf_name,
        "bf_name": bf_name,
        "gf_label": gf_label,
        "bf_label": bf_label,

        "weather_remind_enabled": weather_remind_enabled(),
        "weather_remind_times": weather_times,
        "temp_low_threshold": f"{_get_threshold_float('temp_low_threshold', TEMP_LOW_THRESHOLD):g}",
        "temp_high_threshold": f"{_get_threshold_float('temp_high_threshold', TEMP_HIGH_THRESHOLD):g}",
        "app_temp_low_threshold": f"{_get_threshold_float('app_temp_low_threshold', APP_TEMP_LOW_THRESHOLD):g}",
        "app_temp_high_threshold": f"{_get_threshold_float('app_temp_high_threshold', APP_TEMP_HIGH_THRESHOLD):g}",
        "uv_high_threshold": f"{_get_threshold_float('uv_high_threshold', UV_HIGH_THRESHOLD):g}",
        "humidity_range_threshold": f"{_get_threshold_float('humidity_range_threshold', HUMIDITY_RANGE_THRESHOLD):g}",

        "duo_remind_enabled": duo_remind_enabled(),
        "duo_remind_every_minutes": duo_remind_every_minutes(),
        "duo_remind_start_hour": duo_remind_start_hour(),
        "duo_remind_end_hour": duo_remind_end_hour(),
        "duo_done_today": duo_done_today(_tz_now()),

        "med_pill_enabled": med_pill_enabled(),
        "med_pill_remind_time": _get_str_setting_global("med_pill_remind_time", MED_PILL_REMIND_TIME),
        "med_pill_nudge_minutes": med_pill_nudge_minutes(),
        "med_pill_max_remind_count": med_pill_max_remind_count(),
        "med_pill_quiet_hours": (lambda v: MED_PILL_QUIET_HOURS if v is None else str(v))(_get_setting_global("med_pill_quiet_hours")),

        "role_label_girlfriend": gf_label,
        "role_label_boyfriend": bf_label,
    }
    return render_template_string(DASH_SETTINGS_TEMPLATE, **data)


@app.route("/dash")
def dash():
    resp = _dash_require_page()
    if resp is not None:
        return resp
    data = _build_dashboard_data()
    return render_template_string(DASH_TEMPLATE, **data)


@app.route("/dash/repair", methods=["GET", "POST"])
def dash_repair():
    resp = _dash_require_page()
    if resp is not None:
        return resp

    base = _build_dash_base()
    uid = _dash_current_uid()
    me_id, me_name, other_id, other_name = _dash_pair_for_uid(base, uid)
    status = ""
    error = ""

    emotion_keys = {k for k, _ in REPAIR_EMOTION_OPTIONS}
    need_keys = {k for k, _ in REPAIR_NEED_OPTIONS}
    trigger_keys = {k for k, _ in REPAIR_TRIGGER_OPTIONS}

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "create":
                if not me_id:
                    raise ValueError("找不到目前登入身份")

                vent_text = (request.form.get("vent_text") or "").strip()
                emotion_type = (request.form.get("emotion_type") or "hurt").strip()
                need_type = (request.form.get("need_type") or "companionship").strip()
                try:
                    intensity = int(request.form.get("intensity") or 3)
                except Exception:
                    intensity = 3
                intensity = max(1, min(5, intensity))
                wants_reply_now = (request.form.get("wants_reply_now") or "1").strip() == "1"
                notify_other = bool(request.form.get("notify_other"))

                if not vent_text:
                    raise ValueError("請先填寫『今天不爽什麼』")
                if emotion_type not in emotion_keys:
                    emotion_type = "hurt"
                if need_type not in need_keys:
                    need_type = "companionship"

                cooldown_until = None
                if intensity >= 4:
                    cool_mins = 20 if intensity == 4 else 40
                    cooldown_until = (_tz_now() + datetime.timedelta(minutes=cool_mins)).isoformat(timespec="seconds")

                event_id = create_conflict_event(
                    db_path=LOVE_DB_PATH,
                    created_by=me_id,
                    target_user_id=other_id,
                    vent_text=vent_text,
                    emotion_type=emotion_type,
                    intensity=intensity,
                    wants_reply_now=wants_reply_now,
                    need_type=need_type,
                    cooldown_until=cooldown_until,
                )
                status = f"已建立修復事件 #{event_id}。"

                # Notify counterpart (best-effort; do not fail form submission on push errors)
                if notify_other:
                    try:
                        other_role = None
                        if other_id and other_id == (base.get("gf_id") or ""):
                            other_role = "girlfriend"
                        elif other_id and other_id == (base.get("bf_id") or ""):
                            other_role = "boyfriend"

                        notify_ids: list[str] = []
                        if other_role:
                            notify_ids = [
                                x for x in _get_role_ids(other_role)
                                if (x or "").strip() and (x or "").strip() != (me_id or "")
                            ]
                        elif other_id and other_id != me_id:
                            notify_ids = [other_id]

                        if notify_ids:
                            cool_hint = "強度較高，請先安撫，暫時不要解釋。" if intensity >= 4 else "可直接到修復中心查看任務卡。"
                            summary = (vent_text[:100] + "…") if len(vent_text) > 100 else vent_text
                            notify_text = (
                                "📩 修復中心有新事件\n"
                                f"來自：{me_name}\n"
                                f"情緒：{_repair_emotion_label(emotion_type)}（{intensity}/5）\n"
                                f"需求：{_repair_need_label(need_type)}\n"
                                f"摘要：{summary}\n"
                                f"{cool_hint}\n"
                                "請到「儀表板 > 修復中心」查看。"
                            )
                            for to_uid in sorted(set(notify_ids)):
                                try:
                                    push_and_log(
                                        to_uid,
                                        notify_text,
                                        reason="REPAIR_NEW_EVENT",
                                        target_role=other_role,
                                    )
                                except Exception as pe:
                                    logger.error("[REPAIR_NOTIFY][FAILED] to=%s err=%s", to_uid, str(pe))
                    except Exception as ne:
                        logger.error("[REPAIR_NOTIFY][EXCEPTION] err=%s", str(ne))

            elif action == "save_triggers":
                event_id = int(request.form.get("event_id") or 0)
                ev = get_conflict_event(db_path=LOVE_DB_PATH, event_id=event_id)
                if not ev:
                    raise ValueError("事件不存在")
                participants = {
                    (ev.get("created_by") or "").strip(),
                    (ev.get("target_user_id") or "").strip(),
                }
                participants.discard("")
                if uid not in participants:
                    raise ValueError("你不是這個事件的參與者")
                chosen = [(x or "").strip() for x in request.form.getlist("trigger_keys")]
                chosen = [x for x in chosen if x in trigger_keys]
                set_conflict_event_triggers(db_path=LOVE_DB_PATH, event_id=event_id, trigger_keys=chosen)
                status = f"已更新事件 #{event_id} 的觸發點。"

            elif action == "confirm":
                event_id = int(request.form.get("event_id") or 0)
                result = confirm_conflict_event(db_path=LOVE_DB_PATH, event_id=event_id, user_id=uid)
                if not result.get("ok"):
                    reason = result.get("reason") or "unknown"
                    reason_text = {
                        "user_required": "找不到登入使用者",
                        "not_found": "事件不存在",
                        "not_participant": "你不是這個事件的參與者",
                    }.get(reason, reason)
                    raise ValueError(reason_text)
                if result.get("closed"):
                    status = f"事件 #{event_id} 已完成雙方修復。"
                    try:
                        for to_uid in (result.get("participants") or []):
                            to_uid = (to_uid or "").strip()
                            if not to_uid:
                                continue
                            push_and_log(
                                to_uid,
                                f"✅ 修復中心事件 #{event_id} 已完成：雙方都按了「已修復」。",
                                reason="REPAIR_CLOSED",
                                target_role=None,
                            )
                    except Exception as ne:
                        logger.error("[REPAIR_NOTIFY][CLOSED][FAILED] event=%s err=%s", event_id, str(ne))
                else:
                    status = f"已送出你對事件 #{event_id} 的修復確認。"
                    try:
                        participants = [(x or "").strip() for x in (result.get("participants") or []) if (x or "").strip()]
                        confirmed = {(x or "").strip() for x in (result.get("confirmed_users") or []) if (x or "").strip()}
                        pending = [p for p in participants if p not in confirmed]
                        for to_uid in pending:
                            push_and_log(
                                to_uid,
                                f"📝 修復中心提醒：對方已按事件 #{event_id} 的「已修復」，輪到你了。",
                                reason="REPAIR_PENDING_CONFIRM",
                                target_role=None,
                            )
                    except Exception as ne:
                        logger.error("[REPAIR_NOTIFY][PENDING][FAILED] event=%s err=%s", event_id, str(ne))
        except Exception as e:
            error = f"操作失敗：{e}"

    # Also scan on page requests so cooldown reminders still work even if scheduler is disabled.
    try:
        _repair_process_cooldown_end_notifications(limit=20)
    except Exception:
        pass

    raw_events = list_conflict_events(db_path=LOVE_DB_PATH, limit=40, user_id=uid or None)
    event_ids = [int(ev.get("id") or 0) for ev in raw_events if int(ev.get("id") or 0) > 0]
    trigger_map = list_conflict_triggers_map(db_path=LOVE_DB_PATH, event_ids=event_ids)
    confirm_map = list_conflict_confirms_map(db_path=LOVE_DB_PATH, event_ids=event_ids)
    events = []
    for ev in raw_events:
        event_id = int(ev.get("id") or 0)
        created_by = (ev.get("created_by") or "").strip()
        target_uid = (ev.get("target_user_id") or "").strip()
        participants = []
        for pid in (created_by, target_uid):
            if pid and pid not in participants:
                participants.append(pid)

        confirmed_users = confirm_map.get(event_id, [])
        trigger_rows = trigger_map.get(event_id, [])
        cooldown_left = _repair_cooldown_minutes_remaining(ev.get("cooldown_until"))
        closed = bool(ev.get("closed_at"))
        in_my_cooldown = bool(cooldown_left > 0 and uid and uid == target_uid)

        events.append(
            {
                "id": event_id,
                "owner_label": "你" if created_by and created_by == uid else "對方",
                "created_at": ev.get("created_at") or "",
                "vent_text": ev.get("vent_text") or "",
                "emotion_label": _repair_emotion_label(ev.get("emotion_type") or ""),
                "intensity": int(ev.get("intensity") or 0),
                "need_label": _repair_need_label(ev.get("need_type") or ""),
                "wants_reply_now": bool(ev.get("wants_reply_now")),
                "cooldown_left": cooldown_left,
                "cooldown_active_for_me": in_my_cooldown,
                "show_task_cards": bool(uid and uid == target_uid and not closed and not in_my_cooldown),
                "task_cards": _repair_task_cards(ev),
                "triggers": trigger_rows,
                "can_edit_triggers": bool(uid in participants and not in_my_cooldown),
                "can_confirm": bool(uid in participants and not in_my_cooldown),
                "i_confirmed": bool(uid in set(confirmed_users)),
                "confirmed_count": len(set(confirmed_users)),
                "participant_count": len(participants),
                "closed": closed,
            }
        )

    trigger_top = []
    for row in list_conflict_trigger_top(db_path=LOVE_DB_PATH, days=7, limit=3):
        trigger_top.append(
            {
                "key": row["trigger_key"],
                "label": _repair_trigger_label(row["trigger_key"]),
                "count": int(row["count"]),
            }
        )

    streak_days = get_no_blowup_streak_days(db_path=LOVE_DB_PATH, intense_threshold=4)

    return render_template_string(
        DASH_REPAIR_TEMPLATE,
        **base,
        status=status,
        error=error,
        me_name=me_name,
        other_name=other_name,
        emotion_options=REPAIR_EMOTION_OPTIONS,
        need_options=REPAIR_NEED_OPTIONS,
        trigger_options=REPAIR_TRIGGER_OPTIONS,
        events=events,
        trigger_top=trigger_top,
        streak_days=streak_days,
        streak_badge=_repair_badge(streak_days),
    )


@app.route("/dash/game")
def dash_game():
    resp = _dash_require_page()
    if resp is not None:
        return resp

    base = _build_dash_base()
    uid = _dash_current_uid()
    _, me_name, _, other_name = _dash_pair_for_uid(base, uid)
    gf_id = (base.get("gf_id") or "").strip()
    bf_id = (base.get("bf_id") or "").strip()
    allow_play = bool(uid and (uid == gf_id or uid == bf_id))
    week_stats = get_game_week_stats(db_path=LOVE_DB_PATH, user_id=uid) if allow_play else {"plays": 0, "sum_ko": 0, "sum_damage": 0, "max_combo": 0}
    rewards = _game_rewards_for_ko(int(week_stats.get("sum_ko") or 0))

    report = {
        "avg_damage": 0,
        "top_tool": "無",
        "max_round": 0,
        "summary": "本週還沒有遊戲資料。",
    }
    repair_week = {"closed_count": 0, "open_count": 0, "high_intensity_count": 0}
    challenge = {"goal": 3, "progress": 0, "done": False}
    if allow_play:
        sessions = list_game_sessions_since(db_path=LOVE_DB_PATH, user_id=uid, days=7, limit=500)
        if sessions:
            tool_counter: dict[str, int] = {}
            max_round = 0
            for s in sessions:
                max_round = max(max_round, int(s.get("round_reached") or 0))
                tb_raw = s.get("tool_breakdown") or ""
                if tb_raw:
                    try:
                        tb = json.loads(tb_raw)
                        if isinstance(tb, dict):
                            for k, v in tb.items():
                                kk = (str(k) or "").strip()
                                if kk:
                                    tool_counter[kk] = tool_counter.get(kk, 0) + int(v or 0)
                    except Exception:
                        pass
            top_tool = "無"
            if tool_counter:
                top_tool = sorted(tool_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            plays = int(week_stats.get("plays") or 0)
            sum_damage = int(week_stats.get("sum_damage") or 0)
            avg_damage = int(round(sum_damage / plays)) if plays > 0 else 0
            report = {
                "avg_damage": avg_damage,
                "top_tool": top_tool,
                "max_round": max_round,
                "summary": f"本週玩了 {plays} 場，總 KO {int(week_stats.get('sum_ko') or 0)} 次。",
            }

        repair_week = get_repair_week_stats(db_path=LOVE_DB_PATH, user_id=uid)
        challenge["progress"] = int(repair_week.get("closed_count") or 0)
        challenge["done"] = challenge["progress"] >= challenge["goal"]

    return render_template_string(
        DASH_GAME_TEMPLATE,
        **base,
        me_name=me_name,
        other_name=other_name,
        allow_play=allow_play,
        week_stats=week_stats,
        game_rewards_json=json.dumps(rewards, ensure_ascii=False),
        weekly_report_json=json.dumps(report, ensure_ascii=False),
        repair_challenge_json=json.dumps(challenge, ensure_ascii=False),
        perspective_cards_json=json.dumps([
            "對方可能在怕：你是不是已經不想聽我說了。",
            "對方可能在想：我不是要贏，我只是想被在乎。",
            "對方可能在卡：語氣比內容更刺痛。",
            "對方可能需要：先被安撫，再談道理。",
            "對方可能擔心：這件事會不會又變成舊帳。"
        ], ensure_ascii=False),
    )


@app.route("/dash/game/report", methods=["POST"])
def dash_game_report():
    _dash_require_api()
    uid = _dash_current_uid()
    base = _build_dash_base()
    gf_id = (base.get("gf_id") or "").strip()
    bf_id = (base.get("bf_id") or "").strip()
    if not (uid and (uid == gf_id or uid == bf_id)):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    try:
        total_damage = int(body.get("total_damage") or 0)
        max_combo = int(body.get("max_combo") or 0)
        ko_count = int(body.get("ko_count") or 0)
        round_reached = int(body.get("round_reached") or 1)
        tool_breakdown = body.get("tool_breakdown") or {}
        if not isinstance(tool_breakdown, dict):
            tool_breakdown = {}

        target_uid = bf_id if uid == gf_id else gf_id
        sid = create_game_session(
            db_path=LOVE_DB_PATH,
            user_id=uid,
            target_user_id=target_uid,
            total_damage=total_damage,
            max_combo=max_combo,
            ko_count=ko_count,
            round_reached=round_reached,
            tool_breakdown=json.dumps(tool_breakdown, ensure_ascii=False),
        )
        week_stats = get_game_week_stats(db_path=LOVE_DB_PATH, user_id=uid)
        rewards = _game_rewards_for_ko(int(ko_count))
        repair_week = get_repair_week_stats(db_path=LOVE_DB_PATH, user_id=uid)
        challenge = {"goal": 3, "progress": int(repair_week.get("closed_count") or 0)}
        challenge["done"] = challenge["progress"] >= challenge["goal"]
        return jsonify({"ok": True, "session_id": sid, "week_stats": week_stats, "rewards": rewards, "challenge": challenge})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/dash/tasks")
def dash_tasks():
    resp = _dash_require_page()
    if resp is not None:
        return resp

    base = _build_dash_base()
    # sweep expired open tasks (idempotent) - throttled to avoid hitting SQLite/NFS every refresh
    global _LAST_TASK_EXPIRE_SWEEP_TS
    now_ts = time.time()
    sweep = 0
    if now_ts - _LAST_TASK_EXPIRE_SWEEP_TS >= 60:
        try:
            sweep = expire_open_photo_tasks(db_path=LOVE_DB_PATH)
        except Exception:
            sweep = 0
        _LAST_TASK_EXPIRE_SWEEP_TS = now_ts

    def _decorate(tasks: list[dict]) -> list[dict]:
        out = []
        for t in tasks:
            assign_role = t.get("assign_role") or ""
            assign_label = base["gf_label"] if assign_role == "girlfriend" else (base["bf_label"] if assign_role == "boyfriend" else assign_role)
            created_by = t.get("created_by") or ""
            created_by_name = _safe_name(created_by) or "system"
            out.append(
                {
                    "id": t.get("id"),
                    "assign_role": assign_role,
                    "assign_label": assign_label,
                    "text": t.get("text") or "",
                    "status": t.get("status") or "",
                    "created_by": created_by,
                    "created_by_name": created_by_name,
                    "created_at": t.get("created_at") or "",
                    "expires_at": t.get("expires_at") or "",
                    "done_at": t.get("done_at") or "",
                }
            )
        return out

    open_tasks = _decorate(list_photo_tasks(db_path=LOVE_DB_PATH, status="open", limit=120))
    expired_tasks = _decorate(list_photo_tasks(db_path=LOVE_DB_PATH, status="expired", limit=120))
    done_tasks = _decorate(list_photo_tasks(db_path=LOVE_DB_PATH, status="done", limit=120))

    return render_template_string(
        DASH_TASKS_TEMPLATE,
        **base,
        open_tasks=open_tasks,
        expired_tasks=expired_tasks,
        done_tasks=done_tasks,
        expired_sweep_count=sweep,
    )



@app.route("/dash/gallery")
def dash_gallery():
    resp = _dash_require_page()
    if resp is not None:
        return resp

    base = _build_dash_base()

    group = (request.args.get("group") or "date").strip().lower()
    if group not in ("date", "task"):
        group = "date"

    try:
        limit = int(request.args.get("limit") or 60)
    except Exception:
        limit = 60
    limit = max(20, min(800, limit))

    try:
        offset = int(request.args.get("offset") or 0)
    except Exception:
        offset = 0
    offset = max(0, min(200000, offset))

    task_id = request.args.get("task_id")
    try:
        task_id_int = int(task_id) if task_id else None
    except Exception:
        task_id_int = None

    # Resolve gf/bf ids once (avoid N+1 DB calls in gallery)
    role_ids = _get_active_role_ids()
    gf_id = _pick_primary_uid(role_ids.get("girlfriend") or [])
    bf_id = _pick_primary_uid(role_ids.get("boyfriend") or [])

    def _who(uid: str | None) -> str:
        if not uid:
            return "unknown"
        if gf_id and uid == gf_id:
            return base["gf_name"] or base["gf_label"]
        if bf_id and uid == bf_id:
            return base["bf_name"] or base["bf_label"]
        # fallback (rare)
        return _safe_name(uid) or "unknown"

    def _time_parts(created_at: str) -> tuple[str, str]:
        # (day, time)
        try:
            dt = _parse_dt(created_at)
            return dt.date().isoformat(), dt.strftime("%H:%M")
        except Exception:
            day = created_at[:10] if created_at else "unknown"
            tm = created_at[11:16] if len(created_at) >= 16 else ""
            return day, tm

    if group == "date":
        media_rows = list_media_records_with_task(db_path=LOVE_DB_PATH, limit=limit, offset=offset, task_id=None)

        items: list[dict] = []
        for r in media_rows:
            ctype = (r.get("content_type") or "").lower()
            if ctype and not ctype.startswith("image/"):
                continue
            mid = r.get("message_id")
            if not mid:
                continue
            created_at = r.get("created_at") or ""
            day, tm = _time_parts(created_at)
            tid = r.get("task_id")
            try:
                tid = int(tid) if tid is not None else None
            except Exception:
                tid = None

            items.append(
                {
                    "message_id": mid,
                    "day": day,
                    "time": tm,
                    "who": _who(r.get("from_user_id")),
                    "thumb": dash_thumb_src(mid),
                    "full": dash_media_src(mid),
                    "task_id": tid,
                }
            )

        by_day: dict[str, list[dict]] = {}
        for it in items:
            by_day.setdefault(it["day"], []).append(it)

        date_groups = sorted(by_day.items(), key=lambda kv: kv[0], reverse=True)

        return render_template_string(
            DASH_GALLERY_TEMPLATE,
            **base,
            group=group,
            limit=limit,
            offset=offset,
            task_id=task_id_int,
            date_groups=date_groups,
            task_groups=[],
        )

    # group == task
    # Recent tasks
    tasks = list_photo_tasks(db_path=LOVE_DB_PATH, status=None, limit=200)

    decorated: dict[int, dict] = {}
    for t in tasks:
        try:
            tid = int(t["id"])
        except Exception:
            continue
        assign_role = t.get("assign_role") or ""
        assign_label = base["gf_label"] if assign_role == "girlfriend" else (base["bf_label"] if assign_role == "boyfriend" else assign_role)
        created_by = t.get("created_by") or ""
        created_by_name = _safe_name(created_by) or "system"
        decorated[tid] = {
            "id": tid,
            "assign_role": assign_role,
            "assign_label": assign_label,
            "text": t.get("text") or "",
            "status": t.get("status") or "",
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": t.get("created_at") or "",
            "expires_at": t.get("expires_at") or "",
            "done_at": t.get("done_at") or "",
        }

    joined = list_task_media_items(db_path=LOVE_DB_PATH, task_id=task_id_int, limit=2000)

    by_task: dict[int, list[dict]] = {}
    for it in joined:
        tid = it.get("task_id")
        mid = it.get("message_id")
        if not tid or not mid:
            continue
        try:
            tid_int = int(tid)
        except Exception:
            continue

        created_at = it.get("created_at") or ""
        try:
            dt = _parse_dt(created_at)
            tm = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            tm = created_at[:16] if created_at else ""

        by_task.setdefault(tid_int, []).append(
            {
                "message_id": mid,
                "time": tm,
                "who": _who(it.get("from_user_id")),
                "thumb": dash_thumb_src(mid),
                "full": dash_media_src(mid),
            }
        )

    task_ids = [task_id_int] if task_id_int is not None else sorted(decorated.keys(), reverse=True)

    task_groups = []
    for tid in task_ids:
        if tid is None or tid not in decorated:
            continue
        task_groups.append({"task": decorated[tid], "items": by_task.get(tid, [])})

    return render_template_string(
        DASH_GALLERY_TEMPLATE,
        **base,
        group=group,
        limit=limit,
        offset=offset,
        task_id=task_id_int,
        date_groups=[],
        task_groups=task_groups,
    )



@app.route("/thumb/<message_id>")
def thumb(message_id: str):
    # auth
    if MEDIA_ACCESS_TOKEN:
        if request.args.get("k", "") != MEDIA_ACCESS_TOKEN:
            abort(403)

    try:
        w = int(request.args.get("w") or 480)
    except Exception:
        w = 480
    w = max(160, min(1024, w))

    rec = get_media_record(db_path=LOVE_DB_PATH, message_id=message_id)
    if not rec:
        abort(404)

    filepath = MEDIA_DIR / rec["filename"]
    if not filepath.exists():
        abort(404)

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_DIR / f"{message_id}_{w}.jpg"

    # regen if missing or source updated
    try:
        if (not thumb_path.exists()) or (thumb_path.stat().st_mtime < filepath.stat().st_mtime):
            img = Image.open(filepath)
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            if img.width > w:
                h = max(1, int(img.height * (w / float(img.width))))
                img = img.resize((w, h), Image.LANCZOS)

            tmp = thumb_path.with_suffix(".tmp")
            img.save(tmp, format="JPEG", quality=78, optimize=True)
            os.replace(tmp, thumb_path)
    except Exception:
        # fallback to original if thumbnail generation fails
        ctype = rec.get("content_type") or mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
        resp = send_file(filepath, mimetype=ctype, as_attachment=False, conditional=True, max_age=31536000)
        resp.cache_control.private = True
        resp.cache_control.max_age = 31536000
        resp.cache_control.immutable = True
        return resp

    resp = send_file(thumb_path, mimetype="image/jpeg", as_attachment=False, conditional=True, max_age=31536000)
    resp.cache_control.private = True
    resp.cache_control.max_age = 31536000
    resp.cache_control.immutable = True
    return resp


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
    resp = send_file(filepath, mimetype=ctype, as_attachment=False, conditional=True, max_age=31536000)
    resp.cache_control.private = True
    resp.cache_control.max_age = 31536000
    resp.cache_control.immutable = True
    return resp


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
