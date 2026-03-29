# codyclaw/gateway/session_strategy.py

import logging
import time
from typing import Optional

SESSION_IDLE_TIMEOUT_HOURS = 24  # session 空闲超时时间

logger = logging.getLogger(__name__)


class SessionManager:
    """会话生命周期管理（支持 DB 持久化，重启后可恢复）。

    策略：
    1. 单聊（p2p）：每个用户一个 session，跨消息保持上下文
       key = "{agent_id}:{user_id}"

    2. 群聊（group）：每个群一个 session，所有成员共享上下文
       key = "{agent_id}:{chat_id}"

    3. 超时归档：session 空闲超过 idle_timeout_hours 后下次请求时自动失效，
       新消息将以空 session 开始新对话（旧历史仍保留在 Cody 侧，可通过旧
       session_id 恢复）。

    4. 持久化：每次 set() 时写入 DB，启动时从 DB 恢复。
    """

    def __init__(
        self,
        idle_timeout_hours: int = SESSION_IDLE_TIMEOUT_HOURS,
        db_path: str = "",
    ):
        self._idle_timeout = idle_timeout_hours * 3600
        self._session_map: dict[str, str] = {}        # key → session_id
        self._last_active: dict[str, float] = {}       # key → unix timestamp
        self._db_path = db_path
        if db_path:
            self._restore_from_db()

    def _restore_from_db(self) -> None:
        """从 DB 恢复 session 映射（启动时调用）。"""
        from codyclaw.db import load_sessions
        try:
            rows = load_sessions(self._db_path)
        except Exception as e:
            logger.warning(f"Failed to restore sessions from DB: {e}")
            return
        now = time.time()
        restored = 0
        expired_keys = []
        for row in rows:
            key = row["session_key"]
            updated_at = row["updated_at"]
            if now - updated_at > self._idle_timeout:
                expired_keys.append(key)
                continue
            self._session_map[key] = row["session_id"]
            self._last_active[key] = updated_at
            restored += 1
        # 清理 DB 中过期的条目
        if expired_keys:
            from codyclaw.db import delete_session
            for key in expired_keys:
                try:
                    delete_session(self._db_path, key)
                except Exception:
                    pass
            logger.info(f"Cleaned up {len(expired_keys)} expired sessions from DB")
        if restored:
            logger.info(f"Restored {restored} sessions from DB")

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
        """记录 session_id 并刷新最后活跃时间。同步写 DB。"""
        now = time.time()
        self._session_map[key] = session_id
        self._last_active[key] = now
        if self._db_path:
            self._persist(key, session_id, now)

    def touch(self, key: str) -> None:
        """仅刷新最后活跃时间，不修改 session_id。"""
        if key in self._session_map:
            now = time.time()
            self._last_active[key] = now
            if self._db_path:
                self._persist(key, self._session_map[key], now)

    def _expire(self, key: str) -> None:
        self._session_map.pop(key, None)
        self._last_active.pop(key, None)
        if self._db_path:
            from codyclaw.db import delete_session
            try:
                delete_session(self._db_path, key)
            except Exception:
                pass

    def _persist(self, key: str, session_id: str, updated_at: float) -> None:
        """写入 DB（同步，SQLite 极快）。"""
        from codyclaw.db import save_session
        try:
            save_session(self._db_path, key, session_id, updated_at)
        except Exception as e:
            logger.debug(f"Failed to persist session {key}: {e}")

    def all(self) -> dict[str, str]:
        """返回当前所有活跃 session 的快照（过滤已超时的条目）。"""
        now = time.time()
        return {
            k: v for k, v in self._session_map.items()
            if now - self._last_active.get(k, 0) <= self._idle_timeout
        }
