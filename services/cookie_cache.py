"""缴费会话的有界进程内缓存；凭据键由调用方隔离，Redis 迁移尚未实现。"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass(frozen=True, repr=False)
class PortalSession:
    """Cookie 与 CSRF 不进入 repr；frozen 不冻结内部字典，缓存边界仍须复制。"""

    cookies: dict[str, str] = field(repr=False)
    csrf_token: str = field(repr=False)


class InMemoryCookieCache:
    """使用单调时钟的非滑动 TTL 缓存，超容量按插入顺序淘汰，不是 LRU。"""

    def __init__(self, ttl_seconds: float = 1800, max_entries: int = 1000):
        """容量必须为正；锁只协调当前实例，不提供跨进程互斥。"""
        if max_entries < 1:
            raise ValueError("Cookie cache capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, PortalSession]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> PortalSession | None:
        """返回未过期会话的独立 Cookie 副本，到期时刻视为失效。"""
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, session = entry
            if expires_at <= time.monotonic():
                self._entries.pop(key, None)
                return None
            return PortalSession(dict(session.cookies), session.csrf_token)

    async def set(self, key: str, session: PortalSession) -> None:
        """复制输入，重置有效期，并在同一锁内清理到期项及限制容量。"""
        async with self._lock:
            now = time.monotonic()
            for old_key, (expiry, _) in list(self._entries.items()):
                if expiry <= now:
                    del self._entries[old_key]
            self._entries[key] = (
                now + self.ttl_seconds,
                PortalSession(dict(session.cookies), session.csrf_token),
            )
            while len(self._entries) > self.max_entries:
                self._entries.pop(next(iter(self._entries)))

    async def delete(self, key: str) -> None:
        """幂等删除缓存键，不影响已经返回给调用方的副本。"""
        async with self._lock:
            self._entries.pop(key, None)


# TODO(Redis): 将进程内缓存迁移至共享 Redis。
