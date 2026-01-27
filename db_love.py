# db_love.py - SQLite helpers for love bot (hardened: schema-compat + retry + dedupe)
import os
import sqlite3
import datetime
import time
import random
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_DB = os.getenv("LOVE_DB_PATH", "/data/love.db")

SQLITE_JOURNAL_MODE = (os.getenv("SQLITE_JOURNAL_MODE", "WAL") or "WAL").upper()
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "20000"))  # 20s
SQLITE_TIMEOUT_S = float(os.getenv("SQLITE_TIMEOUT_S", "30"))  # sqlite3.connect timeout (seconds)
SQLITE_MAX_RETRIES = int(os.getenv("SQLITE_MAX_RETRIES", "6"))
SQLITE_RETRY_BASE_MS = int(os.getenv("SQLITE_RETRY_BASE_MS", "80"))


def _tz() -> ZoneInfo:
    tzname = os.getenv("TIMEZONE", "Asia/Taipei")
    try:
        return ZoneInfo(tzname)
    except Exception:
        return ZoneInfo("UTC")

def _tz_now_iso() -> str:
    return datetime.datetime.now(_tz()).isoformat(timespec="seconds")

def _parse_dt_any(s: str) -> datetime.datetime:
    # 支援 ...Z
    s = (s or "").replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(_tz())



def _conn(db_path: str):
    conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_S, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Busy / locking behavior
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")

    # Journal mode: WAL is good for concurrency; on some network volumes, DELETE can be more reliable
    try:
        conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE};")
    except Exception:
        # if unsupported, ignore
        pass

    # Synchronous tuning
    if SQLITE_JOURNAL_MODE == "WAL":
        conn.execute("PRAGMA synchronous=NORMAL;")
    else:
        conn.execute("PRAGMA synchronous=FULL;")

    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _is_locked_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "database is locked" in msg or "database is busy" in msg or "locked" in msg or "busy" in msg


def _sleep_backoff(attempt: int):
    # exponential backoff + jitter
    base = SQLITE_RETRY_BASE_MS * (2 ** attempt)
    jitter = random.randint(0, SQLITE_RETRY_BASE_MS)
    time.sleep((base + jitter) / 1000.0)


def _execute(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    last = None
    for attempt in range(SQLITE_MAX_RETRIES + 1):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            last = e
            if not _is_locked_error(e) or attempt >= SQLITE_MAX_RETRIES:
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            _sleep_backoff(attempt)
    raise last  # pragma: no cover


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


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

    # subscriber (new schema includes created_at; we still support old schemas at runtime)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriber (
            line_user_id TEXT PRIMARY KEY,
            display_name TEXT,
            role TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
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
            assign_role TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
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


    # task_media (link photo_tasks <-> media, supports multiple photos per task)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_media (
            task_id INTEGER NOT NULL,
            message_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(task_id, message_id),
            FOREIGN KEY(task_id) REFERENCES photo_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(message_id) REFERENCES media(message_id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_task_media_task ON task_media(task_id, created_at);")

    # dedupe table (avoid LINE retries creating duplicate side-effects)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_messages (
            message_id TEXT PRIMARY KEY,
            user_id TEXT,
            msg_type TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_processed_messages_user ON processed_messages(user_id);")
    
    # medication: daily pill reminder log/state
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS med_pills (
            day TEXT NOT NULL,                 -- YYYY-MM-DD (bot timezone)
            user_id TEXT NOT NULL,             -- LINE userId (girlfriend)
            required INTEGER NOT NULL DEFAULT 1,

            taken_at TEXT,                     -- ISO datetime when taken (or reported)
            taken_time_text TEXT,              -- HH:MM derived for display
            reported_text TEXT,                -- original user message

            last_remind_at TEXT,               -- ISO datetime
            remind_count INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            PRIMARY KEY(day, user_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_med_pills_user_day ON med_pills(user_id, day);")


    # dashboard magic login tokens (one-time + short-lived)
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS dashboard_magic_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at INTEGER
        )
        '''
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dash_tokens_user_exp ON dashboard_magic_tokens(user_id, expires_at);")



    conn.commit()

    # Light schema migration for older subscriber tables (missing columns)
    try:
        cols = _table_cols(conn, "subscriber")
        now = _tz_now_iso()
        if "created_at" not in cols:
            conn.execute("ALTER TABLE subscriber ADD COLUMN created_at TEXT;")
            conn.execute("UPDATE subscriber SET created_at=? WHERE created_at IS NULL;", (now,))
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE subscriber ADD COLUMN updated_at TEXT;")
            conn.execute("UPDATE subscriber SET updated_at=? WHERE updated_at IS NULL;", (now,))
        if "is_active" not in cols:
            conn.execute("ALTER TABLE subscriber ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;")
        if "role" not in cols:
            conn.execute("ALTER TABLE subscriber ADD COLUMN role TEXT;")
        conn.commit()
    except Exception:
        # ignore migration failures; runtime functions are defensive too
        pass

    # Light schema migration for older photo_tasks/media tables (missing columns)
    try:
        now = _tz_now_iso()
        cols = _table_cols(conn, "photo_tasks")
        if "status" not in cols:
            conn.execute("ALTER TABLE photo_tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'open';")
        if "created_by" not in cols:
            conn.execute("ALTER TABLE photo_tasks ADD COLUMN created_by TEXT;")
        if "expires_at" not in cols:
            conn.execute("ALTER TABLE photo_tasks ADD COLUMN expires_at TEXT;")
            conn.execute("UPDATE photo_tasks SET expires_at=? WHERE expires_at IS NULL;", (now,))
        if "done_at" not in cols:
            conn.execute("ALTER TABLE photo_tasks ADD COLUMN done_at TEXT;")
        conn.commit()
    except Exception:
        pass

    try:
        cols = _table_cols(conn, "media")
        if "content_type" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN content_type TEXT;")
        if "from_user_id" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN from_user_id TEXT;")
        conn.commit()
    except Exception:
        pass

    # seed default love lines / date ideas if empty
    n = conn.execute("SELECT COUNT(*) AS c FROM love_lines").fetchone()["c"]
    if n == 0:
        defaults = [
            "你不在我旁邊的時候，我就把想你當作日常。",
            "你一笑，我今天的壓力就降到 0。",
            "我喜歡你不是一時興起，是每天都更確定。",
            "你是我最想好好珍惜的人。",
        ]
        for t in defaults:
            _execute(conn, "INSERT INTO love_lines(text, created_at) VALUES(?, ?)", (t, _tz_now_iso()))
        conn.commit()

    n2 = conn.execute("SELECT COUNT(*) AS c FROM date_ideas").fetchone()["c"]
    if n2 == 0:
        ideas = [
            "散步 + 買飲料 + 坐路邊聊天",
            "去逛超市各買一個小東西交換",
            "找一間咖啡店各寫一封小紙條給對方",
            "去河堤吹風看夕陽",
        ]
        for t in ideas:
            _execute(conn, "INSERT INTO date_ideas(text, created_at) VALUES(?, ?)", (t, _tz_now_iso()))
        conn.commit()

    conn.close()

# ===== medication: pill reminders =====
def _today_str() -> str:
    return datetime.datetime.now(_tz()).date().isoformat()

def ensure_med_pill_row(db_path: str, user_id: str, day: str | None = None) -> dict:
    day = day or _today_str()
    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        _execute(
            conn,
            """
            INSERT OR IGNORE INTO med_pills(day, user_id, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (day, user_id, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM med_pills WHERE day=? AND user_id=?", (day, user_id)).fetchone()
        return dict(row) if row else {"day": day, "user_id": user_id}
    finally:
        conn.close()

def get_med_pill_row(db_path: str, user_id: str, day: str | None = None) -> Optional[dict]:
    day = day or _today_str()
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT * FROM med_pills WHERE day=? AND user_id=?", (day, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_med_pill_rows_between(
    db_path: str,
    user_id: str,
    start_day: str,
    end_day: str,
) -> list[dict]:
    """
    Return rows in [start_day, end_day] (inclusive) for the given user_id.
    day format: YYYY-MM-DD
    """
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM med_pills
            WHERE user_id=? AND day>=? AND day<=?
            ORDER BY day DESC
            """,
            (user_id, start_day, end_day),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_med_pill_taken(
    db_path: str,
    user_id: str,
    taken_at_iso: str,
    day: str | None = None,
    taken_time_text: str | None = None,
    reported_text: str | None = None,
):
    day = day or _today_str()
    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        _execute(
            conn,
            """
            INSERT OR IGNORE INTO med_pills(day, user_id, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (day, user_id, now, now),
        )
        _execute(
            conn,
            """
            UPDATE med_pills
            SET taken_at=?,
                taken_time_text=?,
                reported_text=?,
                updated_at=?
            WHERE day=? AND user_id=?
            """,
            (taken_at_iso, taken_time_text, reported_text, now, day, user_id),
        )
        conn.commit()
    finally:
        conn.close()
def clear_med_pill_taken(db_path: str, user_id: str, day: str | None = None):
    day = day or _today_str()
    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        _execute(
            conn,
            """
            UPDATE med_pills
            SET taken_at=NULL,
                taken_time_text=NULL,
                reported_text=NULL,
                updated_at=?
            WHERE day=? AND user_id=?
            """,
            (now, day, user_id),
        )
        conn.commit()
    finally:
        conn.close()

def mark_med_pill_reminded(db_path: str, user_id: str, remind_at_iso: str, day: str | None = None):
    day = day or _today_str()
    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        _execute(
            conn,
            """
            INSERT OR IGNORE INTO med_pills(day, user_id, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (day, user_id, now, now),
        )
        _execute(
            conn,
            """
            UPDATE med_pills
            SET last_remind_at=?,
                remind_count=COALESCE(remind_count, 0) + 1,
                updated_at=?
            WHERE day=? AND user_id=?
            """,
            (remind_at_iso, now, day, user_id),
        )
        conn.commit()
    finally:
        conn.close()

# ===== dedupe =====
def mark_message_processed(db_path: str, message_id: str, user_id: str, msg_type: str) -> bool:
    """
    Returns True if this message_id is new; False if already processed.
    """
    conn = _conn(db_path)
    try:
        cur = _execute(
            conn,
            """
            INSERT OR IGNORE INTO processed_messages(message_id, user_id, msg_type, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (message_id, user_id, msg_type, _tz_now_iso()),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ===== love lines =====
def random_love_line(db_path: str = DEFAULT_DB) -> Optional[dict]:
    conn = _conn(db_path)
    row = conn.execute("SELECT id, text FROM love_lines ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def add_love_line(db_path: str, text: str) -> int:
    conn = _conn(db_path)
    cur = _execute(conn, "INSERT INTO love_lines(text, created_at) VALUES(?, ?)", (text, _tz_now_iso()))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return int(lid)


def delete_love_line(db_path: str, love_id: int) -> bool:
    conn = _conn(db_path)
    cur = _execute(conn, "DELETE FROM love_lines WHERE id=?", (love_id,))
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
    cur = _execute(conn, "INSERT INTO date_ideas(text, created_at) VALUES(?, ?)", (text, _tz_now_iso()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return int(rid)


# ===== wishes / moods =====
def add_wish(db_path: str, user_id: str, text: str) -> int:
    conn = _conn(db_path)
    cur = _execute(conn, "INSERT INTO wishes(user_id, text, created_at) VALUES(?, ?, ?)", (user_id, text, _tz_now_iso()))
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
    cur = _execute(conn, "INSERT INTO moods(user_id, text, created_at) VALUES(?, ?, ?)", (user_id, text, _tz_now_iso()))
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
    _execute(
        conn,
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
    """
    Works with both schemas:
      - old: (line_user_id, display_name, updated_at)
      - new: (line_user_id, display_name, created_at, updated_at)
    """
    conn = _conn(db_path)
    now = _tz_now_iso()
    cols = _table_cols(conn, "subscriber")

    if "created_at" in cols and "updated_at" in cols:
        _execute(
            conn,
            """
            INSERT INTO subscriber(line_user_id, display_name, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (user_id, display_name, now, now),
        )
    elif "updated_at" in cols:
        _execute(
            conn,
            """
            INSERT INTO subscriber(line_user_id, display_name, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (user_id, display_name, now),
        )
    else:
        # extreme legacy fallback
        _execute(
            conn,
            """
            INSERT OR REPLACE INTO subscriber(line_user_id, display_name)
            VALUES(?, ?)
            """,
            (user_id, display_name),
        )

    conn.commit()
    conn.close()


def set_role(db_path: str, user_id: str, role: str):
    conn = _conn(db_path)
    now = _tz_now_iso()
    cols = _table_cols(conn, "subscriber")

    if "updated_at" in cols:
        _execute(conn, "UPDATE subscriber SET role=?, updated_at=? WHERE line_user_id=?", (role, now, user_id))
    else:
        _execute(conn, "UPDATE subscriber SET role=? WHERE line_user_id=?", (role, user_id))

    conn.commit()
    conn.close()


def set_active(db_path: str, user_id: str, is_active: bool):
    conn = _conn(db_path)
    now = _tz_now_iso()
    cols = _table_cols(conn, "subscriber")

    if "updated_at" in cols:
        _execute(
            conn,
            "UPDATE subscriber SET is_active=?, updated_at=? WHERE line_user_id=?",
            (1 if is_active else 0, now, user_id),
        )
    else:
        _execute(conn, "UPDATE subscriber SET is_active=? WHERE line_user_id=?", (1 if is_active else 0, user_id))

    conn.commit()
    conn.close()


def get_role_map_active(db_path: str = DEFAULT_DB) -> dict:
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


def get_couple_user_ids(db_path: str = DEFAULT_DB) -> list[str]:
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
    cur = _execute(
        conn,
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


def expire_open_photo_tasks(db_path: str) -> int:
    """
    Mark open photo_tasks as expired if expires_at < now.
    Returns number of tasks changed.
    """
    conn = _conn(db_path)
    try:
        now = datetime.datetime.now(_tz())
        rows = conn.execute(
            "SELECT id, expires_at FROM photo_tasks WHERE status='open' ORDER BY id DESC LIMIT 500"
        ).fetchall()
        expired_ids: list[int] = []
        for r in rows:
            exp_raw = r["expires_at"]
            if not exp_raw:
                continue
            try:
                exp = _parse_dt_any(exp_raw)
            except Exception:
                continue
            if exp < now:
                expired_ids.append(int(r["id"]))

        for tid in expired_ids:
            _execute(conn, "UPDATE photo_tasks SET status='expired' WHERE id=?", (tid,))
        conn.commit()
        return len(expired_ids)
    finally:
        conn.close()


def list_photo_tasks(db_path: str, status: str | None = None, limit: int = 200) -> list[dict]:
    """
    List photo_tasks (optionally by status). Sorted newest first.
    """
    conn = _conn(db_path)
    try:
        if status:
            rows = conn.execute(
                """
                SELECT id, assign_role, text, status, created_by, created_at, expires_at, done_at
                FROM photo_tasks
                WHERE status=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, assign_role, text, status, created_by, created_at, expires_at, done_at
                FROM photo_tasks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def attach_media_to_task(db_path: str, task_id: int, message_id: str):
    conn = _conn(db_path)
    try:
        _execute(
            conn,
            "INSERT OR IGNORE INTO task_media(task_id, message_id, created_at) VALUES(?, ?, ?)",
            (int(task_id), str(message_id), _tz_now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def list_task_media_items(db_path: str, task_id: int | None = None, limit: int = 1000) -> list[dict]:
    """
    Join task_media -> media for gallery rendering.
    """
    conn = _conn(db_path)
    try:
        if task_id is not None:
            rows = conn.execute(
                """
                SELECT tm.task_id, m.message_id, m.filename, m.content_type, m.from_user_id, m.created_at
                FROM task_media tm
                JOIN media m ON m.message_id = tm.message_id
                WHERE tm.task_id=?
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (int(task_id), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT tm.task_id, m.message_id, m.filename, m.content_type, m.from_user_id, m.created_at
                FROM task_media tm
                JOIN media m ON m.message_id = tm.message_id
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()



def claim_latest_open_task_for_role(db_path: str, role: str, expire_minutes: int = 180, message_id: str | None = None):
    """
    Claim the latest open task for the role, mark it done, and optionally attach the incoming media message_id.
    Also sweeps a few recent open tasks to expired if already past expires_at.
    """
    conn = _conn(db_path)
    try:
        now = datetime.datetime.now(_tz())

        rows = conn.execute(
            """
            SELECT id, text, expires_at
            FROM photo_tasks
            WHERE status='open' AND assign_role=?
            ORDER BY id DESC
            LIMIT 10
            """,
            (role,),
        ).fetchall()

        picked = None
        for row in rows:
            exp_raw = row["expires_at"]
            exp = _parse_dt_any(exp_raw) if exp_raw else None

            if exp and exp < now:
                _execute(conn, "UPDATE photo_tasks SET status='expired' WHERE id=?", (row["id"],))
                continue

            picked = row
            break

        if not picked:
            conn.commit()
            return None

        _execute(
            conn,
            "UPDATE photo_tasks SET status='done', done_at=? WHERE id=?",
            (_tz_now_iso(), picked["id"]),
        )

        if message_id:
            try:
                _execute(
                    conn,
                    "INSERT OR IGNORE INTO task_media(task_id, message_id, created_at) VALUES(?, ?, ?)",
                    (int(picked["id"]), str(message_id), _tz_now_iso()),
                )
            except Exception:
                # If the table is missing (old DB) or FK mismatch, ignore.
                pass

        conn.commit()
        return {"id": picked["id"], "text": picked["text"], "expires_at": picked["expires_at"]}
    finally:
        conn.close()


# ===== media =====
def save_media_record(
    db_path: str,
    message_id: str,
    filename: str,
    content_type: Optional[str],
    from_user_id: str,
    created_at: str,
):
    conn = _conn(db_path)
    _execute(
        conn,
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


def list_media_records(db_path: str, limit: int = 500, offset: int = 0) -> list[dict]:
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT message_id, filename, content_type, from_user_id, created_at
            FROM media
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()



# ===== dashboard magic tokens =====
def create_dashboard_magic_token(
    db_path: str,
    user_id: str,
    ttl_seconds: int = 600,
    min_interval_seconds: int = 10,
) -> str:
    """Create a short-lived token for /dash/login.

    - Stored server-side (SQLite) with expiry (epoch seconds)
    - Intended for one-time use (consumed on login)
    """
    import secrets

    now = int(time.time())
    exp = now + int(ttl_seconds)

    conn = _conn(db_path)
    try:
        # basic cleanup (avoid unbounded growth)
        _execute(conn, "DELETE FROM dashboard_magic_tokens WHERE expires_at < ?", (now - 3600,))

        # rate limit: if user requested within min_interval_seconds, reuse latest unexpired unused token
        row = conn.execute(
            """
            SELECT token, created_at, expires_at, used_at
            FROM dashboard_magic_tokens
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row:
            try:
                created_at = int(row["created_at"])
                expires_at = int(row["expires_at"])
                used_at = row["used_at"]
            except Exception:
                created_at = 0
                expires_at = 0
                used_at = 1

            if (now - created_at) < int(min_interval_seconds) and (used_at is None) and (expires_at > now):
                return row["token"]

        token = secrets.token_urlsafe(32)
        _execute(
            conn,
            """
            INSERT INTO dashboard_magic_tokens(token, user_id, created_at, expires_at, used_at)
            VALUES(?, ?, ?, ?, NULL)
            """,
            (token, user_id, now, exp),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def consume_dashboard_magic_token(db_path: str, token: str) -> Optional[str]:
    """Consume a magic token (one-time).

    Returns user_id if valid, else None.
    """
    token = (token or "").strip()
    if not token:
        return None

    now = int(time.time())
    conn = _conn(db_path)
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
            expires_at = int(row["expires_at"])
        except Exception:
            return None
        if row["used_at"] is not None:
            return None
        if expires_at <= now:
            return None

        cur = _execute(
            conn,
            """
            UPDATE dashboard_magic_tokens
            SET used_at=?
            WHERE token=? AND used_at IS NULL AND expires_at > ?
            """,
            (now, token, now),
        )
        conn.commit()
        if getattr(cur, "rowcount", 0) != 1:
            return None
        return row["user_id"]
    finally:
        conn.close()
