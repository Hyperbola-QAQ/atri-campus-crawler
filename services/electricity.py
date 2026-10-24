"""编排电费账户、会话、日缓存与可恢复采集；公开结果不包含登录凭据。
目录和断点可选持久化，账户写入失败则明确报错。
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import tempfile
from datetime import datetime, time, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar
from zoneinfo import ZoneInfo
from weakref import WeakValueDictionary

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from utils.electricity_identity import normalize_room_number
from utils.electricity_reading import same_reading
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
    """仅供内部使用的凭据对象，不进入 repr 或公共 API；学校参与缓存隔离。"""

    username: str = field(repr=False)
    password: str = field(repr=False)
    school: str = "HNUCM"

    @property
    def cache_key(self) -> str:
        """按零字节分隔的学校、用户名、密码生成缓存指纹，凭据变化不复用旧会话。
        仅用于内部寻址，不能作为公开密码验证结果。
        """
        identity = f"{self.school}\0{self.username}\0{self.password}".encode("utf-8")
        return hashlib.sha256(identity).hexdigest()


class ElectricityAccountPool:
    """按学校隔离记录，同一解析后路径在进程内共享写锁；不提供跨进程互斥。
    写入保留其他学校，旧记录缺少 school 时归属 HNUCM。
    """

    _file_locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self, path: str | Path, *, pool_name: str = "电费", school: str = "HNUCM"
    ):
        """校验学校代码并按解析后路径共享锁，不读取账户文件。"""
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
        """读取本校候选凭据，无账户抛出配置异常，不调用其他学校账户。"""
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
        """校验完整账户文档，任何坏记录均拒绝整份文件；错误正文不包含原始凭据。
        用户名去外层空白、密码保留原值；缺省学校为 HNUCM，allow_missing 允许首次创建。
        """
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
        """同目录临时文件 fsync 后原子替换，错误转为 AccountPoolConfigurationError。
        调用方须持有共享锁并传入全部学校记录；finally 仅清理本次临时文件。
        """
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
        """仅返回本校用户名，不返回密码；文件错误向上传播，不解释为空池。"""
        async with self._lock:
            return [
                record["xh"]
                for record in self._read_records()
                if record["school"] == self.school
            ]

    async def add_account(self, username: str, password: str) -> None:
        """在共享锁内按学校、用户名检查唯一性并追加，允许首次建文件。
        输入格式由调用方校验；同校重复抛出 AccountAlreadyExistsError。
        """
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
        """锁内更新本校记录并返回最终用户名，None 保留对应原值。
        目标缺失抛出 AccountNotFoundError，同校改名冲突抛出 AccountAlreadyExistsError。
        """
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
        """锁内删除本校匹配记录，保留其他学校同名账户；未找到抛出 AccountNotFoundError。"""
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
        """每次重读凭据并在锁内推进本实例轮换起点；返回全部本校候选以供失败轮换。"""
        accounts = self._read_accounts()
        async with self._lock:
            start = self._next_index % len(accounts)
            self._next_index = (start + 1) % len(accounts)
        return accounts[start:] + accounts[:start]


class AcademicAccountPool(ElectricityAccountPool):
    """独立教务池复用文件格式；调用方须使用独立路径，不与电量登录会话混用。"""

    def __init__(self, path: str | Path, *, school: str = "HNUCM"):
        super().__init__(path, pool_name="教务", school=school)


class ElectricityService:
    """按账户池学校隔离缓存与文件，目录刷新和读数采集分别由实例锁防止重入。"""

    def __init__(
        self,
        base_url: str,
        account_pool: ElectricityAccountPool,
        client_factory: Callable[[str], Any] = HNUCMElectricityClient,
        cookie_cache: InMemoryCookieCache | None = None,
        reading_cache: DailyElectricityCache | None = None,
        retry_cache: DailyElectricityCache | None = None,
    ):
        """注入客户端与缓存，不登录或采集；环境路径为空时禁用对应持久化。
        HNUCM 保留旧文件名，其他学校加学校后缀隔离。
        """
        self.school = account_pool.school
        self.base_url = base_url
        self.account_pool = account_pool
        self.client_factory = client_factory
        self.cookie_cache = cookie_cache or InMemoryCookieCache()
        self.reading_cache = reading_cache or DailyElectricityCache.from_environment(
            school=self.school
        )
        self.retry_cache = retry_cache or DailyElectricityCache(
            school=self.school,
            key_prefix=os.getenv("ELECTRICITY_CACHE_KEY_PREFIX", "electricity:daily")
            + ":retry-attempt",
        )
        self._query_locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._query_slots = asyncio.Semaphore(8)
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
        """空路径禁用持久化，HNUCM 保留原路径，其他学校在扩展名前加学校后缀。"""
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
        """按学校建立账户池，使用现有 HNUCM 平台；不代表新增学校已有适配器支持。"""
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
        """优先复用凭据隔离会话，失败清缓存后仅重登一次，成功同步保存 Cookie/CSRF。
        重登失败向上传播，由 query_live 轮换账户。
        """
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
        """规范寝室身份后读日缓存，未命中访问平台并缓存成功结果。
        此路径可联网，只读 API 应使用 get_cached_room_reading。
        """
        room_number = normalize_room_number(room_number)
        lock = self._query_locks.setdefault((campus, room_number), asyncio.Lock())
        async with lock:
            cached_result = await self.reading_cache.get(campus, room_number)
            if cached_result is not None:
                return cached_result
            result = await self.query_live(room_number, campus)
            await self.reading_cache.set(campus, room_number, result)
            return result

    async def query_live(self, room_number: str, campus: str) -> dict[str, Any]:
        """绕过日缓存轮换账户，仍可复用 Cookie，成功补 UTC 采集时间。
        失败清除对应会话，最终只暴露平台安全错误或固定提示，不泄露账户和网络异常。
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
                await self.cookie_cache.delete(account.cache_key)
                continue

        if platform_error is not None:
            raise ElectricityQueryError(str(platform_error)) from platform_error
        raise ElectricityQueryError("电费查询失败，请检查账号池或稍后重试")

    async def refresh_room_catalog(self) -> dict[str, Any]:
        """锁内轮换账户发现目录并复用会话；公开目录不含凭据、余额或私有房间 ID。
        本地保存失败可降级，所有账户发现失败抛出 ElectricityQueryError。
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
        """缺失、损坏或学校不符返回 None；读取旧目录也过滤商户，防止采集商户电表。
        按楼栋和寝室重建标准身份，无法识别但带选择项的记录移除 room_number。
        """
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
        """仅查目录和日缓存，不登录平台；校区别名先解析为目录 ID。
        首项 None 表示目录未就绪，False 表示无此寝室，True 表示有效但读数仍可能为空。
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

    async def get_query_room_reading(
        self, room_number: str, campus: str, *, known_unchanged: bool = False
    ) -> tuple[bool | None, dict[str, Any] | None]:
        """晨间今日仍与昨日相同时，前台查询直接联网，不受后台60分钟冷却限制。"""
        catalogued, result = await self.get_cached_room_reading(room_number, campus)
        if not catalogued:
            return catalogued, None
        catalog = self.get_room_catalog()
        assert catalog is not None
        campus = self._resolve_catalog_campus(catalog, campus)
        room_number = normalize_room_number(room_number)
        key = (campus, room_number)
        lock = self._query_locks.setdefault(key, asyncio.Lock())
        async with lock:
            result = await self.reading_cache.get(campus, room_number)
            local = datetime.now(ZoneInfo("Asia/Shanghai"))
            if not time(5, 30) <= local.time() < time(11):
                return True, result
            previous = await self.reading_cache.get(
                campus, room_number, day=local.date() - timedelta(days=1)
            )
            unchanged = (
                same_reading(result, previous)
                if result and previous
                else known_unchanged
            )
            if not unchanged:
                return True, result
            async with self._query_slots:
                if datetime.now(ZoneInfo("Asia/Shanghai")).time() >= time(11):
                    return True, result
                await self.retry_cache.set(
                    campus,
                    room_number,
                    {"attempted_at": datetime.now(timezone.utc).isoformat()},
                )
                result = await self.query_live(room_number, campus)
                await self.reading_cache.set(campus, room_number, result)
                return True, result

    @staticmethod
    def _resolve_catalog_campus(catalog: dict[str, Any], campus: str) -> str:
        """仅含浦、东塘别名按目录名称解析，未知或未匹配时保留输入供后续身份检查。"""
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
        """结算前返回 False，结算后目录缺失或首个有效寝室缺读数则返回 True。
        只查目录与缓存，不触发登录或查表。
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
        """读取当日断点进度，缺失或过期返回零；非法计数归零并限制 queried 不超过 total。
        completed 仅表示所有目标已尝试，不保证每间寝室有读数。
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
        retry_rooms: list[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """实例锁内串行处理有界批次，普通模式复用日缓存并恢复同日全量断点。
        force 刷新全部，retry_missing 只补缺失，两者互斥；批量和间隔须为正，抖动非负。
        按单调时钟计算批次目标时刻，已知查询错误仅计失败；每批保存全量索引。
        动态补采子集不存索引，结束仅标记全部目标已尝试；返回计数仅覆盖本次处理。
        """
        if batch_size < 1 or interval_seconds <= 0 or jitter_seconds < 0:
            raise ValueError("电费采集计划参数无效")
        if sum((force, retry_missing, retry_rooms is not None)) > 1:
            raise ValueError("强制采集、缺失补采和指定寝室重采不能同时使用")
        async with self._collection_lock:
            if retry_rooms is not None:
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                if not time(5, 30) <= now.time() < time(11):
                    return {"succeeded": 0, "failed": 0}
            catalog = self.get_room_catalog()
            if catalog is None:
                if retry_rooms is not None:
                    raise ElectricityQueryError("寝室目录尚未同步完成")
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
                # 缺失补采不重查已有读数，晨间相同读数由server比较后单独指定。
                rooms = [
                    room
                    for room in rooms
                    if await self.reading_cache.get(room["campus"], room["room_number"])
                    is None
                ]
            if retry_rooms is not None:
                requested = {
                    (
                        self._resolve_catalog_campus(catalog, campus),
                        normalize_room_number(room),
                    )
                    for campus, room in retry_rooms
                }
                rooms = [
                    room
                    for room in rooms
                    if (room["campus"], room["room_number"]) in requested
                    and await self._retry_room_due(room["campus"], room["room_number"])
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
            if force or retry_missing or retry_rooms is not None:
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
                        if force or retry_rooms is not None:
                            lock = self._query_locks.setdefault(
                                (room["campus"], room["room_number"]), asyncio.Lock()
                            )
                            async with lock:
                                if retry_rooms is not None:
                                    if datetime.now(
                                        ZoneInfo("Asia/Shanghai")
                                    ).time() >= time(11):
                                        return {
                                            "succeeded": succeeded,
                                            "failed": failed,
                                        }
                                    if not await self._retry_room_due(
                                        room["campus"], room["room_number"]
                                    ):
                                        continue
                                    await self.retry_cache.set(
                                        room["campus"],
                                        room["room_number"],
                                        {
                                            "attempted_at": datetime.now(
                                                timezone.utc
                                            ).isoformat()
                                        },
                                    )
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
                if not retry_missing and retry_rooms is None:
                    self._write_collection_state(
                        {
                            "date": collection_date,
                            "next_index": batch_index + len(batch),
                            "total": len(rooms),
                        }
                    )
            if retry_missing:
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

    async def _retry_room_due(self, campus: str, room: str) -> bool:
        """后台重采至少间隔60分钟；失败及前台尝试也记冷却。"""
        reading = await self.reading_cache.get(campus, room)
        if not reading:
            return False
        try:
            collected = datetime.fromisoformat(reading["collected_at"])
            now = datetime.now(timezone.utc)
            if (
                collected.tzinfo is None
                or collected.astimezone(ZoneInfo("Asia/Shanghai")).date()
                != now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            ):
                return False
            attempted = await self.retry_cache.get(campus, room)
            if attempted:
                last = datetime.fromisoformat(attempted["attempted_at"])
                if last.tzinfo is not None:
                    collected = max(collected, last)
            previous = await self.reading_cache.get(
                campus,
                room,
                day=now.astimezone(ZoneInfo("Asia/Shanghai")).date()
                - timedelta(days=1),
            )
            if previous and not same_reading(reading, previous):
                return False
            return (now - collected).total_seconds() >= 3600
        except (KeyError, TypeError, ValueError):
            return False

    async def resume_today_collection(self) -> dict[str, int] | None:
        """仅恢复同日、目录存在且未完成的全量断点，否则返回 None。
        走普通采集，不强制重查；剩余缺失由补采处理。
        """
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
        """沿用日缓存时区生成断点日期，避免跨日恢复错误扫描。"""
        timezone_name = os.getenv("ELECTRICITY_CACHE_TIMEZONE", "Asia/Shanghai")
        return datetime.now(ZoneInfo(timezone_name)).date().isoformat()

    def _read_collection_state(self) -> dict[str, Any] | None:
        """缺失、损坏或学校不符返回 None，旧文件默认 HNUCM；日期和计数由调用方检查。"""
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
        """附加学校后替换可选断点文件，I/O 失败忽略，重启可能重复尝试已有缓存目标。"""
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
        """临时文件原子替换可选目录；I/O 失败不推翻实时发现结果。"""
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
            return


# TODO(SQLite): 将内部 JSON 账户文件迁移至 SQLite。
