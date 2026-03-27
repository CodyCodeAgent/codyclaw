# codyclaw/gateway/dispatcher.py

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from cody import AsyncCodyClient, Cody
from cody.sdk.types import DoneChunk, InteractionRequestChunk, TextDeltaChunk, ToolCallChunk

from codyclaw.automation.events import Event, EventBus, EventType
from codyclaw.channel.cards import build_streaming_card
from codyclaw.gateway.session_strategy import SessionManager
from codyclaw.gateway.tools import make_cron_tools, make_feishu_tools

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronScheduler
    from codyclaw.channel.base import IncomingMessage, LarkChannel
    from codyclaw.gateway.router import AgentConfig, MessageRouter

_SKILLS_DIR = str(Path(__file__).parent.parent / "skills")

logger = logging.getLogger(__name__)

_CARD_TITLE = {
    "running": "Agent 执行中...",
    "done": "执行完成",
    "error": "执行出错",
}

_FEISHU_SYSTEM_PROMPT = """\
你是一个运行在飞书中的 AI 助手。用户通过飞书消息与你对话。

## 重要规则

1. 你的纯文本输出用户**完全看不到**。你必须通过 feishu 工具发送消息。
2. 每条用户消息开头有 [Feishu context]，包含 chat_id、message_id、sender_name、mentions 等信息。

## 回复方式

- 简短回复：用 feishu_reply(message_id, text) 引用回复
- 长回复/格式化内容：用 feishu_send_card(chat_id, title, content) 发送卡片（支持 Markdown）
- 表情回应：用 feishu_add_reaction(message_id, emoji_type)

## @提及他人

在消息文本中用 `<at user_id="open_id">名字</at>` 格式来 @某人。
open_id 从 [Feishu context] 的 mentions 字段获取。
例如：`<at user_id="ou_abc123">小明</at> 你好！`

## 行为准则

- 自然对话，不要机械地复述你的能力
- 在群聊中注意区分谁在说话、谁被@了
- 直接回答问题或执行任务，不要解释你是怎么工作的
"""


@dataclass
class ActiveRun:
    """正在执行的 Agent 任务"""
    user_id: str
    chat_id: str
    agent_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    accumulated_text: str = ""
    source_message_id: Optional[str] = None  # 用户原始消息 ID


class AgentDispatcher:
    """Agent 执行调度器"""

    def __init__(
        self,
        channel: "LarkChannel",
        router: "MessageRouter",
        cody_config: Optional[dict] = None,
        event_bus: Optional[EventBus] = None,
        db_path: str = "",
    ):
        self._channel = channel
        self._router = router
        self._cody_config = cody_config or {}
        self._event_bus = event_bus
        self._db_path = db_path
        self._active_runs: dict[str, ActiveRun] = {}           # user_id → ActiveRun
        self._clients: dict[str, AsyncCodyClient] = {}          # agent_id → client
        self._client_locks: dict[str, asyncio.Lock] = {}        # agent_id → Lock（防竞态）
        self._sessions = SessionManager()                       # 会话生命周期管理
        self._cron_scheduler: Optional["CronScheduler"] = None
        self._cron_tools = make_cron_tools(lambda: self._cron_scheduler)
        self._feishu_tools = make_feishu_tools(lambda: self._channel)

    # -------------------------------------------------------------------------
    # Client 管理
    # -------------------------------------------------------------------------

    def set_cron_scheduler(self, scheduler: "CronScheduler") -> None:
        self._cron_scheduler = scheduler

    async def get_or_create_client(self, agent_config: "AgentConfig") -> AsyncCodyClient:
        """获取或创建 Agent 对应的 Cody Client（double-checked locking，防并发竞态）"""
        agent_id = agent_config.agent_id
        if agent_id not in self._client_locks:
            self._client_locks[agent_id] = asyncio.Lock()
        async with self._client_locks[agent_id]:
            if agent_id not in self._clients:
                cb = self._cody_config.get("circuit_breaker", {})
                builder = (
                    Cody()
                    .workdir(agent_config.workdir)
                    .model(agent_config.model)
                    .interaction(enabled=False)
                    .circuit_breaker(
                        max_tokens=cb.get("max_tokens", 500_000),
                        max_cost_usd=cb.get("max_cost_usd", 2.0),
                        loop_detect_turns=cb.get("loop_detect_turns", 6),
                    )
                    .skill_dir(_SKILLS_DIR)
                    .extra_system_prompt(_FEISHU_SYSTEM_PROMPT)
                )
                if self._db_path:
                    cody_db = str(
                        Path(self._db_path).parent / "agents" / agent_id / "cody.db"
                    )
                    builder = builder.db_path(cody_db)
                for tool in self._cron_tools:
                    builder = builder.tool(tool)
                for tool in self._feishu_tools:
                    builder = builder.tool(tool)
                builder = self._apply_cody_config(builder, self._cody_config)
                # 每个 Agent 的 api_key/base_url 优先级高于全局 cody 配置
                if agent_config.api_key and hasattr(builder, "api_key"):
                    builder = builder.api_key(agent_config.api_key)
                if agent_config.base_url and hasattr(builder, "base_url"):
                    builder = builder.base_url(agent_config.base_url)
                client = builder.build()
                await client.__aenter__()
                self._clients[agent_id] = client
        return self._clients[agent_id]

    def _apply_cody_config(self, builder, cody_config: dict):
        """将 config.yaml 的安全/权限配置应用到 Cody builder。
        使用 hasattr 安全检测，跳过当前 SDK 版本不支持的选项。
        """
        # 全局 API Key（model_api_key 优先，向后兼容；api_key 为新键）
        api_key = cody_config.get("model_api_key") or cody_config.get("api_key")
        if api_key and hasattr(builder, "api_key"):
            builder = builder.api_key(api_key)

        base_url = cody_config.get("base_url")
        if base_url and hasattr(builder, "base_url"):
            builder = builder.base_url(base_url)

        enable_thinking = cody_config.get("enable_thinking")
        if enable_thinking is not None and hasattr(builder, "thinking"):
            thinking_budget = cody_config.get("thinking_budget", 10000)
            builder = builder.thinking(enable_thinking, thinking_budget)

        security = cody_config.get("security", {})

        blocked = security.get("blocked_commands")
        if blocked and hasattr(builder, "blocked_commands"):
            builder = builder.blocked_commands(blocked)

        timeout = security.get("command_timeout")
        if timeout and hasattr(builder, "command_timeout"):
            builder = builder.command_timeout(timeout)

        permissions = cody_config.get("permissions", {})
        default_level = permissions.get("default_level")
        if default_level and hasattr(builder, "permission_level"):
            builder = builder.permission_level(default_level)

        overrides = permissions.get("overrides", {})
        if overrides and hasattr(builder, "tool_permission"):
            for tool, level in overrides.items():
                builder = builder.tool_permission(tool, level)

        return builder

    # -------------------------------------------------------------------------
    # Session 管理
    # -------------------------------------------------------------------------

    def _get_session_key(self, agent_config: "AgentConfig", msg: "IncomingMessage") -> str:
        """群聊以 chat_id 为 key（所有成员共享上下文），单聊以 user_id 为 key"""
        if msg.chat_type == "group":
            return f"{agent_config.agent_id}:{msg.chat_id}"
        return f"{agent_config.agent_id}:{msg.sender_id}"

    # -------------------------------------------------------------------------
    # 核心调度
    # -------------------------------------------------------------------------

    async def dispatch(self, msg: "IncomingMessage") -> None:
        """调度一条消息到对应的 Agent 执行。应通过 asyncio.create_task() 调用。"""
        agent_config = self._router.resolve(msg)
        if agent_config is None:
            return

        client = await self.get_or_create_client(agent_config)
        session_key = self._get_session_key(agent_config, msg)
        session_id = self._sessions.get(session_key)

        run = ActiveRun(
            user_id=msg.sender_id,
            chat_id=msg.chat_id,
            agent_id=agent_config.agent_id,
            source_message_id=msg.message_id,
        )
        self._active_runs[msg.sender_id] = run

        await self._emit(EventType.AGENT_RUN_START, {
            "agent_id": agent_config.agent_id,
            "user_id": msg.sender_id,
        })

        # 立刻打 🤔 表情，告诉用户消息已收到
        reaction_id = await self._add_reaction_safe(msg.message_id, "THINKING")

        # 注入飞书上下文，让 AI 知道当前对话环境
        mentions_str = ""
        if msg.mentions:
            mention_parts = [f'{m["name"]}(open_id={m["open_id"]})' for m in msg.mentions]
            mentions_str = f" mentions=[{', '.join(mention_parts)}]"
        context = (
            f"[Feishu context] chat_id={msg.chat_id} message_id={msg.message_id} "
            f"chat_type={msg.chat_type} sender_name={msg.sender_name}{mentions_str}\n\n"
            f"{msg.content}"
        )

        try:
            async for chunk in client.stream(
                context,
                session_id=session_id,
                cancel_event=run.cancel_event,
            ):
                if isinstance(chunk, TextDeltaChunk):
                    run.accumulated_text += chunk.content

                elif isinstance(chunk, ToolCallChunk):
                    await self._emit(EventType.AGENT_TOOL_CALL, {
                        "tool": chunk.tool_name,
                        "agent_id": agent_config.agent_id,
                    })

                elif isinstance(chunk, InteractionRequestChunk):
                    await self._handle_interaction_request(run, chunk, client)

                elif isinstance(chunk, DoneChunk):
                    if chunk.session_id:
                        self._sessions.set(session_key, chunk.session_id)

            # 完成：移除 🤔，打 ✅
            await self._replace_reaction_safe(msg.message_id, reaction_id, "DONE")
            await self._emit(EventType.AGENT_RUN_END, {
                "agent_id": agent_config.agent_id,
                "user_id": msg.sender_id,
            })

        except Exception as e:
            logger.exception(f"Agent execution failed: {e}")
            # 出错：移除 🤔，打 ❌
            await self._replace_reaction_safe(msg.message_id, reaction_id, "CrossMark")
            # 兜底发送错误通知
            try:
                error_card = build_streaming_card(
                    _CARD_TITLE["error"], f"执行出错: {str(e)}", "error"
                )
                await self._channel.send_card(
                    msg.chat_id, error_card, reply_to=msg.message_id
                )
            except Exception:
                logger.warning("Failed to send error card")
            await self._emit(EventType.AGENT_RUN_ERROR, {
                "agent_id": agent_config.agent_id,
                "error": str(e),
            })

        finally:
            self._active_runs.pop(msg.sender_id, None)

    async def cancel(self, user_id: str) -> bool:
        """取消用户当前的执行任务"""
        run = self._active_runs.get(user_id)
        if run:
            run.cancel_event.set()
            return True
        return False

    def get_agent(self, agent_id: str):
        """按 agent_id 查找 Agent 配置（委托给 Router）"""
        return self._router.get_agent(agent_id)

    def get_sessions(self) -> dict[str, str]:
        """供管理 API 使用"""
        return self._sessions.all()

    def get_session(self, key: str) -> Optional[str]:
        """获取指定 key 的 session_id"""
        return self._sessions.get(key)

    def set_session(self, key: str, session_id: str) -> None:
        """设置 session_id（供 Web Chat 使用）"""
        self._sessions.set(key, session_id)

    @property
    def active_run_count(self) -> int:
        """当前活跃的 Agent 执行数量"""
        return len(self._active_runs)

    async def shutdown(self) -> None:
        """取消所有活跃任务，然后关闭所有 Client"""
        for run in list(self._active_runs.values()):
            run.cancel_event.set()
        for client in self._clients.values():
            await client.__aexit__(None, None, None)
        self._clients.clear()

    # -------------------------------------------------------------------------
    # 内部辅助
    # -------------------------------------------------------------------------

    async def _handle_interaction_request(
        self,
        run: ActiveRun,
        chunk: InteractionRequestChunk,
        client: AsyncCodyClient,
    ) -> None:
        """Interaction 兜底：自动批准（interaction 已关闭，正常不会触发）。"""
        await client.submit_interaction(chunk.request_id, "approve")

    async def _add_reaction_safe(self, message_id: str, emoji_type: str) -> Optional[str]:
        """给消息打表情，返回 reaction_id（失败返回 None）"""
        try:
            return await self._channel.add_reaction(message_id, emoji_type)
        except Exception as e:
            logger.warning(f"Failed to add reaction {emoji_type}: {e}")
            return None

    async def _replace_reaction_safe(
        self, message_id: str, old_reaction_id: Optional[str], new_emoji: str
    ) -> None:
        """移除旧表情，打上新表情"""
        if old_reaction_id:
            try:
                await self._channel.remove_reaction(message_id, old_reaction_id)
            except Exception as e:
                logger.debug(f"Failed to remove reaction: {e}")
        try:
            await self._channel.add_reaction(message_id, new_emoji)
        except Exception as e:
            logger.debug(f"Failed to add reaction {new_emoji}: {e}")

    async def _emit(self, event_type: EventType, data: dict) -> None:
        """发射事件到 EventBus（若未配置则静默忽略）"""
        if self._event_bus:
            await self._event_bus.emit(Event(type=event_type, data=data, source="dispatcher"))
