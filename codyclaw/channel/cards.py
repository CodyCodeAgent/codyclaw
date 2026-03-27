# codyclaw/channel/cards.py

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
                "content": content[:4096],  # 飞书卡片内容上限
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
            {"tag": "markdown", "content": result[:4096]},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": f"下次执行: {next_run}"},
            ]},
        ],
    }
