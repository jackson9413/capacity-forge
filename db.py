"""SQLite session log for capacity-forge."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CAPACITY_FORGE_DB", os.path.join(BASE_DIR, "data", "capacity.db"))


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT,
                brief TEXT,
                score REAL,
                verdict TEXT,
                result_json TEXT NOT NULL,
                self_rating INTEGER,
                notes TEXT
            )"""
        )


def save_session(title: str, brief: str, result: Dict[str, Any]) -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (created_at, title, kind, brief, score, verdict, result_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                title[:200],
                result.get("meta", {}).get("kind", "api"),
                brief,
                float(result.get("overall", 0)),
                result.get("verdict", ""),
                json.dumps(result),
            ),
        )
        return int(cur.lastrowid)


def list_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title, kind, score, verdict, self_rating, notes "
            "FROM sessions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(sid: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (int(sid),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    try:
        data["result"] = json.loads(data.pop("result_json"))
    except Exception:
        data["result"] = {}
    return data


def update_session(sid: int, self_rating: Optional[int] = None, notes: Optional[str] = None) -> bool:
    sets, vals = [], []
    if self_rating is not None:
        sets.append("self_rating = ?")
        vals.append(int(self_rating))
    if notes is not None:
        sets.append("notes = ?")
        vals.append(notes)
    if not sets:
        return False
    vals.append(int(sid))
    with _db() as conn:
        cur = conn.execute("UPDATE sessions SET %s WHERE id = ?" % ", ".join(sets), vals)
    return cur.rowcount > 0


def delete_session(sid: int) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (int(sid),))
    return cur.rowcount > 0
