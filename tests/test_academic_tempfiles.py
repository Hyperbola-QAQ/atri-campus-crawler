"""无 tmp 子目录的工作环境模拟只读部署布局，离线下载验证临时文件独立及清理。"""

from unittest.mock import AsyncMock

import httpx
import pytest

from adapter.hnucm_adapter.course_schedule import CourseScheduleCrawler
from adapter.hnucm_adapter.profile import ProfileCrawler


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["profile", "schedule"])
async def test_download_without_working_directory_tmp(monkeypatch, tmp_path, kind):
    """验证同账户连续下载不复用同一个临时文件。

    切换到空工作目录后，资料与课表下载都应取得真实可读路径。
    文件字节正确且路径互异，不能依赖源码目录创建 tmp。
    finally 清理本用例取得的路径，避免测试本身遗留数据。
    """
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
    """验证解析失败也删除包含个人信息的下载文件。

    下载替身返回已存在文件，解析替身再抛出格式错误。
    异常必须继续传播，而最终文件应已不存在。
    成功路径清理不足以证明此失败边界安全。
    """
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
