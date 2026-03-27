# codyclaw/gateway/tools.py
#
# Custom tools injected into Cody clients.
# Each factory closes over a live object (e.g. CronScheduler) so tools
# can operate on gateway state without going through HTTP.

import json
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronScheduler

logger = logging.getLogger(__name__)


def make_cron_tools(get_scheduler):
    """Return a list of cron-management tools.

    `get_scheduler` is a zero-argument callable that returns the live
    CronScheduler instance (or None if not yet initialised).  Using a
    getter instead of a direct reference lets us inject the scheduler
    after the dispatcher is constructed.
    """

    async def create_cron_task(
        ctx,
        task_id: str,
        name: str,
        agent_id: str,
        prompt: str,
        schedule: str,
        notify_chat_id: str = "",
    ) -> str:
        """Create a new scheduled cron task.

        Args:
            task_id: Unique identifier, lowercase with hyphens.
            name: Human-readable task name.
            agent_id: ID of the agent that will execute the task.
            prompt: Instruction the agent runs on each execution.
            schedule: Cron expression (\"0 9 * * 1-5\") or interval (\"every 30m\", \"2h\").
            notify_chat_id: Feishu chat ID to send results to (optional).
        """
        scheduler: "CronScheduler" = get_scheduler()
        if scheduler is None:
            return "Error: CronScheduler is not available yet."

        from codyclaw.automation.cron import CronTask

        task = CronTask(
            task_id=task_id or f"task-{uuid.uuid4().hex[:8]}",
            name=name,
            agent_id=agent_id,
            prompt=prompt,
            schedule=schedule,
            notify_chat_id=notify_chat_id or None,
        )
        try:
            scheduler.add_task(task, persist=True)
        except Exception as e:
            return f"Error creating task: {e}"

        return f"Cron task '{name}' ({task_id}) created with schedule '{schedule}'."

    async def list_cron_tasks(ctx) -> str:
        """List all scheduled cron tasks and their next run times."""
        scheduler: "CronScheduler" = get_scheduler()
        if scheduler is None:
            return "Error: CronScheduler is not available yet."

        tasks = []
        for task in scheduler.tasks.values():
            job = scheduler.get_job(task.task_id)
            next_run = (
                job.next_run_time.strftime("%Y-%m-%d %H:%M")
                if job and job.next_run_time
                else "disabled"
            )
            tasks.append({
                "task_id": task.task_id,
                "name": task.name,
                "agent_id": task.agent_id,
                "schedule": task.schedule,
                "enabled": task.enabled,
                "next_run": next_run,
            })

        if not tasks:
            return "No cron tasks scheduled."
        return json.dumps(tasks, ensure_ascii=False, indent=2)

    async def delete_cron_task(ctx, task_id: str) -> str:
        """Delete a scheduled cron task by its ID.

        Args:
            task_id: The task ID to delete.
        """
        scheduler: "CronScheduler" = get_scheduler()
        if scheduler is None:
            return "Error: CronScheduler is not available yet."

        removed = scheduler.remove_task(task_id)
        if removed:
            return f"Cron task '{task_id}' deleted."
        return f"Task '{task_id}' not found."

    return [create_cron_task, list_cron_tasks, delete_cron_task]
