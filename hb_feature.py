from __future__ import annotations

import time
from typing import Callable

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
      --line-soft:#f8dce5;
      --text:#521b2f;
      --muted:#8a5166;
      --accent:#b71c4a;
      --ok-bg:#dcfce7;
      --ok:#0f766e;
      --todo-bg:#fff1d7;
      --todo:#b45309;
      --lock-bg:#edf2f7;
      --lock:#64748b;
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
    .wrap{max-width:1020px; margin:0 auto; padding:26px 16px 46px; position:relative; z-index:1;}
    .hero{
      background:rgba(255,255,255,.76);
      border:1px solid var(--line);
      border-radius:24px;
      padding:20px 18px;
      box-shadow:var(--shadow);
      backdrop-filter:blur(4px);
      animation:heroIn .55s ease both;
    }
    .chip{
      display:inline-block;
      border:1px solid var(--line-soft);
      background:#fff7fb;
      border-radius:999px;
      padding:4px 12px;
      color:var(--accent);
      font-weight:700;
      font-size:12px;
      letter-spacing:.3px;
    }
    .h1{
      margin:8px 0 0;
      font-family:"Cormorant Garamond","Noto Serif TC",serif;
      font-size:42px;
      line-height:1.02;
      letter-spacing:.6px;
      color:#68223f;
    }
    .sub{margin:10px 0 0; color:var(--muted); line-height:1.72; font-size:15px;}
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
      transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }
    .card::after{
      content:"";
      position:absolute;
      width:120px;
      height:120px;
      right:-36px;
      top:-56px;
      border-radius:50%;
      background:radial-gradient(circle, rgba(236,72,153,.13) 0%, rgba(236,72,153,0) 70%);
      pointer-events:none;
    }
    .card:hover{
      transform:translateY(-2px);
      box-shadow:0 16px 34px rgba(87, 26, 53, .14);
      border-color:#efbdd0;
    }
    .top{display:flex; justify-content:space-between; align-items:center; gap:8px;}
    .day{font-weight:800; letter-spacing:.2px;}
    .date{font-size:12px; color:var(--muted);}
    .money{font-size:28px; font-weight:800; margin-top:4px; color:#791d46;}
    .title{margin-top:4px; color:var(--muted); min-height:22px;}
    .title.hidden{opacity:0;}
    .pill{
      display:inline-block;
      margin-top:10px;
      padding:4px 10px;
      border-radius:999px;
      font-size:12px;
      font-weight:800;
    }
    .ok{background:var(--ok-bg); color:var(--ok)}
    .todo{background:var(--todo-bg); color:var(--todo)}
    .lock{background:var(--lock-bg); color:var(--lock)}
    .draw{
      display:inline-block;
      margin-top:10px;
      border:none;
      border-radius:999px;
      padding:7px 14px;
      background:linear-gradient(90deg, #e11d48, #be123c);
      color:#fff;
      font-size:12px;
      font-weight:800;
      letter-spacing:.4px;
      box-shadow:0 8px 16px rgba(190,18,60,.25);
    }
    .draw.disabled{
      background:#d6dde8;
      color:#6b7280;
      box-shadow:none;
    }
    .foot{
      margin-top:16px;
      background:#fff;
      border:1px solid var(--line);
      border-radius:18px;
      padding:14px;
      color:var(--muted);
      box-shadow:var(--shadow);
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
      .sub{font-size:14px;}
      .money{font-size:24px;}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <span class="chip">Red Packet</span>
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
        <div class="title {% if not d.show_title %}hidden{% endif %}">{{ d.title_text }}</div>
        {% if d.show_result %}
          <span class="pill ok">已破關 · 抽中 {{ d.amount_text }}</span>
        {% elif d.can_draw %}
          <button class="draw">抽紅包</button>
        {% else %}
          <button class="draw disabled" disabled>鎖定</button>
        {% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="foot">
      測試 API（需 token 先登入）：<a href="{{ ping_url }}">{{ ping_url }}</a><br/>
      登出：<a href="/hb/logout">/hb/logout</a>
    </div>
  </div>
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
      transition:transform .16s ease, box-shadow .16s ease;
    }
    .item:hover{transform:translateY(-2px); box-shadow:0 14px 26px rgba(124,45,18,.14);}
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
      <div class="muted">僅男友角色可開啟。此頁顯示完整金額，供你驗證流程與數字設定。</div>
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
    return f"{base_url}/hb/login?t={token}&next={target}"


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
) -> None:
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
        today = tz_now().date().isoformat()

        cards = []
        for d in HB_EVENT_DAYS:
            day = d["date"]
            # TODO: replace with real reward record after game APIs are ready.
            is_cleared = False
            if day < today:
                status_class = "lock"
                can_draw = False
            elif day == today:
                status_class = "todo"
                can_draw = not is_cleared
            else:
                status_class = "lock"
                can_draw = False

            amount_text = "NT$ ???"
            if is_cleared:
                amount_text = f"NT$ {d['amount']}"

            cards.append(
                {
                    **d,
                    "status_class": status_class,
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
            updated_at=tz_now().strftime("%Y-%m-%d %H:%M"),
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
