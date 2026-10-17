"""登录失败限流（进程内滑动窗口）。

定位：把"在线爆破无限次"降到"每个账号每窗口 N 次"。多 worker 部署时各进程独立
计数（额度 = N × worker 数）；要全局精确配额应换 Redis——这里是零依赖的基线防护。
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = max(1, int(limit))
        self.window = max(0.001, float(window_seconds))
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float] | None:
        events = self._events.get(key)
        if events is None:
            return None
        cutoff = now - self.window
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            return None
        return events

    def check(self, key: str) -> tuple[bool, int]:
        """返回 (是否允许, 建议 Retry-After 秒)。只读，不记录失败。"""
        key = key or ""
        now = time.monotonic()
        with self._lock:
            events = self._prune(key, now)
            if events is None or len(events) < self.limit:
                return True, 0
            retry_after = max(1, math.ceil(self.window - (now - events[0])))
            return False, retry_after

    def hit(self, key: str) -> None:
        key = key or ""
        now = time.monotonic()
        with self._lock:
            events = self._prune(key, now)
            if events is None:
                events = deque()
                self._events[key] = events
            events.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key or "", None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
