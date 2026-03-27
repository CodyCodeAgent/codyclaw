# codyclaw/gateway/session_strategy.py

import time
from typing import Optional

SESSION_IDLE_TIMEOUT_HOURS = 24  # session 空闲超时时间


class SessionManager:
    """会话生命周期管理。

    策略：
    1. 单聊（p2p）：每个用户一个 session，跨消息保持上下文
       key = "{agent_id}:{user_id}"

    2. 群聊（group）：每个群一个 session，所有成员共享上下文
       key = "{agent_id}:{chat_id}"

    3. 超时归档：session 空闲超过 idle_timeout_hours 后下次请求时自动失效，
       新消息将以空 session 开始新对话（旧历史仍保留在 Cody 侧，可通过旧
       session_id 恢复）。
    """

    def __init__(self, idle_timeout_hours: int = SESSION_IDLE_TIMEOUT_HOURS):
        self._idle_timeout = idle_timeout_hours * 3600
        self._session_map: dict[str, str] = {}        # key → session_id
        self._last_active: dict[str, float] = {}       # key → unix timestamp

    def get(self, key: str) -> Optional[str]:
        """返回有效的 session_id；若 session 已超时则归档并返回 None。"""
        session_id = self._session_map.get(key)
        if session_id is None:
            return None
        if time.time() - self._last_active.get(key, 0) > self._idle_timeout:
            self._expire(key)
            return None
        return session_id

    def set(self, key: str, session_id: str) -> None:
        """记录 session_id 并刷新最后活跃时间。"""
        self._session_map[key] = session_id
        self._last_active[key] = time.time()

    def touch(self, key: str) -> None:
        """仅刷新最后活跃时间，不修改 session_id。"""
        if key in self._session_map:
            self._last_active[key] = time.time()

    def _expire(self, key: str) -> None:
        self._session_map.pop(key, None)
        self._last_active.pop(key, None)

    def all(self) -> dict[str, str]:
        """返回当前所有活跃 session 的快照（过滤已超时的条目）。"""
        now = time.time()
        return {
            k: v for k, v in self._session_map.items()
            if now - self._last_active.get(k, 0) <= self._idle_timeout
        }
