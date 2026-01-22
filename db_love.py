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
        print(f"[DB] 建立資料夾: {db_dir}", flush=True)
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(LOVE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # love lines
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS love_line (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            tags TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # date ideas
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS date_idea (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # wishes
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wish (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # moods
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mood (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # per-user settings
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_setting (
            line_user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (line_user_id, key)
        )
        """
    )

    # subscribers for push / couple roles
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriber (
            line_user_id TEXT PRIMARY KEY,
            display_name TEXT,
            role TEXT,              -- 'girlfriend' or 'boyfriend' (or null)
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    return conn


_conn = _get_conn()


def seed_defaults():
    """資料庫空的時候，塞一些預設情話/約會靈感"""
    with _lock:
        c1 = _conn.execute("SELECT COUNT(1) AS n FROM love_line").fetchone()["n"]
        c2 = _conn.execute("SELECT COUNT(1) AS n FROM date_idea").fetchone()["n"]

        if c1 == 0:
            defaults = [
                "今天也要記得喝水，因為我很在乎你。",
                "你不用很厲害才值得被愛，你本來就值得。",
                "我喜歡你不是因為你完美，是因為你是你。",
                "如果你累了，就靠著我一下，什麼都不用說。",
                "你的情緒我都接得住，慢慢來沒關係。",
                "我想把今天的好心情都分你一半。",
                "你一出現，我的世界就變得更溫柔。",
                "我不是要你堅強，我是想陪你一起面對。",
                "你做得很好了，真的。",
                "你笑的時候，我會不自覺跟著安心。",
                "你不用一直撐著，我在。",
                "我想你了，是真的那種想。",
                "你是我今天最想見到的人。",
                "你的存在本身就是一件很美好的事。",
                "我願意把耐心都留給你。",
                "就算今天很糟，也還有我在你這邊。",
                "你不用逞強，我喜歡你最真實的樣子。",
                "我們慢慢走，但一定一起走。",
                "你辛苦了，現在可以休息一下。",
                "我喜歡你，今天比昨天多一點點。",
            ]
            _conn.executemany(
                "INSERT INTO love_line(text, tags, created_at) VALUES(?, ?, ?)",
                [(t, None, _now()) for t in defaults],
            )

        if c2 == 0:
            ideas = [
                "下班後去散步 30 分鐘，走到一間沒去過的小店",
                "一起去拍一組「今天的天空」：各自拍 3 張互相分享",
                "吃完晚餐去便利商店各挑 1 個對方會喜歡的東西",
                "找一間咖啡廳，各自寫 5 件最近開心的小事交換",
                "在家做簡單料理：煎蛋/炒青菜/泡麵升級版也行",
                "看一部你們都沒看過的電影，結束後互相打分數",
                "一起整理相簿：挑 10 張最喜歡的合照做成小合集",
                "選一首歌，彼此說「為什麼想到對方」",
                "去書店各挑一本書，交換讀 10 頁講心得",
                "在家玩桌遊/撲克牌，輸的人完成一個小任務",
                "晚餐後去超市買水果，回家切盤當甜點",
                "一起規劃下一次小旅行：景點/交通/吃什麼",
            ]
            _conn.executemany(
                "INSERT INTO date_idea(text, created_at) VALUES(?, ?)",
                [(t, _now()) for t in ideas],
            )

        _conn.commit()


# ====== subscriber / role ======
def upsert_subscriber(user_id: str, display_name: str = ""):
    with _lock:
        _conn.execute(
            """
            INSERT INTO subscriber(line_user_id, display_name, role, is_active, created_at, updated_at)
            VALUES(?, ?, NULL, 1, ?, ?)
            ON CONFLICT(line_user_id) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (user_id, display_name, _now(), _now()),
        )
        _conn.commit()


def set_role(user_id: str, role: str):
    with _lock:
        _conn.execute(
            """
            UPDATE subscriber
            SET role=?, updated_at=?
            WHERE line_user_id=?
            """,
            (role, _now(), user_id),
        )
        _conn.commit()


def set_active(user_id: str, active: bool):
    with _lock:
        _conn.execute(
            """
            UPDATE subscriber
            SET is_active=?, updated_at=?
            WHERE line_user_id=?
            """,
            (1 if active else 0, _now(), user_id),
        )
        _conn.commit()


def get_couple_user_ids() -> list[str]:
    """
    回傳目前被標記成 girlfriend/boyfriend 且 is_active=1 的 user_ids
    """
    with _lock:
        rows = _conn.execute(
            """
            SELECT line_user_id FROM subscriber
            WHERE is_active=1 AND role IN ('girlfriend','boyfriend')
            ORDER BY role DESC
            """
        ).fetchall()
        return [r["line_user_id"] for r in rows]


# ====== love lines ======
def add_love_line(text: str, tags: Optional[str] = None) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO love_line(text, tags, created_at) VALUES(?, ?, ?)",
            (text.strip(), tags, _now()),
        )
        _conn.commit()
        return int(cur.lastrowid)


def delete_love_line(line_id: int) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM love_line WHERE id = ?", (int(line_id),))
        _conn.commit()
        return cur.rowcount > 0


def list_love_lines(limit: int = 20) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT id, text, tags, created_at FROM love_line ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]


def random_love_line(tag: Optional[str] = None) -> Optional[dict]:
    with _lock:
        if tag:
            row = _conn.execute(
                "SELECT id, text, tags, created_at FROM love_line WHERE tags LIKE ? ORDER BY RANDOM() LIMIT 1",
                (f"%{tag}%",),
            ).fetchone()
        else:
            row = _conn.execute(
                "SELECT id, text, tags, created_at FROM love_line ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        return dict(row) if row else None


# ====== date ideas ======
def add_date_idea(text: str) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO date_idea(text, created_at) VALUES(?, ?)",
            (text.strip(), _now()),
        )
        _conn.commit()
        return int(cur.lastrowid)


def random_date_idea() -> Optional[dict]:
    with _lock:
        row = _conn.execute(
            "SELECT id, text, created_at FROM date_idea ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ====== wishes ======
def add_wish(line_user_id: str, text: str) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO wish(line_user_id, text, created_at) VALUES(?, ?, ?)",
            (line_user_id, text.strip(), _now()),
        )
        _conn.commit()
        return int(cur.lastrowid)


def list_wishes(line_user_id: str, limit: int = 10) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT id, text, created_at FROM wish WHERE line_user_id=? ORDER BY id DESC LIMIT ?",
            (line_user_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]


# ====== moods ======
def add_mood(line_user_id: str, text: str) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO mood(line_user_id, text, created_at) VALUES(?, ?, ?)",
            (line_user_id, text.strip(), _now()),
        )
        _conn.commit()
        return int(cur.lastrowid)


def list_moods(line_user_id: str, limit: int = 10) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT id, text, created_at FROM mood WHERE line_user_id=? ORDER BY id DESC LIMIT ?",
            (line_user_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]


# ====== settings ======
def set_setting(line_user_id: str, key: str, value: str):
    with _lock:
        _conn.execute(
            """
            INSERT INTO user_setting(line_user_id, key, value, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(line_user_id, key)
            DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (line_user_id, key, value, _now()),
        )
        _conn.commit()


def get_setting(line_user_id: str, key: str) -> Optional[str]:
    with _lock:
        row = _conn.execute(
            "SELECT value FROM user_setting WHERE line_user_id=? AND key=?",
            (line_user_id, key),
        ).fetchone()
        return row["value"] if row else None
