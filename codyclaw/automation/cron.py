# codyclaw/automation/cron.py

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from codyclaw.channel.cards import build_cron_result_card
from codyclaw.db import delete_cron_task, save_cron_run, save_cron_task

if TYPE_CHECKING:
    from codyclaw.channel.base import LarkChannel
    from codyclaw.gateway.dispatcher import AgentDispatcher

logger = logging.getLogger(__name__)


@dataclass
class CronTask:
    """定时任务定义"""
    task_id: str
    name: str
    agent_id: str                    # 执行任务的 Agent
    prompt: str                      # 自然语言指令
    schedule: str                    # cron 表达式 或 interval 描述
    notify_chat_id: Optional[str] = None  # 结果推送到哪个飞书会话
    enabled: bool = True
    timezone: str = "Asia/Shanghai"


class CronScheduler:
    """定时任务调度器"""

    def __init__(self, dispatcher: "AgentDispatcher", channel: "LarkChannel", db_path: str = ""):
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._dispatcher = dispatcher
        self._channel = channel
        self._tasks: dict[str, CronTask] = {}
        self._db_path = db_path

    def add_task(self, task: CronTask, persist: bool = False) -> None:
        """添加定时任务。persist=True 时写入 DB（AI 动态创建时使用）。"""
        self._tasks[task.task_id] = task
        if persist and self._db_path:
            save_cron_task(self._db_path, task)

        if not task.enabled:
            logger.info(f"Skipping disabled cron task: {task.name}")
            return

        schedule_lower = task.schedule.lower()
        if (
            "every" in schedule_lower
            or task.schedule.isdigit()
            or schedule_lower.endswith(("h", "m"))
        ):
            # 简单间隔模式：如 "every 30m"、"every 2h"、"60"(分钟)、"30m"、"2h"
            minutes = self._parse_interval(task.schedule)
            trigger = IntervalTrigger(minutes=minutes)
        else:
            # Cron 表达式模式：如 "0 8 * * *"（每天 8:00）
            trigger = CronTrigger.from_crontab(task.schedule, timezone=task.timezone)

        self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task.task_id,
            args=[task],
            name=task.name,
            replace_existing=True,
        )
        logger.info(f"Registered cron task: {task.name} ({task.schedule})")

    async def _execute_task(self, task: CronTask) -> None:
        """执行定时任务"""
        logger.info(f"Executing cron task: {task.name}")
        started_at = time.time()
        try:
            agent_config = self._dispatcher.get_agent(task.agent_id)
            if not agent_config:
                msg = f"Agent '{task.agent_id}' not found"
                logger.error(f"Cron task {task.name}: {msg}")
                if self._db_path:
                    save_cron_run(self._db_path, task.task_id, task.name,
                                  started_at, time.time() - started_at, "error", None, msg)
                return

            client = await self._dispatcher.get_or_create_client(agent_config)

            # 通过 dispatcher 的 SessionManager 持久化 cron session
            session_key = f"cron:{task.task_id}"
            session_id = self._dispatcher.get_session(session_key)

            result = await client.run(task.prompt, session_id=session_id)

            # 保存返回的 session_id（首次创建或 Cody 内部变更时）
            if result.session_id:
                self._dispatcher.set_session(session_key, result.session_id)

            # 记录执行结果（在飞书推送之前，避免推送失败污染运行状态）
            if self._db_path:
                output = (result.output or "")[:2000] or None
                save_cron_run(self._db_path, task.task_id, task.name,
                              started_at, time.time() - started_at, "success", output, None)

            # 推送结果到飞书（失败只记日志，不影响运行记录）
            if task.notify_chat_id and result.output:
                try:
                    next_run = self._scheduler.get_job(task.task_id).next_run_time
                    card = build_cron_result_card(
                        task_name=task.name,
                        result=result.output,
                        next_run=next_run.strftime("%Y-%m-%d %H:%M") if next_run else "未知",
                    )
                    await self._channel.send_card(task.notify_chat_id, card)
                except Exception as notify_err:
                    logger.warning(f"Cron task {task.name}: failed to send Feishu notification: {notify_err}")

        except Exception as e:
            logger.exception(f"Cron task {task.name} failed: {e}")
            if self._db_path:
                save_cron_run(self._db_path, task.task_id, task.name,
                              started_at, time.time() - started_at, "error", None, str(e)[:1000])
            if task.notify_chat_id:
                try:
                    await self._channel.send_text(
                        task.notify_chat_id,
                        f"⚠️ 定时任务 [{task.name}] 执行失败: {str(e)}",
                    )
                except Exception as notify_err:
                    logger.warning(f"Cron task {task.name}: failed to send error notification: {notify_err}")

    def update_task(self, task_id: str, **kwargs) -> bool:
        """Update fields of an existing task and reschedule it. Returns False if not found.
        Raises ValueError if the new schedule expression is invalid (state is not mutated).
        """
        if task_id not in self._tasks:
            return False
        task = self._tasks[task_id]

        # Validate the new schedule BEFORE touching any state.
        new_schedule = kwargs.get("schedule", task.schedule)
        new_enabled = kwargs.get("enabled", task.enabled)
        trigger = None
        if new_enabled:
            schedule_lower = new_schedule.lower()
            if (
                "every" in schedule_lower
                or new_schedule.isdigit()
                or schedule_lower.endswith(("h", "m"))
            ):
                trigger = IntervalTrigger(minutes=self._parse_interval(new_schedule))
            else:
                # Raises ValueError for invalid cron expression — no state has changed yet.
                trigger = CronTrigger.from_crontab(new_schedule, timezone=task.timezone)

        # Schedule is valid (or task is being disabled). Now mutate state.
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        if self._db_path:
            save_cron_task(self._db_path, task)
        job = self._scheduler.get_job(task_id)
        if job:
            job.remove()
        if trigger is not None:
            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=task.task_id,
                args=[task],
                name=task.name,
                replace_existing=True,
            )
        return True

    def remove_task(self, task_id: str) -> bool:
        """删除定时任务，返回是否找到并删除"""
        if task_id not in self._tasks:
            return False
        self._tasks.pop(task_id)
        if self._db_path:
            delete_cron_task(self._db_path, task_id)
        job = self._scheduler.get_job(task_id)
        if job:
            job.remove()
        return True

    @property
    def tasks(self) -> dict[str, CronTask]:
        return self._tasks

    def get_job(self, task_id: str):
        return self._scheduler.get_job(task_id)

    def start(self) -> None:
        self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    @staticmethod
    def _parse_interval(schedule: str) -> int:
        """解析简单间隔描述为分钟数。格式不合法时回退为 60 分钟。"""
        schedule = schedule.lower().replace("every", "").strip()
        try:
            if schedule.endswith("h"):
                return int(schedule[:-1]) * 60
            elif schedule.endswith("m"):
                return int(schedule[:-1])
            elif schedule.isdigit():
                return int(schedule)
        except ValueError:
            logger.warning(f"Invalid interval format '{schedule}', defaulting to 60 minutes")
        return 60  # 默认 1 小时
