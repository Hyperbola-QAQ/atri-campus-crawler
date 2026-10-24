"""教务抓取及内部账户 API 仅用已注册学校；外层服务仍须按用户授权查询范围。
公开电量查询只读日缓存，内部晨间条件查询和后台重采受令牌保护；教务指定学号不轮换其他学生。
应用拥有启动恢复和调度任务，退出先回收任务再关 Redis。
"""

import asyncio
import logging
import os
import secrets
from datetime import datetime, time
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path as FilePath
from typing import Any, Literal

from utils.electricity_identity import ROOM_INPUT_PATTERN, normalize_room_number

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field, model_validator

load_dotenv()
load_dotenv(f".env.{os.getenv('ENVIRONMENT', 'dev')}")

from adapter.hnucm_adapter import HNUCMAdapter
from schemas.electricity_schema import (
    AcademicAccountCreate,
    AcademicAccountListResponse,
    AcademicAccountResponse,
    AcademicAccountUpdate,
    ElectricityAccountCreate,
    ElectricityAccountListResponse,
    ElectricityAccountResponse,
    ElectricityAccountUpdate,
    ElectricityCollectionStatusResponse,
    ElectricityResponse,
)
from services.electricity import (
    AcademicAccountPool,
    AccountAlreadyExistsError,
    AccountNotFoundError,
    AccountPoolConfigurationError,
    ElectricityAccountPool,
    ElectricityQueryError,
    ElectricityService,
)
from services.electricity_schedule import ElectricitySchedule
from schemas.school import School
from utils.redis import close_redis_client

SCHOOL_ADAPTERS = {"HNUCM": HNUCMAdapter}
ELECTRICITY_SERVICES = {"HNUCM": ElectricityService}
logger = logging.getLogger(__name__)


class CrawlRequest(BaseModel):
    """凭据须同时提供或省略用教务池，student_id 非空且须与显式用户名一致。
    verify_credentials 要求新登录证明密码，动作和学校限注册范围。
    """

    school: School = "HNUCM"
    action: Literal["login", "get_profile", "get_grades", "get_course_schedule"]
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    verify_credentials: bool = False
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def credentials_must_be_provided_together(self):
        """检查凭据组合及目标一致性，不验证密码；省略可用池，空字符串由字段校验拒绝。"""
        if (self.username is None) != (self.password is None):
            raise ValueError("username 和 password 必须同时提供，或同时省略")
        student_id = self.params.get("student_id")
        if student_id is not None:
            if not isinstance(student_id, str) or not student_id.strip():
                raise ValueError("params.student_id 必须为非空字符串")
            if self.username is not None and self.username != student_id:
                raise ValueError("username 与 params.student_id 必须一致")
        return self


class CrawlResponse(BaseModel):
    """业务 success/failed 不等于 HTTP 校验状态；data 由执行层序列化。
    error_code 缺失不输出，兼容旧调用方。
    """

    school: School = "HNUCM"
    status: Literal["success", "failed"]
    data: Any = None
    error: str | None = None
    error_code: str | None = Field(default=None, exclude_if=lambda value: value is None)


@lru_cache(maxsize=None)
def _cached_electricity_service(school: str = "HNUCM") -> ElectricityService:
    """按学校复用服务及内存缓存，账户文件仍按查询重读，不缓存凭据证明。"""
    return ELECTRICITY_SERVICES[school].from_environment(school=school)


async def get_electricity_service(
    school: School = "HNUCM",
) -> ElectricityService:
    """按受支持 School 提供依赖，测试可注入离线服务。"""
    return _cached_electricity_service(school)


async def get_electricity_account_pool(school: School = "HNUCM"):
    """复用采集服务的池与锁，内部对象须转换为无密码公开响应。"""
    return (await get_electricity_service(school)).account_pool


@lru_cache(maxsize=None)
def _cached_academic_account_pool(school: str = "HNUCM") -> AcademicAccountPool:
    """按学校缓存独立教务池，环境可覆盖路径；构造不读取凭据，不复用电量默认文件。"""
    default_accounts_file = (
        FilePath(__file__).resolve().parent / "config" / "academic_accounts.json"
    )
    accounts_file = os.getenv("ACADEMIC_ACCOUNTS_FILE", str(default_accounts_file))
    return AcademicAccountPool(accounts_file, school=school)


async def get_academic_account_pool(school: School = "HNUCM") -> AcademicAccountPool:
    """管理路由按查询学校取教务池，不依赖电量或目录初始化。"""
    return _cached_academic_account_pool(school)


async def get_crawl_academic_account_pool(request: CrawlRequest) -> AcademicAccountPool:
    """查询路由按正文学校取教务池，不固定默认学校。"""
    return _cached_academic_account_pool(request.school)


async def _initial_electricity_sync() -> None:
    """先恢复同日断点再刷新目录、仅补缺失；阶段失败不阻止后续阶段。
    两次缺失检查遵循结算窗口，不因重启强制全量刷新。
    """
    service = await get_electricity_service()
    try:
        if await service.needs_today_collection():
            await service.resume_today_collection()
    except Exception:
        logger.exception("Startup electricity collection resume failed")
    try:
        await service.refresh_room_catalog()
    except (AccountPoolConfigurationError, ElectricityQueryError):
        logger.warning("Initial electricity room catalog sync failed")
    try:
        if await service.needs_today_collection():
            result = await service.collect_room_readings(retry_missing=True)
            logger.info("Startup electricity reading collection finished: %s", result)
    except Exception:
        logger.exception("Startup electricity reading collection failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用持有唯一启动恢复任务，调度共用其句柄作屏障，不阻塞接收请求。
    正常退出路径先停调度，再回收启动恢复，最后关闭 Redis。
    """
    app.state.electricity_catalog_task = asyncio.create_task(
        _initial_electricity_sync()
    )
    app.state.electricity_schedule = ElectricitySchedule(
        await get_electricity_service()
    )
    app.state.electricity_schedule.start(
        startup_task=app.state.electricity_catalog_task
    )
    try:
        yield
    finally:
        await app.state.electricity_schedule.stop()
        task = app.state.electricity_catalog_task
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await close_redis_client()


app = FastAPI(
    title="ATRI Crawler API",
    description="教务信息及寝室剩余电费查询接口",
    version="0.2.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_internal_token(request: Request, call_next):
    """教务、账户及手动目录刷新受内部 Bearer 保护；缺配置 503，完整认证不匹配 401。
    常量时间比较，不从浏览器用户字段授予业务权限。
    """
    protected_paths = (
        request.url.path == "/api/v1/academic"
        or request.url.path.startswith("/api/v1/electricity/accounts")
        or request.url.path == "/api/v1/electricity/rooms/refresh"
        or request.url.path.startswith("/api/v1/academic/accounts")
        or request.url.path == "/api/v1/electricity/retry-unchanged"
        or (
            request.url.path.startswith("/api/v1/electricity/")
            and any(
                request.query_params.get(flag, "false").lower()
                not in {"false", "0", "off", "no", ""}
                for flag in ("recheck", "known_unchanged")
            )
        )
    )
    if protected_paths:
        token = os.getenv("CRAWLER_INTERNAL_TOKEN")
        supplied = request.headers.get("Authorization", "")
        if not token:
            return Response(
                status_code=503, content="crawler internal token is not configured"
            )
        if not secrets.compare_digest(supplied, f"Bearer {token}"):
            return Response(status_code=401, content="invalid internal credentials")
    return await call_next(request)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    """仅存活，不探测平台、凭据或采集状态，不证明数据就绪。"""
    return {"status": "ok"}


@app.get(
    "/api/v1/electricity/accounts",
    response_model=ElectricityAccountListResponse,
    tags=["electricity-account-pool"],
)
async def list_electricity_accounts(
    account_pool: ElectricityAccountPool = Depends(get_electricity_account_pool),
    school: School = "HNUCM",
) -> ElectricityAccountListResponse:
    """仅公开本校用户名，池配置错误 503，不将损坏文件伪装空列表。"""
    try:
        accounts = await account_pool.list_accounts()
        return ElectricityAccountListResponse(
            school=school,
            accounts=[
                ElectricityAccountResponse(school=school, xh=username)
                for username in accounts
            ],
        )
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/api/v1/electricity/accounts",
    response_model=ElectricityAccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["electricity-account-pool"],
)
async def create_electricity_account(
    request: ElectricityAccountCreate,
    account_pool: ElectricityAccountPool = Depends(get_electricity_account_pool),
    school: School = "HNUCM",
) -> ElectricityAccountResponse:
    """正文学校须与查询一致；同校重复 409、存储错误 503，成功 201 不含密码。"""
    if request.school is not None and request.school != school:
        raise HTTPException(
            status_code=422, detail="请求体 school 与查询参数 school 不一致"
        )
    try:
        await account_pool.add_account(request.xh, request.pwd)
        return ElectricityAccountResponse(school=school, xh=request.xh)
    except AccountAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put(
    "/api/v1/electricity/accounts/{username}",
    response_model=ElectricityAccountResponse,
    tags=["electricity-account-pool"],
)
async def update_electricity_account(
    username: str,
    request: ElectricityAccountUpdate,
    account_pool: ElectricityAccountPool = Depends(get_electricity_account_pool),
    school: School = "HNUCM",
) -> ElectricityAccountResponse:
    """空更新或正文学校冲突为 422，省略字段保留；缺目标 404、重名 409、存储错误 503。
    响应使用最终用户名，不回显密码。
    """
    if request.xh is None and request.pwd is None:
        raise HTTPException(status_code=422, detail="至少提供 xh 或 pwd 之一")
    if request.school is not None and request.school != school:
        raise HTTPException(
            status_code=422, detail="请求体 school 与查询参数 school 不一致"
        )
    try:
        updated_username = await account_pool.update_account(
            username, new_username=request.xh, password=request.pwd
        )
        return ElectricityAccountResponse(school=school, xh=updated_username)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete(
    "/api/v1/electricity/accounts/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["electricity-account-pool"],
)
async def delete_electricity_account(
    username: str,
    account_pool: ElectricityAccountPool = Depends(get_electricity_account_pool),
    school: School = "HNUCM",
) -> Response:
    """仅删本校账户，成功 204、缺目标 404、存储错误 503，不跨学校按用户名删除。"""
    try:
        await account_pool.delete_account(username)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(
    "/api/v1/academic/accounts",
    response_model=AcademicAccountListResponse,
    tags=["academic-account-pool"],
)
async def list_academic_accounts(
    account_pool: AcademicAccountPool = Depends(get_academic_account_pool),
    school: School = "HNUCM",
) -> AcademicAccountListResponse:
    """仅列独立教务池用户名，配置错 503，不回退电量池或公开密码。"""
    try:
        accounts = await account_pool.list_accounts()
        return AcademicAccountListResponse(
            school=school,
            accounts=[
                AcademicAccountResponse(school=school, xh=username)
                for username in accounts
            ],
        )
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/api/v1/academic/accounts",
    response_model=AcademicAccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["academic-account-pool"],
)
async def create_academic_account(
    request: AcademicAccountCreate,
    account_pool: AcademicAccountPool = Depends(get_academic_account_pool),
    school: School = "HNUCM",
) -> AcademicAccountResponse:
    """仅向查询学校教务池新增；正文学校冲突 422、重名 409、存储错 503，响应无密码。"""
    if request.school is not None and request.school != school:
        raise HTTPException(
            status_code=422, detail="请求体 school 与查询参数 school 不一致"
        )
    try:
        await account_pool.add_account(request.xh, request.pwd)
        return AcademicAccountResponse(school=school, xh=request.xh)
    except AccountAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put(
    "/api/v1/academic/accounts/{username}",
    response_model=AcademicAccountResponse,
    tags=["academic-account-pool"],
)
async def update_academic_account(
    username: str,
    request: AcademicAccountUpdate,
    account_pool: AcademicAccountPool = Depends(get_academic_account_pool),
    school: School = "HNUCM",
) -> AcademicAccountResponse:
    """未提交字段保留；空更新或学校冲突 422，缺目标 404、重名 409、存储错 503。"""
    if request.xh is None and request.pwd is None:
        raise HTTPException(status_code=422, detail="至少提供 xh 或 pwd 之一")
    if request.school is not None and request.school != school:
        raise HTTPException(
            status_code=422, detail="请求体 school 与查询参数 school 不一致"
        )
    try:
        updated_username = await account_pool.update_account(
            username, new_username=request.xh, password=request.pwd
        )
        return AcademicAccountResponse(school=school, xh=updated_username)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete(
    "/api/v1/academic/accounts/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["academic-account-pool"],
)
async def delete_academic_account(
    username: str,
    account_pool: AcademicAccountPool = Depends(get_academic_account_pool),
    school: School = "HNUCM",
) -> Response:
    """独立教务池删除，成功 204、缺目标 404、存储错 503，不删电量同名账户。"""
    try:
        await account_pool.delete_account(username)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(
    "/api/v1/electricity/rooms",
    tags=["electricity"],
)
async def get_electricity_rooms(
    service: ElectricityService = Depends(get_electricity_service),
    school: School = "HNUCM",
) -> dict[str, Any]:
    """只读已保存宿舍目录，未就绪 503，不登录平台；响应学校及区域身份来自目录。"""
    catalog = service.get_room_catalog()
    if catalog is None:
        raise HTTPException(status_code=503, detail="寝室目录尚未同步完成")
    return {**catalog, "school": school}


@app.post(
    "/api/v1/electricity/rooms/refresh",
    tags=["electricity"],
)
async def refresh_electricity_rooms(
    service: ElectricityService = Depends(get_electricity_service),
    school: School = "HNUCM",
) -> dict[str, Any]:
    """内部认证后主动发现平台目录；账户配置错 503、平台查询失败 502。"""
    try:
        return {**(await service.refresh_room_catalog()), "school": school}
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ElectricityQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/api/v1/electricity/collection-status",
    response_model=ElectricityCollectionStatusResponse,
    tags=["electricity"],
)
async def get_electricity_collection_status(
    service: ElectricityService = Depends(get_electricity_service),
    school: School = "HNUCM",
) -> ElectricityCollectionStatusResponse:
    """仅从断点读进度，不逐间查缓存；completed 表示尝试完成，不保证读数齐全。"""
    return ElectricityCollectionStatusResponse(
        school=school, **service.daily_collection_status()
    )


class ElectricityRetryRoom(BaseModel):
    campus: str = Field(min_length=1, max_length=128)
    room_number: str = Field(min_length=4, max_length=32, pattern=ROOM_INPUT_PATTERN)


class ElectricityRetryRequest(BaseModel):
    """仅含公开目录中的寝室标识，不传输历史余额或电量。"""

    rooms: list[ElectricityRetryRoom] = Field(max_length=1000)


@app.post("/api/v1/electricity/retry-unchanged", tags=["electricity"])
async def retry_unchanged_electricity(
    payload: ElectricityRetryRequest,
    service: ElectricityService = Depends(get_electricity_service),
    school: School = "HNUCM",
):
    """内部晨间任务只重采server已比较为相同的寝室，11:00后不再发起请求。"""
    try:
        return await service.collect_room_readings(
            retry_rooms=[(room.campus, room.room_number) for room in payload.rooms]
        )
    except (AccountPoolConfigurationError, ElectricityQueryError) as exc:
        raise HTTPException(503, "晨间电费重采暂不可用") from exc


@app.get(
    "/api/v1/electricity/{campus}/{room_number}",
    response_model=ElectricityResponse,
    tags=["electricity"],
)
async def get_electricity(
    campus: str = Path(min_length=1, max_length=128),
    room_number: str = Path(
        min_length=4,
        max_length=32,
        pattern=ROOM_INPUT_PATTERN,
    ),
    service: ElectricityService = Depends(get_electricity_service),
    school: School = "HNUCM",
    recheck: bool = False,
    known_unchanged: bool = False,
) -> ElectricityResponse:
    """默认只读规范寝室日缓存；内部 recheck 模式允许晨间条件刷新。

    无目录或未采集 503，无此寝室 404。
    非空 collected_at 须带时区、为上海当天 05:30 后且不晚于当前；不符视为未采集。
    缺失或空采集时间沿用旧缓存兼容，不伪造时间。
    """
    room_number = normalize_room_number(room_number)
    if recheck:
        try:
            catalogued, result = await service.get_query_room_reading(
                room_number, campus, known_unchanged=known_unchanged
            )
        except (AccountPoolConfigurationError, ElectricityQueryError) as exc:
            raise HTTPException(503, "电费条件刷新暂不可用") from exc
    else:
        catalogued, result = await service.get_cached_room_reading(room_number, campus)
    if catalogued is None:
        raise HTTPException(status_code=503, detail="寝室目录尚未同步完成")
    if not catalogued:
        raise HTTPException(status_code=404, detail="校区或寝室号码不存在")
    if result is not None and result.get("collected_at"):
        try:
            collected = datetime.fromisoformat(result["collected_at"])
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            if collected.tzinfo is None:
                result = None
            else:
                collected = collected.astimezone(now.tzinfo)
                if (
                    collected.date() != now.date()
                    or collected.time() < time(5, 30)
                    or collected > now
                ):
                    result = None
        except (ValueError, TypeError):
            result = None
    if result is None:
        raise HTTPException(status_code=503, detail="该寝室今日电费尚未采集完成")
    return ElectricityResponse(**{**result, "school": school})


async def _crawl_with_credentials(
    request: CrawlRequest, username: str, password: str
) -> tuple[CrawlResponse, bool]:
    """单账户执行，登录动作或强制证明须新登录；返回布尔值只表示登录是否成功。
    登录成功后数据失败仍为 True，禁止借动作失败轮换其他学生；模型结果序列化为普通数据。
    """
    adapter_class = SCHOOL_ADAPTERS[request.school]
    adapter = adapter_class()

    if request.verify_credentials or request.action == "login":
        success, message, cookies = await adapter.login(
            username, password, force_login=True
        )
    else:
        success, message, cookies = await adapter.login(username, password)
    if not success:
        return CrawlResponse(
            school=request.school,
            status="failed",
            error=message,
            error_code="ACADEMIC_LOGIN_FAILED",
        ), False
    if request.action == "login":
        return CrawlResponse(
            school=request.school, status="success", data={"success": True}
        ), True

    if request.action == "get_profile":
        success, message, data = await adapter.get_profile(cookies, username)
    elif request.action == "get_grades":
        success, message, data = await adapter.get_grades(
            cookies, username, request.params.get("semester", "")
        )
    else:
        success, message, data = await adapter.get_course_schedule(
            cookies, username, request.params.get("semester", "")
        )

    if not success or data is None:
        return CrawlResponse(
            school=request.school, status="failed", error=message
        ), True

    if isinstance(data, list):
        serialized = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in data
        ]
    else:
        serialized = data.model_dump() if hasattr(data, "model_dump") else data
    return CrawlResponse(school=request.school, status="success", data=serialized), True


@app.post("/api/v1/academic", response_model=CrawlResponse, tags=["academic"])
async def crawl(
    request: CrawlRequest,
    account_pool: AcademicAccountPool = Depends(get_crawl_academic_account_pool),
) -> CrawlResponse:
    """显式凭据优先，否则独立教务池；指定学号仅限该账户，缺目标 404、池不可用 503。
    无目标可轮换登录，一旦成功即返回该账户动作结果；全部登录失败为业务 failed。
    """
    if request.username is not None:
        assert request.password is not None  # 由 CrawlRequest 校验保证。
        result, _ = await _crawl_with_credentials(
            request, request.username, request.password
        )
        return result

    try:
        accounts = await account_pool.rotated_accounts()
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    student_id = request.params.get("student_id")
    if student_id is not None:
        accounts = [account for account in accounts if account.username == student_id]
        if not accounts:
            raise HTTPException(status_code=404, detail="教务账号池中未找到目标学号")

    for account in accounts:
        result, login_succeeded = await _crawl_with_credentials(
            request, account.username, account.password
        )
        if login_succeeded:
            return result
    return CrawlResponse(
        school=request.school, status="failed", error="教务账号池中没有可登录账号"
    )


def main() -> None:
    """仅直接运行时启动 Uvicorn；reload 只识别 true，非法端口转换直接失败。"""
    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
