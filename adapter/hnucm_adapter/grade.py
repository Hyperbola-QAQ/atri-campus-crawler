from typing import Dict, List, Optional, Any
from httpx import Cookies
import httpx
import re
from lxml import etree # type: ignore
from schemas.grade_schema import GradeItem
import asyncio
from utils.log import logger
from utils.type import safe_float, safe_int

class GradeCrawler:
    def __init__(self, base_url: str, headers: Optional[dict] = None, username: str = "unknown"):
        self.base_url = base_url
        self.headers = headers
        self.username = username

    async def get_grades_from_jwxt(
        self,
        cookies: Cookies,
        semester: str = "",
    ) -> List[GradeItem]:
        """
        从教务系统获取成绩信息
        
        Args:
            cookies (Cookies): 登录后的Cookies
            semester (str, optional): 学期标识符
            headers (Optional[dict], optional): 请求头信息
            base_url (str): 教务系统基础URL
            username (str): 用户名，用于日志标识
            
        Returns:
            List[GradeItem]: 成绩数据列表
        """
        logger.debug(f"[{self.username}] 开始获取成绩信息，学期: {semester}")
        
        # 页面参数：学期格式为 yyyy-yyyy-1 或 yyyy-yyyy-2
        if semester and re.fullmatch(r'^\d{4}-\d{4}-[12]$', semester):
            params = {'kksj': semester}
        else:
            params = {}
        
        # 获取主页面数据
        logger.debug(f"[{self.username}] 获取成绩主页面数据")
        course_data = await self.fetch_main_page(params, cookies)
        logger.debug(f"[{self.username}] 获取到 {len(course_data)} 条课程记录")
        
        # 顺序获取详情页数据
        logger.debug(f"[{self.username}] 获取详情页数据")

        async def _fetch_and_update(course, cookies):
            if not course.get('详情页链接'):
                return
            try:
                detail = await self.fetch_detail_page(course['详情页链接'], cookies)
                course.update(detail)
            except Exception as e:
                logger.warning(f"[{self.username}] 获取课程详情失败: {course.get('课程名称', '未知课程')} - {e}")

        # 并发执行
        await asyncio.gather(*[
            _fetch_and_update(course, cookies)
            for course in course_data
        ])

        # 将原始字典数据映射为 GradeItem 列表
        grade_items = [
            GradeItem(
                course_code=course.get('课程编号'),
                course_name=course.get('课程名称', ''),
                course_credit=safe_float(course.get('学分')) or 0.0,
                total_hours=safe_int(course.get('总学时')),
                lab_score=safe_float(course.get('实验成绩')),
                lab_ratio=safe_float(course.get('实验成绩占比')),
                midterm_score=safe_float(course.get('期中成绩')),
                midterm_ratio=safe_float(course.get('期中成绩占比')),
                regular_score=safe_float(course.get('平时成绩')),
                regular_ratio=safe_float(course.get('平时成绩占比')),
                final_score=safe_float(course.get('期末成绩')),
                final_ratio=safe_float(course.get('期末成绩占比')),
                total_score=course.get('成绩', 'N/A'),
                grade_mark=course.get('成绩标识'),
                grade_point=safe_float(course.get('绩点')) or 0.0,
                offered_semester=course.get('开课学期', ''),
                retake_semester=course.get('补重学期'),
                assessment_method=course.get('考核方式'),
                exam_type=course.get('考试性质'),
                course_attribute=course.get('课程属性'),
                course_nature=course.get('课程性质'),
                general_elective_category=course.get('通选课类别'),
                general_elective_module=course.get('通选课模块'),
            )
            for course in course_data
        ]

        logger.debug(f"[{self.username}] 成绩信息获取完成，共 {len(grade_items)} 条记录")
        return grade_items

    async def parse_main_page_html(self, html_content: str) -> List[Dict]:
        """
        解析主页面HTML内容
        
        Args:
            html_content: HTML字符串
            
        Returns:
            解析后的课程数据列表
        """
        # 使用lxml解析HTML
        tree = etree.HTML(html_content)
        
        # 根据HTML结构，先查找id为dataList的表格
        tables = tree.xpath('//table[@id="dataList"]')
        table = tables[0] if tables else None

        if not tables:
            # 如果找不到，尝试查找任意包含成绩信息的表格
            all_tables = tree.xpath('//table')
            for t in all_tables:
                # 查找表格中的th元素，检查是否包含'课程名称'
                ths = t.xpath('.//th')
                for th in ths:
                    if th.text and '课程名称' in th.text:
                        table = t
                        break
                if table:
                    break
            
            if not tables:
                logger.warning(f"[{self.username}] 未找到主页面表格，页面内容可能已更改")
                # 尝试获取页面标题
                title_elements = tree.xpath('//title')
                title = title_elements[0].text if title_elements else '无标题'
                logger.debug(f"[{self.username}] 页面标题: {title}")
                return []
        
        # 获取所有tr元素，跳过表头（第一个tr）
        rows = table.xpath('./tr')[1:] if table is not None else []
        
        course_data = []
        for row_index, row in enumerate(rows):
            # 使用xpath获取所有td元素
            columns = row.xpath('./td')
            if len(columns) < 16:  # 根据HTML结构，需要至少16列数据
                logger.warning(f"[{self.username}] 第 {row_index+1} 行数据列数不足，跳过")
                continue
            
            # 提取基本信息，根据HTML表格结构调整列索引
            # 提取成绩和详情页链接
            score_cell = columns[4]
            # logger.debug(f"第 {row_index+1} 行成绩单元格内容: {etree.tostring(score_cell, encoding='unicode')}")
            score_a_tag = score_cell.xpath('.//a')[0] if score_cell.xpath('.//a') else None
            score = 'N/A'
            detail_link = None
            href = score_a_tag.attrib.get('href', None) if score_a_tag is not None else None
            
            if href:
                # 数据示例{'href': "javascript:openWindow('/jsxsd/kscj/pscj_list.do?xs0************2C&zcj=84',700,500)"}
                # 详情页末尾为成绩 TODO
                score_match = re.search(r"zcj=([\u4e00-\u9fa5]+|\d+)", href)
                score = score_match.group(1) if score_match else 'N/A'
                # 提取详情页链接
                match = re.search(r"openWindow\('(.*?)'", href)
                if match:
                    detail_link = match.group(1)
            
            course = {
                '开课学期': columns[1].text.strip() if columns[1].text else '',
                '课程编号': columns[2].text.strip() if columns[2].text else '',
                '课程名称': columns[3].text.strip() if columns[3].text else '',
                '成绩': score,
                '成绩标识': columns[5].text.strip() if columns[5].text else '',
                '学分': columns[6].text.strip() if columns[6].text else '',
                '总学时': columns[7].text.strip() if columns[7].text else '',
                '绩点': columns[8].text.strip() if columns[8].text else '',
                '补重学期': columns[9].text.strip() if columns[9].text else '',
                '考核方式': columns[10].text.strip() if columns[10].text else '',
                '考试性质': columns[11].text.strip() if columns[11].text else '',
                '课程属性': columns[12].text.strip() if columns[12].text else '',
                '课程性质': columns[13].text.strip() if columns[13].text else '',
                '通选课类别': columns[14].text.strip() if columns[14].text else '',
                '通选课模块': columns[15].text.strip() if columns[15].text else '',
                '详情页链接': detail_link
            }
            # logger.debug(f"[{username}] 解析课程信息: {course['课程名称']}")

            course_data.append(course)

        logger.debug(f"[{self.username}] 主页面数据解析完成，共获取 {len(course_data)} 条记录")
        return course_data

    async def fetch_main_page(self, params: Dict[str, str], cookies: Cookies) -> List[Dict]:
        """
        获取主页面数据
        
        Args:
            base_url: 基础URL
            params: 请求参数
            cookies: Cookies对象
            headers: 请求头
            username: 用户名，用于日志标识
            
        Returns:
            解析后的课程数据列表
        """
        logger.debug(f"[{self.username}] 请求成绩主页面: {self.base_url+'/jsxsd/kscj/cjcx_list'}")
        async with httpx.AsyncClient(base_url=self.base_url, cookies=cookies, headers=self.headers) as client:
            response = await client.get('/jsxsd/kscj/cjcx_list', params=params)
            logger.debug(f"[{self.username}] 主页面响应状态码: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"[{self.username}] 主页面请求失败，状态码: {response.status_code}")
                return []

            # 调用解析函数处理HTML内容
            return await self.parse_main_page_html(response.text)

    async def parse_detail_page_html(self, html_content: str) -> Dict[str, Any]:
        """
        解析详情页HTML内容
        
        Args:
            html_content: HTML字符串            
        Returns:
            解析后的详情数据字典
        """
        # 使用lxml解析HTML
        tree = etree.HTML(html_content)
        
        # 修改表格查找逻辑，首先查找schedule_id，然后查找id
        tables = tree.xpath('//table[@schedule_id="dataList"]')
        table = tables[0] if tables else None
        
        if not tables:
            tables = tree.xpath('//table[@id="dataList"]')
            table = tables[0] if tables else None
        
        if not tables:
            logger.warning(f"[{self.username}] 未找到详情页表格")
            # 添加更多调试信息
            title_elements = tree.xpath('//title')
            title = title_elements[0].text if title_elements else '无标题'
            logger.debug(f"[{self.username}] 页面标题: {title}")
            return {}

        # 获取所有tr元素
        rows = table.xpath('./tr') if table is not None else []
        if len(rows) < 2:
            logger.warning(f"[{self.username}] 详情页表格数据不完整")
            return {}

        # 提取表头
        headers_list = [th.text.strip() if th.text else '' for th in rows[0].xpath('./th')]
        # 提取数据行
        data_row = rows[1].xpath('./td')
        
        if len(data_row) < len(headers_list):
            logger.warning(f"[{self.username}] 详情页列数不匹配")
            return {}

        # 创建详情数据字典
        detail_data = {}
        for i, header in enumerate(headers_list):
            if i < len(data_row):
                detail_data[header] = data_row[i].text.strip() if data_row[i].text else ''
        logger.debug(f"[{self.username}] 详情页数据解析完成")
        
        return detail_data

    async def fetch_detail_page(self, detail_url: str, cookies: Cookies) -> Dict[str, Any]:
        """
        获取详情页数据
        
        Args:
            detail_url: 详情页相对URL
            cookies: Cookies对象
            
        Returns:
            解析后的详情数据字典
        """
        if not detail_url:
            logger.warning(f"[{self.username}] 详情页链接为空，跳过")
            return {}
            
        
        try:
            async with httpx.AsyncClient(base_url=self.base_url, cookies=cookies, headers=self.headers) as client:
                response: httpx.Response = await client.get(detail_url, timeout=10)
                response.raise_for_status()
        except httpx.RequestError as e:
            logger.error(f"[{self.username}] 请求详情页失败: {detail_url}，错误信息: {e}")
            return {}

        # 调用解析函数处理HTML内容
        return await self.parse_detail_page_html(response.text)
