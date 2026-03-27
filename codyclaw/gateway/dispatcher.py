# codyclaw/gateway/dispatcher.py

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from cody import AsyncCodyClient, Cody
from cody.sdk.types import TextDeltaChunk, ToolCallChunk
from cody.sdk.types import DoneChunk, InteractionRequestChunk

from codyclaw.channel.cards import build_streaming_card, build_approval_card
from codyclaw.gateway.session_strategy import SessionManager
from codyclaw.gateway.tools import make_cron_tools
from codyclaw.automation.events import EventBus, Event, EventType

if TYPE_CHECKING:
    from codyclaw.channel.base import LarkChannel, IncomingMessage
    from codyclaw.gateway.router import MessageRouter, AgentConfig
    from codyclaw.automation.cron import CronScheduler

_SKILLS_DIR = str(Path(__file__).parent.parent / "skills")

logger = logging.getLogger(__name__)

_CARD_TITLE = {
    "running": "Agent 执行中...",
    "done": "执行完成",
    "error": "执行出错",
}
_INTERACTION_TIMEOUT = 300.0  # 审批超时秒数（5 分钟）


@dataclass
class ActiveRun:
    """正在执行的 Agent 任务"""
    user_id: str
    chat_id: str
    agent_id: str
    card_message_id: Optional[str] = None    # 流式输出卡片的 message_id
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    accumulated_text: str = ""
    auto_approve: bool = False               # 用户已授权本次 session 所有 CONFIRM 操作


@dataclass
class PendingInteraction:
    """等待用户审批的 human-in-the-loop 请求"""
    future: asyncio.Future
    client: AsyncCodyClient
    user_id: str                             # 用于 approve_all 时定位对应的 ActiveRun


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
        self._pending_interactions: dict[str, PendingInteraction] = {}
        self._user_pending: dict[str, str] = {}              # user_id → request_id
        self._card_update_interval = 1.5  # 飞书卡片更新节流（秒）
        self._cron_scheduler: Optional["CronScheduler"] = None
        self._cron_tools = make_cron_tools(lambda: self._cron_scheduler)

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
                    .interaction(enabled=True, timeout=_INTERACTION_TIMEOUT)
                    .circuit_breaker(
                        max_tokens=cb.get("max_tokens", 500_000),
                        max_cost_usd=cb.get("max_cost_usd", 2.0),
                        loop_detect_turns=cb.get("loop_detect_turns", 6),
                    )
                    .skill_dir(_SKILLS_DIR)
                )
                if self._db_path:
                    cody_db = str(
                        Path(self._db_path).parent / "agents" / agent_id / "cody.db"
                    )
                    builder = builder.db_path(cody_db)
                for tool in self._cron_tools:
                    builder = builder.tool(tool)
                builder = self._apply_cody_config(builder, self._cody_config)
                client = builder.build()
                await client.__aenter__()
                self._clients[agent_id] = client
        return self._clients[agent_id]

    def _apply_cody_config(self, builder, cody_config: dict):
        """将 config.yaml 的安全/权限配置应用到 Cody builder。
        使用 hasattr 安全检测，跳过当前 SDK 版本不支持的选项。
        """
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

        if msg.sender_id in self._active_runs:
            await self._channel.send_text(
                msg.chat_id,
                "上一个任务还在执行中，请稍候或发送「取消」终止。",
                reply_to=msg.message_id,
            )
            return

        client = await self.get_or_create_client(agent_config)
        session_key = self._get_session_key(agent_config, msg)
        session_id = self._sessions.get(session_key)

        run = ActiveRun(
            user_id=msg.sender_id,
            chat_id=msg.chat_id,
            agent_id=agent_config.agent_id,
        )
        self._active_runs[msg.sender_id] = run

        await self._emit(EventType.AGENT_RUN_START, {
            "agent_id": agent_config.agent_id,
            "user_id": msg.sender_id,
        })

        try:
            card = build_streaming_card("Agent 正在思考...", "", "running")
            run.card_message_id = await self._channel.send_card(
                msg.chat_id, card, reply_to=msg.message_id,
            )

            accumulated_text = ""
            last_update_time = 0.0

            async for chunk in client.stream(
                msg.content,
                session_id=session_id,
                cancel_event=run.cancel_event,
            ):
                if isinstance(chunk, TextDeltaChunk):
                    accumulated_text += chunk.content
                    run.accumulated_text = accumulated_text
                    now = asyncio.get_running_loop().time()
                    if now - last_update_time > self._card_update_interval:
                        await self._update_streaming_card(run, accumulated_text)
                        last_update_time = now

                elif isinstance(chunk, ToolCallChunk):
                    tool_info = f"\n\n`🔧 调用工具: {chunk.tool_name}`\n"
                    accumulated_text += tool_info
                    run.accumulated_text = accumulated_text
                    await self._emit(EventType.AGENT_TOOL_CALL, {
                        "tool": chunk.tool_name,
                        "agent_id": agent_config.agent_id,
                    })

                elif isinstance(chunk, InteractionRequestChunk):
                    await self._handle_interaction_request(run, chunk, client)

                elif isinstance(chunk, DoneChunk):
                    if chunk.session_id:
                        self._sessions.set(session_key, chunk.session_id)

            await self._update_streaming_card(run, accumulated_text or "(无输出)", "done")
            await self._emit(EventType.AGENT_RUN_END, {
                "agent_id": agent_config.agent_id,
                "user_id": msg.sender_id,
            })

        except Exception as e:
            logger.exception(f"Agent execution failed: {e}")
            if run.card_message_id:
                await self._update_streaming_card(run, f"执行出错: {str(e)}", "error")
            else:
                # 初始卡片发送失败，退回纯文本错误通知
                try:
                    await self._channel.send_text(
                        run.chat_id, f"⚠️ 执行出错: {str(e)}", reply_to=msg.message_id
                    )
                except Exception:
                    logger.warning("Failed to send fallback error text")
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

    async def resolve_interaction(self, request_id: str, decision: str) -> bool:
        """处理飞书卡片审批回调。
        decision: "approve" | "reject" | "approve_all"
        """
        pending = self._pending_interactions.pop(request_id, None)
        if pending is None:
            logger.warning(f"No pending interaction for request_id={request_id}")
            return False

        self._user_pending.pop(pending.user_id, None)

        actual_decision = decision
        if decision == "approve_all":
            # 标记该用户本次 session 后续所有 CONFIRM 操作自动批准
            run = self._active_runs.get(pending.user_id)
            if run:
                run.auto_approve = True
            actual_decision = "approve"

        try:
            await pending.client.submit_interaction(request_id, actual_decision)
        except Exception as e:
            logger.warning(f"submit_interaction failed: {e}")
        finally:
            if not pending.future.done():
                pending.future.set_result(actual_decision)

        await self._emit(EventType.AGENT_APPROVAL_RESOLVE, {
            "request_id": request_id,
            "decision": actual_decision,
        })
        return True

    async def try_resolve_by_message(self, user_id: str, content: str) -> bool:
        """用消息文本（允许/拒绝/全部允许）触发审批，返回是否消费了该消息。"""
        request_id = self._user_pending.get(user_id)
        if not request_id:
            return False
        decision_map = {"允许": "approve", "拒绝": "reject", "全部允许": "approve_all"}
        decision = decision_map.get(content)
        if not decision:
            return False
        await self.resolve_interaction(request_id, decision)
        return True

    def get_agent(self, agent_id: str):
        """按 agent_id 查找 Agent 配置（委托给 Router）"""
        return self._router.get_agent(agent_id)

    def get_sessions(self) -> dict[str, str]:
        """供管理 API 使用"""
        return self._sessions.all()

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
        """human-in-the-loop：发审批卡片 → 挂起协程 → 等回调唤醒 → 继续流。"""
        if run.auto_approve:
            # 用户已授权全部操作，直接批准无需打扰
            await client.submit_interaction(chunk.request_id, "approve")
            return

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_interactions[chunk.request_id] = PendingInteraction(
            future=future,
            client=client,
            user_id=run.user_id,
        )
        self._user_pending[run.user_id] = chunk.request_id

        card = build_approval_card(
            command=chunk.content,
            agent_name=run.agent_id,
        )
        await self._channel.send_card(run.chat_id, card)

        await self._emit(EventType.AGENT_APPROVAL_REQUEST, {
            "request_id": chunk.request_id,
            "agent_id": run.agent_id,
        })

        try:
            # shield 防止外部 cancel 导致 future 被取消（submit_interaction 需要正常完成）
            await asyncio.wait_for(asyncio.shield(future), timeout=_INTERACTION_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending_interactions.pop(chunk.request_id, None)
            if not future.done():
                future.cancel()
            raise RuntimeError(f"审批超时（{int(_INTERACTION_TIMEOUT // 60)} 分钟），操作已取消")

    async def _update_streaming_card(
        self, run: ActiveRun, content: str, status: str = "running"
    ) -> None:
        """更新流式输出卡片"""
        if not run.card_message_id:
            return
        card = build_streaming_card(
            title=_CARD_TITLE.get(status, "执行完成"),
            content=content,
            status=status,
        )
        try:
            await self._channel.update_card(run.card_message_id, card)
        except Exception as e:
            logger.warning(f"Failed to update card: {e}")

    async def _emit(self, event_type: EventType, data: dict) -> None:
        """发射事件到 EventBus（若未配置则静默忽略）"""
        if self._event_bus:
            await self._event_bus.emit(Event(type=event_type, data=data, source="dispatcher"))
