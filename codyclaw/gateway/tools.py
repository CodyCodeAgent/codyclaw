# codyclaw/gateway/tools.py
#
# Custom tools injected into Cody clients.
# Each factory closes over a live object (e.g. CronScheduler) so tools
# can operate on gateway state without going through HTTP.

import json
import logging
import re
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronScheduler
    from codyclaw.channel.base import LarkChannel

logger = logging.getLogger(__name__)

# 匹配 @ou_xxxx 格式（AI 可能用这种简写），自动转为飞书 <at> 标签
_AT_OPEN_ID_RE = re.compile(r'@(ou_[a-f0-9]{32,})')


def _fix_mentions(text: str) -> str:
    """把 AI 写的 @ou_xxx 自动转成飞书识别的 <at user_id="ou_xxx">@</at>"""
    return _AT_OPEN_ID_RE.sub(r'<at user_id="\1">@</at>', text)


def make_feishu_tools(get_channel):
    """Return a list of Feishu messaging tools.

    `get_channel` is a zero-argument callable that returns the live
    LarkChannel instance (or None if not yet initialised).
    """

    async def feishu_send_text(
        ctx,
        chat_id: str,
        text: str,
        reply_to: str = "",
    ) -> str:
        """Send a text message to a Feishu chat.

        Args:
            chat_id: The Feishu chat ID (group chat_id or user open_id).
            text: The text content to send.
            reply_to: Optional message_id to reply to (quote the original message).
        """
        channel: "LarkChannel" = get_channel()
        if channel is None:
            return "Error: Feishu channel is not available."
        try:
            msg_id = await channel.send_text(
                chat_id, _fix_mentions(text), reply_to=reply_to or None
            )
            return f"Message sent. message_id={msg_id}"
        except Exception as e:
            return f"Error sending text: {e}"

    async def feishu_send_card(
        ctx,
        chat_id: str,
        title: str,
        content: str,
        color: str = "blue",
        reply_to: str = "",
    ) -> str:
        """Send a rich card message with Markdown support to a Feishu chat.

        Args:
            chat_id: The Feishu chat ID.
            title: Card header title.
            content: Card body in Markdown format.
            color: Header color: blue, green, red, orange, turquoise, grey.
            reply_to: Optional message_id to reply to.
        """
        channel: "LarkChannel" = get_channel()
        if channel is None:
            return "Error: Feishu channel is not available."
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [{"tag": "markdown", "content": _fix_mentions(content)}],
        }
        try:
            msg_id = await channel.send_card(chat_id, card, reply_to=reply_to or None)
            return f"Card sent. message_id={msg_id}"
        except Exception as e:
            return f"Error sending card: {e}"

    async def feishu_reply(
        ctx,
        message_id: str,
        text: str,
    ) -> str:
        """Reply to a specific Feishu message (quote-reply).

        Args:
            message_id: The message_id to reply to.
            text: The text content of the reply.
        """
        channel: "LarkChannel" = get_channel()
        if channel is None:
            return "Error: Feishu channel is not available."
        try:
            msg_id = await channel.send_text("", _fix_mentions(text), reply_to=message_id)
            return f"Reply sent. message_id={msg_id}"
        except Exception as e:
            return f"Error replying: {e}"

    async def feishu_add_reaction(
        ctx,
        message_id: str,
        emoji_type: str,
    ) -> str:
        """Add an emoji reaction to a Feishu message.

        Args:
            message_id: The message to react to.
            emoji_type: Emoji type string, e.g. THUMBSUP, DONE, SMILE, HEART,
                        THANKS, OK, MUSCLE, CLAP, FIRE, PARTY, CrossMark, THINKING.
        """
        channel: "LarkChannel" = get_channel()
        if channel is None:
            return "Error: Feishu channel is not available."
        try:
            reaction_id = await channel.add_reaction(message_id, emoji_type)
            return f"Reaction added. reaction_id={reaction_id}"
        except Exception as e:
            return f"Error adding reaction: {e}"

    return [feishu_send_text, feishu_send_card, feishu_reply, feishu_add_reaction]


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
