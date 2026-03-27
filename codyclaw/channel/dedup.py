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
        # 先做重复判断，避免为已知重复消息额外淘汰有效条目
        if event_id in self._seen:
            return True
        # 容量保护（仅在确认是新消息时才执行淘汰）
        while len(self._seen) >= self._max_size:
            self._seen.popitem(last=False)
        self._seen[event_id] = now
        return False
