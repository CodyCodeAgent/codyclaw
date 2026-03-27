# CodyClaw 技术设计文档

> 基于 Cody Agent Framework 构建飞书驱动的持久化 AI Agent 系统

## 1. 项目概述

### 1.1 目标

构建一个类 OpenClaw 的 AI Agent 系统，核心差异：

| 维度 | OpenClaw | CodyClaw（本项目） |
|------|----------|-------------------|
| 消息渠道 | WhatsApp/Telegram/Discord 等 15+ 渠道 | **仅飞书**，做深做透 |
| Agent 引擎 | 调用外部 CLI（Claude Code/Codex） | **Cody 内置引擎**，进程内运行 |
| 语言 | TypeScript | **Python** |
| 定位 | 全平台个人 AI 管家 | 企业级飞书 AI 工作台 |

### 1.2 核心能力

- **飞书即入口**：通过飞书消息/群聊/机器人指令操控 Agent，手机上发消息、电脑上执行
- **7×24 在线**：Gateway 常驻后台，Cron 定时任务主动触发
- **有记忆**：跨会话持久化记忆，Agent 知道你的偏好和上下文
- **可扩展**：Skills 技能包 + MCP 协议 + 自定义工具

### 1.3 一句话架构

```
飞书消息 → Gateway（FastAPI daemon）→ Cody SDK（AsyncCodyClient）→ LLM → 工具执行 → 飞书回复
```

---

## 2. 整体架构

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     飞书开放平台                              │
│          （消息事件推送 / 主动发消息 API）                      │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP Webhook / WebSocket 长连接
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: 飞书渠道适配层 (lark_channel.py)                    │
│  ├─ 事件接收（消息、指令、卡片交互）                             │
│  ├─ 消息发送（文本、富文本、交互卡片）                           │
│  ├─ 身份解析（user_id → Agent 路由）                          │
│  └─ 媒体处理（图片/文件上传下载）                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Layer 2: Gateway 调度层 (gateway.py)                        │
│  ├─ 消息路由（用户/群 → Agent 映射）                           │
│  ├─ 会话管理（session 生命周期）                               │
│  ├─ 执行审批（危险操作 → 飞书卡片确认）                         │
│  ├─ 并发控制（per-user 执行队列）                              │
│  └─ 健康监控（渠道连接状态）                                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Layer 3: 自动化引擎 (automation.py)                         │
│  ├─ Cron 定时任务（APScheduler）                              │
│  ├─ Hooks 事件总线（pub/sub）                                 │
│  └─ BOOT.md 启动脚本（自然语言启动指令）                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Layer 4: Cody Agent 引擎 (cody.sdk.AsyncCodyClient)         │
│  ├─ AgentRunner（Pydantic AI 编排）                           │
│  ├─ 30 个内置工具（文件/搜索/命令/子Agent/MCP/LSP）             │
│  ├─ Skills 技能系统                                          │
│  ├─ 记忆系统（ProjectMemoryStore）                            │
│  ├─ 上下文自动压缩                                            │
│  ├─ 权限 & 审计                                              │
│  └─ Circuit Breaker（成本 & 循环保护）                         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 与 OpenClaw 八层架构的映射

| OpenClaw 层 | CodyClaw 对应 | 实现方式 |
|-------------|---------------|---------|
| ① 入口层（6 种客户端） | 飞书 App + Web 管理台 | 飞书 SDK + Cody Web UI |
| ② 消息渠道（15+ 渠道） | 飞书单渠道 | lark-oapi SDK |
| ③ Gateway（WebSocket 服务） | FastAPI daemon | 改造 Cody Web Backend |
| ④ Hooks & Cron | 自动化引擎 | APScheduler + EventBus |
| ⑤ Agent 执行（外部 CLI） | Cody 内置引擎 | AsyncCodyClient（进程内） |
| ⑥ LLM Provider + 浏览器 | Cody 多模型支持 | Pydantic AI model 抽象 |
| ⑦ 记忆 & Skills | Cody 记忆 + Skills | ProjectMemoryStore + SKILL.md |
| ⑧ 基础设施 | Cody 安全层 | 权限/审计/沙箱 |

---

## 3. 飞书渠道适配层

### 3.1 飞书开放平台接入

**前置条件**：
- 在[飞书开放平台](https://open.feishu.cn/)创建企业自建应用
- 获取 App ID + App Secret
- 开启「机器人」能力
- 配置事件订阅（消息接收回调）
- 配置权限：`im:message`（收发消息）、`im:resource`（上传下载文件）

**两种事件接收方式对比**：

| 方式 | 长连接（WebSocket） | Webhook |
|------|-------------------|---------|
| 网络要求 | 无需公网 IP | 需要公网可达的 URL |
| 实现复杂度 | 低（SDK 封装） | 中（需处理验证、解密） |
| 部署场景 | 本地开发、内网部署 | 云服务器 |
| 推荐场景 | **个人/小团队** | 企业生产环境 |

本项目**同时支持两种方式**，通过配置切换。

### 3.2 核心接口设计

```python
# codyclaw/channel/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

@dataclass
class IncomingMessage:
    """统一的入站消息格式"""
    message_id: str                    # 飞书消息 ID（用于去重和回复）
    chat_id: str                       # 会话 ID（单聊或群聊）
    chat_type: str                     # "p2p" | "group"
    sender_id: str                     # 发送者 open_id
    sender_name: str                   # 发送者名称
    content: str                       # 纯文本内容（已从飞书 JSON 格式提取）
    msg_type: str                      # "text" | "image" | "file" | "post"
    images: list[bytes] = field(default_factory=list)  # 图片二进制数据
    mentions: list[str] = field(default_factory=list)  # @提及的用户列表
    is_mention_bot: bool = False       # 是否 @了机器人
    raw: dict = field(default_factory=dict)  # 飞书原始事件数据


@dataclass
class OutgoingMessage:
    """统一的出站消息格式"""
    chat_id: str
    content: str
    msg_type: str = "interactive"      # 默认用交互卡片，支持 Markdown 渲染
    reply_to: Optional[str] = None     # 回复某条消息的 message_id


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class LarkChannel(ABC):
    """飞书渠道抽象基类"""

    @abstractmethod
    async def start(self) -> None:
        """启动渠道连接"""

    @abstractmethod
    async def stop(self) -> None:
        """关闭渠道连接"""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> str:
        """发送纯文本消息，返回 message_id"""

    @abstractmethod
    async def send_card(self, chat_id: str, card: dict, reply_to: Optional[str] = None) -> str:
        """发送交互卡片，返回 message_id"""

    @abstractmethod
    async def send_file(self, chat_id: str, file_path: str, file_name: str) -> str:
        """发送文件，返回 message_id"""

    @abstractmethod
    async def download_resource(self, message_id: str, file_key: str) -> bytes:
        """下载消息中的图片/文件资源"""

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None:
        """注册消息处理回调"""

    @abstractmethod
    async def update_card(self, message_id: str, card: dict) -> None:
        """更新已发送的交互卡片（用于进度展示）"""
```

### 3.3 飞书 SDK 实现

```python
# codyclaw/channel/lark_impl.py

import json
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

class LarkChannelImpl(LarkChannel):
    """基于 lark-oapi SDK 的飞书渠道实现"""

    def __init__(self, config: "LarkConfig"):
        self.config = config
        self._handlers: list[MessageHandler] = []

        # 初始化飞书 SDK 客户端
        self._client = lark.Client.builder() \
            .app_id(config.app_id) \
            .app_secret(config.app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # 事件处理器
        self._event_handler = lark.EventDispatcherHandler.builder(
            config.encrypt_key or "",
            config.verification_token or "",
        ).register_p2_im_message_receive_v1(
            self._on_message_event
        ).build()

    async def start(self) -> None:
        if self.config.mode == "websocket":
            # 长连接模式（推荐）——无需公网 IP
            self._ws_client = lark.ws.Client(
                self.config.app_id,
                self.config.app_secret,
                event_handler=self._event_handler,
                log_level=lark.LogLevel.WARNING,
            )
            # 在后台线程启动 WebSocket（SDK 内部管理重连）
            import asyncio
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, self._ws_client.start)
        else:
            # Webhook 模式——需要在 FastAPI 中注册路由
            # 路由注册由 Gateway 层完成，这里只准备 handler
            pass

    def get_webhook_handler(self):
        """返回 Webhook 请求处理器，供 FastAPI 路由使用"""
        return self._event_handler

    async def _on_message_event(self, ctx, event: P2ImMessageReceiveV1) -> None:
        """处理飞书消息事件"""
        msg = event.event.message

        # 解析消息内容（飞书消息内容是 JSON 字符串）
        content_json = json.loads(msg.content)
        text = content_json.get("text", "")

        # 检查是否 @了机器人
        mentions = []
        is_mention_bot = False
        if msg.mentions:
            for m in msg.mentions:
                mentions.append(m.id.open_id)
                if m.id.open_id == self.config.bot_open_id:
                    is_mention_bot = True
                    # 移除 @机器人 的占位符
                    text = text.replace(f"@_user_{m.key}", "").strip()

        # 构造统一消息格式
        incoming = IncomingMessage(
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            chat_type=msg.chat_type,
            sender_id=event.event.sender.sender_id.open_id,
            sender_name=event.event.sender.sender_id.open_id,  # 需要额外 API 获取名称
            content=text,
            msg_type=msg.message_type,
            mentions=mentions,
            is_mention_bot=is_mention_bot,
            raw=event.event.__dict__,
        )

        # 图片消息：下载图片数据
        if msg.message_type == "image":
            image_key = content_json.get("image_key", "")
            if image_key:
                data = await self.download_resource(msg.message_id, image_key)
                incoming.images.append(data)

        # 分发给所有注册的 handler
        for handler in self._handlers:
            await handler(incoming)

    async def send_text(self, chat_id: str, text: str, reply_to=None) -> str:
        """发送文本消息"""
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("text") \
            .content(json.dumps({"text": text})) \
            .build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        response = self._client.im.v1.message.create(request)
        return response.data.message_id

    async def send_card(self, chat_id: str, card: dict, reply_to=None) -> str:
        """发送交互卡片（支持 Markdown 渲染）"""
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card)) \
            .build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        response = self._client.im.v1.message.create(request)
        return response.data.message_id

    async def update_card(self, message_id: str, card: dict) -> None:
        """更新卡片内容（用于流式输出进度展示）"""
        # PATCH /open-apis/im/v1/messages/{message_id}
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps(card)) \
            .build()

        request = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()

        self._client.im.v1.message.patch(request)

    def on_message(self, handler: MessageHandler) -> None:
        self._handlers.append(handler)
```

### 3.4 飞书交互卡片（用于流式输出和审批）

```python
# codyclaw/channel/cards.py

def build_streaming_card(title: str, content: str, status: str = "running") -> dict:
    """构建流式输出卡片——Agent 执行过程中实时更新"""
    status_emoji = {"running": "⏳", "done": "✅", "error": "❌"}[status]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{status_emoji} {title}"},
            "template": {"running": "blue", "done": "green", "error": "red"}[status],
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content[:4096],  # 飞书卡片内容上限
            },
            # 运行中时显示取消按钮
            *([{
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消执行"},
                    "type": "danger",
                    "value": {"action": "cancel"},
                }],
            }] if status == "running" else []),
        ],
    }


def build_approval_card(
    command: str, agent_name: str, request_id: str
) -> dict:
    """构建执行审批卡片——危险操作需要人工确认"""
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
                    f"是否允许执行？"
                ),
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "允许"},
                        "type": "primary",
                        "value": {"action": "approve", "request_id": request_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {"action": "reject", "request_id": request_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "全部允许"},
                        "type": "default",
                        "value": {"action": "approve_all", "request_id": request_id},
                    },
                ],
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
```

### 3.5 消息去重

飞书采用至少一次投递策略（at-least-once），必须做去重：

```python
# codyclaw/channel/dedup.py

import time
from collections import OrderedDict

class MessageDeduplicator:
    """基于 event_id 的消息去重器，滑动窗口 1 小时"""

    def __init__(self, window_seconds: int = 3600, max_size: int = 10000):
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._window = window_seconds
        self._max_size = max_size

    def is_duplicate(self, event_id: str) -> bool:
        now = time.time()
        # 清理过期条目
        while self._seen and next(iter(self._seen.values())) < now - self._window:
            self._seen.popitem(last=False)
        # 容量保护
        while len(self._seen) >= self._max_size:
            self._seen.popitem(last=False)

        if event_id in self._seen:
            return True
        self._seen[event_id] = now
        return False
```

---

## 4. Gateway 调度层

### 4.1 核心职责

Gateway 是整个系统的调度中枢，负责：

1. **消息路由**：决定用户消息交给哪个 Agent 处理
2. **会话管理**：维护用户 ↔ session 的映射关系
3. **执行调度**：管理 Agent 并发执行，防止同一用户重复触发
4. **审批网关**：拦截危险操作，通过飞书卡片请求人工确认
5. **健康监控**：监控飞书连接状态，自动重连

### 4.2 消息路由

```python
# codyclaw/gateway/router.py

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AgentConfig:
    """Agent 配置"""
    agent_id: str
    name: str
    workdir: str                           # Agent 工作目录
    model: str = "claude-sonnet-4-20250514"
    description: str = ""
    allowed_users: list[str] = field(default_factory=list)   # 空 = 所有人
    allowed_groups: list[str] = field(default_factory=list)  # 空 = 所有群
    trigger_mode: str = "mention"          # "mention" | "all" | "prefix"
    prefix: str = "/"                      # prefix 模式下的触发前缀
    boot_file: Optional[str] = None        # BOOT.md 路径


class MessageRouter:
    """消息路由器：决定消息交给哪个 Agent"""

    def __init__(self):
        self._agents: dict[str, AgentConfig] = {}        # agent_id → config
        self._user_bindings: dict[str, str] = {}          # user_id → agent_id（单聊绑定）
        self._group_bindings: dict[str, str] = {}         # chat_id → agent_id（群聊绑定）
        self._default_agent: Optional[str] = None         # 默认 Agent

    def register_agent(self, config: AgentConfig) -> None:
        self._agents[config.agent_id] = config

    def set_default_agent(self, agent_id: str) -> None:
        self._default_agent = agent_id

    def bind_user(self, user_id: str, agent_id: str) -> None:
        """将用户绑定到指定 Agent（单聊场景）"""
        self._user_bindings[user_id] = agent_id

    def bind_group(self, chat_id: str, agent_id: str) -> None:
        """将群聊绑定到指定 Agent"""
        self._group_bindings[chat_id] = agent_id

    def resolve(self, msg: "IncomingMessage") -> Optional[AgentConfig]:
        """根据消息解析应该处理的 Agent"""
        # 1. 群聊场景
        if msg.chat_type == "group":
            agent_id = self._group_bindings.get(msg.chat_id)
            if agent_id and agent_id in self._agents:
                agent = self._agents[agent_id]
                # 检查触发条件
                if agent.trigger_mode == "mention" and not msg.is_mention_bot:
                    return None  # 群聊必须 @机器人
                if agent.trigger_mode == "prefix" and not msg.content.startswith(agent.prefix):
                    return None
                return agent

        # 2. 单聊场景
        if msg.chat_type == "p2p":
            agent_id = self._user_bindings.get(msg.sender_id)
            if agent_id and agent_id in self._agents:
                return self._agents[agent_id]

        # 3. 回退到默认 Agent
        if self._default_agent and self._default_agent in self._agents:
            agent = self._agents[self._default_agent]
            # 检查用户权限
            if agent.allowed_users and msg.sender_id not in agent.allowed_users:
                return None
            return agent

        return None
```

### 4.3 执行调度器

```python
# codyclaw/gateway/dispatcher.py

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from cody import AsyncCodyClient, Cody
from cody.sdk.types import StreamChunk, TextDeltaChunk, ToolCallChunk, ToolResultChunk
from cody.sdk.types import DoneChunk, ThinkingChunk, InteractionRequestChunk

logger = logging.getLogger(__name__)


@dataclass
class ActiveRun:
    """正在执行的 Agent 任务"""
    user_id: str
    chat_id: str
    agent_id: str
    session_id: Optional[str] = None
    card_message_id: Optional[str] = None   # 流式输出卡片的 message_id
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    accumulated_text: str = ""               # 累积的输出文本


class AgentDispatcher:
    """Agent 执行调度器"""

    def __init__(self, channel: "LarkChannel", router: "MessageRouter"):
        self._channel = channel
        self._router = router
        self._active_runs: dict[str, ActiveRun] = {}          # user_id → ActiveRun
        self._clients: dict[str, AsyncCodyClient] = {}         # agent_id → client
        self._session_map: dict[str, str] = {}                 # "{agent_id}:{user_id}" → session_id
        self._update_lock = asyncio.Lock()
        self._card_update_interval = 1.5  # 卡片更新间隔（秒），避免触发飞书限流

    async def get_or_create_client(self, agent_config: "AgentConfig") -> AsyncCodyClient:
        """获取或创建 Agent 对应的 Cody Client"""
        if agent_config.agent_id not in self._clients:
            client = (
                Cody()
                .workdir(agent_config.workdir)
                .model(agent_config.model)
                .interaction(enabled=True, timeout=120.0)  # 启用 human-in-the-loop
                .circuit_breaker(
                    max_tokens=500_000,
                    max_cost_usd=2.0,
                    loop_detect_turns=6,
                )
                .build()
            )
            await client.__aenter__()
            self._clients[agent_config.agent_id] = client
        return self._clients[agent_config.agent_id]

    def _get_session_key(self, agent_id: str, user_id: str) -> str:
        return f"{agent_id}:{user_id}"

    async def dispatch(self, msg: "IncomingMessage") -> None:
        """调度一条消息到对应的 Agent 执行"""
        # 1. 路由到 Agent
        agent_config = self._router.resolve(msg)
        if agent_config is None:
            return  # 消息不需要处理

        # 2. 检查是否有正在执行的任务
        if msg.sender_id in self._active_runs:
            await self._channel.send_text(
                msg.chat_id,
                "上一个任务还在执行中，请稍候或发送「取消」终止。",
                reply_to=msg.message_id,
            )
            return

        # 3. 获取 Client 和 Session
        client = await self.get_or_create_client(agent_config)
        session_key = self._get_session_key(agent_config.agent_id, msg.sender_id)
        session_id = self._session_map.get(session_key)

        # 4. 创建执行任务
        run = ActiveRun(
            user_id=msg.sender_id,
            chat_id=msg.chat_id,
            agent_id=agent_config.agent_id,
        )
        self._active_runs[msg.sender_id] = run

        try:
            # 5. 发送初始状态卡片
            card = build_streaming_card(
                title="Agent 正在思考...",
                content="",
                status="running",
            )
            run.card_message_id = await self._channel.send_card(
                msg.chat_id, card, reply_to=msg.message_id,
            )

            # 6. 流式执行
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
                    # 节流更新卡片
                    now = asyncio.get_event_loop().time()
                    if now - last_update_time > self._card_update_interval:
                        await self._update_streaming_card(run, accumulated_text)
                        last_update_time = now

                elif isinstance(chunk, ToolCallChunk):
                    tool_info = f"\n\n`🔧 调用工具: {chunk.tool_name}`\n"
                    accumulated_text += tool_info
                    run.accumulated_text = accumulated_text

                elif isinstance(chunk, InteractionRequestChunk):
                    # Human-in-the-loop：发送审批卡片
                    await self._handle_interaction_request(run, chunk, client)

                elif isinstance(chunk, DoneChunk):
                    # 更新 session 映射
                    if chunk.session_id:
                        self._session_map[session_key] = chunk.session_id

            # 7. 最终更新卡片为完成状态
            await self._update_streaming_card(
                run, accumulated_text or "(无输出)", status="done",
            )

        except Exception as e:
            logger.exception(f"Agent execution failed: {e}")
            await self._update_streaming_card(
                run, f"执行出错: {str(e)}", status="error",
            )
        finally:
            self._active_runs.pop(msg.sender_id, None)

    async def cancel(self, user_id: str) -> bool:
        """取消用户当前的执行任务"""
        run = self._active_runs.get(user_id)
        if run:
            run.cancel_event.set()
            return True
        return False

    async def _update_streaming_card(
        self, run: ActiveRun, content: str, status: str = "running"
    ) -> None:
        """更新流式输出卡片"""
        if not run.card_message_id:
            return
        card = build_streaming_card(
            title="Agent 执行中..." if status == "running" else "执行完成",
            content=content,
            status=status,
        )
        try:
            await self._channel.update_card(run.card_message_id, card)
        except Exception as e:
            logger.warning(f"Failed to update card: {e}")

    async def _handle_interaction_request(
        self,
        run: ActiveRun,
        chunk: InteractionRequestChunk,
        client: AsyncCodyClient,
    ) -> None:
        """处理 Agent 的 human-in-the-loop 请求"""
        card = build_approval_card(
            command=chunk.content,
            agent_name=run.agent_id,
            request_id=chunk.request_id,
        )
        await self._channel.send_card(run.chat_id, card)
        # 审批结果通过飞书卡片回调 → Gateway → client.submit_interaction() 闭环

    async def shutdown(self) -> None:
        """关闭所有 Client"""
        for client in self._clients.values():
            await client.__aexit__(None, None, None)
        self._clients.clear()
```

### 4.4 会话策略

```python
# codyclaw/gateway/session_strategy.py

"""
会话映射策略：

1. 单聊（p2p）：每个用户一个 session，跨消息保持上下文
   key = "{agent_id}:{user_id}"

2. 群聊（group）：每个群一个 session，所有成员共享上下文
   key = "{agent_id}:{chat_id}"

3. 命令模式：特定前缀（如 /new）强制创建新 session
   用户发送 "/new 帮我写一个脚本" → 新建 session + 执行

4. 超时策略：session 空闲超过 N 小时后自动归档
   新消息进来时创建新 session（旧 session 不删除，可恢复）
"""

SESSION_IDLE_TIMEOUT_HOURS = 24  # session 空闲超时时间
```

---

## 5. 自动化引擎

### 5.1 Cron 定时任务

```python
# codyclaw/automation/cron.py

import logging
from dataclasses import dataclass, field
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


@dataclass
class CronTask:
    """定时任务定义"""
    task_id: str
    name: str
    agent_id: str                    # 执行任务的 Agent
    prompt: str                      # 自然语言指令
    schedule: str                    # cron 表达式 或 interval 描述
    notify_chat_id: Optional[str] = None  # 结果推送到哪个飞书会话
    enabled: bool = True
    timezone: str = "Asia/Shanghai"


class CronScheduler:
    """定时任务调度器"""

    def __init__(self, dispatcher: "AgentDispatcher", channel: "LarkChannel"):
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._dispatcher = dispatcher
        self._channel = channel
        self._tasks: dict[str, CronTask] = {}

    def add_task(self, task: CronTask) -> None:
        """添加定时任务"""
        self._tasks[task.task_id] = task

        if "every" in task.schedule.lower() or task.schedule.isdigit():
            # 简单间隔模式：如 "every 30m"、"every 2h"、"60"(分钟)
            minutes = self._parse_interval(task.schedule)
            trigger = IntervalTrigger(minutes=minutes)
        else:
            # Cron 表达式模式：如 "0 8 * * *"（每天 8:00）
            trigger = CronTrigger.from_crontab(task.schedule, timezone=task.timezone)

        self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task.task_id,
            args=[task],
            name=task.name,
            replace_existing=True,
        )
        logger.info(f"Registered cron task: {task.name} ({task.schedule})")

    async def _execute_task(self, task: CronTask) -> None:
        """执行定时任务"""
        logger.info(f"Executing cron task: {task.name}")
        try:
            agent_config = self._dispatcher._router._agents.get(task.agent_id)
            if not agent_config:
                logger.error(f"Agent {task.agent_id} not found for cron task {task.name}")
                return

            client = await self._dispatcher.get_or_create_client(agent_config)

            # 用临时 session 执行，不污染用户会话
            result = await client.run(
                task.prompt,
                session_id=f"cron-{task.task_id}",
            )

            # 推送结果到飞书
            if task.notify_chat_id and result.output:
                next_run = self._scheduler.get_job(task.task_id).next_run_time
                card = build_cron_result_card(
                    task_name=task.name,
                    result=result.output,
                    next_run=next_run.strftime("%Y-%m-%d %H:%M") if next_run else "未知",
                )
                await self._channel.send_card(task.notify_chat_id, card)

        except Exception as e:
            logger.exception(f"Cron task {task.name} failed: {e}")
            if task.notify_chat_id:
                await self._channel.send_text(
                    task.notify_chat_id,
                    f"⚠️ 定时任务 [{task.name}] 执行失败: {str(e)}",
                )

    def start(self) -> None:
        self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    @staticmethod
    def _parse_interval(schedule: str) -> int:
        """解析简单间隔描述为分钟数"""
        schedule = schedule.lower().replace("every", "").strip()
        if schedule.endswith("h"):
            return int(schedule[:-1]) * 60
        elif schedule.endswith("m"):
            return int(schedule[:-1])
        elif schedule.isdigit():
            return int(schedule)
        return 60  # 默认 1 小时
```

### 5.2 事件总线

```python
# codyclaw/automation/events.py

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any
from enum import Enum

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
```

### 5.3 BOOT.md 启动脚本

```python
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
    for agent_id, agent_config in router._agents.items():
        boot_path = Path(agent_config.workdir) / "BOOT.md"
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
```

---

## 6. Gateway 主进程

### 6.1 主入口

```python
# codyclaw/main.py

import asyncio
import logging
import signal
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn

from codyclaw.config import load_config, CodyClawConfig
from codyclaw.channel.lark_impl import LarkChannelImpl
from codyclaw.channel.dedup import MessageDeduplicator
from codyclaw.gateway.router import MessageRouter
from codyclaw.gateway.dispatcher import AgentDispatcher
from codyclaw.automation.cron import CronScheduler
from codyclaw.automation.events import EventBus, EventType, Event
from codyclaw.automation.boot import execute_boot_scripts

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理"""
    config: CodyClawConfig = app.state.config

    # --- 启动 ---
    # 1. 初始化飞书渠道
    channel = LarkChannelImpl(config.lark)
    app.state.channel = channel

    # 2. 初始化路由和调度器
    router = MessageRouter()
    for agent_cfg in config.agents:
        router.register_agent(agent_cfg)
    if config.default_agent:
        router.set_default_agent(config.default_agent)
    app.state.router = router

    dispatcher = AgentDispatcher(channel, router)
    app.state.dispatcher = dispatcher

    # 3. 初始化事件总线
    event_bus = EventBus()
    app.state.event_bus = event_bus

    # 4. 初始化去重器
    dedup = MessageDeduplicator()
    app.state.dedup = dedup

    # 5. 注册消息处理
    async def handle_message(msg):
        if dedup.is_duplicate(msg.message_id):
            return
        # 特殊命令处理
        if msg.content.strip() == "取消":
            await dispatcher.cancel(msg.sender_id)
            return
        await dispatcher.dispatch(msg)

    channel.on_message(handle_message)

    # 6. 启动飞书连接
    await channel.start()
    logger.info("Lark channel connected")

    # 7. 执行 BOOT.md
    await execute_boot_scripts(dispatcher, router, event_bus)

    # 8. 启动 Cron 调度器
    cron = CronScheduler(dispatcher, channel)
    for task in config.cron_tasks:
        cron.add_task(task)
    cron.start()
    app.state.cron = cron
    logger.info(f"Cron scheduler started with {len(config.cron_tasks)} tasks")

    logger.info("🚀 CodyClaw Gateway is running")

    yield

    # --- 关闭 ---
    logger.info("Shutting down CodyClaw Gateway...")
    await event_bus.emit(Event(type=EventType.GATEWAY_SHUTDOWN))
    cron.stop()
    await dispatcher.shutdown()
    await channel.stop()


def create_app(config: CodyClawConfig) -> FastAPI:
    app = FastAPI(
        title="CodyClaw Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = config

    # --- Webhook 路由（飞书事件回调） ---
    @app.post("/webhook/lark")
    async def lark_webhook(request: Request):
        """飞书 Webhook 事件接收端点"""
        body = await request.body()
        channel: LarkChannelImpl = app.state.channel
        handler = channel.get_webhook_handler()
        # 委托给飞书 SDK 的事件处理器
        resp = handler.do(lark.RawRequest(
            uri=str(request.url),
            headers={k: v for k, v in request.headers.items()},
            body=body,
        ))
        return Response(
            content=resp.body,
            status_code=resp.status_code,
            headers=resp.headers,
        )

    # --- 飞书卡片回调（审批按钮点击） ---
    @app.post("/webhook/lark/card")
    async def lark_card_callback(request: Request):
        """飞书交互卡片回调"""
        data = await request.json()
        action = data.get("action", {})
        value = action.get("value", {})

        action_type = value.get("action")
        request_id = value.get("request_id")

        dispatcher: AgentDispatcher = app.state.dispatcher

        if action_type == "cancel":
            user_id = data.get("open_id", "")
            await dispatcher.cancel(user_id)

        elif action_type in ("approve", "reject", "approve_all") and request_id:
            # 将审批结果传递给等待中的 Agent
            # 通过 Cody SDK 的 submit_interaction 机制
            pass  # 详见 §6.2

        return {"toast": {"type": "info", "content": "已处理"}}

    # --- 管理 API ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/agents")
    async def list_agents():
        router: MessageRouter = app.state.router
        return {"agents": [
            {"id": a.agent_id, "name": a.name, "workdir": a.workdir}
            for a in router._agents.values()
        ]}

    @app.get("/api/cron")
    async def list_cron_tasks():
        cron: CronScheduler = app.state.cron
        tasks = []
        for task in cron._tasks.values():
            job = cron._scheduler.get_job(task.task_id)
            tasks.append({
                "id": task.task_id,
                "name": task.name,
                "schedule": task.schedule,
                "enabled": task.enabled,
                "next_run": str(job.next_run_time) if job else None,
            })
        return {"tasks": tasks}

    @app.get("/api/sessions")
    async def list_sessions():
        """列出所有活跃会话"""
        dispatcher: AgentDispatcher = app.state.dispatcher
        return {"sessions": [
            {"key": k, "session_id": v}
            for k, v in dispatcher._session_map.items()
        ]}

    return app


def main():
    config = load_config()
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
```

### 6.2 审批闭环

```
用户发送 "帮我删除 /tmp/data 下的所有文件"
  │
  ▼
Cody Agent 执行 → 调用 exec_command("rm -rf /tmp/data/*")
  │
  ▼  权限检查：exec_command 默认是 CONFIRM 级别
Cody 的 interaction_handler 触发 InteractionRequestChunk
  │
  ▼
Dispatcher 收到 InteractionRequestChunk
  │
  ▼
发送审批卡片到飞书 → 用户看到卡片，点击"允许"
  │
  ▼
飞书卡片回调 POST /webhook/lark/card
  │
  ▼
Gateway 调用 client.submit_interaction(request_id, "approve")
  │
  ▼
Cody Agent 继续执行 rm -rf /tmp/data/*
  │
  ▼
结果推送到飞书
```

---

## 7. 配置系统

### 7.1 配置文件

```yaml
# config.yaml

# --- 飞书应用配置 ---
lark:
  app_id: "cli_xxx"
  app_secret: "xxx"
  encrypt_key: ""                      # 可选，事件加密
  verification_token: ""               # 可选，事件验证
  bot_open_id: "ou_xxx"                # 机器人自身的 open_id
  mode: "websocket"                    # "websocket" | "webhook"

# --- Gateway 配置 ---
gateway:
  host: "0.0.0.0"
  port: 8080
  log_level: "info"

# --- Agent 定义 ---
agents:
  - agent_id: "assistant"
    name: "通用助手"
    workdir: "/home/user/workspace"
    model: "claude-sonnet-4-20250514"
    trigger_mode: "all"                # 单聊直接响应
    description: "通用编程助手，可以读写文件、执行命令"

  - agent_id: "monitor"
    name: "运维监控"
    workdir: "/home/user/ops"
    model: "claude-haiku-4-5-20251001"  # 轻量模型，降低成本
    trigger_mode: "mention"            # 群聊需 @
    allowed_groups: ["oc_xxx"]         # 限定运维群
    description: "服务器监控和告警处理"

default_agent: "assistant"

# --- 定时任务 ---
cron_tasks:
  - task_id: "daily_news"
    name: "每日技术新闻"
    agent_id: "assistant"
    prompt: "搜索今天的 AI 和前端技术新闻，整理成简报，不超过 500 字。"
    schedule: "0 9 * * 1-5"            # 工作日 9:00
    notify_chat_id: "oc_xxx"           # 推送到指定群

  - task_id: "health_check"
    name: "服务健康检查"
    agent_id: "monitor"
    prompt: "检查所有服务的运行状态，如果有异常立即报告。"
    schedule: "every 30m"              # 每 30 分钟
    notify_chat_id: "oc_xxx"

# --- 全局 Cody 配置覆盖 ---
cody:
  model_api_key: "${ANTHROPIC_API_KEY}"  # 支持环境变量引用
  enable_thinking: false
  security:
    command_timeout: 60
    blocked_commands: ["rm -rf /", "mkfs", "dd if="]
  permissions:
    default_level: "confirm"
    overrides:
      read_file: "allow"
      list_directory: "allow"
      grep: "allow"
      glob: "allow"
```

### 7.2 配置加载

```python
# codyclaw/config.py

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml
import os
import re

from codyclaw.gateway.router import AgentConfig
from codyclaw.automation.cron import CronTask


@dataclass
class LarkConfig:
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    bot_open_id: str = ""
    mode: str = "websocket"            # "websocket" | "webhook"


@dataclass
class GatewayConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"


@dataclass
class CodyClawConfig:
    lark: LarkConfig = field(default_factory=LarkConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    agents: list[AgentConfig] = field(default_factory=list)
    default_agent: Optional[str] = None
    cron_tasks: list[CronTask] = field(default_factory=list)
    cody: dict = field(default_factory=dict)


def _resolve_env_vars(value: str) -> str:
    """解析 ${ENV_VAR} 引用"""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return re.sub(r'\$\{(\w+)\}', replacer, value)


def load_config(path: Optional[str] = None) -> CodyClawConfig:
    """加载配置文件"""
    if path is None:
        # 搜索顺序: ./config.yaml → ~/.codyclaw/config.yaml
        candidates = [
            Path.cwd() / "config.yaml",
            Path.home() / ".codyclaw" / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                path = str(p)
                break

    if not path:
        raise FileNotFoundError("No config.yaml found")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 递归解析环境变量
    raw = _deep_resolve(raw)

    config = CodyClawConfig()

    # 解析各部分
    if "lark" in raw:
        config.lark = LarkConfig(**raw["lark"])

    if "gateway" in raw:
        config.gateway = GatewayConfig(**raw["gateway"])

    if "agents" in raw:
        config.agents = [AgentConfig(**a) for a in raw["agents"]]

    config.default_agent = raw.get("default_agent")

    if "cron_tasks" in raw:
        config.cron_tasks = [CronTask(**t) for t in raw["cron_tasks"]]

    if "cody" in raw:
        config.cody = raw["cody"]

    return config


def _deep_resolve(obj):
    """递归解析所有字符串中的环境变量"""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    return obj
```

---

## 8. 项目结构

```
codyclaw/
├── __init__.py
├── main.py                          # Gateway 主入口 + FastAPI app
├── config.py                        # 配置加载（YAML + 环境变量）
│
├── channel/                         # 飞书渠道适配层
│   ├── __init__.py
│   ├── base.py                      # 抽象接口（LarkChannel, IncomingMessage, OutgoingMessage）
│   ├── lark_impl.py                 # lark-oapi SDK 实现
│   ├── cards.py                     # 交互卡片模板（流式输出、审批、Cron 结果）
│   └── dedup.py                     # 消息去重器
│
├── gateway/                         # 调度层
│   ├── __init__.py
│   ├── router.py                    # 消息路由（用户/群 → Agent 映射）
│   ├── dispatcher.py                # 执行调度器（管理 Cody Client + 流式输出）
│   └── session_strategy.py          # 会话映射策略
│
├── automation/                      # 自动化引擎
│   ├── __init__.py
│   ├── cron.py                      # 定时任务（APScheduler）
│   ├── events.py                    # 事件总线（pub/sub）
│   └── boot.py                      # BOOT.md 启动脚本执行
│
├── skills/                          # 自定义 Skills（可选）
│   └── feishu-notify/
│       └── SKILL.md                 # 飞书通知技能
│
└── config.yaml                      # 配置文件模板
```

---

## 9. 关键设计决策

### 9.1 为什么 Cody 引擎比 OpenClaw 的方案更优

| 维度 | OpenClaw（调外部 CLI） | CodyClaw（Cody 内置引擎） |
|------|----------------------|--------------------------|
| 进程模型 | spawn 子进程，JSON 序列化通信 | 进程内直接调用，零序列化开销 |
| 上下文管理 | 依赖外部 CLI 的 session 机制 | 自主控制：自动压缩 + 选择性裁剪 |
| 工具注册 | 受限于外部 CLI 的工具集 | 30 个内置 + 自定义工具 + MCP |
| Human-in-the-loop | 通过消息渠道绕一圈 | 原生 InteractionRequest 机制 |
| 成本控制 | 无内置 | Circuit Breaker（token/cost/loop 三重保护）|
| 错误恢复 | 进程崩溃 = 丢失上下文 | 异常 catch + 自动 retry + 会话持久化 |

### 9.2 飞书特有的交互设计

**交互卡片作为主要输出格式**，而非纯文本：

- **流式输出**：Agent 执行过程中实时更新同一张卡片，避免消息轰炸
- **操作按钮**：取消执行、审批确认、展开详情——全部在卡片内完成
- **状态指示**：卡片头部颜色变化（蓝色=运行中，绿色=完成，红色=出错）
- **富文本**：卡片内支持 Markdown，代码块有语法高亮

**群聊场景的差异化处理**：

- 群聊必须 @机器人才响应（避免误触发）
- 群聊共享 session（所有成员看到同一个上下文）
- 支持按群绑定不同 Agent（运维群 → monitor Agent，开发群 → assistant Agent）

### 9.3 安全模型

```
┌──────────────────────────────────────────────┐
│  Layer 1: 飞书平台                            │
│  ├─ 应用可见范围（限定哪些用户/部门能用）        │
│  └─ IP 白名单（限定事件推送来源）               │
├──────────────────────────────────────────────┤
│  Layer 2: CodyClaw Gateway                   │
│  ├─ 用户白名单（allowed_users per Agent）      │
│  ├─ 群聊白名单（allowed_groups per Agent）      │
│  └─ 消息去重（防止重放）                        │
├──────────────────────────────────────────────┤
│  Layer 3: Cody 权限系统                       │
│  ├─ 工具级权限（ALLOW/DENY/CONFIRM）           │
│  ├─ 路径沙箱（allowed_roots + 反遍历检查）      │
│  ├─ 命令黑名单（blocked_commands）              │
│  └─ 审计日志（所有操作记录到 SQLite）            │
├──────────────────────────────────────────────┤
│  Layer 4: Cody Circuit Breaker               │
│  ├─ Token 上限（防止无限循环消耗）              │
│  ├─ 成本上限（USD 硬限制）                     │
│  └─ 循环检测（相似输出自动中断）                │
└──────────────────────────────────────────────┘
```

---

## 10. 依赖清单

```toml
# pyproject.toml

[project]
name = "codyclaw"
version = "0.1.0"
requires-python = ">=3.10"

dependencies = [
    # 核心
    "cody-ai[all]",                 # Cody Agent Framework（含 SDK + CLI + Web）
    "fastapi>=0.115.0",             # Gateway HTTP 框架
    "uvicorn>=0.30.0",              # ASGI 服务器

    # 飞书
    "lark-oapi>=1.4.0",            # 飞书开放平台 SDK

    # 自动化
    "apscheduler>=3.10.0",         # 定时任务调度
    "pyyaml>=6.0",                 # 配置文件解析
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.7.0",
]

[project.scripts]
codyclaw = "codyclaw.main:main"
```

---

## 11. 部署方案

### 11.1 本地开发（推荐起步方式）

```bash
# 1. 安装
pip install -e ".[dev]"

# 2. 配置飞书应用（获取 App ID/Secret 后填入 config.yaml）
cp config.yaml.template config.yaml
vim config.yaml

# 3. 启动（WebSocket 长连接模式，无需公网 IP）
export ANTHROPIC_API_KEY="sk-ant-xxx"
codyclaw

# 4. 在飞书中给机器人发消息测试
```

### 11.2 生产部署（systemd）

```ini
# /etc/systemd/system/codyclaw.service

[Unit]
Description=CodyClaw AI Agent Gateway
After=network.target

[Service]
Type=simple
User=codyclaw
WorkingDirectory=/opt/codyclaw
EnvironmentFile=/opt/codyclaw/.env
ExecStart=/opt/codyclaw/venv/bin/codyclaw
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 11.3 Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install .

COPY codyclaw/ codyclaw/
COPY config.yaml .

EXPOSE 8080
CMD ["codyclaw"]
```

---

## 12. 开发路线图

### Phase 1: MVP（可运行的最小版本）

- [x] 技术设计文档
- [ ] 飞书渠道适配（收发文本消息）
- [ ] Gateway 核心（消息路由 + 执行调度）
- [ ] 单 Agent + 单用户流式输出
- [ ] 基础配置加载

### Phase 2: 核心能力

- [ ] 多 Agent 路由
- [ ] 群聊支持（@触发）
- [ ] 会话持久化（跨消息保持上下文）
- [ ] 交互卡片（流式输出 + 取消按钮）
- [ ] 执行审批（CONFIRM 级别工具 → 飞书卡片确认）

### Phase 3: 自动化

- [ ] Cron 定时任务
- [ ] BOOT.md 启动脚本
- [ ] 事件总线 + Hooks

### Phase 4: 增强

- [ ] 图片/文件消息支持
- [ ] 多模型支持（按 Agent 配置不同模型）
- [ ] Skills 管理（飞书命令安装/启用/禁用）
- [ ] Web 管理台（基于 Cody Web UI 改造）
- [ ] 记忆系统增强（飞书对话自动沉淀到记忆）

