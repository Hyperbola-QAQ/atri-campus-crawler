from unittest.mock import AsyncMock

import httpx
import pytest

from adapter.hnucm_adapter.course_schedule import CourseScheduleCrawler
from adapter.hnucm_adapter.profile import ProfileCrawler


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["profile", "schedule"])
async def test_download_without_working_directory_tmp(monkeypatch, tmp_path, kind):
    """部署目录没有 tmp 时仍可下载，同一账号的请求使用不同文件。"""
    monkeypatch.chdir(tmp_path)
    cls = ProfileCrawler if kind == "profile" else CourseScheduleCrawler
    crawler = cls("https://example.test", {}, "HNUCM", "student")
    response = httpx.Response(
        200, content=b"xls-data", request=httpx.Request("POST", "https://example.test")
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    paths = []
    try:
        for _ in range(2):
            path = await (
                crawler.fetch_profile_xls(httpx.Cookies())
                if kind == "profile"
                else crawler.fetch_course_schedule_xls("2026-2027-1", httpx.Cookies())
            )
            paths.append(path)
            assert path.read_bytes() == b"xls-data"
        assert paths[0] != paths[1]
        assert not (tmp_path / "tmp").exists()
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["profile", "schedule"])
async def test_parse_failure_removes_download(monkeypatch, tmp_path, kind):
    """解析失败也清理包含个人信息的下载文件。"""
    cls = ProfileCrawler if kind == "profile" else CourseScheduleCrawler
    crawler = cls("https://example.test", {}, "HNUCM", "student")
    path = tmp_path / "download.xls"
    path.write_bytes(b"invalid")
    fetch = "fetch_profile_xls" if kind == "profile" else "fetch_course_schedule_xls"
    parse = "parse_profile_xls" if kind == "profile" else "parse_course_schedule_xls"
    monkeypatch.setattr(crawler, fetch, AsyncMock(return_value=path))
    monkeypatch.setattr(
        crawler, parse, AsyncMock(side_effect=ValueError("invalid Excel"))
    )
    with pytest.raises(ValueError):
        if kind == "profile":
            await crawler.get_profile_from_jwxt(httpx.Cookies())
        else:
            await crawler.get_course_schedule_from_jwxt("2026-2027-1", httpx.Cookies())
    assert not path.exists()
