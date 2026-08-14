"""按 user_id 存储最近对话轮次（SQLite，每用户环形窗口）。"""
from __future__ import annotations

import sqlite3
import threading
import time


class SessionStore:
    def __init__(self, db_path: str, max_turns_per_user: int = 50):
        self._lock = threading.Lock()
        self.max_turns = max_turns_per_user
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS turns(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id TEXT NOT NULL,
                 role TEXT NOT NULL,
                 content TEXT NOT NULL,
                 ts REAL NOT NULL
               )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id, id)"
        )
        self._conn.commit()

    def push(self, user_id: str, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns(user_id, role, content, ts) VALUES(?,?,?,?)",
                (user_id, role, content, time.time()),
            )
            self._conn.execute(
                "DELETE FROM turns WHERE user_id=? AND id NOT IN "
                "(SELECT id FROM turns WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                (user_id, user_id, self.max_turns),
            )
            self._conn.commit()

    def recent(self, user_id: str, n: int) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM turns WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, n),
            ).fetchall()
        return list(reversed([(role, content) for role, content in rows]))
