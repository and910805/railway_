# db_love.py
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

LOVE_DB_PATH = os.getenv("LOVE_DB_PATH", "/data/love.db")
_lock = threading.Lock()

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _get_conn():
    db_dir = os.path.dirname(LOVE_DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(LOVE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # --- 建立所有表格 ---
    conn.execute("CREATE TABLE IF NOT EXISTS love_line (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, tags TEXT, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS date_idea (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS wish (id INTEGER PRIMARY KEY AUTOINCREMENT, line_user_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS mood (id INTEGER PRIMARY KEY AUTOINCREMENT, line_user_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS user_setting (line_user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (line_user_id, key))")
    
    # 新增：攝影任務表格
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photo_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_text TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0,
            completed_by TEXT,
            created_at TEXT NOT NULL
        )
    """)
    return conn

_conn = _get_conn()

def seed_defaults():
    with _lock:
        c1 = _conn.execute("SELECT COUNT(1) AS n FROM love_line").fetchone()["n"]
        if c1 == 0:
            defaults = ["今天也要記得喝水，因為我很在乎你。", "你不用逞強，我喜歡你最真實的樣子。", "我們慢慢走，但一定一起走。"]
            _conn.executemany("INSERT INTO love_line(text, tags, created_at) VALUES(?, ?, ?)", [(t, None, _now()) for t in defaults])

        c2 = _conn.execute("SELECT COUNT(1) AS n FROM date_idea").fetchone()["n"]
        if c2 == 0:
            ideas = ["下班後去散步 30 分鐘", "一起去拍一組「今天的天空」", "找一間咖啡廳，各自寫 5 件最近開心的小事交換"]
            _conn.executemany("INSERT INTO date_idea(text, created_at) VALUES(?, ?)", [(t, _now()) for t in ideas])

        # 新增攝影任務種子資料
        c3 = _conn.execute("SELECT COUNT(1) AS n FROM photo_task").fetchone()["n"]
        if c3 == 0:
            tasks = [
                "用富士 X-T2 拍一張有『陳綺貞風格』的街角照片送給對方",
                "捕捉今天臭寶覺得最正的一個瞬間（手機拍也行！）",
                "拍一張臭咘咘認真工作的側臉（資安男專業時刻）",
                "找一個有富士感的光影，拍下我們今天的晚餐"
            ]
            _conn.executemany("INSERT INTO photo_task(task_text, created_at) VALUES(?, ?)", [(t, _now()) for t in tasks])
        _conn.commit()

# --- 攝影任務專屬函數 ---
def get_random_photo_task():
    with _lock:
        row = _conn.execute("SELECT * FROM photo_task WHERE is_completed=0 ORDER BY RANDOM() LIMIT 1").fetchone()
        return dict(row) if row else None

def complete_photo_task(task_id, user_id):
    with _lock:
        cur = _conn.execute("UPDATE photo_task SET is_completed=1, completed_by=?, created_at=? WHERE id=?", (user_id, _now(), task_id))
        _conn.commit()
        return cur.rowcount > 0

# --- 原有所有功能函數 (完整保留) ---
def add_love_line(text, tags=None):
    with _lock:
        cur = _conn.execute("INSERT INTO love_line(text, tags, created_at) VALUES(?, ?, ?)", (text.strip(), tags, _now()))
        _conn.commit()
        return cur.lastrowid
def delete_love_line(lid):
    with _lock:
        cur = _conn.execute("DELETE FROM love_line WHERE id=?", (int(lid),))
        _conn.commit()
        return cur.rowcount > 0
def list_love_lines(limit=20):
    with _lock:
        rows = _conn.execute("SELECT * FROM love_line ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
def random_love_line(tag=None):
    with _lock:
        sql = "SELECT * FROM love_line WHERE tags LIKE ? ORDER BY RANDOM() LIMIT 1" if tag else "SELECT * FROM love_line ORDER BY RANDOM() LIMIT 1"
        row = _conn.execute(sql, (f"%{tag}%",)).fetchone() if tag else _conn.execute(sql).fetchone()
        return dict(row) if row else None
def add_date_idea(text):
    with _lock:
        cur = _conn.execute("INSERT INTO date_idea(text, created_at) VALUES(?, ?)", (text.strip(), _now()))
        _conn.commit()
        return cur.lastrowid
def random_date_idea():
    with _lock:
        row = _conn.execute("SELECT * FROM date_idea ORDER BY RANDOM() LIMIT 1").fetchone()
        return dict(row) if row else None
def add_wish(uid, text):
    with _lock:
        cur = _conn.execute("INSERT INTO wish(line_user_id, text, created_at) VALUES(?, ?, ?)", (uid, text.strip(), _now()))
        _conn.commit()
        return cur.lastrowid
def list_wishes(uid, limit=10):
    with _lock:
        rows = _conn.execute("SELECT * FROM wish WHERE line_user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
        return [dict(r) for r in rows]
def add_mood(uid, text):
    with _lock:
        cur = _conn.execute("INSERT INTO mood(line_user_id, text, created_at) VALUES(?, ?, ?)", (uid, text.strip(), _now()))
        _conn.commit()
        return cur.lastrowid
def list_moods(uid, limit=10):
    with _lock:
        rows = _conn.execute("SELECT * FROM mood WHERE line_user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
        return [dict(r) for r in rows]
def set_setting(uid, k, v):
    with _lock:
        _conn.execute("INSERT INTO user_setting(line_user_id, key, value, updated_at) VALUES(?, ?, ?, ?) ON CONFLICT(line_user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (uid, k, v, _now()))
        _conn.commit()
def get_setting(uid, k):
    with _lock:
        row = _conn.execute("SELECT value FROM user_setting WHERE line_user_id=? AND key=?", (uid, k)).fetchone()
        return row["value"] if row else None