# db_love.py - SQLite helpers for love bot
import os
import sqlite3
import datetime
from typing import Optional

DEFAULT_DB = os.getenv("LOVE_DB_PATH", "/data/love.db")


def _tz_now_iso(tz: str = "Asia/Taipei") -> str:
    # keep as simple ISO; app uses tz-aware timestamps too
    return datetime.datetime.now().isoformat(timespec="seconds")


def _conn(db_path: str):
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def seed_defaults(db_path: str = DEFAULT_DB):
    conn = _conn(db_path)
    cur = conn.cursor()

    # core tables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS love_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS date_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS wishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            user_id TEXT NOT NULL,
            k TEXT NOT NULL,
            v TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, k)
        )
        """
    )

    # subscriber table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriber (
            line_user_id TEXT PRIMARY KEY,
            display_name TEXT,
            role TEXT,                  -- 'girlfriend' or 'boyfriend'
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriber_role_active ON subscriber(role, is_active);")

    # photo tasks
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS photo_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assign_role TEXT NOT NULL,       -- girlfriend / boyfriend
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',  -- open / done / cancelled
            created_by TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            done_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_tasks_status ON photo_tasks(status, assign_role);")

    # media
    cur.execute(
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

    # seed default love lines / date ideas if empty
    n = cur.execute("SELECT COUNT(*) AS c FROM love_lines").fetchone()["c"]
    if n == 0:
        defaults = [
            "你不在我旁邊的時候，我就把想你當作日常。",
            "你一笑，我今天的壓力就降到 0。",
            "我喜歡你不是一時興起，是每天都更確定。",
            "你是我最想好好珍惜的人。",
        ]
        for t in defaults:
            cur.execute("INSERT INTO love_lines(text, created_at) VALUES(?, ?)", (t, _tz_now_iso()))
        conn.commit()

    n2 = cur.execute("SELECT COUNT(*) AS c FROM date_ideas").fetchone()["c"]
    if n2 == 0:
        ideas = [
            "散步 + 買飲料 + 坐路邊聊天",
            "去逛超市各買一個小東西交換",
            "找一間咖啡店各寫一封小紙條給對方",
            "去河堤吹風看夕陽",
        ]
        for t in ideas:
            cur.execute("INSERT INTO date_ideas(text, created_at) VALUES(?, ?)", (t, _tz_now_iso()))
        conn.commit()

    conn.close()


# ===== love lines =====
def random_love_line(db_path: str = DEFAULT_DB) -> Optional[dict]:
    conn = _conn(db_path)
    row = conn.execute("SELECT id, text FROM love_lines ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def add_love_line(db_path: str, text: str) -> int:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO love_lines(text, created_at) VALUES(?, ?)", (text, _tz_now_iso()))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return int(lid)


def delete_love_line(db_path: str, love_id: int) -> bool:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM love_lines WHERE id=?", (love_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def list_love_lines(db_path: str, limit: int = 20) -> list[dict]:
    conn = _conn(db_path)
    rows = conn.execute("SELECT id, text FROM love_lines ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== date ideas =====
def random_date_idea(db_path: str = DEFAULT_DB) -> Optional[dict]:
    conn = _conn(db_path)
    row = conn.execute("SELECT id, text FROM date_ideas ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def add_date_idea(db_path: str, text: str) -> int:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO date_ideas(text, created_at) VALUES(?, ?)", (text, _tz_now_iso()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return int(rid)


# ===== wishes / moods =====
def add_wish(db_path: str, user_id: str, text: str) -> int:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO wishes(user_id, text, created_at) VALUES(?, ?, ?)", (user_id, text, _tz_now_iso()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return int(rid)


def list_wishes(db_path: str, user_id: str, limit: int = 10) -> list[dict]:
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT id, text, created_at FROM wishes WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_mood(db_path: str, user_id: str, text: str) -> int:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO moods(user_id, text, created_at) VALUES(?, ?, ?)", (user_id, text, _tz_now_iso()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return int(rid)


def list_moods(db_path: str, user_id: str, limit: int = 10) -> list[dict]:
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT id, text, created_at FROM moods WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== settings =====
def set_setting(db_path: str, user_id: str, key: str, value: str):
    conn = _conn(db_path)
    conn.execute(
        """
        INSERT INTO settings(user_id, k, v, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(user_id, k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at
        """,
        (user_id, key, value, _tz_now_iso()),
    )
    conn.commit()
    conn.close()


def get_setting(db_path: str, user_id: str, key: str) -> Optional[str]:
    conn = _conn(db_path)
    row = conn.execute("SELECT v FROM settings WHERE user_id=? AND k=?", (user_id, key)).fetchone()
    conn.close()
    return row["v"] if row else None


# ===== subscriber / roles =====
def upsert_subscriber(db_path: str, user_id: str, display_name: str):
    conn = _conn(db_path)
    conn.execute(
        """
        INSERT INTO subscriber(line_user_id, display_name, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(line_user_id) DO UPDATE SET
            display_name=excluded.display_name,
            updated_at=excluded.updated_at
        """,
        (user_id, display_name, _tz_now_iso()),
    )
    conn.commit()
    conn.close()


def set_role(db_path: str, user_id: str, role: str):
    conn = _conn(db_path)
    conn.execute(
        """
        UPDATE subscriber SET role=?, updated_at=? WHERE line_user_id=?
        """,
        (role, _tz_now_iso(), user_id),
    )
    conn.commit()
    conn.close()


def set_active(db_path: str, user_id: str, is_active: bool):
    conn = _conn(db_path)
    conn.execute(
        """
        UPDATE subscriber SET is_active=?, updated_at=? WHERE line_user_id=?
        """,
        (1 if is_active else 0, _tz_now_iso(), user_id),
    )
    conn.commit()
    conn.close()


def get_role_map_active(db_path: str) -> dict:
    conn = _conn(db_path)
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


def get_couple_user_ids(db_path: str) -> list[str]:
    m = get_role_map_active(db_path=db_path)
    ids = []
    if m.get("girlfriend"):
        ids.append(m["girlfriend"])
    if m.get("boyfriend"):
        ids.append(m["boyfriend"])
    return ids


def get_display_name(db_path: str, user_id: str) -> str:
    conn = _conn(db_path)
    row = conn.execute("SELECT display_name FROM subscriber WHERE line_user_id=?", (user_id,)).fetchone()
    conn.close()
    return (row["display_name"] or "") if row else ""


def get_user_role(db_path: str, user_id: str) -> Optional[str]:
    conn = _conn(db_path)
    row = conn.execute("SELECT role FROM subscriber WHERE line_user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["role"] if row else None


# ===== photo tasks =====
def create_photo_task(db_path: str, assign_role: str, text: str, created_by: str, expires_at: str) -> int:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO photo_tasks(assign_role, text, status, created_by, created_at, expires_at)
        VALUES(?, ?, 'open', ?, ?, ?)
        """,
        (assign_role, text, created_by, _tz_now_iso(), expires_at),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return int(rid)


def list_open_photo_tasks(db_path: str, limit: int = 10) -> list[dict]:
    conn = _conn(db_path)
    rows = conn.execute(
        """
        SELECT id, assign_role, text, expires_at
        FROM photo_tasks
        WHERE status='open'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def claim_latest_open_task_for_role(db_path: str, role: str, expire_minutes: int = 180) -> Optional[dict]:
    """
    Find latest open task for role where expires_at >= now, mark it done, return task.
    """
    now = datetime.datetime.now()
    conn = _conn(db_path)
    cur = conn.cursor()

    # pick latest open task for role
    row = cur.execute(
        """
        SELECT id, text, expires_at
        FROM photo_tasks
        WHERE status='open' AND assign_role=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (role,),
    ).fetchone()

    if not row:
        conn.close()
        return None

    # check expiry
    try:
        exp = datetime.datetime.fromisoformat(row["expires_at"])
    except Exception:
        exp = None

    if exp and exp < now:
        conn.close()
        return None

    # mark done
    cur.execute(
        "UPDATE photo_tasks SET status='done', done_at=? WHERE id=?",
        (_tz_now_iso(), row["id"]),
    )
    conn.commit()
    conn.close()
    return {"id": row["id"], "text": row["text"], "expires_at": row["expires_at"]}


# ===== media =====
def save_media_record(db_path: str, message_id: str, filename: str, content_type: Optional[str], from_user_id: str, created_at: str):
    conn = _conn(db_path)
    conn.execute(
        """
        INSERT INTO media(message_id, filename, content_type, from_user_id, created_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            filename=excluded.filename,
            content_type=excluded.content_type,
            from_user_id=excluded.from_user_id
        """,
        (message_id, filename, content_type, from_user_id, created_at),
    )
    conn.commit()
    conn.close()


def get_media_record(db_path: str, message_id: str) -> Optional[dict]:
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT message_id, filename, content_type, from_user_id, created_at FROM media WHERE message_id=?",
        (message_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None
