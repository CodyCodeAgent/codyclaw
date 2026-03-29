# codyclaw/gateway/user_memory.py
#
# 用户级持久化记忆——跟着 user_id 走，跨群、跨 agent、跨 session。
# 让 AI 记住每个用户的偏好、背景、习惯、关系等个人信息。

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 100
_MAX_PROMPT_TOKENS = 1500  # 注入 prompt 的 token 预算（~4 chars/token）


@dataclass
class UserMemoryEntry:
    """一条用户记忆。"""
    content: str
    created_at: float = field(default_factory=time.time)


class UserMemoryStore:
    """基于文件的用户记忆存储，按 user_id 隔离。

    存储路径：{base_dir}/users/{user_id}.json
    每个用户一个 JSON 文件，包含最多 _MAX_ENTRIES 条记忆。
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self._base_dir = Path(base_dir) / "users"
        else:
            self._base_dir = Path.home() / ".codyclaw" / "users"
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _user_path(self, user_id: str) -> Path:
        # 用 user_id 直接做文件名（飞书 open_id 是 ou_xxx 格式，安全）
        return self._base_dir / f"{user_id}.json"

    def _load(self, user_id: str) -> list[UserMemoryEntry]:
        path = self._user_path(user_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [UserMemoryEntry(**item) for item in data]
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.warning(f"Failed to load user memory for {user_id}, resetting")
            return []

    def _save(self, user_id: str, entries: list[UserMemoryEntry]) -> None:
        path = self._user_path(user_id)
        data = [asdict(e) for e in entries]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, user_id: str, content: str) -> int:
        """添加一条记忆，返回当前总条数。"""
        entries = self._load(user_id)
        entries.append(UserMemoryEntry(content=content.strip()))
        # FIFO 淘汰
        if len(entries) > _MAX_ENTRIES:
            entries = entries[-_MAX_ENTRIES:]
        self._save(user_id, entries)
        return len(entries)

    def get_all(self, user_id: str) -> list[UserMemoryEntry]:
        """获取用户的所有记忆。"""
        return self._load(user_id)

    def get_for_prompt(self, user_id: str) -> str:
        """格式化用户记忆，用于注入消息上下文。

        返回空字符串表示无记忆。
        """
        entries = self._load(user_id)
        if not entries:
            return ""

        budget = _MAX_PROMPT_TOKENS * 4  # ~4 chars/token
        lines = ["[User profile]"]
        used = len(lines[0])

        for entry in entries:
            line = f"- {entry.content}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)

        return "\n".join(lines) if len(lines) > 1 else ""

    def clear(self, user_id: str) -> None:
        """清空用户的所有记忆。"""
        path = self._user_path(user_id)
        if path.exists():
            path.unlink()
