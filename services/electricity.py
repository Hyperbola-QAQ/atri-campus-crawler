"""Credential rotation and query orchestration for dorm electricity data."""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from services.cookie_cache import InMemoryCookieCache


class AccountPoolConfigurationError(RuntimeError):
    """Raised when the internal account pool is missing or invalid."""


class ElectricityQueryError(RuntimeError):
    """Raised when every account in the pool fails to retrieve the reading."""


@dataclass(frozen=True, repr=False)
class ElectricityAccount:
    username: str = field(repr=False)
    password: str = field(repr=False)

    @property
    def cache_key(self) -> str:
        """Return a non-reversible cache key without retaining credentials in it."""
        identity = f"{self.username}\0{self.password}".encode("utf-8")
        return hashlib.sha256(identity).hexdigest()


class ElectricityAccountPool:
    """Read an internal JSON account list and rotate account order."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._next_index = 0
        self._lock = asyncio.Lock()

    def _read_accounts(self) -> list[ElectricityAccount]:
        if not self.path.is_file():
            raise AccountPoolConfigurationError("电费账号池文件未配置或不存在")
        try:
            with self.path.open("r", encoding="utf-8") as accounts_file:
                data = json.load(accounts_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AccountPoolConfigurationError("电费账号池文件无法读取") from exc

        records = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(records, list) or not records:
            raise AccountPoolConfigurationError("电费账号池中没有可用账号")

        accounts: list[ElectricityAccount] = []
        for record in records:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("xh"), str)
                or not isinstance(record.get("pwd"), str)
                or not record["xh"].strip()
                or not record["pwd"]
            ):
                raise AccountPoolConfigurationError(
                    "账号池中的每个账号必须包含非空 xh 和 pwd 字段"
                )
            accounts.append(ElectricityAccount(record["xh"], record["pwd"]))
        return accounts

    async def rotated_accounts(self) -> list[ElectricityAccount]:
        accounts = self._read_accounts()
        async with self._lock:
            start = self._next_index % len(accounts)
            self._next_index = (start + 1) % len(accounts)
        return accounts[start:] + accounts[:start]


class ElectricityService:
    """Attempts configured accounts in rotating order and returns meter data."""

    def __init__(
        self,
        base_url: str,
        account_pool: ElectricityAccountPool,
        client_factory: Callable[[str], Any] = HNUCMElectricityClient,
        cookie_cache: InMemoryCookieCache | None = None,
    ):
        self.base_url = base_url
        self.account_pool = account_pool
        self.client_factory = client_factory
        self.cookie_cache = cookie_cache or InMemoryCookieCache()

    @classmethod
    def from_environment(cls) -> "ElectricityService":
        default_accounts_file = (
            Path(__file__).resolve().parent.parent
            / "config"
            / "electricity_accounts.json"
        )
        accounts_file = os.getenv(
            "ELECTRICITY_ACCOUNTS_FILE", str(default_accounts_file)
        )
        return cls(
            base_url=os.getenv("ELECTRICITY_BASE_URL", "").strip(),
            account_pool=ElectricityAccountPool(accounts_file),
        )

    async def _query_account(
        self, account: ElectricityAccount, room_number: str
    ) -> dict[str, Any]:
        client = self.client_factory(self.base_url)
        cached_session = await self.cookie_cache.get(account.cache_key)
        if cached_session is not None:
            try:
                result, session = await client.query(
                    room_number=room_number,
                    username=account.username,
                    password=account.password,
                    session=cached_session,
                )
            except Exception:
                # A cached portal session may have expired; discard it and log in once.
                await self.cookie_cache.delete(account.cache_key)
            else:
                await self.cookie_cache.set(account.cache_key, session)
                return result

        result, session = await client.query(
            room_number=room_number,
            username=account.username,
            password=account.password,
            session=None,
        )
        await self.cookie_cache.set(account.cache_key, session)
        return result

    async def query(self, room_number: str) -> dict[str, Any]:
        if not self.base_url:
            raise AccountPoolConfigurationError("未配置电费平台地址")

        accounts = await self.account_pool.rotated_accounts()
        platform_error: ElectricityPlatformError | None = None
        for account in accounts:
            try:
                return await self._query_account(account, room_number)
            except ElectricityPlatformError as exc:
                platform_error = exc
                await self.cookie_cache.delete(account.cache_key)
            except Exception:
                # Do not attach the exception or account identity to logs or API output.
                await self.cookie_cache.delete(account.cache_key)
                continue

        if platform_error is not None:
            raise ElectricityQueryError(str(platform_error)) from platform_error
        raise ElectricityQueryError("电费查询失败，请检查账号池或稍后重试")


# TODO(SQLite): replace the internal JSON account file with SQLite persistence.
