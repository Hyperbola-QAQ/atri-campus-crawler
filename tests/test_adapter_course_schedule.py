from typing import Any, List, Dict
import pytest
import pytest_asyncio
from pathlib import Path
from adapter.hnucm_adapter import HNUCMAdapter
from adapter.hnucm_adapter.course_schedule import (
    CourseScheduleCrawler,
    parse_weeks_to_list,
    parse_periods_to_list,
    parse_course_block,
    parse_cell_content,
)
from utils.log import logger
import dotenv
import os

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
dotenv.load_dotenv(PROJECT_DIR / ".env.test")


@pytest.mark.asyncio
async def test_parse_course_schedule_xls_semester() -> None:
    assert await parse_weeks_to_list("1-11") == [i for i in range(1, 12)]
    assert await parse_weeks_to_list("19") == [19]
    assert await parse_weeks_to_list("1-9,11") == [i for i in range(1, 10)] + [11]
    assert await parse_weeks_to_list("1-3,5-7,9-10,12-13,15-16") == [
        1,
        2,
        3,
        5,
        6,
        7,
        9,
        10,
        12,
        13,
        15,
        16,
    ]


@pytest.mark.asyncio
async def test_parse_periods_to_list() -> None:
    assert await parse_periods_to_list("03-04-05节") == [3, 4, 5]
    assert await parse_periods_to_list("01-02-03-04节") == [1, 2, 3, 4]
    assert await parse_periods_to_list("01-02-03-04-05-06节") == [1, 2, 3, 4, 5, 6]
    assert await parse_periods_to_list("03-04-05-06-07-08节") == [3, 4, 5, 6, 7, 8]


@pytest.mark.asyncio
async def test_parse_course_block() -> None:
    assert await parse_course_block(
        [
            "科研能力综合实践（医学数据挖掘等）",
            "杨平(讲师)",
            "20([周])[03-04-05-06-07-08节]",
            "5机房(图书馆后栋506)",
        ]
    ) == {
        "course_name": "科研能力综合实践（医学数据挖掘等）",
        "teacher_name": "杨平",
        "teacher_title": "讲师",
        "week_list": [20],
        "class_period": [3, 4, 5, 6, 7, 8],
        "location": "5机房(图书馆后栋506)",
    }
    assert await parse_course_block(
        [
            "科研能力综合实践（医学数据挖掘等）",
            "20([周])[03-04-05-06-07-08节]",
            "5机房(图书馆后栋506)",
        ]
    ) == {
        "course_name": "科研能力综合实践（医学数据挖掘等）",
        "teacher_name": "未知教师",
        "teacher_title": None,
        "week_list": [20],
        "class_period": [3, 4, 5, 6, 7, 8],
        "location": "5机房(图书馆后栋506)",
    }
    assert await parse_course_block(
        [
            "科研能力综合实践（医学数据挖掘等）",
            "杨平(讲师)",
            "20([周])[03-04-05-06-07-08节]",
            "5机房(图书馆后栋506)",
        ]
    ) == {
        "course_name": "科研能力综合实践（医学数据挖掘等）",
        "teacher_name": "杨平",
        "teacher_title": "讲师",
        "week_list": [20],
        "class_period": [3, 4, 5, 6, 7, 8],
        "location": "5机房(图书馆后栋506)",
    }


@pytest.mark.asyncio
async def test_parse_cell_content() -> None:
    assert (
        await parse_cell_content(
            """
        
        操作系统原理
        况玲(无)
        17([周])[03节]
        济世楼507教室

        科研能力综合实践（医学数据挖掘等））
        杨平(讲师)
        20([周])[03-04-05-06-07-08节]
        5机房(图书馆后栋506)

    """
        )
        == [
            {
                "course_name": "操作系统原理",
                "teacher_name": "况玲",
                "teacher_title": None,
                "week_list": [17],
                "class_period": [3],
                "location": "济3507",
            },
            {
                "course_name": "科研能力综合实践（医学数据挖掘等））",
                "teacher_name": "杨平",
                "teacher_title": "讲师",
                "week_list": [20],
                "class_period": [3, 4, 5, 6, 7, 8],
                "location": "5机房(图书馆后栋506)",
            },
        ]
    )


class TestHNUCMAdapter:

    # 从 .env.test 文件中读取测试用的用户名和密码

    username: str = os.getenv("HNUCM_ADAPTER_TEST_USERNAME") or ""
    password: str = os.getenv("HNUCM_ADAPTER_TEST_PASSWORD") or ""

    @pytest_asyncio.fixture
    async def adapter(self) -> HNUCMAdapter:
        return HNUCMAdapter()

    @pytest.mark.asyncio
    async def test_fetch_course_schedule_xls(self, adapter: HNUCMAdapter) -> None:
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        course_schedule_crawler = CourseScheduleCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            school_name=adapter.school_name,
            username=self.username,
        )
        xls_path: Path = await course_schedule_crawler.fetch_course_schedule_xls(
            cookies=cookies, semester="2023-2024-2"
        )

        # 断言
        assert xls_path.exists(), "xls 文件不存在"
        assert xls_path.is_file(), "xls_path 不是文件"
        assert xls_path.suffix == ".xls", "xls 文件后缀不是 .xls"

    @pytest.mark.asyncio
    async def test_parse_course_schedule_xls(self, adapter: HNUCMAdapter) -> None:
        course_schedule_crawler = CourseScheduleCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            school_name=adapter.school_name,
            username=self.username,
        )
        xls_path: Path = (
            SCRIPT_DIR
            / "fixtures"
            / "HNUCM_202301020304_course_schedule_2023-2024-2.xls"
        )
        course_schedules: List[Dict[str, Any]] = (
            await course_schedule_crawler.parse_course_schedule_xls(xls_path)
        )

        logger.debug(course_schedules)
        assert course_schedules == [
            {
                "course_name": "专业实训（数据结构等）",
                "teacher_name": "辛国江",
                "teacher_title": "副教授",
                "week_list": [19],
                "class_period": [1, 2, 3, 4, 5, 6],
                "location": "12机房(图书馆后栋602)",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "线性代数",
                "teacher_name": "刘静",
                "teacher_title": "助教",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13],
                "class_period": [1, 2],
                "location": "济3501",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术(分组02)",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [16],
                "class_period": [],
                "location": "模拟电子技术室(图书馆后栋607)",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "专业实训（数据结构等）",
                "teacher_name": "辛国江",
                "teacher_title": "副教授",
                "week_list": [19],
                "class_period": [1, 2, 3, 4, 5, 6],
                "location": "12机房(图书馆后栋602)",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "线性代数",
                "teacher_name": "刘静",
                "teacher_title": "助教",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13],
                "class_period": [1, 2],
                "location": "立1606",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [16],
                "class_period": [1, 2],
                "location": "立1508",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "专业实训（数据结构等）",
                "teacher_name": "辛国江",
                "teacher_title": "副教授",
                "week_list": [19],
                "class_period": [1, 2, 3, 4, 5, 6],
                "location": "12机房(图书馆后栋602)",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "大学英语（2）",
                "teacher_name": "陈剑",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 5, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17],
                "class_period": [1, 2],
                "location": "济3403",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "专业实训（数据结构等）",
                "teacher_name": "辛国江",
                "teacher_title": "副教授",
                "week_list": [19],
                "class_period": [1, 2, 3, 4, 5, 6],
                "location": "12机房(图书馆后栋602)",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 5, 7, 8, 9, 11, 12, 13, 14, 15],
                "class_period": [1, 2],
                "location": "济3303",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术(分组02)",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [17],
                "class_period": [],
                "location": "模拟电子技术室(图书馆后栋607)",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "专业实训（数据结构等）",
                "teacher_name": "辛国江",
                "teacher_title": "副教授",
                "week_list": [19],
                "class_period": [1, 2, 3, 4, 5, 6],
                "location": "12机房(图书馆后栋602)",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "大学英语（2）",
                "teacher_name": "陈剑",
                "teacher_title": "讲师",
                "week_list": [9],
                "class_period": [1, 2],
                "location": "济3403",
                "day_of_week": "7",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15],
                "class_period": [4, 5],
                "location": "济3403",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [10],
                "class_period": [4, 5],
                "location": "济3301",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术(分组02)",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [17],
                "class_period": [],
                "location": "模拟电子技术室(图书馆后栋607)",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15],
                "class_period": [3, 4, 5],
                "location": "立1507",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [10],
                "class_period": [3, 4, 5],
                "location": "立1605",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [16],
                "class_period": [4, 5],
                "location": "立1507",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "数据结构",
                "teacher_name": "丁长松",
                "teacher_title": "教授",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 15, 16],
                "class_period": [4, 5],
                "location": "济3105",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "数据结构",
                "teacher_name": "丁长松",
                "teacher_title": "教授",
                "week_list": [9, 11, 14],
                "class_period": [4, 5],
                "location": "7机房(图书馆后栋504)",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "数据结构",
                "teacher_name": "丁长松",
                "teacher_title": "教授",
                "week_list": [1, 2, 3, 4, 5, 7, 8, 12, 13, 15, 16],
                "class_period": [4, 5],
                "location": "济3608",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "数据结构",
                "teacher_name": "丁长松",
                "teacher_title": "教授",
                "week_list": [9, 11, 14],
                "class_period": [4, 5],
                "location": "7机房(图书馆后栋504)",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "中国近现代史纲要",
                "teacher_name": "戴玉梅",
                "teacher_title": "讲师",
                "week_list": [17],
                "class_period": [3, 4, 5],
                "location": "济3108",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 7, 8, 9, 11, 12, 13, 14, 15],
                "class_period": [3, 4, 5],
                "location": "立1605",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "高等数学（2）",
                "teacher_name": "尚晶",
                "teacher_title": "讲师",
                "week_list": [5],
                "class_period": [3, 4, 5],
                "location": "济3309",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "数据结构",
                "teacher_name": "丁长松",
                "teacher_title": "教授",
                "week_list": [9],
                "class_period": [4, 5],
                "location": "济3608",
                "day_of_week": "7",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "体育（2）(初级长拳10607-7)",
                "teacher_name": "赵壮",
                "teacher_title": "助教",
                "week_list": [1, 2],
                "class_period": [6, 7],
                "location": "含浦体育馆07",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "体育（2）(初级长拳10607-7)",
                "teacher_name": "赵壮",
                "teacher_title": "助教",
                "week_list": [3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 17],
                "class_period": [6, 7],
                "location": "含浦体育馆07",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "体育（2）(初级长拳10607-7)",
                "teacher_name": "赵壮",
                "teacher_title": "助教",
                "week_list": [10],
                "class_period": [6, 7],
                "location": "含浦体育馆07",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "中国近现代史纲要",
                "teacher_name": "戴玉梅",
                "teacher_title": "讲师",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15],
                "class_period": [6, 7, 8],
                "location": "济3415",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "电路与模拟电子技术(分组02)",
                "teacher_name": "张洁",
                "teacher_title": "讲师",
                "week_list": [16],
                "class_period": [],
                "location": "模拟电子技术室(图书馆后栋607)",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "中国近现代史纲要",
                "teacher_name": "戴玉梅",
                "teacher_title": "讲师",
                "week_list": [17],
                "class_period": [6, 7, 8],
                "location": "济3105",
                "day_of_week": "3",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "离散数学",
                "teacher_name": "韦昌法",
                "teacher_title": "教授",
                "week_list": [1, 2, 3, 4, 5, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17],
                "class_period": [6, 7],
                "location": "立1403",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "形势与政策（2）",
                "teacher_name": "杨晓溪",
                "teacher_title": "高级政工师",
                "week_list": [14, 15],
                "class_period": [6, 7, 8],
                "location": "济3208",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "形势与政策（2）",
                "teacher_name": "杨晓溪",
                "teacher_title": "高级政工师",
                "week_list": [16],
                "class_period": [6, 7],
                "location": "济3208",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "离散数学",
                "teacher_name": "韦昌法",
                "teacher_title": "教授",
                "week_list": [9],
                "class_period": [6, 7],
                "location": "立1403",
                "day_of_week": "7",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "离散数学",
                "teacher_name": "韦昌法",
                "teacher_title": "教授",
                "week_list": [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 17],
                "class_period": [8, 9],
                "location": "济3511",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "离散数学",
                "teacher_name": "韦昌法",
                "teacher_title": "教授",
                "week_list": [10],
                "class_period": [8, 9],
                "location": "济3511",
                "day_of_week": "1",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "创业基础",
                "teacher_name": "湛欢",
                "teacher_title": "副教授",
                "week_list": [1, 2, 3, 7, 11, 13, 14],
                "class_period": [8, 9],
                "location": "济3216",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "创业基础",
                "teacher_name": "湛欢",
                "teacher_title": "副教授",
                "week_list": [4, 5, 8, 9, 12],
                "class_period": [8, 9],
                "location": "济3216",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "创业基础",
                "teacher_name": "湛欢",
                "teacher_title": "副教授",
                "week_list": [9],
                "class_period": [8, 9],
                "location": "济3116",
                "day_of_week": "5",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "摄影后期制作",
                "teacher_name": "高式英",
                "teacher_title": "副教授",
                "week_list": [9, 11, 12, 13, 14, 15, 16, 17],
                "class_period": [10, 11],
                "location": "济3212",
                "day_of_week": "2",
                "semester": "2023-2024-2",
            },
            {
                "course_name": "创业基础",
                "teacher_name": "湛欢",
                "teacher_title": "副教授",
                "week_list": [15, 16],
                "class_period": [10, 11, 12],
                "location": "济3216",
                "day_of_week": "4",
                "semester": "2023-2024-2",
            },
        ]

    @pytest.mark.asyncio
    async def test_course_schedule_crawler(self, adapter: HNUCMAdapter) -> None:

        _, message, cookies = await adapter.login(self.username, self.password)
        if not cookies:
            pytest.skip(f"登录失败，跳过测试：{message}")

        _, message, schedules = await adapter.get_course_schedule(
            username=self.username,
            cookies=cookies,
            semester="2024-2025-1",
        )

        logger.debug(schedules)
        assert len(schedules) > 0
