# codyclaw/gateway/dispatcher.py

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from cody import AsyncCodyClient, Cody
from cody.core.memory import ProjectMemoryStore
from cody.sdk.types import DoneChunk, InteractionRequestChunk, TextDeltaChunk, ToolCallChunk

from codyclaw.automation.events import Event, EventBus, EventType
from codyclaw.channel.cards import build_streaming_card
from codyclaw.gateway.session_strategy import SessionManager
from codyclaw.gateway.tools import make_cron_tools, make_feishu_tools, make_skill_tools

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronScheduler
    from codyclaw.channel.base import IncomingMessage, LarkChannel
    from codyclaw.gateway.router import AgentConfig, MessageRouter

_BUILTIN_SKILLS_DIR = str(Path(__file__).parent.parent / "skills")
_MANAGED_SKILLS_DIR = str(Path.home() / ".codyclaw" / "skills")

logger = logging.getLogger(__name__)

# 流式卡片更新节流间隔（秒）
_CARD_UPDATE_INTERVAL = 1.5

_CARD_TITLE = {
    "running": "Agent 执行中...",
    "done": "执行完成",
    "error": "执行出错",
}

_FEISHU_SYSTEM_PROMPT = """\
你是一个运行在飞书中的 AI 助手。用户通过飞书消息与你对话。

## 上下文格式

每条用户消息包含以下结构：
1. `[Feishu context]` 行：chat_id、message_id、sender_name、chat_type、mentions
2. `[Recent chat history]`（群聊时提供）：群内最近的对话记录，帮助你理解讨论背景
3. 当前用户消息正文

## 回复方式

你的文字输出会自动以卡片形式发送给用户，**无需手动调用工具来回复**。直接输出你的回答即可。

以下工具仅在需要**额外**操作时使用：
- feishu_send_text / feishu_send_card：向其他会话或同一会话发送额外消息
- feishu_reply：引用回复某条特定消息
- feishu_add_reaction：给消息加表情回应

## @提及他人

在消息文本中用 `<at user_id="open_id">名字</at>` 格式来 @某人。
open_id 可从 [Feishu context] 的 mentions 字段或 [Recent chat history] 中获取。
例如：`<at user_id="ou_abc123">小明</at> 你好！`

## 行为准则

- 自然对话，像一个真实的群成员一样参与讨论
- 仔细阅读聊天历史，理解当前讨论的上下文和语境
- 在群聊中注意区分谁在说话、谁被@了、谁在和谁对话
- 如果历史记录中有人提到了某个话题，用户的问题很可能与此相关
- 直接回答问题或执行任务，不要解释你是怎么工作的
"""

# 群聊历史拉取条数
_GROUP_HISTORY_COUNT = 15


@dataclass
class ActiveRun:
    """正在执行的 Agent 任务"""
    user_id: str
    chat_id: str
    agent_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    accumulated_text: str = ""
    source_message_id: Optional[str] = None  # 用户原始消息 ID
    # 流式卡片状态
    card_message_id: Optional[str] = None  # 已发送的流式卡片 message_id
    last_card_update: float = 0.0  # 上次卡片更新时间戳
    tool_calls: list[str] = field(default_factory=list)  # 已调用的工具名列表
    has_sent_feishu_message: bool = False  # AI 是否主动通过工具发了消息


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
        self._skill_tools = make_skill_tools(self._on_skill_changed)

    # -------------------------------------------------------------------------
    # Client 管理
    # -------------------------------------------------------------------------

    def set_cron_scheduler(self, scheduler: "CronScheduler") -> None:
        self._cron_scheduler = scheduler

    async def _on_skill_changed(self) -> None:
        """Skill 安装/删除后调用：清除空闲的缓存 client，下条消息时自动重建。

        正在执行中的 client 不会被关闭（dispatch 持有引用），
        仅从缓存移除，执行结束后自然释放。
        """
        active_agent_ids = {r.agent_id for r in self._active_runs.values()}
        to_close = {
            aid: client for aid, client in self._clients.items()
            if aid not in active_agent_ids
        }
        for aid, client in to_close.items():
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
        # 从缓存中移除所有 client（包括在用的），下次 get_or_create_client 会重建
        self._clients.clear()
        logger.info("Agent clients invalidated due to skill change")

    async def get_or_create_client(self, agent_config: "AgentConfig") -> AsyncCodyClient:
        """获取或创建 Agent 对应的 Cody Client（double-checked locking，防并发竞态）"""
        agent_id = agent_config.agent_id
        if agent_id not in self._client_locks:
            self._client_locks[agent_id] = asyncio.Lock()
        async with self._client_locks[agent_id]:
            if agent_id not in self._clients:
                cb = self._cody_config.get("circuit_breaker", {})
                # 拼接系统提示词：通用 + Agent 专属
                system_prompt = _FEISHU_SYSTEM_PROMPT
                if agent_config.system_prompt:
                    system_prompt += f"\n\n## Agent 专属指令\n\n{agent_config.system_prompt}"
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
                    .skill_dirs([_BUILTIN_SKILLS_DIR, _MANAGED_SKILLS_DIR])
                    .extra_system_prompt(system_prompt)
                )
                # 启用持久化记忆——AI 可以跨会话记住用户偏好和项目知识
                memory_dir = Path(self._db_path).parent if self._db_path else None
                builder = builder.memory_store(
                    ProjectMemoryStore.from_workdir(
                        Path(agent_config.workdir), base_dir=memory_dir
                    )
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
                for tool in self._skill_tools:
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

        context_parts = [
            f"[Feishu context] chat_id={msg.chat_id} message_id={msg.message_id} "
            f"chat_type={msg.chat_type} sender_name={msg.sender_name}{mentions_str}",
        ]

        # 群聊时拉取最近聊天记录，注入为上下文
        if msg.chat_type == "group":
            history = await self._fetch_history_safe(msg.chat_id, msg.message_id)
            if history:
                context_parts.append(self._format_chat_history(history))

        context_parts.append(f"{msg.sender_name}: {msg.content}")
        context = "\n\n".join(context_parts)

        try:
            async for chunk in client.stream(
                context,
                session_id=session_id,
                cancel_event=run.cancel_event,
            ):
                if isinstance(chunk, TextDeltaChunk):
                    run.accumulated_text += chunk.content
                    await self._update_streaming_card(run, msg)

                elif isinstance(chunk, ToolCallChunk):
                    # 跟踪是否调了飞书发消息工具（send_text/send_card/reply 都算）
                    if chunk.tool_name and chunk.tool_name.startswith(
                        ("feishu_send", "feishu_reply")
                    ):
                        run.has_sent_feishu_message = True
                    run.tool_calls.append(chunk.tool_name or "unknown")
                    await self._emit(EventType.AGENT_TOOL_CALL, {
                        "tool": chunk.tool_name,
                        "agent_id": agent_config.agent_id,
                    })
                    # 在流式卡片中显示工具调用进度
                    await self._update_streaming_card(run, msg, force=True)

                elif isinstance(chunk, InteractionRequestChunk):
                    await self._handle_interaction_request(run, chunk, client)

                elif isinstance(chunk, DoneChunk):
                    if chunk.session_id:
                        self._sessions.set(session_key, chunk.session_id)

            # 完成：最终更新卡片状态
            await self._finalize_streaming_card(run, msg)
            # 移除 🤔，打 ✅
            await self._replace_reaction_safe(msg.message_id, reaction_id, "DONE")
            await self._emit(EventType.AGENT_RUN_END, {
                "agent_id": agent_config.agent_id,
                "user_id": msg.sender_id,
            })

        except Exception as e:
            logger.exception(f"Agent execution failed: {e}")
            # 出错：移除 🤔，打 ❌
            await self._replace_reaction_safe(msg.message_id, reaction_id, "CrossMark")
            # 更新流式卡片为错误状态，或发送新的错误卡片
            await self._error_streaming_card(run, msg, e)
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

    async def _fetch_history_safe(
        self, chat_id: str, current_message_id: str
    ) -> list[dict]:
        """安全拉取群聊历史，失败时返回空列表（不阻塞主流程）。"""
        try:
            history = await self._channel.fetch_chat_history(
                chat_id, count=_GROUP_HISTORY_COUNT
            )
            # 排除当前消息（避免重复）
            return [h for h in history if h.get("message_id") != current_message_id]
        except Exception as e:
            logger.debug(f"Failed to fetch chat history: {e}")
            return []

    @staticmethod
    def _format_chat_history(history: list[dict]) -> str:
        """将聊天记录格式化为 AI 可读的上下文。"""
        lines = ["[Recent chat history]"]
        for h in history:
            name = h.get("sender_name", "unknown")
            text = h.get("content", "")
            lines.append(f"  {name}: {text}")
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # 流式卡片
    # -------------------------------------------------------------------------

    def _build_card_content(self, run: ActiveRun, status: str = "running") -> str:
        """拼接卡片正文：accumulated_text + 工具调用进度。"""
        parts = []
        if run.accumulated_text.strip():
            parts.append(run.accumulated_text.strip())
        if status == "running" and run.tool_calls:
            tool_lines = []
            for t in run.tool_calls:
                tool_lines.append(f"  ✓ {t}")
            parts.append("**🔧 工具调用**\n" + "\n".join(tool_lines))
        return "\n\n".join(parts) if parts else "⏳ 思考中..."

    async def _update_streaming_card(
        self, run: ActiveRun, msg: "IncomingMessage", force: bool = False
    ) -> None:
        """节流更新流式卡片：首次发送新卡片，后续每 1.5s 更新一次。"""
        now = time.monotonic()
        if not force and (now - run.last_card_update) < _CARD_UPDATE_INTERVAL:
            return

        content = self._build_card_content(run, "running")
        card = build_streaming_card(_CARD_TITLE["running"], content, "running")

        try:
            if run.card_message_id is None:
                # 首次：发送新卡片（引用回复用户消息）
                run.card_message_id = await self._channel.send_card(
                    msg.chat_id, card, reply_to=msg.message_id
                )
            else:
                # 后续：更新同一张卡片
                await self._channel.update_card(run.card_message_id, card)
            run.last_card_update = now
        except Exception as e:
            logger.debug(f"Failed to update streaming card: {e}")

    async def _finalize_streaming_card(
        self, run: ActiveRun, msg: "IncomingMessage"
    ) -> None:
        """执行结束时将卡片更新为最终状态，或发送兜底回复。"""
        content = run.accumulated_text.strip()

        if run.card_message_id:
            # 已有流式卡片 → 更新为完成状态
            if content:
                card = build_streaming_card(_CARD_TITLE["done"], content, "done")
            else:
                card = build_streaming_card(_CARD_TITLE["done"], "✅ 已完成", "done")
            try:
                await self._channel.update_card(run.card_message_id, card)
            except Exception as e:
                logger.debug(f"Failed to finalize streaming card: {e}")
        elif content and not run.has_sent_feishu_message:
            # 没有流式卡片且 AI 没主动发消息 → 兜底发送 accumulated_text
            card = build_streaming_card(_CARD_TITLE["done"], content, "done")
            try:
                await self._channel.send_card(msg.chat_id, card, reply_to=msg.message_id)
            except Exception as e:
                logger.debug(f"Failed to send fallback card: {e}")

    async def _error_streaming_card(
        self, run: ActiveRun, msg: "IncomingMessage", error: Exception
    ) -> None:
        """出错时更新流式卡片为错误状态，或发送错误卡片。"""
        error_content = f"执行出错: {str(error)}"
        if run.accumulated_text.strip():
            error_content = run.accumulated_text.strip() + f"\n\n---\n\n❌ {error_content}"

        card = build_streaming_card(_CARD_TITLE["error"], error_content, "error")
        try:
            if run.card_message_id:
                await self._channel.update_card(run.card_message_id, card)
            else:
                await self._channel.send_card(msg.chat_id, card, reply_to=msg.message_id)
        except Exception:
            logger.warning("Failed to send error card")

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
