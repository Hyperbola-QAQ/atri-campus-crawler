"""教务适配器和 HTML/Excel 异常输入的离线测试。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import adapter.hnucm_adapter as adapter
from adapter.hnucm_adapter import course_schedule as schedule
from adapter.hnucm_adapter.grade import GradeCrawler
from adapter.hnucm_adapter.profile import ProfileCrawler
from utils import ocr


@pytest.mark.parametrize(
    "method,cls,inner,payload",
    [
        (
            "get_profile",
            adapter.ProfileCrawler,
            "get_profile_from_jwxt",
            SimpleNamespace(name="student"),
        ),
        ("get_grades", adapter.GradeCrawler, "get_grades_from_jwxt", []),
        (
            "get_course_schedule",
            adapter.CourseScheduleCrawler,
            "get_course_schedule_from_jwxt",
            [],
        ),
    ],
)
@pytest.mark.parametrize("fail", [False, True])
async def test_adapter_results(monkeypatch, method, cls, inner, payload, fail):
    mock = AsyncMock(
        return_value=payload, side_effect=ValueError("upstream") if fail else None
    )
    monkeypatch.setattr(cls, inner, mock)
    result = await getattr(adapter.HNUCMAdapter(), method)(httpx.Cookies(), "student")
    assert result == ((False, "upstream", None) if fail else (True, "Success", payload))
    mock.assert_awaited_once()


@pytest.mark.parametrize("fail", [False, True])
async def test_adapter_login(monkeypatch, fail):
    cookies = httpx.Cookies({"sid": "ok"})
    monkeypatch.setattr(
        adapter,
        "get_valid_cookies",
        AsyncMock(
            return_value=cookies, side_effect=ValueError("upstream") if fail else None
        ),
    )
    success, message, result = await adapter.HNUCMAdapter().login("student", "password")
    assert success is not fail
    assert message == ("upstream" if fail else "Success")
    assert dict(result) == ({} if fail else {"sid": "ok"})


@pytest.mark.parametrize(
    "month,expected",
    [(1, "2025-2026-1"), (6, "2025-2026-1"), (7, "2026-2027-2"), (12, "2026-2027-2")],
)
async def test_semester_clock(monkeypatch, month, expected):
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, month, 1)

    monkeypatch.setattr(schedule, "datetime", SimpleNamespace(date=Clock))
    assert await schedule.get_current_semester() == expected


@pytest.mark.parametrize(
    "lines,name,title",
    [
        (["课程", "时间", "教室"], "未知教师", None),
        (["课程", "", "时间", "教室"], "未知教师", None),
        (["课程", "王老师", "时间", "教室"], "王老师", None),
        (["课程", "王老师(分组)", "时间", "教室"], "王老师(分组)", None),
        (["课程", "王老师(无)", "时间", "教室"], "王老师", None),
        (["课程", "王老师(助教)", "时间", "教室"], "王老师", "助教"),
    ],
)
async def test_course_teacher_variants(lines, name, title):
    result = await schedule.parse_course_block(lines)
    assert result["teacher_name"] == name
    assert result["teacher_title"] == title
    assert result["week_list"] == []
    assert result["class_period"] == []
    assert result["location"] == ""


async def test_course_block_invalid_and_merged():
    with pytest.raises(ValueError, match="课程解析出错"):
        await schedule.parse_course_block(["课程"])
    assert await schedule.parse_cell_content(" \n\n ") == []
    courses = await schedule.parse_cell_content(
        "课程\n\n1-2周[1-2节]\n\n教室\n\n另一门\n教师\n3周[3节]\n修业楼201"
    )
    assert [course["course_name"] for course in courses] == ["课程", "另一门"]
    assert courses[0]["week_list"] == [1, 2]
    assert courses[1]["location"] == "修2201"
    assert await schedule.parse_periods_to_list("1-a-3节") == [1, 3]


@pytest.mark.parametrize("kind", ["profile", "schedule"])
@pytest.mark.parametrize("failure", ["http", "network"])
async def test_excel_download_errors(monkeypatch, kind, failure):
    cls = ProfileCrawler if kind == "profile" else schedule.CourseScheduleCrawler
    crawler = cls("https://example.test", {}, "HNUCM", "student")
    response = httpx.Response(
        500, request=httpx.Request("POST", "https://example.test")
    )
    monkeypatch.setattr(
        httpx.AsyncClient,
        "post",
        AsyncMock(
            return_value=response,
            side_effect=httpx.ConnectError("offline") if failure == "network" else None,
        ),
    )
    with pytest.raises(ValueError, match="获取"):
        if kind == "profile":
            await crawler.fetch_profile_xls(httpx.Cookies())
        else:
            await crawler.fetch_course_schedule_xls("invalid", httpx.Cookies())


def mock_workbook(monkeypatch, module, rows):
    sheet = SimpleNamespace(nrows=len(rows), row_values=lambda i: rows[i])
    monkeypatch.setattr(
        module.xlrd,
        "open_workbook",
        lambda *args, **kwargs: SimpleNamespace(sheet_by_index=lambda i: sheet),
    )


@pytest.mark.parametrize("kind", ["profile", "schedule"])
@pytest.mark.parametrize("failure", ["index", "format"])
async def test_excel_parse_errors(monkeypatch, tmp_path, kind, failure):
    from adapter.hnucm_adapter import profile

    module = profile if kind == "profile" else schedule
    crawler = (ProfileCrawler if kind == "profile" else schedule.CourseScheduleCrawler)(
        "https://example.test", {}, "HNUCM", "student"
    )
    path = tmp_path / "input.xls"
    path.write_bytes(b"invalid")
    if failure == "index":
        mock_workbook(monkeypatch, module, [])
    else:
        monkeypatch.setattr(
            module.xlrd,
            "open_workbook",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad xls")),
        )
    with pytest.raises(
        ValueError, match="失败" if failure == "index" else "Excel 格式"
    ):
        await (
            crawler.parse_profile_xls(path)
            if kind == "profile"
            else crawler.parse_course_schedule_xls(path)
        )


async def test_schedule_title_and_column_validation(monkeypatch, tmp_path):
    crawler = schedule.CourseScheduleCrawler(
        "https://example.test", {}, "HNUCM", "student"
    )
    mock_workbook(
        monkeypatch,
        schedule,
        [
            [""] * 8,
            ["no semester"],
            [""] * 8,
            ["", "课程\n1周[1节]\n教室", None, "", "", "", "", ""],
            [""] * 8,
        ],
    )
    courses = await crawler.parse_course_schedule_xls(tmp_path / "input.xls")
    assert courses[0]["semester"] == "未知学期"
    assert courses[0]["day_of_week"] == "1"
    mock_workbook(monkeypatch, schedule, [[""], ["no semester"]])
    with pytest.raises(ValueError, match="Excel 格式"):
        await crawler.parse_course_schedule_xls(tmp_path / "input.xls")


@pytest.mark.parametrize("kind", ["profile", "schedule"])
async def test_model_conversion_and_cleanup(monkeypatch, tmp_path, kind):
    crawler = (ProfileCrawler if kind == "profile" else schedule.CourseScheduleCrawler)(
        "https://example.test", {}, "HNUCM", "student"
    )
    path = tmp_path / "input.xls"
    path.touch()
    payload = (
        dict(
            department="学院", major="专业", class_name="一班", name="学生", gender="男"
        )
        if kind == "profile"
        else [
            dict(
                course_name="课程",
                day_of_week="1",
                semester="2026-2027-1",
                week_list=[1],
                class_period=[1],
            )
        ]
    )
    monkeypatch.setattr(
        crawler,
        "fetch_profile_xls" if kind == "profile" else "fetch_course_schedule_xls",
        AsyncMock(return_value=path),
    )
    monkeypatch.setattr(
        crawler,
        "parse_profile_xls" if kind == "profile" else "parse_course_schedule_xls",
        AsyncMock(return_value=payload),
    )
    result = await (
        crawler.get_profile_from_jwxt(httpx.Cookies())
        if kind == "profile"
        else crawler.get_course_schedule_from_jwxt("2026-2027-1", httpx.Cookies())
    )
    assert (result.name if kind == "profile" else result[0].course_name) == (
        "学生" if kind == "profile" else "课程"
    )
    assert not path.exists()
    monkeypatch.setattr(
        crawler,
        "fetch_profile_xls" if kind == "profile" else "fetch_course_schedule_xls",
        AsyncMock(side_effect=IndexError("invalid")),
    )
    with pytest.raises(IndexError):
        await (
            crawler.get_profile_from_jwxt(httpx.Cookies())
            if kind == "profile"
            else crawler.get_course_schedule_from_jwxt("2026-2027-1", httpx.Cookies())
        )


@pytest.mark.parametrize(
    "semester,params",
    [("2026-2027-1", {"kksj": "2026-2027-1"}), ("invalid", {}), ("", {})],
)
async def test_grade_mapping_and_detail_isolation(monkeypatch, semester, params):
    crawler = GradeCrawler("https://example.test")
    main = AsyncMock(
        return_value=[
            {
                "课程名称": "课程",
                "学分": "3",
                "总学时": "48",
                "成绩": "优秀",
                "详情页链接": "/ok",
            },
            {"课程名称": "失败详情", "详情页链接": "/error"},
            {"课程名称": "无详情"},
        ]
    )
    detail = AsyncMock(
        side_effect=[{"平时成绩": "88", "平时成绩占比": "20"}, RuntimeError("offline")]
    )
    monkeypatch.setattr(crawler, "fetch_main_page", main)
    monkeypatch.setattr(crawler, "fetch_detail_page", detail)
    cookies = httpx.Cookies()
    results = await crawler.get_grades_from_jwxt(cookies, semester)
    main.assert_awaited_once_with(params, cookies)
    assert len(results) == 3
    assert results[0].course_credit == 3
    assert results[0].regular_score == 88
    assert results[0].total_score == "优秀"
    assert results[1].course_credit == 0


@pytest.mark.parametrize(
    "html",
    [
        "<title>login</title>",
        "<table><tr><th>课程名称</th></tr></table>",
        "<table><tr><th>other</th></tr></table>",
    ],
)
async def test_grade_missing_main_table(html):
    assert await GradeCrawler("https://example.test").parse_main_page_html(html) == []


@pytest.mark.parametrize(
    "score",
    [
        "",
        "<a>无链接</a>",
        '<a href="other">无成绩</a>',
        "<a href=\"javascript:openWindow('/detail?zcj=优秀')\">优秀</a>",
    ],
)
async def test_grade_sparse_rows(score):
    cells = [""] * 16
    cells[4] = score
    html = (
        '<table id="dataList"><tr><th>课程名称</th></tr><tr><td>short</td></tr><tr>'
        + "".join(f"<td>{cell}</td>" for cell in cells)
        + "</tr></table>"
    )
    result = await GradeCrawler("https://example.test").parse_main_page_html(html)
    assert len(result) == 1
    assert result[0]["成绩"] == ("优秀" if "zcj" in score else "N/A")
    assert result[0]["课程名称"] == ""


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<title>login</title>", {}),
        ('<table id="dataList"><tr><th>成绩</th></tr></table>', {}),
        (
            '<table id="dataList"><tr><th>成绩</th><th>占比</th></tr><tr><td>88</td></tr></table>',
            {},
        ),
        (
            '<table schedule_id="dataList"><tr><th>成绩</th><th></th></tr><tr><td> 88 </td><td></td></tr></table>',
            {"成绩": "88", "": ""},
        ),
    ],
)
async def test_grade_detail_shapes(html, expected):
    assert (
        await GradeCrawler("https://example.test").parse_detail_page_html(html)
        == expected
    )


@pytest.mark.parametrize("status", [200, 503])
async def test_grade_fetch_main(monkeypatch, status):
    crawler = GradeCrawler("https://example.test")
    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        AsyncMock(
            return_value=httpx.Response(
                status, text='<table id="dataList"><tr></tr></table>'
            )
        ),
    )
    assert await crawler.fetch_main_page({}, httpx.Cookies()) == []


@pytest.mark.parametrize("mode", ["empty", "network", "success", "http"])
async def test_grade_fetch_detail(monkeypatch, mode):
    response = httpx.Response(
        503 if mode == "http" else 200,
        text='<table id="dataList"><tr><th>成绩</th></tr><tr><td>88</td></tr></table>',
        request=httpx.Request("GET", "https://example.test/detail"),
    )
    get = AsyncMock(
        return_value=response,
        side_effect=httpx.ConnectError("offline") if mode == "network" else None,
    )
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    crawler = GradeCrawler("https://example.test")
    if mode == "http":
        with pytest.raises(httpx.HTTPStatusError):
            await crawler.fetch_detail_page("/detail", httpx.Cookies())
    else:
        assert await crawler.fetch_detail_page(
            "" if mode == "empty" else "/detail", httpx.Cookies()
        ) == ({"成绩": "88"} if mode == "success" else {})
    if mode == "empty":
        get.assert_not_awaited()


@pytest.mark.parametrize("value,match", [(1234, "不是字符串"), ("123", "长度错误")])
async def test_ocr_result_validation(monkeypatch, value, match):
    monkeypatch.setattr(ocr.ocr, "classification", lambda image: value)
    with pytest.raises(ValueError, match=match):
        await ocr.img2txt("YWJjZA==")
