"""Concurrency control and Multi-Tier Rate Limiting."""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TokenBucketLimiter:
    """In-memory sliding window rate limiter for user/group spam prevention."""

    def __init__(self, capacity: int, window_seconds: float = 60.0):
        self.capacity = capacity
        self.window_seconds = window_seconds
        self._timestamps: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        """
        Check if an action is allowed for `key`. If allowed, records the timestamp and returns True.
        Otherwise returns False.
        """
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._timestamps[key] if t > cutoff]
            if len(timestamps) < self.capacity:
                timestamps.append(now)
                self._timestamps[key] = timestamps
                return True
            self._timestamps[key] = timestamps
            return False

    async def get_retry_after(self, key: str) -> float:
        """Get the time in seconds until the oldest request in the window expires."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._timestamps[key] if t > cutoff]
            if not timestamps or len(timestamps) < self.capacity:
                return 0.0
            oldest = timestamps[0]
            return max(0.0, (oldest + self.window_seconds) - now)


class ConcurrencyManager:
    """
    Manages multi-tier concurrency limits:
    - Global max concurrent tasks
    - Per-user max active tasks
    - Per-group max active tasks
    """

    def __init__(
        self,
        max_global: int = 3,
        max_user: int = 1,
        max_group: int = 2,
    ):
        self.max_global = max_global
        self.max_user = max_user
        self.max_group = max_group

        self._global_semaphore = asyncio.Semaphore(max_global)
        self._active_users: Dict[int, int] = defaultdict(int)
        self._active_groups: Dict[int, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def can_start(self, user_id: int, chat_id: Optional[int] = None, is_group: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Pre-flight check if user/group is within active task limits.
        """
        async with self._lock:
            if self._active_users[user_id] >= self.max_user:
                return False, f"You already have {self._active_users[user_id]} active download task running. Please wait for it to finish."

            if is_group and chat_id is not None:
                if self._active_groups[chat_id] >= self.max_group:
                    return False, f"This group already has {self._active_groups[chat_id]} active downloads in progress. Please wait for them to finish."

            return True, None

    class _ContextToken:
        def __init__(self, manager: "ConcurrencyManager", user_id: int, chat_id: Optional[int], is_group: bool):
            self.manager = manager
            self.user_id = user_id
            self.chat_id = chat_id
            self.is_group = is_group

        async def __aenter__(self):
            # Acquire global semaphore first
            await self.manager._global_semaphore.acquire()
            async with self.manager._lock:
                self.manager._active_users[self.user_id] += 1
                if self.is_group and self.chat_id is not None:
                    self.manager._active_groups[self.chat_id] += 1
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            async with self.manager._lock:
                self.manager._active_users[self.user_id] = max(0, self.manager._active_users[self.user_id] - 1)
                if self.manager._active_users[self.user_id] == 0:
                    self.manager._active_users.pop(self.user_id, None)

                if self.is_group and self.chat_id is not None:
                    self.manager._active_groups[self.chat_id] = max(0, self.manager._active_groups[self.chat_id] - 1)
                    if self.manager._active_groups[self.chat_id] == 0:
                        self.manager._active_groups.pop(self.chat_id, None)

            self.manager._global_semaphore.release()

    def acquire(self, user_id: int, chat_id: Optional[int] = None, is_group: bool = False) -> "_ContextToken":
        """Context manager to safely acquire and release slot in the worker pool."""
        return self._ContextToken(self, user_id, chat_id, is_group)
