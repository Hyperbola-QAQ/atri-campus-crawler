import os
from functools import lru_cache
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path
from pydantic import BaseModel, Field

load_dotenv()
load_dotenv(f".env.{os.getenv('ENVIRONMENT', 'dev')}")

from adapter.hnucm_adapter import HNUCMAdapter
from schemas.electricity_schema import ElectricityResponse
from services.electricity import (
    AccountPoolConfigurationError,
    ElectricityQueryError,
    ElectricityService,
)

SCHOOL_ADAPTERS = {"HNUCM": HNUCMAdapter}


class CrawlRequest(BaseModel):
    school: Literal["HNUCM"]
    action: Literal["login", "get_profile", "get_grades", "get_course_schedule"]
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


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


app = FastAPI(
    title="ATRI Crawler API",
    description="教务信息及寝室剩余电费查询接口",
    version="0.2.0",
)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/electricity/{room_number}",
    response_model=ElectricityResponse,
    tags=["electricity"],
)
async def get_electricity(
    room_number: str = Path(
        min_length=4,
        max_length=6,
        pattern=r"^(?:\d{4,5}|\d{1,2}-\d{3})$",
    ),
    service: ElectricityService = Depends(get_electricity_service),
) -> ElectricityResponse:
    """查询寝室电表信息，并兼容四位号及常见的带连字符门牌号。"""
    room_number = room_number.replace("-", "")
    if len(room_number) == 4:
        room_number = f"0{room_number}"
    try:
        result = await service.query(room_number)
        return ElectricityResponse(**result)
    except AccountPoolConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ElectricityQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/crawl", response_model=CrawlResponse, tags=["academic"])
async def crawl(request: CrawlRequest) -> CrawlResponse:
    """以 HTTP 方式提供原有的 HNUCM 教务查询能力。"""
    adapter_class = SCHOOL_ADAPTERS[request.school]
    adapter = adapter_class()

    success, message, cookies = await adapter.login(request.username, request.password)
    if not success:
        return CrawlResponse(status="failed", error=message)
    if request.action == "login":
        return CrawlResponse(status="success", data={"success": True})

    if request.action == "get_profile":
        success, message, data = await adapter.get_profile(cookies, request.username)
    elif request.action == "get_grades":
        success, message, data = await adapter.get_grades(
            cookies, request.username, request.params.get("semester", "")
        )
    else:
        success, message, data = await adapter.get_course_schedule(
            cookies, request.username, request.params.get("semester", "")
        )

    if not success or data is None:
        return CrawlResponse(status="failed", error=message)

    if isinstance(data, list):
        serialized = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in data
        ]
    else:
        serialized = data.model_dump() if hasattr(data, "model_dump") else data
    return CrawlResponse(status="success", data=serialized)


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
