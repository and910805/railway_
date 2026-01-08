# db_tasks.py
import os
import sqlite3
import threading
from datetime import datetime

TASKS_DB_PATH = os.getenv("TASKS_DB_PATH", "/data/tasks.db")
_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(TASKS_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_user_id TEXT NOT NULL,
            line_display_name TEXT,
            -- 任務說明，例如：台北→高雄 1/27 早上 自強135 2張以上
            description TEXT,

            ride_date TEXT NOT NULL,        -- 2026/01/27
            start_station TEXT NOT NULL,    -- 1008-台北
            end_station TEXT NOT NULL,      -- 4220-高雄
            start_time TEXT NOT NULL,       -- 06:00
            end_time TEXT NOT NULL,         -- 12:00

            train_keyword TEXT NOT NULL,    -- 自強135
            min_seats INTEGER NOT NULL,     -- 2

            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_notify_at TEXT
        )
        """
    )
    return conn


# 全域 connection
_conn = _get_conn()


def create_task(
    line_user_id: str,
    line_display_name: str | None,
    description: str,
    ride_date: str,
    start_station: str,
    end_station: str,
    start_time: str,
    end_time: str,
    train_keyword: str,
    min_seats: int,
) -> int:
    """LINE 指令新增任務時呼叫，回傳 task_id"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        cur = _conn.execute(
            """
            INSERT INTO ticket_task (
                line_user_id, line_display_name, description,
                ride_date, start_station, end_station,
                start_time, end_time,
                train_keyword, min_seats,
                is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                line_user_id,
                line_display_name,
                description,
                ride_date,
                start_station,
                end_station,
                start_time,
                end_time,
                train_keyword,
                min_seats,
                now,
            ),
        )
        _conn.commit()
        return cur.lastrowid


def expire_past_tasks(today_yyyymmdd: str):
    """
    把已經「搭車日 < 今天」的任務關掉（is_active=0）
    today_yyyymmdd 例如 '2026-01-08'
    """
    with _lock:
        _conn.execute(
            """
            UPDATE ticket_task
               SET is_active = 0
             WHERE is_active = 1
               AND ride_date < ?
            """,
            (today_yyyymmdd.replace("-", "/"),),
        )
        _conn.commit()


def get_active_future_tasks(today_yyyymmdd: str) -> list[dict]:
    """
    取出所有「尚未過期 & 啟用中」的任務
    """
    with _lock:
        cur = _conn.execute(
            """
            SELECT *
              FROM ticket_task
             WHERE is_active = 1
               AND ride_date >= ?
             ORDER BY ride_date, start_time
            """,
            (today_yyyymmdd.replace("-", "/"),),
        )
        rows = cur.fetchall()
        tasks = []
        for r in rows:
            tasks.append(
                {
                    "id": r["id"],
                    "line_user_id": r["line_user_id"],
                    "line_display_name": r["line_display_name"],
                    "description": r["description"],
                    "ride_date": r["ride_date"],
                    "start_station": r["start_station"],
                    "end_station": r["end_station"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "train_keyword": r["train_keyword"],
                    "min_seats": r["min_seats"],
                    "is_active": r["is_active"],
                    "created_at": r["created_at"],
                    "last_notify_at": r["last_notify_at"],
                }
            )
        return tasks


def mark_notified(task_id: int):
    """有成功通知（有票了）時更新 last_notify_at"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _conn.execute(
            "UPDATE ticket_task SET last_notify_at = ? WHERE id = ?",
            (now, task_id),
        )
        _conn.commit()
