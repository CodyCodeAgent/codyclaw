# codyclaw/gateway/tools.py
#
# Custom tools injected into Cody clients.
# Each factory closes over a live object (e.g. CronScheduler) so tools
# can operate on gateway state without going through HTTP.

import json
import logging
import re
import uuid
from pathlib import Path
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


def _managed_skills_dir() -> Path:
    """返回用户可写的 managed skill 目录（~/.codyclaw/skills/）。"""
    d = Path.home() / ".codyclaw" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_skill_tools(on_skill_changed):
    """Return tools for AI to create/list/remove skills.

    `on_skill_changed` is an async callback invoked after install/remove
    so the dispatcher can invalidate cached clients.
    """

    async def install_skill(
        ctx,
        name: str,
        description: str,
        content: str,
    ) -> str:
        """Create and install a new skill. The skill becomes active on the next message.

        A skill teaches the AI new capabilities or behavioral patterns via
        a SKILL.md instruction file. Use this to extend your own abilities.

        Args:
            name: Skill name, lowercase with hyphens (e.g. "code-reviewer").
            description: One-line description of what the skill does.
            content: The full Markdown body of the SKILL.md (instructions,
                     tool descriptions, examples, guidelines). Do NOT include
                     the YAML frontmatter — it is generated automatically.
        """
        name = re.sub(r'[^a-z0-9\-]', '-', name.lower().strip())
        if not name:
            return "Error: skill name is required."

        skill_dir = _managed_skills_dir() / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"---\n\n"
        )
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(frontmatter + content, encoding="utf-8")

        await on_skill_changed()
        return (
            f"Skill '{name}' installed at {skill_path}. "
            f"It will be active on the next message."
        )

    async def list_skills(ctx) -> str:
        """List all installed skills (both built-in and user-installed)."""
        builtin_dir = Path(__file__).parent.parent / "skills"
        managed_dir = _managed_skills_dir()

        skills = []
        for d in [builtin_dir, managed_dir]:
            if not d.exists():
                continue
            for skill_dir in sorted(d.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    source = "built-in" if d == builtin_dir else "installed"
                    # 读取 description from frontmatter
                    desc = ""
                    for line in skill_md.read_text(encoding="utf-8").splitlines():
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                    skills.append({
                        "name": skill_dir.name,
                        "source": source,
                        "description": desc,
                    })

        if not skills:
            return "No skills installed."
        return json.dumps(skills, ensure_ascii=False, indent=2)

    async def remove_skill(ctx, name: str) -> str:
        """Remove a user-installed skill. Built-in skills cannot be removed.

        Args:
            name: The skill name to remove.
        """
        skill_dir = _managed_skills_dir() / name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return f"Skill '{name}' not found in installed skills."

        # 删除整个 skill 目录
        import shutil
        shutil.rmtree(skill_dir)

        await on_skill_changed()
        return f"Skill '{name}' removed. Change takes effect on the next message."

    return [install_skill, list_skills, remove_skill]
