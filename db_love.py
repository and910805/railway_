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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wishes_user_created ON wishes(user_id, created_at DESC);")
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_moods_user_created ON moods(user_id, created_at DESC);")
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


    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_media_created_at ON media(created_at DESC);")

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

    # valentine surprise delivery/ack logs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS valentine_delivery_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            send_key TEXT NOT NULL,
            valentine_date TEXT NOT NULL,
            slot_index INTEGER NOT NULL,
            target_user_id TEXT NOT NULL,
            target_role TEXT NOT NULL,
            message_text TEXT NOT NULL,
            scheduled_for TEXT,
            push_status TEXT NOT NULL,
            push_error TEXT,
            pushed_at TEXT NOT NULL,
            acked_at TEXT,
            ack_text TEXT,
            ack_source_user_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(send_key, target_user_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_valentine_logs_user ON valentine_delivery_logs(target_user_id, pushed_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_valentine_logs_date ON valentine_delivery_logs(valentine_date, slot_index);")
    
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

    # conflict repair workflow (vent -> cooldown -> repair -> review)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conflict_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL,
            target_user_id TEXT,
            vent_text TEXT NOT NULL,
            emotion_type TEXT NOT NULL,
            intensity INTEGER NOT NULL,
            wants_reply_now INTEGER NOT NULL DEFAULT 1,
            need_type TEXT NOT NULL,
            cooldown_until TEXT,
            cooldown_notified_at TEXT,
            closed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_events_open ON conflict_events(closed_at, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_events_target ON conflict_events(target_user_id, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_events_created_by ON conflict_events(created_by, created_at DESC);")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conflict_event_triggers (
            event_id INTEGER NOT NULL,
            trigger_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(event_id, trigger_key),
            FOREIGN KEY(event_id) REFERENCES conflict_events(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_triggers_key_created ON conflict_event_triggers(trigger_key, created_at DESC);")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conflict_event_confirms (
            event_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(event_id, user_id),
            FOREIGN KEY(event_id) REFERENCES conflict_events(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conflict_confirms_user ON conflict_event_confirms(user_id, confirmed_at DESC);")

    # stress game sessions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS game_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            target_user_id TEXT,
            total_damage INTEGER NOT NULL DEFAULT 0,
            max_combo INTEGER NOT NULL DEFAULT 0,
            ko_count INTEGER NOT NULL DEFAULT 0,
            round_reached INTEGER NOT NULL DEFAULT 1,
            tool_breakdown TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_game_sessions_user_created ON game_sessions(user_id, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_game_sessions_created ON game_sessions(created_at DESC);")



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

    # Light schema migration for conflict_events (new optional columns)
    try:
        cols = _table_cols(conn, "conflict_events")
        if "cooldown_notified_at" not in cols:
            conn.execute("ALTER TABLE conflict_events ADD COLUMN cooldown_notified_at TEXT;")
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


def upsert_valentine_delivery_log(
    db_path: str,
    send_key: str,
    valentine_date: str,
    slot_index: int,
    target_user_id: str,
    target_role: str,
    message_text: str,
    scheduled_for: str | None,
    push_status: str,
    push_error: str | None = None,
    pushed_at: str | None = None,
):
    now_iso = _tz_now_iso()
    pushed_at = (pushed_at or "").strip() or now_iso
    conn = _conn(db_path)
    try:
        _execute(
            conn,
            """
            INSERT INTO valentine_delivery_logs(
                send_key, valentine_date, slot_index, target_user_id, target_role,
                message_text, scheduled_for, push_status, push_error, pushed_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(send_key, target_user_id) DO UPDATE SET
                valentine_date=excluded.valentine_date,
                slot_index=excluded.slot_index,
                target_role=excluded.target_role,
                message_text=excluded.message_text,
                scheduled_for=excluded.scheduled_for,
                push_status=excluded.push_status,
                push_error=excluded.push_error,
                pushed_at=excluded.pushed_at,
                updated_at=excluded.updated_at
            """,
            (
                str(send_key),
                str(valentine_date),
                int(slot_index),
                str(target_user_id),
                str(target_role),
                str(message_text),
                (scheduled_for or None),
                str(push_status),
                (push_error or None),
                str(pushed_at),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_valentine_delivery_logs(db_path: str, valentine_date: str | None = None, limit: int = 80) -> list[dict]:
    limit = max(1, min(500, int(limit)))
    conn = _conn(db_path)
    try:
        if valentine_date and str(valentine_date).strip():
            rows = conn.execute(
                """
                SELECT id, send_key, valentine_date, slot_index, target_user_id, target_role,
                       message_text, scheduled_for, push_status, push_error, pushed_at,
                       acked_at, ack_text, ack_source_user_id
                FROM valentine_delivery_logs
                WHERE valentine_date=?
                ORDER BY slot_index DESC, id DESC
                LIMIT ?
                """,
                (str(valentine_date).strip(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, send_key, valentine_date, slot_index, target_user_id, target_role,
                       message_text, scheduled_for, push_status, push_error, pushed_at,
                       acked_at, ack_text, ack_source_user_id
                FROM valentine_delivery_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def ack_latest_valentine_delivery_for_user(
    db_path: str,
    target_user_id: str,
    ack_text: str,
    acked_at: str | None = None,
) -> Optional[dict]:
    uid = (target_user_id or "").strip()
    if not uid:
        return None
    now_iso = (acked_at or "").strip() or _tz_now_iso()
    conn = _conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, send_key, valentine_date, slot_index, target_user_id, target_role,
                   message_text, scheduled_for, push_status, push_error, pushed_at,
                   acked_at, ack_text, ack_source_user_id
            FROM valentine_delivery_logs
            WHERE target_user_id=?
              AND push_status='accepted'
              AND (acked_at IS NULL OR acked_at='')
            ORDER BY id DESC
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        if not row:
            return None

        row_id = int(row["id"])
        _execute(
            conn,
            """
            UPDATE valentine_delivery_logs
            SET acked_at=?, ack_text=?, ack_source_user_id=?, updated_at=?
            WHERE id=?
            """,
            (now_iso, str(ack_text or ""), uid, now_iso, row_id),
        )
        conn.commit()

        updated = conn.execute(
            """
            SELECT id, send_key, valentine_date, slot_index, target_user_id, target_role,
                   message_text, scheduled_for, push_status, push_error, pushed_at,
                   acked_at, ack_text, ack_source_user_id
            FROM valentine_delivery_logs
            WHERE id=?
            """,
            (row_id,),
        ).fetchone()
        return dict(updated) if updated else None
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

def get_settings_map(db_path: str, user_id: str) -> dict[str, str]:
    """Fetch all settings for a user in one query (faster than repeated get_setting calls)."""
    conn = _conn(db_path)
    rows = conn.execute("SELECT k, v FROM settings WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    out: dict[str, str] = {}
    for r in rows:
        out[str(r["k"])] = str(r["v"])
    return out




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




def list_media_records_with_task(db_path: str, limit: int = 500, offset: int = 0, task_id: int | None = None) -> list[dict]:
    """
    Media list with optional task_id via LEFT JOIN task_media.
    Used by /dash/gallery to avoid extra queries.
    """
    conn = _conn(db_path)
    try:
        if task_id is not None:
            rows = conn.execute(
                """
                SELECT m.message_id, m.filename, m.content_type, m.from_user_id, m.created_at, tm.task_id
                FROM media m
                LEFT JOIN task_media tm ON tm.message_id = m.message_id
                WHERE tm.task_id = ?
                ORDER BY m.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (int(task_id), limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.message_id, m.filename, m.content_type, m.from_user_id, m.created_at, tm.task_id
                FROM media m
                LEFT JOIN task_media tm ON tm.message_id = m.message_id
                ORDER BY m.created_at DESC
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


# ===== conflict repair workflow =====
def create_conflict_event(
    db_path: str,
    created_by: str,
    target_user_id: str | None,
    vent_text: str,
    emotion_type: str,
    intensity: int,
    wants_reply_now: bool,
    need_type: str,
    cooldown_until: str | None = None,
) -> int:
    created_by = (created_by or "").strip()
    target_user_id = (target_user_id or "").strip() or None
    vent_text = (vent_text or "").strip()
    emotion_type = (emotion_type or "").strip()
    need_type = (need_type or "").strip()
    intensity = int(max(1, min(5, int(intensity))))
    if not created_by:
        raise ValueError("created_by is required")
    if not vent_text:
        raise ValueError("vent_text is required")
    if not emotion_type:
        raise ValueError("emotion_type is required")
    if not need_type:
        raise ValueError("need_type is required")

    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        cur = _execute(
            conn,
            """
            INSERT INTO conflict_events(
                created_by, target_user_id, vent_text, emotion_type, intensity,
                wants_reply_now, need_type, cooldown_until, closed_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                created_by,
                target_user_id,
                vent_text,
                emotion_type,
                intensity,
                1 if wants_reply_now else 0,
                need_type,
                (cooldown_until or "").strip() or None,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_conflict_event(db_path: str, event_id: int) -> Optional[dict]:
    conn = _conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT *
            FROM conflict_events
            WHERE id=?
            """,
            (int(event_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_conflict_events(db_path: str, limit: int = 30, user_id: str | None = None) -> list[dict]:
    limit = max(1, min(200, int(limit)))
    uid = (user_id or "").strip()
    conn = _conn(db_path)
    try:
        if uid:
            rows = conn.execute(
                """
                SELECT *
                FROM conflict_events
                WHERE created_by=? OR target_user_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (uid, uid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM conflict_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_open_conflict_events(db_path: str, limit: int = 20, user_id: str | None = None) -> list[dict]:
    limit = max(1, min(200, int(limit)))
    uid = (user_id or "").strip()
    conn = _conn(db_path)
    try:
        if uid:
            rows = conn.execute(
                """
                SELECT *
                FROM conflict_events
                WHERE closed_at IS NULL AND (created_by=? OR target_user_id=?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (uid, uid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM conflict_events
                WHERE closed_at IS NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_conflict_triggers_for_event(db_path: str, event_id: int) -> list[str]:
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT trigger_key
            FROM conflict_event_triggers
            WHERE event_id=?
            ORDER BY trigger_key
            """,
            (int(event_id),),
        ).fetchall()
        return [str(r["trigger_key"]) for r in rows]
    finally:
        conn.close()


def set_conflict_event_triggers(db_path: str, event_id: int, trigger_keys: list[str]) -> list[str]:
    clean = sorted({(x or "").strip() for x in (trigger_keys or []) if (x or "").strip()})
    conn = _conn(db_path)
    try:
        now = _tz_now_iso()
        _execute(conn, "DELETE FROM conflict_event_triggers WHERE event_id=?", (int(event_id),))
        for k in clean:
            _execute(
                conn,
                """
                INSERT OR IGNORE INTO conflict_event_triggers(event_id, trigger_key, created_at)
                VALUES(?, ?, ?)
                """,
                (int(event_id), k, now),
            )
        _execute(conn, "UPDATE conflict_events SET updated_at=? WHERE id=?", (now, int(event_id)))
        conn.commit()
        return clean
    finally:
        conn.close()


def list_conflict_confirmed_users(db_path: str, event_id: int) -> list[str]:
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT user_id
            FROM conflict_event_confirms
            WHERE event_id=?
            ORDER BY user_id
            """,
            (int(event_id),),
        ).fetchall()
        return [str(r["user_id"]) for r in rows]
    finally:
        conn.close()


def confirm_conflict_event(db_path: str, event_id: int, user_id: str) -> dict:
    user_id = (user_id or "").strip()
    if not user_id:
        return {"ok": False, "reason": "user_required"}

    conn = _conn(db_path)
    try:
        ev = conn.execute(
            "SELECT id, created_by, target_user_id, closed_at FROM conflict_events WHERE id=?",
            (int(event_id),),
        ).fetchone()
        if not ev:
            return {"ok": False, "reason": "not_found"}

        participants = []
        for uid in (ev["created_by"], ev["target_user_id"]):
            uid = (uid or "").strip()
            if uid and uid not in participants:
                participants.append(uid)

        if user_id not in participants:
            return {"ok": False, "reason": "not_participant"}

        now = _tz_now_iso()
        _execute(
            conn,
            """
            INSERT OR IGNORE INTO conflict_event_confirms(event_id, user_id, confirmed_at)
            VALUES(?, ?, ?)
            """,
            (int(event_id), user_id, now),
        )

        rows = conn.execute(
            "SELECT user_id FROM conflict_event_confirms WHERE event_id=?",
            (int(event_id),),
        ).fetchall()
        confirmed = {(r["user_id"] or "").strip() for r in rows if (r["user_id"] or "").strip()}

        closed = all(uid in confirmed for uid in participants)
        if closed and not ev["closed_at"]:
            _execute(
                conn,
                "UPDATE conflict_events SET closed_at=?, updated_at=? WHERE id=?",
                (now, now, int(event_id)),
            )
        else:
            _execute(conn, "UPDATE conflict_events SET updated_at=? WHERE id=?", (now, int(event_id)))
        conn.commit()

        return {
            "ok": True,
            "closed": closed,
            "participants": participants,
            "confirmed_users": sorted(confirmed),
        }
    finally:
        conn.close()


def list_conflict_trigger_top(db_path: str, days: int = 7, limit: int = 3) -> list[dict]:
    days = max(1, min(90, int(days)))
    limit = max(1, min(20, int(limit)))
    since = (datetime.datetime.now(_tz()) - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT trigger_key, COUNT(*) AS c
            FROM conflict_event_triggers
            WHERE created_at>=?
            GROUP BY trigger_key
            ORDER BY c DESC, trigger_key ASC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        return [{"trigger_key": r["trigger_key"], "count": int(r["c"])} for r in rows]
    finally:
        conn.close()


def get_no_blowup_streak_days(db_path: str, intense_threshold: int = 4) -> int:
    threshold = max(1, min(5, int(intense_threshold)))
    conn = _conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT created_at
            FROM conflict_events
            WHERE intensity>=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (threshold,),
        ).fetchone()
        if not row:
            return 0
        dt = _parse_dt_any(row["created_at"])
        today = datetime.datetime.now(_tz()).date()
        days = (today - dt.date()).days
        return max(0, int(days))
    finally:
        conn.close()


def list_conflict_triggers_map(db_path: str, event_ids: list[int]) -> dict[int, list[str]]:
    ids = sorted({int(x) for x in (event_ids or []) if int(x) > 0})
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT event_id, trigger_key
            FROM conflict_event_triggers
            WHERE event_id IN ({placeholders})
            ORDER BY event_id ASC, trigger_key ASC
            """,
            tuple(ids),
        ).fetchall()
        out: dict[int, list[str]] = {eid: [] for eid in ids}
        for r in rows:
            eid = int(r["event_id"])
            out.setdefault(eid, []).append(str(r["trigger_key"]))
        return out
    finally:
        conn.close()


def list_conflict_confirms_map(db_path: str, event_ids: list[int]) -> dict[int, list[str]]:
    ids = sorted({int(x) for x in (event_ids or []) if int(x) > 0})
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT event_id, user_id
            FROM conflict_event_confirms
            WHERE event_id IN ({placeholders})
            ORDER BY event_id ASC, user_id ASC
            """,
            tuple(ids),
        ).fetchall()
        out: dict[int, list[str]] = {eid: [] for eid in ids}
        for r in rows:
            eid = int(r["event_id"])
            uid = (r["user_id"] or "").strip()
            if uid:
                out.setdefault(eid, []).append(uid)
        return out
    finally:
        conn.close()


def list_conflict_events_ready_for_cooldown_notify(db_path: str, now_iso: str, limit: int = 30) -> list[dict]:
    limit = max(1, min(200, int(limit)))
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, created_by, target_user_id, cooldown_until
            FROM conflict_events
            WHERE closed_at IS NULL
              AND cooldown_until IS NOT NULL
              AND cooldown_until <> ''
              AND cooldown_until <= ?
              AND (cooldown_notified_at IS NULL OR cooldown_notified_at = '')
            ORDER BY id ASC
            LIMIT ?
            """,
            (str(now_iso), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_conflict_cooldown_notified(db_path: str, event_id: int, notified_at_iso: str):
    conn = _conn(db_path)
    try:
        _execute(
            conn,
            """
            UPDATE conflict_events
            SET cooldown_notified_at=?, updated_at=?
            WHERE id=? AND (cooldown_notified_at IS NULL OR cooldown_notified_at='')
            """,
            (str(notified_at_iso), str(notified_at_iso), int(event_id)),
        )
        conn.commit()
    finally:
        conn.close()


def create_game_session(
    db_path: str,
    user_id: str,
    target_user_id: str | None,
    total_damage: int,
    max_combo: int,
    ko_count: int,
    round_reached: int,
    tool_breakdown: str | None = None,
) -> int:
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    target_user_id = (target_user_id or "").strip() or None
    total_damage = max(0, int(total_damage))
    max_combo = max(0, int(max_combo))
    ko_count = max(0, int(ko_count))
    round_reached = max(1, int(round_reached))
    tool_breakdown = (tool_breakdown or "").strip() or None

    conn = _conn(db_path)
    try:
        cur = _execute(
            conn,
            """
            INSERT INTO game_sessions(
                user_id, target_user_id, total_damage, max_combo, ko_count, round_reached, tool_breakdown, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                target_user_id,
                total_damage,
                max_combo,
                ko_count,
                round_reached,
                tool_breakdown,
                _tz_now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_game_week_stats(db_path: str, user_id: str) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        return {"plays": 0, "sum_ko": 0, "sum_damage": 0, "max_combo": 0}
    since = (datetime.datetime.now(_tz()) - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    conn = _conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS plays,
                COALESCE(SUM(ko_count), 0) AS sum_ko,
                COALESCE(SUM(total_damage), 0) AS sum_damage,
                COALESCE(MAX(max_combo), 0) AS max_combo
            FROM game_sessions
            WHERE user_id=? AND created_at>=?
            """,
            (uid, since),
        ).fetchone()
        return {
            "plays": int(row["plays"] or 0),
            "sum_ko": int(row["sum_ko"] or 0),
            "sum_damage": int(row["sum_damage"] or 0),
            "max_combo": int(row["max_combo"] or 0),
        }
    finally:
        conn.close()


def list_game_sessions_since(db_path: str, user_id: str, days: int = 7, limit: int = 500) -> list[dict]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    days = max(1, min(90, int(days)))
    limit = max(1, min(2000, int(limit)))
    since = (datetime.datetime.now(_tz()) - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, user_id, target_user_id, total_damage, max_combo, ko_count, round_reached, tool_breakdown, created_at
            FROM game_sessions
            WHERE user_id=? AND created_at>=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (uid, since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_repair_week_stats(db_path: str, user_id: str) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        return {"closed_count": 0, "open_count": 0, "high_intensity_count": 0}
    since = (datetime.datetime.now(_tz()) - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    conn = _conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN closed_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS closed_count,
                COALESCE(SUM(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END), 0) AS open_count,
                COALESCE(SUM(CASE WHEN intensity>=4 THEN 1 ELSE 0 END), 0) AS high_intensity_count
            FROM conflict_events
            WHERE (created_by=? OR target_user_id=?) AND created_at>=?
            """,
            (uid, uid, since),
        ).fetchone()
        return {
            "closed_count": int(row["closed_count"] or 0),
            "open_count": int(row["open_count"] or 0),
            "high_intensity_count": int(row["high_intensity_count"] or 0),
        }
    finally:
        conn.close()
