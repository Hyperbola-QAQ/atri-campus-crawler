"""Process-local cache for authenticated payment portal sessions."""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass(frozen=True, repr=False)
class PortalSession:
    """The minimum session state needed to reuse a portal login."""

    cookies: dict[str, str] = field(repr=False)
    csrf_token: str = field(repr=False)


class InMemoryCookieCache:
    """TTL cache; values remain local to this process and are never persisted."""

    def __init__(self, ttl_seconds: float = 1800, max_entries: int = 1000):
        if max_entries < 1:
            raise ValueError("Cookie cache capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, PortalSession]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> PortalSession | None:
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
        async with self._lock:
            self._entries.pop(key, None)


# TODO(Redis): replace this process-local cache with a shared Redis backend.
