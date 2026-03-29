# codyclaw/channel/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


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
    mentions: list[dict] = field(default_factory=list)  # @提及的用户列表 [{name, open_id}]
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
    async def download_resource(
        self, message_id: str, file_key: str, resource_type: str = "image"
    ) -> bytes:
        """下载消息中的图片/文件资源"""

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None:
        """注册消息处理回调"""

    @abstractmethod
    async def update_card(self, message_id: str, card: dict) -> None:
        """更新已发送的交互卡片（用于进度展示）"""

    @abstractmethod
    async def add_reaction(self, message_id: str, emoji_type: str) -> str:
        """给消息添加表情回应，返回 reaction_id"""

    @abstractmethod
    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """移除消息上的表情回应"""

    @abstractmethod
    async def fetch_chat_history(
        self, chat_id: str, count: int = 10, before_message_id: Optional[str] = None
    ) -> list[dict]:
        """拉取群聊最近的消息历史（用于注入上下文）。

        返回 list[dict]，每条包含 sender_name, content, msg_type, create_time。
        按时间正序排列（最早的在前）。
        """
