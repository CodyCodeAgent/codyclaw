# codyclaw/channel/cards.py

_CARD_CONTENT_LIMIT = 4096
_TRUNCATION_SUFFIX = "\n\n...(内容过长，已截断)"


def _truncate(content: str) -> str:
    """截断超出飞书卡片限制的内容，并附加提示。"""
    if len(content) <= _CARD_CONTENT_LIMIT:
        return content
    return content[: _CARD_CONTENT_LIMIT - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def build_streaming_card(title: str, content: str, status: str = "running") -> dict:
    """构建流式输出卡片——Agent 执行过程中实时更新"""
    status_emoji = {"running": "⏳", "done": "✅", "error": "❌"}.get(status, "ℹ️")
    template_color = {"running": "blue", "done": "green", "error": "red"}.get(status, "grey")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{status_emoji} {title}"},
            "template": template_color,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": _truncate(content),
            },
            # 运行中时显示取消提示
            *([{
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "发送「取消」可终止执行"}],
            }] if status == "running" else []),
        ],
    }


def build_approval_card(command: str, agent_name: str) -> dict:
    """构建执行审批卡片——危险操作需要人工确认，通过消息回复交互"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔐 执行审批"},
            "template": "orange",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"**Agent**: {agent_name}\n"
                    f"**操作**: `{command}`\n\n"
                    f"请回复：**允许** / **拒绝** / **全部允许**"
                ),
            },
        ],
    }


def build_cron_result_card(task_name: str, result: str, next_run: str) -> dict:
    """构建定时任务结果卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⏰ {task_name}"},
            "template": "turquoise",
        },
        "elements": [
            {"tag": "markdown", "content": _truncate(result)},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": f"下次执行: {next_run}"},
            ]},
        ],
    }
