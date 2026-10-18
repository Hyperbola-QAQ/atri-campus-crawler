"""宿舍电量查询编排与凭据轮换。"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import tempfile
from datetime import datetime, time, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar
from zoneinfo import ZoneInfo

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from utils.electricity_identity import normalize_room_number
from services.cookie_cache import InMemoryCookieCache
from services.electricity_cache import DailyElectricityCache

logger = logging.getLogger(__name__)


class AccountPoolConfigurationError(RuntimeError):
    """内部账户池缺失或无效时抛出。"""


class ElectricityQueryError(RuntimeError):
    """账户池中的所有账户均无法获取读数时抛出。"""


class AccountNotFoundError(RuntimeError):
    """请求的账户不在账户池中时抛出。"""


class AccountAlreadyExistsError(RuntimeError):
    """账户标识已存在于账户池时抛出。"""


@dataclass(frozen=True, repr=False)
class ElectricityAccount:
    username: str = field(repr=False)
    password: str = field(repr=False)
    school: str = "HNUCM"

    @property
    def cache_key(self) -> str:
        """生成不可逆缓存键，键中不保留凭据。"""
        identity = f"{self.school}\0{self.username}\0{self.password}".encode("utf-8")
        return hashlib.sha256(identity).hexdigest()


class ElectricityAccountPool:
    """读取内部 JSON 账户列表并轮换使用顺序。"""

    _file_locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self, path: str | Path, *, pool_name: str = "电费", school: str = "HNUCM"
    ):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", school):
            raise ValueError("学校代码格式无效")
        self.school = school
        self.path = Path(path).expanduser()
        self.pool_name = pool_name
        self._next_index = 0
        self._lock = self._file_locks.setdefault(
            str(self.path.resolve()), asyncio.Lock()
        )

    def _read_accounts(self) -> list[ElectricityAccount]:
        records = [
            record for record in self._read_records() if record["school"] == self.school
        ]
        if not records:
            raise AccountPoolConfigurationError(f"{self.pool_name}账号池中没有可用账号")
        return [
            ElectricityAccount(record["xh"], record["pwd"], record["school"])
            for record in records
        ]

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
            school = record.get("school", "HNUCM")
            if not isinstance(school, str) or not re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,31}", school
            ):
                raise AccountPoolConfigurationError(
                    "账号池中的 school 必须为有效学校代码"
                )
            validated_records.append(
                {"school": school, "xh": record["xh"].strip(), "pwd": record["pwd"]}
            )
        return validated_records

    def _write_records(self, records: list[dict[str, str]]) -> None:
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as accounts_file:
                temporary_path = Path(accounts_file.name)
                json.dump(
                    {"accounts": records}, accounts_file, ensure_ascii=False, indent=2
                )
                accounts_file.write("\n")
                accounts_file.flush()
                os.fsync(accounts_file.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError) as exc:
            raise AccountPoolConfigurationError(
                f"{self.pool_name}账号池文件无法写入"
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def list_accounts(self) -> list[str]:
        """仅返回账户标识，密码不能离开此层。"""
        async with self._lock:
            return [
                record["xh"]
                for record in self._read_records()
                if record["school"] == self.school
            ]

    async def add_account(self, username: str, password: str) -> None:
        async with self._lock:
            records = self._read_records(allow_missing=True)
            if any(
                record["school"] == self.school and record["xh"] == username
                for record in records
            ):
                raise AccountAlreadyExistsError("账号已存在")
            records.append({"school": self.school, "xh": username, "pwd": password})
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
                    if record["school"] == self.school and record["xh"] == username
                ),
                None,
            )
            if index is None:
                raise AccountNotFoundError("账号不存在")
            if new_username is not None and new_username != username:
                if any(
                    record["school"] == self.school and record["xh"] == new_username
                    for record in records
                ):
                    raise AccountAlreadyExistsError("账号已存在")
                records[index]["xh"] = new_username
            if password is not None:
                records[index]["pwd"] = password
            self._write_records(records)
            return records[index]["xh"]

    async def delete_account(self, username: str) -> None:
        async with self._lock:
            records = self._read_records()
            new_records = [
                record
                for record in records
                if record["school"] != self.school or record["xh"] != username
            ]
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
    """教务系统独立使用的轮换凭据池。"""

    def __init__(self, path: str | Path, *, school: str = "HNUCM"):
        super().__init__(path, pool_name="教务", school=school)


class ElectricityService:
    """按轮换顺序尝试配置的账户并返回电表数据。"""

    def __init__(
        self,
        base_url: str,
        account_pool: ElectricityAccountPool,
        client_factory: Callable[[str], Any] = HNUCMElectricityClient,
        cookie_cache: InMemoryCookieCache | None = None,
        reading_cache: DailyElectricityCache | None = None,
    ):
        self.school = account_pool.school
        self.base_url = base_url
        self.account_pool = account_pool
        self.client_factory = client_factory
        self.cookie_cache = cookie_cache or InMemoryCookieCache()
        self.reading_cache = reading_cache or DailyElectricityCache.from_environment(
            school=self.school
        )
        default_catalog_path = (
            Path(__file__).resolve().parent.parent / "config" / "electricity_rooms.json"
        )
        catalog_path = os.getenv(
            "ELECTRICITY_ROOM_CATALOG_FILE", str(default_catalog_path)
        ).strip()
        self.catalog_path = self._school_path(catalog_path)
        default_state_path = default_catalog_path.with_name(
            "electricity_collection_state.json"
        )
        state_path = os.getenv(
            "ELECTRICITY_COLLECTION_STATE_FILE", str(default_state_path)
        ).strip()
        self.collection_state_path = self._school_path(state_path)
        self._catalog_lock = asyncio.Lock()
        self._collection_lock = asyncio.Lock()

    def _school_path(self, path: str) -> Path | None:
        if not path:
            return None
        value = Path(path)
        return (
            value
            if self.school == "HNUCM"
            else value.with_name(f"{value.stem}.{self.school}{value.suffix}")
        )

    @classmethod
    def from_environment(cls, school: str = "HNUCM") -> "ElectricityService":
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
            account_pool=ElectricityAccountPool(accounts_file, school=school),
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
                # 缓存的平台会话可能已过期，清除后重新登录一次。
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
        room_number = normalize_room_number(room_number)
        cached_result = await self.reading_cache.get(campus, room_number)
        if cached_result is not None:
            return cached_result

        result = await self.query_live(room_number, campus)
        await self.reading_cache.set(campus, room_number, result)
        return result

    async def query_live(self, room_number: str, campus: str) -> dict[str, Any]:
        """直接读取平台当前电表值，不使用日缓存。

        此接口独立于 query，使运维监测在日缓存已填充后仍能观察财务系统刷新。
        """
        if not self.base_url:
            raise AccountPoolConfigurationError("未配置电费平台地址")

        room_number = normalize_room_number(room_number)
        accounts = await self.account_pool.rotated_accounts()
        platform_error: ElectricityPlatformError | None = None
        for account in accounts:
            try:
                result = await self._query_account(account, room_number, campus)
                result["collected_at"] = datetime.now(timezone.utc).isoformat()
                return result
            except ElectricityPlatformError as exc:
                platform_error = exc
                await self.cookie_cache.delete(account.cache_key)
            except Exception:
                # 日志和 API 输出不能附带异常原文或账户身份。
                await self.cookie_cache.delete(account.cache_key)
                continue

        if platform_error is not None:
            raise ElectricityQueryError(str(platform_error)) from platform_error
        raise ElectricityQueryError("电费查询失败，请检查账号池或稍后重试")

    async def refresh_room_catalog(self) -> dict[str, Any]:
        """登录一次并持久化平台完整的可选宿舍目录。

        目录不含凭据、Cookie、电表读数或平台房间 ID，可安全提供给调用方，
        供客户端在查表前发现有效的校区与寝室组合。
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
                    "school": self.school,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "rooms": rooms,
                }
                self._write_room_catalog(catalog)
                return catalog

        if platform_error is not None:
            raise ElectricityQueryError(str(platform_error)) from platform_error
        raise ElectricityQueryError("寝室目录同步失败，请检查账号池或稍后重试")

    def get_room_catalog(self) -> dict[str, Any] | None:
        """读取最近一次成功同步的公开寝室目录。"""
        if self.catalog_path is None or not self.catalog_path.is_file():
            return None
        try:
            with self.catalog_path.open("r", encoding="utf-8") as catalog_file:
                catalog = json.load(catalog_file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(catalog, dict)
            or catalog.get("school", "HNUCM") != self.school
            or not isinstance(catalog.get("rooms"), list)
        ):
            return None
        # 旧版目录可能包含商户区域，读取缓存时也须过滤，防止定时采集查询商户电表。
        rooms = [
            room
            for room in catalog["rooms"]
            if isinstance(room, dict)
            and isinstance(room.get("campus_name"), str)
            and "宿舍" in room["campus_name"]
            and "商户" not in room["campus_name"]
        ]
        for room in rooms:
            number = HNUCMElectricityClient._room_number_from_options(
                str(room.get("building", "")), str(room.get("room", ""))
            )
            if number is not None:
                room["room_number"] = number
            elif room.get("building") or room.get("room"):
                room.pop("room_number", None)
        return {**catalog, "rooms": rooms}

    async def get_cached_room_reading(
        self, room_number: str, campus: str
    ) -> tuple[bool | None, dict[str, Any] | None]:
        """仅从缓存返回读数，不访问平台。

        元组首项 False 表示无效寝室，True 表示目录中有效寝室，None 表示目录尚未就绪。
        当日定时采集尚未结束时，有效寝室也可能暂时没有读数。
        """
        catalog = self.get_room_catalog()
        if catalog is None:
            return None, None
        room_number = normalize_room_number(room_number)
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
        """将中英文校区别名映射为平台区域 ID。"""
        labels = {
            "hanpu": "含浦",
            "含浦": "含浦",
            "dongtang": "东塘",
            "东塘": "东塘",
        }
        label = labels.get(campus)
        if label is None:
            return campus
        for room in catalog["rooms"]:
            if (
                isinstance(room, dict)
                and isinstance(room.get("campus"), str)
                and isinstance(room.get("campus_name"), str)
                and label in room["campus_name"]
            ):
                return room["campus"]
        return campus

    async def needs_today_collection(
        self, *, scheduled_time: time = time(5, 30)
    ) -> bool:
        """判断结算后是否仍有当天读数缺失。

        用于进程启动时恢复，确保定时采集时间之后重启也能补齐缺失或不完整的日缓存。
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

    def daily_collection_status(self) -> dict[str, Any]:
        """返回当天定时全寝室采集进度。

        服务端通过此轻量状态等待爬虫填充日缓存，避免逐间探测。
        completed 表示本轮已查询全部寝室，个别失败项仍可能由补采任务继续处理。
        """
        state = self._read_collection_state()
        today = self._collection_date()
        if state is None or state.get("date") != today:
            return {
                "collection_date": today,
                "total": 0,
                "queried": 0,
                "completed": False,
            }
        total = state.get("total")
        queried = state.get("next_index")
        if not isinstance(total, int) or total < 0:
            total = 0
        if not isinstance(queried, int) or queried < 0:
            queried = 0
        queried = min(queried, total)
        return {
            "collection_date": today,
            "total": total,
            "queried": queried,
            "completed": queried >= total,
        }

    async def collect_room_readings(
        self,
        *,
        batch_size: int = 2,
        interval_seconds: float = 1,
        jitter_seconds: float = 0,
        force: bool = False,
        retry_missing: bool = False,
    ) -> dict[str, int]:
        """采集日电量，可选择仅补采缓存缺失项。

        force 用于结算后的首轮全量刷新；补采任务不能启用，避免单个电表不可用触发全目录重查。
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
            catalog_total = len(rooms)
            if retry_missing:
                # 已缓存寝室已有当天不可变快照，重试仅补采缺失项，减少财务平台压力。
                rooms = [
                    room
                    for room in rooms
                    if await self.reading_cache.get(room["campus"], room["room_number"])
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
                # 重试目标是动态子集，不能使用全量扫描断点跳过；不持久化重试进度，后续重新检查缺失项。
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
            if retry_missing:
                # 缓存项已采集，本轮已尝试全部缺失寝室；即使无启动断点也须发布完整目录状态。
                # 不能持久化动态重试子集的索引。
                self._write_collection_state(
                    {
                        "date": collection_date,
                        "next_index": catalog_total,
                        "total": catalog_total,
                    }
                )
            if failed:
                logger.warning(
                    "Daily electricity collection incomplete: succeeded=%s failed=%s retry_missing=%s",
                    succeeded,
                    failed,
                    retry_missing,
                )
            return {"succeeded": succeeded, "failed": failed}

    async def resume_today_collection(self) -> dict[str, int] | None:
        """进程重启后恢复被中断的当日扫描。"""
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
        if (
            self.collection_state_path is None
            or not self.collection_state_path.is_file()
        ):
            return None
        try:
            with self.collection_state_path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return (
            state
            if isinstance(state, dict) and state.get("school", "HNUCM") == self.school
            else None
        )

    def _write_collection_state(self, state: dict[str, Any]) -> None:
        state = {**state, "school": self.school}
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
            # 发现目录成功后，即使可选的本地目录保存失败，也须继续提供实时查询。
            return


# TODO(SQLite): 将内部 JSON 账户文件迁移至 SQLite。
