# codyclaw/automation/boot.py

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


async def execute_boot_scripts(
    dispatcher: "AgentDispatcher",
    router: "MessageRouter",
    event_bus: "EventBus",
) -> None:
    """Gateway 启动时执行所有 Agent 的 BOOT.md"""
    from codyclaw.automation.events import Event, EventType

    for agent_config in router.iter_agents():
        boot_path = (
            Path(agent_config.boot_file)
            if agent_config.boot_file
            else Path(agent_config.workdir) / "BOOT.md"
        )
        if not boot_path.exists():
            continue

        logger.info(f"Executing BOOT.md for agent: {agent_config.name}")
        try:
            content = boot_path.read_text(encoding="utf-8")
            client = await dispatcher.get_or_create_client(agent_config)

            # 用临时 session 执行，不污染用户会话
            boot_session_id = f"boot-{uuid.uuid4().hex[:8]}"

            prompt = (
                "你正在执行启动脚本（BOOT.md）。请严格按照以下指令执行：\n\n"
                f"{content}\n\n"
                "如果指令要求发送消息，请使用消息工具。"
                "执行完毕后简要总结你做了什么。"
            )

            result = await client.run(prompt, session_id=boot_session_id)
            logger.info(f"BOOT.md for {agent_config.name} completed: {result.output[:200]}")

        except Exception as e:
            # 失败不阻塞 Gateway 启动
            logger.warning(f"BOOT.md for {agent_config.name} failed: {e}")
            continue

    await event_bus.emit(Event(type=EventType.GATEWAY_STARTUP))
