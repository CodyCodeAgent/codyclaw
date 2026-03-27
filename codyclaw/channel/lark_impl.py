# codyclaw/channel/lark_impl.py

import asyncio
import json
import logging
import os
from collections import OrderedDict
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    P2ImMessageReceiveV1,
)

from codyclaw.channel.base import LarkChannel, IncomingMessage, MessageHandler

logger = logging.getLogger(__name__)

_MAX_NAME_CACHE = 1000  # 用户名缓存上限（LRU 淘汰）


class LarkChannelImpl(LarkChannel):
    """基于 lark-oapi SDK 的飞书渠道实现"""

    def __init__(self, config: "LarkConfig"):
        self.config = config
        self._handlers: list[MessageHandler] = []
        self._ws_client = None
        self._ws_future = None                               # executor future，用于错误监控和 stop()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._user_name_cache: OrderedDict[str, str] = OrderedDict()  # open_id → name（LRU）

        # 初始化飞书 SDK 客户端
        self._client = lark.Client.builder() \
            .app_id(config.app_id) \
            .app_secret(config.app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # 注册同步包装器——lark SDK 在独立线程中调用，需桥接到 asyncio 事件循环
        self._event_handler = lark.EventDispatcherHandler.builder(
            config.encrypt_key or "",
            config.verification_token or "",
        ).register_p2_im_message_receive_v1(
            self._sync_on_message_event
        ).build()

    def _sync_on_message_event(self, ctx, event: P2ImMessageReceiveV1) -> None:
        """同步包装器：lark SDK 线程 → asyncio 事件循环桥接。"""
        if self._loop is None:
            logger.warning("Event loop not initialized, dropping message")
            return
        asyncio.run_coroutine_threadsafe(
            self._on_message_event(ctx, event), self._loop
        )

    def _on_ws_done(self, future) -> None:
        """WebSocket 线程结束回调，记录异常。"""
        if future.cancelled():
            # stop() 主动取消时的正常路径，不视为错误
            logger.info("WebSocket connection cancelled (normal shutdown)")
            return
        exc = future.exception()
        if exc is not None:
            logger.error(f"WebSocket connection terminated with error: {exc}")
        else:
            logger.info("WebSocket connection closed")

    async def start(self) -> None:
        # 保存事件循环引用，供 _sync_on_message_event 跨线程使用
        self._loop = asyncio.get_running_loop()

        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=self._event_handler,
            log_level=lark.LogLevel.WARNING,
        )
        # start() 是阻塞调用，放到线程池避免阻塞事件循环
        self._ws_future = self._loop.run_in_executor(None, self._ws_client.start)
        self._ws_future.add_done_callback(self._on_ws_done)

    async def stop(self) -> None:
        """关闭渠道连接"""
        if self._ws_future is not None and not self._ws_future.done():
            self._ws_future.cancel()
        self._ws_client = None
        self._ws_future = None

    async def _fetch_user_name(self, open_id: str) -> str:
        """获取用户显示名，LRU 缓存（上限 1000 条）。失败时回退到 open_id。
        需要飞书应用拥有 contact:user.base:readonly 权限。
        """
        if open_id in self._user_name_cache:
            self._user_name_cache.move_to_end(open_id)  # 更新 LRU 顺序
            return self._user_name_cache[open_id]

        name = open_id  # 默认回退值
        try:
            request = GetUserRequest.builder() \
                .user_id(open_id) \
                .user_id_type("open_id") \
                .build()
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, lambda: self._client.contact.v3.user.get(request)
            )
            if response.success() and response.data and response.data.user:
                name = response.data.user.name or open_id
        except Exception as e:
            logger.debug(f"Failed to fetch user name for {open_id}: {e}")

        self._user_name_cache[open_id] = name
        self._user_name_cache.move_to_end(open_id)
        if len(self._user_name_cache) > _MAX_NAME_CACHE:
            self._user_name_cache.popitem(last=False)  # 淘汰最久未使用的条目
        return name

    async def _on_message_event(self, ctx, event: P2ImMessageReceiveV1) -> None:
        """处理飞书消息事件（在 asyncio 事件循环中执行）"""
        msg = event.event.message
        content_json = json.loads(msg.content)
        text = content_json.get("text", "")

        mentions = []
        is_mention_bot = False
        if msg.mentions:
            for m in msg.mentions:
                mentions.append(m.id.open_id)
                if m.id.open_id == self.config.bot_open_id:
                    is_mention_bot = True
                    text = text.replace(f"@_user_{m.key}", "").strip()

        sender_open_id = event.event.sender.sender_id.open_id
        sender_name = await self._fetch_user_name(sender_open_id)

        incoming = IncomingMessage(
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            chat_type=msg.chat_type,
            sender_id=sender_open_id,
            sender_name=sender_name,
            content=text,
            msg_type=msg.message_type,
            mentions=mentions,
            is_mention_bot=is_mention_bot,
            raw=event.event.__dict__,
        )

        if msg.message_type == "image":
            image_key = content_json.get("image_key", "")
            if image_key:
                data = await self.download_resource(msg.message_id, image_key, type="image")
                incoming.images.append(data)

        for handler in self._handlers:
            await handler(incoming)

    async def send_text(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> str:
        """发送文本消息"""
        body_builder = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("text") \
            .content(json.dumps({"text": text}))
        if reply_to:
            body_builder = body_builder.quote_message_id(reply_to)
        body = body_builder.build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )
        if not response.success():
            raise RuntimeError(f"send_text failed: {response.msg}")
        return response.data.message_id

    async def send_card(self, chat_id: str, card: dict, reply_to: Optional[str] = None) -> str:
        """发送交互卡片（支持 Markdown 渲染）"""
        body_builder = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card))
        if reply_to:
            body_builder = body_builder.quote_message_id(reply_to)
        body = body_builder.build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )
        if not response.success():
            raise RuntimeError(f"send_card failed: {response.msg}")
        return response.data.message_id

    async def send_file(self, chat_id: str, file_path: str, file_name: str) -> str:
        """发送文件，返回 message_id"""
        ext = os.path.splitext(file_name)[1].lower()
        file_type_map = {
            ".pdf": "pdf",
            ".doc": "doc", ".docx": "doc",
            ".xls": "xls", ".xlsx": "xls",
            ".ppt": "ppt", ".pptx": "ppt",
            ".mp4": "mp4",
        }
        file_type = file_type_map.get(ext, "stream")
        loop = asyncio.get_running_loop()

        # Step 1: 上传文件到飞书 IM，获取 file_key
        with open(file_path, "rb") as f:
            upload_body = CreateFileRequestBody.builder() \
                .file_type(file_type) \
                .file_name(file_name) \
                .file(f) \
                .build()
            upload_request = CreateFileRequest.builder() \
                .request_body(upload_body) \
                .build()
            upload_resp = await loop.run_in_executor(
                None, lambda: self._client.im.v1.file.create(upload_request)
            )

        if not upload_resp.success():
            raise RuntimeError(f"Failed to upload file: {upload_resp.msg}")
        file_key = upload_resp.data.file_key

        # Step 2: 发送文件消息
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("file") \
            .content(json.dumps({"file_key": file_key})) \
            .build()
        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()

        response = await loop.run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )
        if not response.success():
            raise RuntimeError(f"Failed to send file message: {response.msg}")
        return response.data.message_id

    async def download_resource(
        self, message_id: str, file_key: str, type: str = "image"
    ) -> bytes:
        """下载消息中的图片或文件资源。
        type: "image" | "file"
        """
        request = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type(type) \
            .build()

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.im.v1.message_resource.get(request)
        )
        if not response.success():
            raise RuntimeError(f"Failed to download resource {file_key}: {response.msg}")
        return response.file.read()

    async def update_card(self, message_id: str, card: dict) -> None:
        """更新卡片内容（用于流式输出进度展示）"""
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps(card)) \
            .build()
        request = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: self._client.im.v1.message.patch(request)
        )

    def on_message(self, handler: MessageHandler) -> None:
        self._handlers.append(handler)
