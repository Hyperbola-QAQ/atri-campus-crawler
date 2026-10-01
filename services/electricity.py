"""Credential rotation and query orchestration for dorm electricity data."""

import asyncio
import hashlib
import json
import os
import random
from datetime import datetime, time, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from services.cookie_cache import InMemoryCookieCache
from services.electricity_cache import DailyElectricityCache


class AccountPoolConfigurationError(RuntimeError):
    """Raised when the internal account pool is missing or invalid."""


class ElectricityQueryError(RuntimeError):
    """Raised when every account in the pool fails to retrieve the reading."""


class AccountNotFoundError(RuntimeError):
    """Raised when a requested account does not exist in the pool."""


class AccountAlreadyExistsError(RuntimeError):
    """Raised when an account identifier is already present in the pool."""


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

    def __init__(self, path: str | Path, *, pool_name: str = "电费"):
        self.path = Path(path).expanduser()
        self.pool_name = pool_name
        self._next_index = 0
        self._lock = asyncio.Lock()

    def _read_accounts(self) -> list[ElectricityAccount]:
        records = self._read_records()
        if not records:
            raise AccountPoolConfigurationError(f"{self.pool_name}账号池中没有可用账号")
        return [ElectricityAccount(record["xh"], record["pwd"]) for record in records]

    def _read_records(self, *, allow_missing: bool = False) -> list[dict[str, str]]:
        if not self.path.is_file():
            if allow_missing:
                return []
            raise AccountPoolConfigurationError(
                f"{self.pool_name}账号池文件未配置或不存在"
            )
        try:
            with self.path.open("r", encoding="utf-8") as accounts_file:
                data = json.load(accounts_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AccountPoolConfigurationError(
                f"{self.pool_name}账号池文件无法读取"
            ) from exc

        records = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise AccountPoolConfigurationError(f"{self.pool_name}账号池格式无效")

        validated_records: list[dict[str, str]] = []
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
            validated_records.append({"xh": record["xh"].strip(), "pwd": record["pwd"]})
        return validated_records

    def _write_records(self, records: list[dict[str, str]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            with temporary_path.open("w", encoding="utf-8") as accounts_file:
                json.dump(
                    {"accounts": records}, accounts_file, ensure_ascii=False, indent=2
                )
                accounts_file.write("\n")
            os.replace(temporary_path, self.path)
        except (OSError, TypeError) as exc:
            raise AccountPoolConfigurationError(
                f"{self.pool_name}账号池文件无法写入"
            ) from exc

    async def list_accounts(self) -> list[str]:
        """Return account identifiers only; passwords must never leave this layer."""
        async with self._lock:
            return [record["xh"] for record in self._read_records()]

    async def add_account(self, username: str, password: str) -> None:
        async with self._lock:
            records = self._read_records(allow_missing=True)
            if any(record["xh"] == username for record in records):
                raise AccountAlreadyExistsError("账号已存在")
            records.append({"xh": username, "pwd": password})
            self._write_records(records)

    async def update_account(
        self, username: str, *, new_username: str | None, password: str | None
    ) -> str:
        async with self._lock:
            records = self._read_records()
            index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if record["xh"] == username
                ),
                None,
            )
            if index is None:
                raise AccountNotFoundError("账号不存在")
            if new_username is not None and new_username != username:
                if any(record["xh"] == new_username for record in records):
                    raise AccountAlreadyExistsError("账号已存在")
                records[index]["xh"] = new_username
            if password is not None:
                records[index]["pwd"] = password
            self._write_records(records)
            return records[index]["xh"]

    async def delete_account(self, username: str) -> None:
        async with self._lock:
            records = self._read_records()
            new_records = [record for record in records if record["xh"] != username]
            if len(new_records) == len(records):
                raise AccountNotFoundError("账号不存在")
            self._write_records(new_records)

    async def rotated_accounts(self) -> list[ElectricityAccount]:
        accounts = self._read_accounts()
        async with self._lock:
            start = self._next_index % len(accounts)
            self._next_index = (start + 1) % len(accounts)
        return accounts[start:] + accounts[:start]


class AcademicAccountPool(ElectricityAccountPool):
    """A separate rotating credential pool for the academic affairs system."""

    def __init__(self, path: str | Path):
        super().__init__(path, pool_name="教务")


class ElectricityService:
    """Attempts configured accounts in rotating order and returns meter data."""

    def __init__(
        self,
        base_url: str,
        account_pool: ElectricityAccountPool,
        client_factory: Callable[[str], Any] = HNUCMElectricityClient,
        cookie_cache: InMemoryCookieCache | None = None,
        reading_cache: DailyElectricityCache | None = None,
    ):
        self.base_url = base_url
        self.account_pool = account_pool
        self.client_factory = client_factory
        self.cookie_cache = cookie_cache or InMemoryCookieCache()
        self.reading_cache = reading_cache or DailyElectricityCache.from_environment()
        default_catalog_path = (
            Path(__file__).resolve().parent.parent / "config" / "electricity_rooms.json"
        )
        catalog_path = os.getenv(
            "ELECTRICITY_ROOM_CATALOG_FILE", str(default_catalog_path)
        ).strip()
        self.catalog_path = Path(catalog_path) if catalog_path else None
        default_state_path = default_catalog_path.with_name(
            "electricity_collection_state.json"
        )
        state_path = os.getenv(
            "ELECTRICITY_COLLECTION_STATE_FILE", str(default_state_path)
        ).strip()
        self.collection_state_path = Path(state_path) if state_path else None
        self._catalog_lock = asyncio.Lock()
        self._collection_lock = asyncio.Lock()

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
            base_url=HNUCMElectricityClient.DEFAULT_BASE_URL,
            account_pool=ElectricityAccountPool(accounts_file),
        )

    async def _query_account(
        self, account: ElectricityAccount, room_number: str, campus: str
    ) -> dict[str, Any]:
        client = self.client_factory(self.base_url)
        cached_session = await self.cookie_cache.get(account.cache_key)
        if cached_session is not None:
            try:
                result, session = await client.query(
                    room_number=room_number,
                    campus=campus,
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
            campus=campus,
            username=account.username,
            password=account.password,
            session=None,
        )
        await self.cookie_cache.set(account.cache_key, session)
        return result

    async def query(self, room_number: str, campus: str) -> dict[str, Any]:
        cached_result = await self.reading_cache.get(campus, room_number)
        if cached_result is not None:
            return cached_result

        result = await self.query_live(room_number, campus)
        await self.reading_cache.set(campus, room_number, result)
        return result

    async def query_live(self, room_number: str, campus: str) -> dict[str, Any]:
        """Read a current meter value from the portal without using daily cache.

        This is deliberately separate from :meth:`query`: operational monitors
        need to see a finance-system refresh even after the daily API cache has
        been populated.
        """
        if not self.base_url:
            raise AccountPoolConfigurationError("未配置电费平台地址")

        accounts = await self.account_pool.rotated_accounts()
        platform_error: ElectricityPlatformError | None = None
        for account in accounts:
            try:
                result = await self._query_account(account, room_number, campus)
                return result
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

    async def refresh_room_catalog(self) -> dict[str, Any]:
        """Log in once and persist the portal's complete selectable room inventory.

        Catalog records deliberately contain no credentials, cookies, meter
        readings, or portal room IDs.  They are safe to serve to callers and
        let clients discover valid campus/room combinations before querying a
        meter.
        """
        if not self.base_url:
            raise AccountPoolConfigurationError("未配置电费平台地址")

        async with self._catalog_lock:
            accounts = await self.account_pool.rotated_accounts()
            platform_error: ElectricityPlatformError | None = None
            for account in accounts:
                client = self.client_factory(self.base_url)
                cached_session = await self.cookie_cache.get(account.cache_key)
                try:
                    rooms, session = await client.discover_rooms(
                        account.username, account.password, session=cached_session
                    )
                except ElectricityPlatformError as exc:
                    platform_error = exc
                    await self.cookie_cache.delete(account.cache_key)
                    continue
                except Exception:
                    await self.cookie_cache.delete(account.cache_key)
                    continue

                await self.cookie_cache.set(account.cache_key, session)
                catalog = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "rooms": rooms,
                }
                self._write_room_catalog(catalog)
                return catalog

        if platform_error is not None:
            raise ElectricityQueryError(str(platform_error)) from platform_error
        raise ElectricityQueryError("寝室目录同步失败，请检查账号池或稍后重试")

    def get_room_catalog(self) -> dict[str, Any] | None:
        """Read the last successfully synchronised public room catalog."""
        if self.catalog_path is None or not self.catalog_path.is_file():
            return None
        try:
            with self.catalog_path.open("r", encoding="utf-8") as catalog_file:
                catalog = json.load(catalog_file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(catalog, dict) or not isinstance(catalog.get("rooms"), list):
            return None
        # Catalogs written by older releases can include non-dorm merchant
        # areas.  Filter them at read time too, so an existing cache never
        # causes the scheduled reader to query merchant meters.
        rooms = [
            room
            for room in catalog["rooms"]
            if isinstance(room, dict)
            and isinstance(room.get("campus_name"), str)
            and "宿舍" in room["campus_name"]
            and "商户" not in room["campus_name"]
        ]
        return {**catalog, "rooms": rooms}

    async def get_cached_room_reading(
        self, room_number: str, campus: str
    ) -> tuple[bool | None, dict[str, Any] | None]:
        """Return a reading only from cache, without contacting the portal.

        The first tuple value is ``False`` for an invalid room, ``True`` for a
        valid catalogued room, and ``None`` while the catalog itself is not
        ready.  A valid room can temporarily have no reading while the daily
        scheduled collection is still progressing.
        """
        catalog = self.get_room_catalog()
        if catalog is None:
            return None, None
        resolved_campus = self._resolve_catalog_campus(catalog, campus)
        known_room = any(
            room.get("campus") == resolved_campus
            and room.get("room_number") == room_number
            for room in catalog["rooms"]
        )
        if not known_room:
            return False, None
        return True, await self.reading_cache.get(resolved_campus, room_number)

    @staticmethod
    def _resolve_catalog_campus(catalog: dict[str, Any], campus: str) -> str:
        """Map the legacy ``hanpu`` API alias to its current portal area ID."""
        if campus != "hanpu":
            return campus
        for room in catalog["rooms"]:
            if (
                isinstance(room, dict)
                and isinstance(room.get("campus"), str)
                and isinstance(room.get("campus_name"), str)
                and "含浦" in room["campus_name"]
            ):
                return room["campus"]
        return campus

    async def needs_today_collection(
        self, *, scheduled_time: time = time(11)
    ) -> bool:
        """Return whether any current-day reading is missing after settlement.

        This is used on process startup: a restart after the scheduled time
        should repair a missing or incomplete daily cache.
        """
        timezone_name = os.getenv("ELECTRICITY_CACHE_TIMEZONE", "Asia/Shanghai")
        now = datetime.now(ZoneInfo(timezone_name))
        if now.time() < scheduled_time:
            return False
        catalog = self.get_room_catalog()
        if catalog is None:
            return True
        rooms = [
            room
            for room in catalog["rooms"]
            if isinstance(room, dict)
            and isinstance(room.get("campus"), str)
            and isinstance(room.get("room_number"), str)
        ]
        for room in rooms:
            if not await self.reading_cache.get(room["campus"], room["room_number"]):
                return True
        return False

    async def collect_room_readings(
        self,
        *,
        batch_size: int = 2,
        interval_seconds: float = 1,
        jitter_seconds: float = 0,
        force: bool = False,
        retry_missing: bool = False,
    ) -> dict[str, int]:
        """Collect daily readings, optionally retrying only cache misses.

        ``force`` is for the first daily scan: it deliberately refreshes every
        reading after settlement.  Retry jobs must not use it, otherwise a
        single unavailable meter causes the whole catalog to be queried again.
        """
        if batch_size < 1 or interval_seconds <= 0 or jitter_seconds < 0:
            raise ValueError("电费采集计划参数无效")
        if force and retry_missing:
            raise ValueError("强制采集不能与缺失重试同时使用")
        async with self._collection_lock:
            catalog = self.get_room_catalog()
            if catalog is None:
                catalog = await self.refresh_room_catalog()

            rooms = [
                room
                for room in catalog["rooms"]
                if isinstance(room, dict)
                and isinstance(room.get("campus"), str)
                and isinstance(room.get("room_number"), str)
            ]
            if retry_missing:
                # Cached rooms already have today's immutable snapshot.  Keep
                # retries small and avoid putting avoidable pressure on the
                # finance platform.
                rooms = [
                    room
                    for room in rooms
                    if await self.reading_cache.get(
                        room["campus"], room["room_number"]
                    )
                    is None
                ]
            collection_date = self._collection_date()
            state = self._read_collection_state()
            next_index = (
                int(state["next_index"])
                if state is not None
                and state.get("date") == collection_date
                and isinstance(state.get("next_index"), int)
                else 0
            )
            if force or retry_missing:
                # Retry targets are a dynamic subset, so a checkpoint from
                # the full scan must never skip them.  Retry progress is not
                # persisted; a later retry safely rechecks remaining misses.
                next_index = 0
            next_index = min(max(0, next_index), len(rooms))
            succeeded = 0
            failed = 0
            loop = asyncio.get_running_loop()
            first_batch_at = loop.time()
            for batch_index in range(next_index, len(rooms), batch_size):
                target = (
                    first_batch_at
                    + ((batch_index - next_index) // batch_size) * interval_seconds
                    + random.uniform(-jitter_seconds, jitter_seconds)
                )
                await asyncio.sleep(max(0, target - loop.time()))
                batch = rooms[batch_index : batch_index + batch_size]
                for room in batch:
                    try:
                        if force:
                            result = await self.query_live(
                                room["room_number"], room["campus"]
                            )
                            await self.reading_cache.set(
                                room["campus"], room["room_number"], result
                            )
                        else:
                            await self.query(room["room_number"], room["campus"])
                    except (AccountPoolConfigurationError, ElectricityQueryError):
                        failed += 1
                    else:
                        succeeded += 1
                if not retry_missing:
                    self._write_collection_state(
                        {
                            "date": collection_date,
                            "next_index": batch_index + len(batch),
                            "total": len(rooms),
                        }
                    )
            return {"succeeded": succeeded, "failed": failed}

    async def resume_today_collection(self) -> dict[str, int] | None:
        """Resume an interrupted daily scan after a process restart."""
        state = self._read_collection_state()
        if state is None or state.get("date") != self._collection_date():
            return None
        catalog = self.get_room_catalog()
        if catalog is None:
            return None
        if not isinstance(state.get("next_index"), int):
            return None
        if state["next_index"] >= len(catalog["rooms"]):
            return None
        return await self.collect_room_readings()

    def _collection_date(self) -> str:
        timezone_name = os.getenv("ELECTRICITY_CACHE_TIMEZONE", "Asia/Shanghai")
        return datetime.now(ZoneInfo(timezone_name)).date().isoformat()

    def _read_collection_state(self) -> dict[str, Any] | None:
        if self.collection_state_path is None or not self.collection_state_path.is_file():
            return None
        try:
            with self.collection_state_path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _write_collection_state(self, state: dict[str, Any]) -> None:
        if self.collection_state_path is None:
            return
        try:
            self.collection_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.collection_state_path.with_suffix(
                f"{self.collection_state_path.suffix}.tmp"
            )
            with temporary_path.open("w", encoding="utf-8") as state_file:
                json.dump(state, state_file, ensure_ascii=False)
                state_file.write("\n")
            os.replace(temporary_path, self.collection_state_path)
        except OSError:
            return

    def _write_room_catalog(self, catalog: dict[str, Any]) -> None:
        if self.catalog_path is None:
            return
        try:
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.catalog_path.with_suffix(
                f"{self.catalog_path.suffix}.tmp"
            )
            with temporary_path.open("w", encoding="utf-8") as catalog_file:
                json.dump(catalog, catalog_file, ensure_ascii=False, indent=2)
                catalog_file.write("\n")
            os.replace(temporary_path, self.catalog_path)
        except OSError:
            # Discovery succeeded, so keep serving live queries even if an
            # optional local catalog cannot be persisted.
            return


# TODO(SQLite): replace the internal JSON account file with SQLite persistence.
