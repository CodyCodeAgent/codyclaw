# codyclaw/gateway/router.py

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    from codyclaw.channel.base import IncomingMessage


@dataclass
class AgentConfig:
    """Agent 配置"""
    agent_id: str
    name: str
    workdir: str                           # Agent 工作目录
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""                      # 模型 API Key（覆盖全局 cody.model_api_key）
    base_url: str = ""                     # 模型 Base URL（用于第三方兼容接口）
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

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """按 agent_id 查找 Agent 配置"""
        return self._agents.get(agent_id)

    def iter_agents(self) -> Iterator[AgentConfig]:
        """遍历所有已注册的 Agent 配置"""
        return iter(self._agents.values())

    def resolve(self, msg: "IncomingMessage") -> Optional[AgentConfig]:
        """根据消息解析应该处理的 Agent"""
        # 1. 群聊场景：查运行时绑定
        if msg.chat_type == "group":
            agent_id = self._group_bindings.get(msg.chat_id)
            if agent_id and agent_id in self._agents:
                agent = self._agents[agent_id]
                # 检查触发条件
                if agent.trigger_mode == "mention" and not msg.is_mention_bot:
                    return None
                if agent.trigger_mode == "prefix" and not msg.content.startswith(agent.prefix):
                    return None
                # 用户白名单（空 = 所有人）
                if agent.allowed_users and msg.sender_id not in agent.allowed_users:
                    return None
                return agent

        # 2. 单聊场景：查运行时绑定
        if msg.chat_type == "p2p":
            agent_id = self._user_bindings.get(msg.sender_id)
            if agent_id and agent_id in self._agents:
                return self._agents[agent_id]

        # 3. 回退到默认 Agent
        if self._default_agent and self._default_agent in self._agents:
            agent = self._agents[self._default_agent]
            # 用户白名单（空 = 所有人）
            if agent.allowed_users and msg.sender_id not in agent.allowed_users:
                return None
            # 群聊白名单（空 = 所有群；p2p 消息跳过此检查）
            if msg.chat_type == "group" and agent.allowed_groups and \
                    msg.chat_id not in agent.allowed_groups:
                return None
            # 触发模式（仅群聊需要检查；p2p 消息始终触发）
            if msg.chat_type == "group":
                if agent.trigger_mode == "mention" and not msg.is_mention_bot:
                    return None
                if agent.trigger_mode == "prefix" and not msg.content.startswith(agent.prefix):
                    return None
            return agent

        return None
