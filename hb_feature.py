from __future__ import annotations

import sqlite3
import secrets
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
  <title>{{ bot_name }} - 紅包試玩場</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Zen+Maru+Gothic:wght@400;500;700;900&display=swap" rel="stylesheet" />
  <style>
    :root{
      --bg-a:#fff8f9;
      --bg-b:#ffeef4;
      --line:#f4c7d7;
      --line-soft:#f7dbe6;
      --text:#5a1733;
      --muted:#8a4b66;
      --card:#ffffff;
      --accent:#be185d;
      --ok:#0f766e;
      --ok-bg:#dcfce7;
      --shadow:0 12px 30px rgba(90, 23, 51, .12);
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      color:var(--text);
      font-family:"Zen Maru Gothic","PingFang TC","Microsoft JhengHei",sans-serif;
      background:
        radial-gradient(1000px 460px at 5% -8%, #ffdbe8 0%, transparent 60%),
        radial-gradient(950px 420px at 95% -6%, #ffe7f0 0%, transparent 60%),
        linear-gradient(180deg, var(--bg-a) 0%, var(--bg-b) 100%);
      min-height:100vh;
      overflow-x:hidden;
    }
    body::before{
      content:"";
      position:fixed;
      inset:0;
      pointer-events:none;
      opacity:.32;
      background-image:radial-gradient(circle at 14px 14px, rgba(190,24,93,.08) 0 2px, transparent 2px);
      background-size:40px 40px;
    }
    .wrap{max-width:1020px; margin:0 auto; padding:24px 16px 42px; position:relative; z-index:1;}
    .hero{
      background:var(--card);
      border:1px solid var(--line);
      border-radius:22px;
      padding:18px;
      box-shadow:var(--shadow);
      backdrop-filter:blur(2px);
    }
    .h1{
      margin:0;
      font-size:40px;
      line-height:1.02;
      font-family:"Cormorant Garamond","Noto Serif TC",serif;
      letter-spacing:.4px;
    }
    .muted{margin-top:8px; color:var(--muted); line-height:1.7}
    .card{
      background:#fff;
      border:1px solid var(--line-soft);
      border-radius:18px;
      padding:14px;
      margin-top:12px;
      box-shadow:var(--shadow);
    }
    .tools{
      display:flex;
      gap:10px;
      align-items:center;
      flex-wrap:wrap;
      margin-top:10px;
    }
    .btn{
      border:none;
      border-radius:999px;
      padding:8px 14px;
      font-size:13px;
      font-weight:800;
      cursor:pointer;
      color:#fff;
      background:linear-gradient(90deg, #e11d48, #be185d);
      box-shadow:0 8px 16px rgba(190,24,93,.24);
    }
    .btn.secondary{
      text-decoration:none;
      background:#fff;
      color:var(--accent);
      border:1px solid var(--line);
      box-shadow:none;
    }
    .grid{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(215px,1fr));
      gap:12px;
      margin-top:14px;
    }
    .item{
      background:#fff;
      border:1px solid var(--line);
      border-radius:16px;
      padding:12px;
      box-shadow:var(--shadow);
      position:relative;
      overflow:hidden;
      transition:transform .16s ease, box-shadow .16s ease;
    }
    .item.ready:hover{
      transform:translateY(-2px);
      box-shadow:0 14px 28px rgba(90,23,51,.16);
    }
    .item.locked{
      opacity:.8;
      filter:saturate(.72);
    }
    .top{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:8px;
    }
    .day{
      font-size:30px;
      font-weight:900;
      color:#7a2045;
      margin-top:4px;
    }
    .title{
      margin-top:3px;
      min-height:20px;
      color:var(--muted);
    }
    .pill{
      display:inline-block;
      margin-top:9px;
      border-radius:999px;
      padding:4px 10px;
      font-size:12px;
      font-weight:900;
      background:#f5f7ff;
      color:#577;
    }
    .pill.ok{
      background:var(--ok-bg);
      color:var(--ok);
    }
    .play{
      margin-top:9px;
      width:100%;
      border:none;
      border-radius:999px;
      padding:8px 10px;
      font-size:13px;
      font-weight:900;
      color:#fff;
      background:linear-gradient(90deg, #fb7185, #e11d48);
      cursor:pointer;
      box-shadow:0 6px 14px rgba(225,29,72,.25);
    }
    .play:disabled{
      background:#d5deea;
      color:#617185;
      box-shadow:none;
      cursor:not-allowed;
    }
    .arena h2{
      margin:0;
      font-size:20px;
      font-weight:900;
    }
    .hint{margin-top:6px; color:var(--muted); font-size:14px;}
    .target{
      margin-top:10px;
      font-weight:900;
      color:#7a2045;
      min-height:24px;
    }
    .hold{
      margin-top:12px;
      width:100%;
      max-width:380px;
      border:none;
      border-radius:16px;
      padding:18px 14px;
      font-size:24px;
      font-weight:900;
      color:#fff;
      letter-spacing:.6px;
      background:linear-gradient(180deg, #fb7185 0%, #e11d48 100%);
      box-shadow:0 12px 20px rgba(225,29,72,.28);
      cursor:pointer;
      touch-action:none;
      user-select:none;
      transition:transform .06s ease, filter .12s ease;
    }
    .hold:active{transform:scale(.99);}
    .hold.holding{filter:saturate(1.15) brightness(1.03);}
    .hold:disabled{
      background:#d3dbe8;
      color:#6b7280;
      box-shadow:none;
      cursor:not-allowed;
    }
    .result{
      margin-top:10px;
      min-height:24px;
      font-weight:800;
      color:var(--muted);
    }
    .result.ok{color:var(--ok);}
    .result.fail{color:#9f1239;}
    a{color:var(--accent)}
    @media (max-width: 720px){
      .h1{font-size:32px;}
      .day{font-size:26px;}
      .hold{font-size:22px;}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1 class="h1">紅包活動 /test 試玩場</h1>
      <div class="muted">僅男友角色可開啟。這頁可直接試玩關卡，成功後會解鎖該 Day 的金額。</div>
    </div>
    <div class="card">
      身份：{{ me_name }}（{{ uid }}）<br/>
      正常頁：<a href="/hb">/hb</a> ｜ 測試 API：<a href="{{ ping_url }}">{{ ping_url }}</a>
      <div class="tools">
        <button id="resetBtn" class="btn" type="button">重置試玩進度</button>
        <a class="btn secondary" href="/hb/logout">登出</a>
      </div>
    </div>

    <div class="grid">
      {% for d in cards %}
      <div class="item {% if d.is_cleared %}done{% elif d.can_play %}ready{% else %}locked{% endif %}">
        <div class="top">
          <div><strong>Day {{ d.day_index }} {{ d.icon }}</strong></div>
          <div>{{ d.date }}</div>
        </div>
        <div class="day">{{ d.amount_text }}</div>
        <div class="title">{{ d.title_text }}</div>
        {% if d.is_cleared %}
          <div class="pill ok">已通關</div>
          <button class="play" disabled>已完成</button>
        {% elif d.can_play %}
          <div class="pill">可挑戰</div>
          <button class="play" data-day="{{ d.day_index }}" type="button">開始試玩</button>
        {% else %}
          <div class="pill">未解鎖</div>
          <button class="play" disabled>尚未解鎖</button>
        {% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="card arena">
      <h2>試玩關卡：心跳長按</h2>
      <div class="hint" id="hintText">選一個可挑戰 Day，系統會給目標秒數；按住愛心後放開，後端判定是否過關。</div>
      <div class="target" id="targetLine">目標：-</div>
      <button id="holdBtn" class="hold" type="button" disabled>💗 按住我</button>
      <div id="resultLine" class="result"></div>
    </div>
  </div>
  <script>
    (function () {
      const playButtons = Array.from(document.querySelectorAll(".play[data-day]"));
      const holdBtn = document.getElementById("holdBtn");
      const targetLine = document.getElementById("targetLine");
      const resultLine = document.getElementById("resultLine");
      const resetBtn = document.getElementById("resetBtn");
      const hintText = document.getElementById("hintText");

      let challenge = null;
      let pressStartMs = 0;
      let isHolding = false;

      function setResult(text, cls) {
        resultLine.textContent = text || "";
        resultLine.classList.remove("ok", "fail");
        if (cls) resultLine.classList.add(cls);
      }

      function msToSec(ms) {
        return (Number(ms || 0) / 1000).toFixed(2);
      }

      async function startChallenge(dayIndex) {
        holdBtn.disabled = true;
        holdBtn.classList.remove("holding");
        setResult("準備中...", "");
        try {
          const resp = await fetch("/hb/api/test/start", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({day_index: dayIndex})
          });
          const data = await resp.json();
          if (!resp.ok || !data || !data.ok) {
            setResult("無法開始挑戰，請重整後再試。", "fail");
            return;
          }
          if (data.already) {
            setResult("這一關已經完成。", "ok");
            window.setTimeout(() => window.location.reload(), 320);
            return;
          }
          challenge = {
            day_index: Number(data.day_index),
            nonce: String(data.nonce || ""),
            target_ms: Number(data.target_ms || 0),
            window_ms: Number(data.window_ms || 220)
          };
          targetLine.textContent = "Day " + challenge.day_index + " 目標：" + msToSec(challenge.target_ms) + " 秒（容錯 ±" + challenge.window_ms + "ms）";
          hintText.textContent = "按住再放開，越接近目標越好。";
          holdBtn.disabled = false;
          setResult("開始吧。", "");
        } catch (e) {
          setResult("網路異常，請再試一次。", "fail");
        }
      }

      async function submitChallenge(durationMs, clientStartMs, clientEndMs) {
        if (!challenge) return;
        holdBtn.disabled = true;
        try {
          const resp = await fetch("/hb/api/test/submit", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              day_index: challenge.day_index,
              nonce: challenge.nonce,
              duration_ms: Math.round(durationMs),
              client_start_ms: Math.round(clientStartMs),
              client_end_ms: Math.round(clientEndMs)
            })
          });
          const data = await resp.json();
          if (!resp.ok || !data || !data.ok) {
            setResult("送出失敗，請重新開始。", "fail");
            challenge = null;
            return;
          }
          if (data.success) {
            setResult("過關！抽到 NT$ " + data.amount + "。", "ok");
            window.setTimeout(() => window.location.reload(), 520);
            return;
          }
          const diff = Number(data.diff_ms || 0);
          setResult("差了 " + diff + "ms，再挑戰一次。", "fail");
          challenge = null;
          holdBtn.disabled = true;
          targetLine.textContent = "目標：-";
          hintText.textContent = "再點一次「開始試玩」即可重開。";
        } catch (e) {
          setResult("送出失敗，請重試。", "fail");
          challenge = null;
        }
      }

      function beginHold(ev) {
        if (!challenge || holdBtn.disabled || isHolding) return;
        ev.preventDefault();
        isHolding = true;
        pressStartMs = performance.now();
        holdBtn.classList.add("holding");
        setResult("計時中...", "");
      }

      function endHold(ev) {
        if (!isHolding) return;
        if (ev) ev.preventDefault();
        isHolding = false;
        holdBtn.classList.remove("holding");
        const endMs = performance.now();
        const duration = Math.max(0, endMs - pressStartMs);
        submitChallenge(duration, pressStartMs, endMs);
      }

      playButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const dayIndex = Number(btn.getAttribute("data-day") || "0");
          if (dayIndex <= 0) return;
          startChallenge(dayIndex);
        });
      });

      holdBtn.addEventListener("pointerdown", beginHold);
      holdBtn.addEventListener("pointerup", endHold);
      holdBtn.addEventListener("pointercancel", endHold);
      holdBtn.addEventListener("pointerleave", (ev) => {
        if (isHolding) endHold(ev);
      });

      if (resetBtn) {
        resetBtn.addEventListener("click", async () => {
          if (!window.confirm("重置後會清空 /test 全部通關紀錄，確定嗎？")) return;
          resetBtn.disabled = true;
          try {
            const resp = await fetch("/hb/api/test/reset", {method: "POST"});
            if (resp.ok) {
              window.location.reload();
              return;
            }
          } catch (e) {}
          resetBtn.disabled = false;
          setResult("重置失敗，稍後再試。", "fail");
        });
      }
    })();
  </script>
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hb_test_record (
                user_id TEXT NOT NULL,
                day_index INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                cleared_at TEXT NOT NULL,
                PRIMARY KEY(user_id, day_index)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _event_by_day_index(day_index: int) -> dict | None:
    for d in HB_EVENT_DAYS:
        if int(d["day_index"]) == int(day_index):
            return d
    return None


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


def _list_test_cleared_days(db_path: str, user_id: str) -> dict[int, int]:
    conn = _hb_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT day_index, amount FROM hb_test_record WHERE user_id=?",
            (user_id,),
        ).fetchall()
        out: dict[int, int] = {}
        for r in rows:
            try:
                out[int(r["day_index"])] = int(r["amount"])
            except Exception:
                continue
        return out
    finally:
        conn.close()


def _record_test_clear(db_path: str, user_id: str, day_index: int, amount: int, cleared_at: str) -> bool:
    conn = _hb_conn(db_path)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO hb_test_record(user_id, day_index, amount, cleared_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, int(day_index), int(amount), cleared_at),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def _clear_test_progress(db_path: str, user_id: str) -> None:
    conn = _hb_conn(db_path)
    try:
        conn.execute("DELETE FROM hb_test_record WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _next_unlock_day(cleared_days: set[int]) -> int:
    day = 1
    last_day = len(HB_EVENT_DAYS)
    while day <= last_day and day in cleared_days:
        day += 1
    return day


def _recover_recently_used_magic_token_user(db_path: str, token: str, grace_seconds: int = 180) -> str | None:
    token = (token or "").strip()
    if not token:
        return None
    now = int(time.time())
    conn = _hb_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT user_id, expires_at, used_at
            FROM dashboard_magic_tokens
            WHERE token=?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        try:
            exp = int(row["expires_at"] or 0)
            used_at = int(row["used_at"] or 0)
        except Exception:
            return None
        if exp <= now:
            return None
        if used_at <= 0:
            return None
        if now - used_at > int(grace_seconds):
            return None
        uid = (row["user_id"] or "").strip()
        return uid or None
    except Exception:
        return None
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
            # LINE in-app preview/crawler may consume one-time tokens before user taps the link.
            # Allow a short grace window for already-used-but-fresh tokens.
            uid = _recover_recently_used_magic_token_user(
                db_path=love_db_path,
                token=token,
                grace_seconds=180,
            )
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
        session.pop("hb_test_game", None)

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
        session.pop("hb_test_game", None)
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

        cleared_map = _list_test_cleared_days(love_db_path, uid)
        cleared_days = set(cleared_map.keys())
        unlock_day = _next_unlock_day(cleared_days)
        cards = []
        for d in HB_EVENT_DAYS:
            day_index = int(d["day_index"])
            is_cleared = day_index in cleared_days
            can_play = (day_index == unlock_day) and (not is_cleared) and day_index <= len(HB_EVENT_DAYS)
            cards.append(
                {
                    **d,
                    "is_cleared": is_cleared,
                    "can_play": can_play,
                    "amount_text": f"NT$ {d['amount']}" if is_cleared else "NT$ ???",
                    "title_text": d["title"] if is_cleared else "",
                }
            )

        return render_template_string(
            HB_TEST_TEMPLATE,
            bot_name=bot_name,
            uid=uid,
            me_name=get_display_name(db_path=love_db_path, user_id=uid) or "使用者",
            cards=cards,
            ping_url=f"{get_public_base_url()}/hb/api/ping",
        )

    @app.route("/hb/api/test/start", methods=["POST"])
    def hb_api_test_start():
        if not _hb_session_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        uid = _hb_current_uid()
        if not hb_test_allowed(get_user_role=get_user_role, db_path=love_db_path, user_id=uid):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        payload = request.get_json(silent=True) or {}
        try:
            day_index = int(payload.get("day_index") or 0)
        except Exception:
            day_index = 0
        if day_index <= 0:
            return jsonify({"ok": False, "error": "bad_day_index"}), 400

        ev = _event_by_day_index(day_index)
        if not ev:
            return jsonify({"ok": False, "error": "bad_day_index"}), 400

        cleared_map = _list_test_cleared_days(love_db_path, uid)
        if day_index in cleared_map:
            return jsonify({"ok": True, "already": True, "day_index": day_index, "amount": int(cleared_map[day_index])})

        unlock_day = _next_unlock_day(set(cleared_map.keys()))
        if day_index != unlock_day:
            return jsonify({"ok": False, "error": "locked", "unlock_day": unlock_day}), 400

        target_ms = 2200 + secrets.randbelow(1801)  # 2200~4000ms
        window_ms = 220
        nonce = secrets.token_urlsafe(16)
        session["hb_test_game"] = {
            "uid": uid,
            "day_index": int(day_index),
            "nonce": nonce,
            "target_ms": int(target_ms),
            "window_ms": int(window_ms),
            "issued_at": int(time.time()),
        }
        return jsonify(
            {
                "ok": True,
                "day_index": day_index,
                "nonce": nonce,
                "target_ms": int(target_ms),
                "window_ms": int(window_ms),
                "expire_sec": 120,
            }
        )

    @app.route("/hb/api/test/submit", methods=["POST"])
    def hb_api_test_submit():
        if not _hb_session_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        uid = _hb_current_uid()
        if not hb_test_allowed(get_user_role=get_user_role, db_path=love_db_path, user_id=uid):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        payload = request.get_json(silent=True) or {}
        try:
            day_index = int(payload.get("day_index") or 0)
        except Exception:
            day_index = 0
        nonce = str(payload.get("nonce") or "").strip()
        try:
            duration_ms = int(payload.get("duration_ms") or 0)
        except Exception:
            duration_ms = 0

        game = session.get("hb_test_game") or {}
        if not isinstance(game, dict) or not game:
            return jsonify({"ok": False, "error": "no_game"}), 400

        try:
            g_uid = str(game.get("uid") or "").strip()
            g_day = int(game.get("day_index") or 0)
            g_target = int(game.get("target_ms") or 0)
            g_window = int(game.get("window_ms") or 220)
            g_nonce = str(game.get("nonce") or "").strip()
            g_issued_at = int(game.get("issued_at") or 0)
        except Exception:
            session.pop("hb_test_game", None)
            return jsonify({"ok": False, "error": "bad_game"}), 400

        now_ts = int(time.time())
        if g_uid != uid or g_day != day_index or g_nonce != nonce:
            session.pop("hb_test_game", None)
            return jsonify({"ok": False, "error": "mismatch"}), 400
        if now_ts - g_issued_at > 120:
            session.pop("hb_test_game", None)
            return jsonify({"ok": False, "error": "expired"}), 400
        if duration_ms < 600 or duration_ms > 9000:
            session.pop("hb_test_game", None)
            return jsonify({"ok": False, "error": "bad_duration"}), 400

        session.pop("hb_test_game", None)
        diff_ms = abs(int(duration_ms) - int(g_target))
        success = diff_ms <= int(g_window)
        if not success:
            return jsonify(
                {
                    "ok": True,
                    "success": False,
                    "day_index": int(day_index),
                    "target_ms": int(g_target),
                    "window_ms": int(g_window),
                    "diff_ms": int(diff_ms),
                }
            )

        ev = _event_by_day_index(day_index)
        if not ev:
            return jsonify({"ok": False, "error": "bad_day_index"}), 400

        inserted = _record_test_clear(
            love_db_path,
            uid,
            int(day_index),
            int(ev["amount"]),
            tz_now().isoformat(timespec="seconds"),
        )
        return jsonify(
            {
                "ok": True,
                "success": True,
                "already": (not inserted),
                "day_index": int(day_index),
                "amount": int(ev["amount"]),
                "diff_ms": int(diff_ms),
            }
        )

    @app.route("/hb/api/test/reset", methods=["POST"])
    def hb_api_test_reset():
        if not _hb_session_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        uid = _hb_current_uid()
        if not hb_test_allowed(get_user_role=get_user_role, db_path=love_db_path, user_id=uid):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        _clear_test_progress(love_db_path, uid)
        session.pop("hb_test_game", None)
        return jsonify({"ok": True})

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
