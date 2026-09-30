import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path as FilePath
from typing import Any, Literal

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

SCHOOL_ADAPTERS = {"HNUCM": HNUCMAdapter}
logger = logging.getLogger(__name__)


class CrawlRequest(BaseModel):
    school: Literal["HNUCM"]
    action: Literal["login", "get_profile", "get_grades", "get_course_schedule"]
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def credentials_must_be_provided_together(self):
        if (self.username is None) != (self.password is None):
            raise ValueError("username 和 password 必须同时提供，或同时省略")
        return self


class CrawlResponse(BaseModel):
    status: Literal["success", "failed"]
    data: Any = None
    error: str | None = None


@lru_cache(maxsize=1)
def _cached_electricity_service() -> ElectricityService:
    """复用轮转器；账号文件在每次查询时读取且不会记录到日志。"""
    return ElectricityService.from_environment()


async def get_electricity_service() -> ElectricityService:
    return _cached_electricity_service()


async def get_electricity_account_pool():
    """Expose the pool storage independently from the query orchestration service."""
    return (await get_electricity_service()).account_pool


@lru_cache(maxsize=1)
def _cached_academic_account_pool() -> AcademicAccountPool:
    default_accounts_file = (
        FilePath(__file__).resolve().parent / "config" / "academic_accounts.json"
    )
    accounts_file = os.getenv("ACADEMIC_ACCOUNTS_FILE", str(default_accounts_file))
    return AcademicAccountPool(accounts_file)


async def get_academic_account_pool() -> AcademicAccountPool:
    return _cached_academic_account_pool()


async def _initial_electricity_sync() -> None:
    """Synchronise catalog, then repair a missing cache after 11:00 settlement."""
    service = await get_electricity_service()
    try:
        await service.refresh_room_catalog()
    except (AccountPoolConfigurationError, ElectricityQueryError):
        logger.warning("Initial electricity room catalog sync failed")
    try:
        if await service.needs_today_collection():
            result = await service.collect_room_readings(force=True)
            logger.info("Startup electricity reading collection finished: %s", result)
    except (AccountPoolConfigurationError, ElectricityQueryError):
        logger.warning("Startup electricity reading collection failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.electricity_catalog_task = asyncio.create_task(
        _initial_electricity_sync()
    )
    app.state.electricity_schedule = ElectricitySchedule(
        await get_electricity_service()
    )
    app.state.electricity_schedule.start()
    try:
        yield
    finally:
        await app.state.electricity_schedule.stop()
        task = app.state.electricity_catalog_task
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="ATRI Crawler API",
    description="教务信息及寝室剩余电费查询接口",
    version="0.2.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_internal_token(request: Request, call_next):
    """仅允许持有部署时内部令牌的服务访问业务与账号池接口。"""
    protected_paths = (
        request.url.path == "/api/v1/academic"
        or request.url.path.startswith("/api/v1/electricity/accounts")
        or request.url.path == "/api/v1/electricity/rooms/refresh"
        or request.url.path.startswith("/api/v1/academic/accounts")
    )
    if protected_paths:
        token = os.getenv("CRAWLER_INTERNAL_TOKEN")
        supplied = request.headers.get("Authorization", "")
        if not token:
            return Response(status_code=503, content="crawler internal token is not configured")
        if not secrets.compare_digest(supplied, f"Bearer {token}"):
            return Response(status_code=401, content="invalid internal credentials")
    return await call_next(request)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/v1/electricity/accounts",
    response_model=ElectricityAccountListResponse,
    tags=["electricity-account-pool"],
)
async def list_electricity_accounts(
    account_pool: ElectricityAccountPool = Depends(get_electricity_account_pool),
) -> ElectricityAccountListResponse:
    """List account IDs. Passwords are never included in responses."""
    try:
        accounts = await account_pool.list_accounts()
        return ElectricityAccountListResponse(
            accounts=[ElectricityAccountResponse(xh=username) for username in accounts]
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
) -> ElectricityAccountResponse:
    try:
        await account_pool.add_account(request.xh, request.pwd)
        return ElectricityAccountResponse(xh=request.xh)
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
) -> ElectricityAccountResponse:
    if request.xh is None and request.pwd is None:
        raise HTTPException(status_code=422, detail="至少提供 xh 或 pwd 之一")
    try:
        updated_username = await account_pool.update_account(
            username, new_username=request.xh, password=request.pwd
        )
        return ElectricityAccountResponse(xh=updated_username)
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
) -> Response:
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
) -> AcademicAccountListResponse:
    try:
        accounts = await account_pool.list_accounts()
        return AcademicAccountListResponse(
            accounts=[AcademicAccountResponse(xh=username) for username in accounts]
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
) -> AcademicAccountResponse:
    try:
        await account_pool.add_account(request.xh, request.pwd)
        return AcademicAccountResponse(xh=request.xh)
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
) -> AcademicAccountResponse:
    if request.xh is None and request.pwd is None:
        raise HTTPException(status_code=422, detail="至少提供 xh 或 pwd 之一")
    try:
        updated_username = await account_pool.update_account(
            username, new_username=request.xh, password=request.pwd
        )
        return AcademicAccountResponse(xh=updated_username)
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
) -> Response:
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
) -> dict[str, Any]:
    """Return the most recent startup-discovered room catalog for all campuses."""
    catalog = service.get_room_catalog()
    if catalog is None:
        raise HTTPException(status_code=503, detail="寝室目录尚未同步完成")
    return catalog


@app.post(
    "/api/v1/electricity/rooms/refresh",
    tags=["electricity"],
)
async def refresh_electricity_rooms(
    service: ElectricityService = Depends(get_electricity_service),
) -> dict[str, Any]:
    """Immediately re-enumerate valid dorm rooms for every available campus."""
    try:
        return await service.refresh_room_catalog()
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ElectricityQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/api/v1/electricity/{campus}/{room_number}",
    response_model=ElectricityResponse,
    tags=["electricity"],
)
async def get_electricity(
    campus: str = Path(min_length=1, max_length=128),
    room_number: str = Path(
        min_length=4,
        max_length=6,
        pattern=r"^(?:\d{4,5}|\d{1,2}-\d{3})$",
    ),
    service: ElectricityService = Depends(get_electricity_service),
) -> ElectricityResponse:
    """从每日缓存读取电表信息，绝不在请求期间访问财务平台。"""
    room_number = room_number.replace("-", "")
    if len(room_number) == 4:
        room_number = f"0{room_number}"
    catalogued, result = await service.get_cached_room_reading(room_number, campus)
    if catalogued is None:
        raise HTTPException(status_code=503, detail="寝室目录尚未同步完成")
    if not catalogued:
        raise HTTPException(status_code=404, detail="校区或寝室号码不存在")
    if result is None:
        raise HTTPException(status_code=503, detail="该寝室今日电费尚未采集完成")
    return ElectricityResponse(**result)


async def _crawl_with_credentials(
    request: CrawlRequest, username: str, password: str
) -> tuple[CrawlResponse, bool]:
    """Run one academic request and return whether its login succeeded."""
    adapter_class = SCHOOL_ADAPTERS[request.school]
    adapter = adapter_class()

    success, message, cookies = await adapter.login(username, password)
    if not success:
        return CrawlResponse(status="failed", error=message), False
    if request.action == "login":
        return CrawlResponse(status="success", data={"success": True}), True

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
        return CrawlResponse(status="failed", error=message), True

    if isinstance(data, list):
        serialized = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in data
        ]
    else:
        serialized = data.model_dump() if hasattr(data, "model_dump") else data
    return CrawlResponse(status="success", data=serialized), True


@app.post("/api/v1/academic", response_model=CrawlResponse, tags=["academic"])
async def crawl(
    request: CrawlRequest,
    account_pool: AcademicAccountPool = Depends(get_academic_account_pool),
) -> CrawlResponse:
    """提供教务查询；省略账号密码时使用独立的教务账号池。"""
    if request.username is not None:
        assert request.password is not None  # enforced by CrawlRequest validation
        result, _ = await _crawl_with_credentials(
            request, request.username, request.password
        )
        return result

    try:
        accounts = await account_pool.rotated_accounts()
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    for account in accounts:
        result, login_succeeded = await _crawl_with_credentials(
            request, account.username, account.password
        )
        if login_succeeded:
            return result
    return CrawlResponse(status="failed", error="教务账号池中没有可登录账号")


def main() -> None:
    """运行 FastAPI HTTP 服务。"""
    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
