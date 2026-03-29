# codyclaw/db.py
#
# SQLite 数据库初始化与 CRUD。
# 所有操作均为同步（SQLite 极快，且这些操作不在热路径上）。

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronTask

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS cron_tasks (
    task_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    schedule        TEXT NOT NULL,
    notify_chat_id  TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    timezone        TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    session_key     TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_map (
    session_key     TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    updated_at      REAL NOT NULL
);
"""


def init_db(db_path: str) -> None:
    """建库建表（幂等）"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_DDL)
    logger.info(f"Database initialised at {db_path}")


def load_cron_tasks(db_path: str) -> list[dict]:
    """读取所有 cron 任务，返回 dict 列表（可直接用于构造 CronTask）"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT task_id, name, agent_id, prompt, schedule, "
            "notify_chat_id, enabled, timezone FROM cron_tasks"
        ).fetchall()
    return [
        {
            "task_id": r["task_id"],
            "name": r["name"],
            "agent_id": r["agent_id"],
            "prompt": r["prompt"],
            "schedule": r["schedule"],
            "notify_chat_id": r["notify_chat_id"] or None,
            "enabled": bool(r["enabled"]),
            "timezone": r["timezone"],
        }
        for r in rows
    ]


def save_cron_task(db_path: str, task: "CronTask") -> None:
    """INSERT OR REPLACE 一条 cron 任务"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cron_tasks "
            "(task_id, name, agent_id, prompt, schedule, notify_chat_id, enabled, timezone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.task_id,
                task.name,
                task.agent_id,
                task.prompt,
                task.schedule,
                task.notify_chat_id,
                int(task.enabled),
                task.timezone,
            ),
        )


def delete_cron_task(db_path: str, task_id: str) -> None:
    """删除一条 cron 任务"""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM cron_tasks WHERE task_id = ?", (task_id,))


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------

def save_chat_message(db_path: str, msg: dict) -> None:
    """保存一条 Web 聊天消息"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chat_messages "
            "(id, agent_id, session_key, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                msg["id"],
                msg["agent_id"],
                msg["session_key"],
                msg["role"],
                msg["content"],
                msg["created_at"],
            ),
        )


# ---------------------------------------------------------------------------
# Session map (持久化 session_key → session_id 映射，重启后可恢复)
# ---------------------------------------------------------------------------

def save_session(db_path: str, session_key: str, session_id: str, updated_at: float) -> None:
    """保存或更新 session 映射"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO session_map (session_key, session_id, updated_at) "
            "VALUES (?, ?, ?)",
            (session_key, session_id, updated_at),
        )


def load_sessions(db_path: str) -> list[dict]:
    """加载所有 session 映射"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT session_key, session_id, updated_at FROM session_map"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_session(db_path: str, session_key: str) -> None:
    """删除一条 session 映射"""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM session_map WHERE session_key = ?", (session_key,))


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------

def load_chat_messages(
    db_path: str,
    agent_id: str = "",
    session_key: str = "",
    limit: int = 100,
) -> list[dict]:
    """加载聊天历史"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM chat_messages WHERE 1=1"
        params: list = []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if session_key:
            sql += " AND session_key = ?"
            params.append(session_key)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in reversed(rows)]
