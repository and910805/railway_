from __future__ import annotations

import sqlite3
import time
from typing import Callable
from urllib.parse import quote

from flask import jsonify, redirect, render_template_string, request, session


HB_EVENT_DAYS = [
    {"date": "2026-02-14", "day_index": 1, "amount": 111, "title": "一心一意", "icon": "❤️"},
    {"date": "2026-02-15", "day_index": 2, "amount": 333, "title": "三生三世", "icon": "💕"},
    {"date": "2026-02-16", "day_index": 3, "amount": 111, "title": "一心一意", "icon": "💌"},
    {"date": "2026-02-17", "day_index": 4, "amount": 444, "title": "四季有你", "icon": "🌹"},
    {"date": "2026-02-18", "day_index": 5, "amount": 315, "title": "前五日合成 1314", "icon": "💞"},
    {"date": "2026-02-19", "day_index": 6, "amount": 131, "title": "一生", "icon": "🧩"},
    {"date": "2026-02-20", "day_index": 7, "amount": 147, "title": "一世情", "icon": "✨"},
    {"date": "2026-02-21", "day_index": 8, "amount": 888, "title": "發發發", "icon": "🧧"},
    {"date": "2026-02-22", "day_index": 9, "amount": 520, "title": "我愛你", "icon": "🥰"},
]


HB_HOME_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} - 心動九日紅包</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Noto+Sans+TC:wght@400;500;700;800&display=swap" rel="stylesheet" />
  <style>
    :root{
      --bg-1:#fff8f6;
      --bg-2:#ffeef3;
      --card:#ffffff;
      --line:#f3c8d7;
      --text:#521b2f;
      --muted:#8a5166;
      --accent:#b71c4a;
      --ok-bg:#dcfce7;
      --ok:#0f766e;
      --shadow:0 10px 30px rgba(87, 26, 53, .10);
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      color:var(--text);
      font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
      background:
        radial-gradient(1100px 480px at 8% -8%, #ffdbe8 0%, transparent 62%),
        radial-gradient(900px 420px at 92% -5%, #ffe7ef 0%, transparent 60%),
        linear-gradient(180deg, var(--bg-1) 0%, var(--bg-2) 100%);
      min-height:100vh;
      overflow-x:hidden;
    }
    body::before{
      content:"";
      position:fixed;
      inset:0;
      pointer-events:none;
      opacity:.35;
      background-image:
        radial-gradient(circle at 16px 16px, rgba(183, 28, 74, .07) 0 2px, transparent 2px);
      background-size:42px 42px;
    }
    .wrap{max-width:1020px; margin:0 auto; padding:24px 16px 42px; position:relative; z-index:1;}
    .hero{
      background:rgba(255,255,255,.76);
      border:1px solid var(--line);
      border-radius:24px;
      padding:18px;
      box-shadow:var(--shadow);
      backdrop-filter:blur(4px);
      animation:heroIn .55s ease both;
    }
    .h1{
      margin:0;
      font-family:"Cormorant Garamond","Noto Serif TC",serif;
      font-size:42px;
      line-height:1.02;
      letter-spacing:.6px;
      color:#68223f;
    }
    .meta{margin-top:8px; color:var(--muted); font-size:12px;}
    .grid{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
      gap:14px;
      margin-top:16px;
    }
    .card{
      --d:0;
      background:linear-gradient(180deg,#fff 0%,#fffdfd 100%);
      border:1px solid var(--line);
      border-radius:18px;
      padding:14px;
      box-shadow:var(--shadow);
      position:relative;
      overflow:hidden;
      opacity:0;
      transform:translateY(12px) scale(.985);
      animation:cardIn .55s ease forwards;
      animation-delay:calc(var(--d) * 70ms);
    }
    .top{display:flex; justify-content:space-between; align-items:center; gap:8px;}
    .day{font-weight:800; letter-spacing:.2px;}
    .date{font-size:12px; color:var(--muted);}
    .money{font-size:28px; font-weight:800; margin-top:4px; color:#791d46;}
    .title{margin-top:4px; min-height:20px; opacity:.0;}
    .title.show{opacity:1; color:var(--muted);}
    .pill{
      display:inline-block;
      margin-top:10px;
      padding:4px 10px;
      border-radius:999px;
      font-size:12px;
      font-weight:800;
      background:var(--ok-bg);
      color:var(--ok);
    }
    .draw{
      margin-top:10px;
      border:none;
      border-radius:999px;
      width:42px;
      height:42px;
      background:linear-gradient(90deg, #e11d48, #be123c);
      color:#fff;
      font-size:20px;
      font-weight:800;
      box-shadow:0 8px 16px rgba(190,18,60,.25);
      cursor:pointer;
    }
    .draw:disabled{
      background:#d6dde8;
      color:#6b7280;
      box-shadow:none;
      cursor:not-allowed;
    }
    .draw.loading{
      opacity:.65;
      cursor:wait;
    }
    .foot{
      margin-top:16px;
      background:#fff;
      border:1px solid var(--line);
      border-radius:18px;
      padding:14px;
      color:var(--muted);
      box-shadow:var(--shadow);
      font-size:12px;
    }
    a{color:var(--accent);}
    @keyframes heroIn{
      from{opacity:0; transform:translateY(10px);}
      to{opacity:1; transform:translateY(0);}
    }
    @keyframes cardIn{
      from{opacity:0; transform:translateY(12px) scale(.985);}
      to{opacity:1; transform:translateY(0) scale(1);}
    }
    @media (max-width: 720px){
      .wrap{padding:16px 12px 30px;}
      .h1{font-size:34px;}
      .money{font-size:24px;}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1 class="h1">心動九日・紅包計畫</h1>
      <div class="meta">登入身份：{{ me_name }}（{{ uid }}）｜更新時間：{{ updated_at }}</div>
    </div>

    <div class="grid">
      {% for d in cards %}
      <div class="card" style="--d: {{ loop.index0 }};">
        <div class="top">
          <div class="day">Day {{ d.day_index }} {{ d.icon }}</div>
          <div class="date">{{ d.date }}</div>
        </div>
        <div class="money">{{ d.amount_text }}</div>
        <div class="title {% if d.show_title %}show{% endif %}">{{ d.title_text }}</div>
        {% if d.show_result %}
          <span class="pill">已破關 · 抽中 {{ d.amount_text }}</span>
        {% elif d.can_draw %}
          <button class="draw" data-day="{{ d.day_index }}" aria-label="draw">🧧</button>
        {% else %}
          <button class="draw" disabled aria-label="locked">🧧</button>
        {% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="foot">
      測試 API：<a href="{{ ping_url }}">{{ ping_url }}</a>｜登出：<a href="/hb/logout">/hb/logout</a>
    </div>
  </div>
  <script>
    (function () {
      const buttons = Array.from(document.querySelectorAll(".draw[data-day]"));
      if (!buttons.length) return;
      async function drawOne(btn) {
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.classList.add("loading");
        const dayIndex = Number(btn.getAttribute("data-day") || "0");
        try {
          const resp = await fetch("/hb/api/draw", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({day_index: dayIndex})
          });
          const data = await resp.json();
          if (resp.ok && data && data.ok) {
            window.location.reload();
            return;
          }
        } catch (e) {}
        btn.classList.remove("loading");
        btn.disabled = false;
      }
      buttons.forEach((btn) => {
        btn.addEventListener("click", () => drawOne(btn));
      });
    })();
  </script>
</body>
</html>
"""


HB_TEST_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ bot_name }} - 紅包測試頁</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Noto+Sans+TC:wght@400;500;700;800&display=swap" rel="stylesheet" />
  <style>
    :root{
      --bg:#fff8ef;
      --line:#f5cda4;
      --line-soft:#f8e0c7;
      --text:#7c2d12;
      --muted:#9a5b39;
      --card:#fff;
      --accent:#c2410c;
      --shadow:0 10px 24px rgba(124,45,18,.10);
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      color:var(--text);
      font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
      background:
        radial-gradient(1200px 600px at 5% -10%, #ffe7cf 0%, transparent 62%),
        linear-gradient(180deg, #fffdf9 0%, var(--bg) 100%);
      min-height:100vh;
    }
    .wrap{max-width:920px; margin:0 auto; padding:24px 16px 40px;}
    .hero{
      background:var(--card);
      border:1px solid var(--line);
      border-radius:20px;
      padding:18px;
      box-shadow:var(--shadow);
    }
    .h1{
      margin:0;
      font-size:38px;
      line-height:1.02;
      font-family:"Cormorant Garamond","Noto Serif TC",serif;
    }
    .muted{margin-top:8px; color:var(--muted); line-height:1.7}
    .card{
      background:#fffdf9;
      border:1px solid var(--line-soft);
      border-radius:16px;
      padding:12px;
      margin-top:12px;
    }
    .grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:14px;}
    .item{
      background:#fff;
      border:1px solid var(--line);
      border-radius:14px;
      padding:10px;
      box-shadow:var(--shadow);
    }
    .money{font-size:24px; font-weight:900; color:#b45309; margin-top:3px;}
    a{color:var(--accent)}
    @media (max-width: 720px){
      .h1{font-size:32px;}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1 class="h1">紅包活動 /test 測試頁</h1>
      <div class="muted">僅男友角色可開啟。此頁顯示完整金額，供你測試流程。</div>
    </div>
    <div class="card">
      身份：{{ me_name }}（{{ uid }}）<br/>
      正常頁：<a href="/hb">/hb</a> ｜ 測試 API：<a href="{{ ping_url }}">{{ ping_url }}</a>
    </div>
    <div class="grid">
      {% for d in days %}
      <div class="item">
        <div>Day {{ d.day_index }} {{ d.icon }} · {{ d.date }}</div>
        <div class="money">NT$ {{ d.amount }}</div>
        <div>{{ d.title }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
</body>
</html>
"""


def _hb_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_hb_tables(db_path: str) -> None:
    conn = _hb_conn(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hb_draw_record (
                user_id TEXT NOT NULL,
                day_index INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                drawn_at TEXT NOT NULL,
                PRIMARY KEY(user_id, day_index)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _list_drawn_days(db_path: str, user_id: str) -> set[int]:
    conn = _hb_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT day_index FROM hb_draw_record WHERE user_id=?",
            (user_id,),
        ).fetchall()
        out: set[int] = set()
        for r in rows:
            try:
                out.add(int(r["day_index"]))
            except Exception:
                continue
        return out
    finally:
        conn.close()


def _record_draw(db_path: str, user_id: str, day_index: int, amount: int, drawn_at: str) -> bool:
    conn = _hb_conn(db_path)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO hb_draw_record(user_id, day_index, amount, drawn_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, int(day_index), int(amount), drawn_at),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def hb_make_login_url(
    *,
    base_url: str,
    create_magic_token: Callable[..., str],
    db_path: str,
    user_id: str,
    ttl_seconds: int,
    next_path: str = "/hb",
) -> str:
    token = create_magic_token(db_path=db_path, user_id=user_id, ttl_seconds=ttl_seconds)
    target = (next_path or "/hb").strip()
    if target not in ("/hb", "/test"):
        target = "/hb"
    return f"{base_url}/hb/login?t={token}&next={quote(target, safe='')}"


def hb_test_allowed(*, get_user_role: Callable[..., str | None], db_path: str, user_id: str) -> bool:
    role = (get_user_role(db_path=db_path, user_id=user_id) or "").strip().lower()
    return role == "boyfriend"


def register_hb_routes(
    app,
    *,
    bot_name: str,
    love_db_path: str,
    dash_session_ttl_seconds: int,
    get_public_base_url: Callable[[], str],
    consume_magic_token: Callable[..., str | None],
    dash_user_allowed: Callable[[str], bool],
    get_display_name: Callable[..., str | None],
    get_user_role: Callable[..., str | None],
    tz_now: Callable[[], object],
    login_required_template: str,
    login_fail_template: str,
    login_forbidden_template: str,
    push_line_messages: Callable[[str, list[dict]], object] | None = None,
) -> None:
    _ensure_hb_tables(love_db_path)

    def _hb_session_valid() -> bool:
        try:
            exp = int(session.get("hb_exp") or 0)
        except Exception:
            exp = 0
        uid = (session.get("hb_uid") or "").strip()
        return bool(uid) and int(time.time()) < exp

    def _hb_require_page():
        if _hb_session_valid():
            return None
        return render_template_string(
            login_required_template,
            bot_name=bot_name,
            docs_url=f"{get_public_base_url()}/docs",
        )

    def _hb_current_uid() -> str:
        return (session.get("hb_uid") or "").strip()

    def _today_event(today_iso: str) -> dict | None:
        for d in HB_EVENT_DAYS:
            if d["date"] == today_iso:
                return d
        return None

    @app.route("/hb/login")
    def hb_login():
        token = (request.args.get("t") or "").strip()
        next_path = (request.args.get("next") or "/hb").strip()
        if next_path not in ("/hb", "/test"):
            next_path = "/hb"

        uid = consume_magic_token(db_path=love_db_path, token=token)
        if not uid:
            return render_template_string(
                login_fail_template,
                bot_name=bot_name,
                docs_url=f"{get_public_base_url()}/docs",
            ), 401

        if not dash_user_allowed(uid):
            return render_template_string(
                login_forbidden_template,
                bot_name=bot_name,
                docs_url=f"{get_public_base_url()}/docs",
            ), 403

        session["hb_uid"] = uid
        session["hb_exp"] = int(time.time()) + int(dash_session_ttl_seconds)
        session["hb_at"] = int(time.time())

        if next_path == "/test" and not hb_test_allowed(get_user_role=get_user_role, db_path=love_db_path, user_id=uid):
            return render_template_string(
                login_forbidden_template,
                bot_name=bot_name,
                docs_url=f"{get_public_base_url()}/docs",
            ), 403
        return redirect(next_path)

    @app.route("/hb/logout")
    def hb_logout():
        session.pop("hb_uid", None)
        session.pop("hb_exp", None)
        session.pop("hb_at", None)
        return redirect("/hb")

    @app.route("/hb")
    def hb_home():
        resp = _hb_require_page()
        if resp is not None:
            return resp

        uid = _hb_current_uid()
        role = (get_user_role(db_path=love_db_path, user_id=uid) or "").strip().lower()
        if role != "girlfriend":
            return render_template_string(
                login_forbidden_template,
                bot_name=bot_name,
                docs_url=f"{get_public_base_url()}/docs",
            ), 403

        me_name = get_display_name(db_path=love_db_path, user_id=uid) or "使用者"
        now = tz_now()
        today = now.date().isoformat()
        drawn_days = _list_drawn_days(love_db_path, uid)

        cards = []
        for d in HB_EVENT_DAYS:
            day = d["date"]
            is_cleared = int(d["day_index"]) in drawn_days
            can_draw = bool(day == today and not is_cleared)

            amount_text = f"NT$ {d['amount']}" if is_cleared else "NT$ ???"
            cards.append(
                {
                    **d,
                    "amount_text": amount_text,
                    "show_title": is_cleared,
                    "title_text": d["title"] if is_cleared else "",
                    "show_result": is_cleared,
                    "can_draw": can_draw,
                }
            )

        return render_template_string(
            HB_HOME_TEMPLATE,
            bot_name=bot_name,
            uid=uid,
            me_name=me_name,
            updated_at=now.strftime("%Y-%m-%d %H:%M"),
            cards=cards,
            ping_url=f"{get_public_base_url()}/hb/api/ping",
        )

    @app.route("/test")
    def hb_test_page():
        resp = _hb_require_page()
        if resp is not None:
            return resp

        uid = _hb_current_uid()
        if not hb_test_allowed(get_user_role=get_user_role, db_path=love_db_path, user_id=uid):
            return render_template_string(
                login_forbidden_template,
                bot_name=bot_name,
                docs_url=f"{get_public_base_url()}/docs",
            ), 403

        return render_template_string(
            HB_TEST_TEMPLATE,
            bot_name=bot_name,
            uid=uid,
            me_name=get_display_name(db_path=love_db_path, user_id=uid) or "使用者",
            days=HB_EVENT_DAYS,
            ping_url=f"{get_public_base_url()}/hb/api/ping",
        )

    @app.route("/hb/api/draw", methods=["POST"])
    def hb_api_draw():
        if not _hb_session_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        uid = _hb_current_uid()
        role = (get_user_role(db_path=love_db_path, user_id=uid) or "").strip().lower()
        if role != "girlfriend":
            return jsonify({"ok": False, "error": "forbidden"}), 403

        payload = request.get_json(silent=True) or {}
        try:
            day_index = int(payload.get("day_index") or 0)
        except Exception:
            day_index = 0
        if day_index <= 0:
            return jsonify({"ok": False, "error": "bad_day_index"}), 400

        today = tz_now().date().isoformat()
        ev = _today_event(today)
        if not ev:
            return jsonify({"ok": False, "error": "not_open"}), 400
        today_index = int(ev["day_index"])
        if day_index != today_index:
            return jsonify({"ok": False, "error": "day_mismatch"}), 400

        drawn_days = _list_drawn_days(love_db_path, uid)
        if today_index in drawn_days:
            return jsonify({"ok": True, "already": True, "day_index": today_index, "amount": int(ev["amount"])})

        inserted = _record_draw(
            love_db_path,
            uid,
            today_index,
            int(ev["amount"]),
            tz_now().isoformat(timespec="seconds"),
        )
        if not inserted:
            return jsonify({"ok": True, "already": True, "day_index": today_index, "amount": int(ev["amount"])})

        if push_line_messages is not None:
            try:
                push_line_messages(uid, [{"type": "text", "text": "🧧🧧🧧🧧🧧"}])
            except Exception:
                pass

        return jsonify({"ok": True, "day_index": today_index, "amount": int(ev["amount"])})

    @app.route("/hb/api/ping")
    def hb_api_ping():
        if not _hb_session_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return jsonify(
            {
                "ok": True,
                "uid": _hb_current_uid(),
                "now": tz_now().isoformat(timespec="seconds"),
                "path": "/hb/api/ping",
            }
        )
