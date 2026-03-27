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
