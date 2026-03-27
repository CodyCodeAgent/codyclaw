# codyclaw/automation/events.py

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    # 系统生命周期
    GATEWAY_STARTUP = "gateway.startup"
    GATEWAY_SHUTDOWN = "gateway.shutdown"

    # 消息事件
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"

    # Agent 事件
    AGENT_RUN_START = "agent.run.start"
    AGENT_RUN_END = "agent.run.end"
    AGENT_RUN_ERROR = "agent.run.error"
    AGENT_TOOL_CALL = "agent.tool.call"
    AGENT_TOOL_RESULT = "agent.tool.result"
    AGENT_APPROVAL_REQUEST = "agent.approval.request"
    AGENT_APPROVAL_RESOLVE = "agent.approval.resolve"

    # Cron 事件
    CRON_TASK_START = "cron.task.start"
    CRON_TASK_END = "cron.task.end"
    CRON_TASK_ERROR = "cron.task.error"

    # 配置事件
    CONFIG_RELOAD = "config.reload"


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    source: str = ""           # 事件来源标识


EventHandler = Callable[[Event], Awaitable[Any]]


class EventBus:
    """系统级事件总线"""

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = {}

    def on(self, event_type: EventType | str, handler: EventHandler) -> None:
        """注册事件处理器。支持精确匹配和前缀匹配"""
        key = str(event_type)
        self._handlers.setdefault(key, []).append(handler)

    def off(self, event_type: EventType | str, handler: EventHandler) -> None:
        """注销事件处理器"""
        key = str(event_type)
        if key in self._handlers:
            self._handlers[key] = [h for h in self._handlers[key] if h != handler]

    async def emit(self, event: Event) -> None:
        """触发事件，通知所有匹配的处理器"""
        key = str(event.type)

        # 精确匹配
        for handler in self._handlers.get(key, []):
            try:
                await handler(event)
            except Exception as e:
                logger.exception(f"Event handler error for {key}: {e}")

        # 前缀匹配（如 "agent" 匹配 "agent.run.start"）
        prefix = key.split(".")[0]
        if prefix != key:
            for handler in self._handlers.get(prefix, []):
                try:
                    await handler(event)
                except Exception as e:
                    logger.exception(f"Event handler error for {prefix}: {e}")
