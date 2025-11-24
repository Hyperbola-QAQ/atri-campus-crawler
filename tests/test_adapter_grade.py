import pytest
import pytest_asyncio
from pathlib import Path
import aiofiles
from adapter.hnucm_adapter import HNUCMAdapter
from adapter.hnucm_adapter.grade import GradeCrawler
from schemas.grade_schema import GradeItem
from utils.log import logger
import dotenv
import os


SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
dotenv.load_dotenv(PROJECT_DIR / '.env.test')


class TestHNUCMAdapter:
    # 从 .env.test 文件中读取测试用的用户名和密码
    
    username: str = os.getenv('HNUCM_ADAPTER_TEST_USERNAME') or ""
    password: str = os.getenv('HNUCM_ADAPTER_TEST_PASSWORD') or ""

    @pytest_asyncio.fixture
    async def adapter(self):
        return HNUCMAdapter()
    
    @pytest.mark.asyncio
    async def test_parse_main_grade(self, adapter: HNUCMAdapter) -> None:
        # 读取./fixtures/main_grade.html
        async with aiofiles.open(SCRIPT_DIR / "fixtures" / "main_grade.html", "r", encoding="utf-8") as f:
            html_content = await f.read()
        
        grade_crawler = GradeCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            username=self.username,
        )

        # 解析 HTML 内容
        grade_items: list = await grade_crawler.parse_main_page_html(html_content=html_content)
        logger.debug(f"解析到的成绩项: {grade_items}")
        assert isinstance(grade_items, list)
        assert len(grade_items) > 0


        assert grade_items == [{'开课学期': '2024-2025-2', '课程编号': 'A130501000', '课程名称': '习近平新时代中国特色社会主义思想概论', '成绩': '77', '成绩标识': '', '学分': '3', '总学时': '48', '绩点': '2.7', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '通识教育课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004887&cj0708id=3A44123CB43A92E4E063480A0A0AC640&zcj=77'}, {'开课学期': '2024-2025-2', '课程编号': 'A130606000', '课程名称': '形势与政策（4）', '成绩': '79', '成绩标识': '', '学分': '0.5', '总学时': '8', '绩点': '2.9', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '通识教育课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004879&cj0708id=3A8F747C5E085029E063480A0A0A6459&zcj=79'}, {'开课学期': '2024-2025-2', '课程编号': 'A140501000', '课程名称': '体育（4）', '成绩': '90', '成绩标识': '', '学分': '2', '总学时': '32', '绩点': '4', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '通识教育课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252000978&cj0708id=3B48CEC4C7F24EDAE063480A0A0A38C3&zcj=90'}, {'开课学期': '2024-2025-2', '课程编号': 'A160101000', '课程名称': '大学生就业指导', '成绩': '90', '成绩标识': '', '学分': '1.5', '总学时': '24', '绩点': '4', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '通识教育课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004895&cj0708id=3A5A6E9780A00356E063470A0A0A4FDA&zcj=90'}, {'开课学期': '2024-2025-2', '课程编号': 'B080406304', '课程名称': '计算机组成原理与汇编语言', '成绩': '90', '成绩标识': '', '学分': '4', '总学时': '80', '绩点': '4', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '专业基础课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004892&cj0708id=38A2D84A3AAF1706E063480A0A0A6053&zcj=90'}, {'开课学期': '2024-2025-2', '课程编号': 'C080204000', '课程名称': 'Web技术（Java）', '成绩': '80', '成绩标识': '', '学分': '2.5', '总学时': '48', '绩点': '3', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '选修', '课程性质': '专业基础课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004904&cj0708id=39B9874004A75D82E063470A0A0A0D83&zcj=80'}, {'开课学期': '2024-2025-2', '课程编号': 'C080416000', '课程名称': '移动应用开发', '成绩': '93', '成绩标识': '', '学分': '2', '总学时': '48', '绩点': '4.3', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '选修', '课程性质': '专业基础课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252006446&cj0708id=398BE2B4EE89BAB2E063470A0A0A0F3D&zcj=93'}, {'开课学期': '2024-2025-2', '课程编号': 'D080214304', '课程名称': '计算机网络原理与应用', '成绩': '70', '成绩标识': '', '学分': '3.5', '总学时': '62', '绩点': '2', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '专业课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004912&cj0708id=39ED1E5A08A4EEAEE063470A0A0A0ACE&zcj=70'}, {'开课学期': '2024-2025-2', '课程编号': 'D080222304', '课程名称': '人工智能原理', '成绩': '86', '成绩标识': '', '学分': '2.5', '总学时': '48', '绩点': '3.6', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '专业课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004908&cj0708id=3AFF2213C3989B6AE063470A0A0A6C3A&zcj=86'}, {'开课学期': '2024-2025-2', '课程编号': 'D080226304', '课程名称': '算法分析与设计', '成绩': '87', '成绩标识': '', '学分': '2.5', '总学时': '44', '绩点': '3.7', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '专业课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004900&cj0708id=398CD206B85D6EDCE063480A0A0A667A&zcj=87'}, {'开课学期': '2024-2025-2', '课程编号': 'H00011200B', '课程名称': '插花艺术', '成绩': '98', '成绩标识': '', '学分': '1', '总学时': '29', '绩点': '4.8', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '选修', '课程性质': '通识教育课', '通选课类别': '线上通选课', '通选课模块': '艺术与美育', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=&cj0708id=2027A2069E274B5EBBEA2B2870FE8F0B&zcj=98'}, {'开课学期': '2024-2025-2', '课程编号': 'H00011400E', '课程名称': '食品安全', '成绩': '97', '成绩标识': '', '学分': '1', '总学时': '30', '绩点': '4.7', '补重学期': '', '考核方式': '期末统考', '考试性质': '正常考试', '课程属性': '选修', '课程性质': '通识教育课', '通选课类别': '线上通选课', '通选课模块': '自然与科学', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=&cj0708id=5EF9211628544AD4B6A894A4D81F3CD4&zcj=97'}, {'开课学期': '2024-2025-2', '课程编号': 'K040101000', '课程名称': '基础医学概论', '成绩': '87', '成绩标识': '', '学分': '3', '总学时': '48', '绩点': '3.7', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '选修', '课程性质': '专业拓展课', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252004883&cj0708id=397A5BEF4462E3DDE063470A0A0A49BD&zcj=87'}, {'开课学期': '2024-2025-2', '课程编号': 'S080215304', '课程名称': '科研方法训练（医学数据分析等）', '成绩': '88', '成绩标识': '', '学分': '1', '总学时': '30', '绩点': '3.8', '补重学期': '', '考核方式': '随堂考查', '考试性质': '正常考试', '课程属性': '必修', '课程性质': '集中实践环节', '通选课类别': '', '通选课模块': '', '详情页链接': '/jsxsd/kscj/pscj_list.do?xs0101id=202301020445&jx0404id=202420252006442&cj0708id=39E6259F67AB3997E063470A0A0AA396&zcj=88'}]

    @pytest.mark.asyncio
    async def test_parse_detail_grade(self, adapter: HNUCMAdapter) -> None:
        # 读取./fixtures/detail_grade.html
        with open(SCRIPT_DIR / "fixtures" / "detail_grade.html", "r", encoding="utf-8") as f:
            html_content = f.read()

        # 解析 HTML 内容
        grade_crawler = GradeCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            username=self.username,
        )
        detail_grade: dict = await grade_crawler.parse_detail_page_html(html_content=html_content)
        logger.debug(f"解析到的详细成绩: {detail_grade}")
        assert isinstance(detail_grade, dict)
        assert len(detail_grade) > 0

    @pytest.mark.asyncio
    async def test_get_grades(self, adapter: HNUCMAdapter) -> None:
        # 使用真实的登录请求
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        logger.debug(f"[{self.username}] 登录成功，获取到的 cookies: {cookies}")

        success, message, grade_list = await adapter.get_grades(
            cookies=cookies,
            semester="2023-2024-1",
            username=self.username,
        )

        assert isinstance(grade_list, list)
        assert len(grade_list) > 0, "成绩列表为空"

    @pytest.mark.asyncio
    async def test_get_all_grades(self, adapter: HNUCMAdapter) -> None:
        # 测试获取所有学期成绩
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        success, message, all_grades = await adapter.get_grades(
            cookies=cookies,
            username=self.username,
        )

        assert isinstance(all_grades, list)
        assert len(all_grades) > 0, "所有学期成绩列表为空"

    @pytest.mark.asyncio
    async def test_get_grades_with_invalid_semester(self, adapter: HNUCMAdapter) -> None:
        # 测试使用无效学期参数
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        # 应该能处理无效学期参数的情况
        success, message, grades = await adapter.get_grades(
            cookies=cookies,
            semester="invalid-term",
            username=self.username,
        )

        # 即使学期无效也应该返回列表格式
        assert isinstance(grades, list)

    @pytest.mark.asyncio
    async def test_get_grades_structure(self, adapter: HNUCMAdapter) -> None:
        # 测试成绩数据结构
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        success, message, grade_list = await adapter.get_grades(
            cookies=cookies,
            semester="2024-2025-1",
            username=self.username,
        )

        # 确保返回的是列表且至少有一个元素
        assert isinstance(grade_list, list)
        assert len(grade_list) > 0, "成绩列表为空，无法验证结构"

        first_grade = grade_list[0]

        # 确保 first_grade 是 GradeItem 类型
        assert isinstance(first_grade, GradeItem), f"期望成绩项为 GradeItem 类型，实际为 {type(first_grade)}"

        # 检查必要属性是否存在
        required_attrs = ["course_name", "course_credit", "total_score", "grade_point", "offered_semester"]
        for attr in required_attrs:
            assert hasattr(first_grade, attr), f"缺少必要属性: {attr}"
