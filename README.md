以下是对你提供的 Markdown 文档的 **规范化、语法修正与排版优化**，使其更专业、清晰、符合技术文档标准：

---

# ATRI-Crawler

本微服务是上代产品后端中「爬虫模块」的重构版，核心目标：**更快、更稳、更易维护**。

## 主要优化

1. **高性能解析**  
   使用 `lxml` 替代 `BeautifulSoup (bs4)`，显著提升 HTML/XML 解析效率。

2. **彻底解耦**  
   将**解析**、**存储**、**调度**三大职责完全拆分，各模块可独立升级或替换。

3. **高内聚设计**  
   每个子模块职责单一，接口清晰，杜绝“上帝类”。

4. **协程并发**  
   基于 `asyncio` 实现高并发请求处理，资源利用率更高。

5. **完备日志体系**  
   结构化日志 + 多级日志控制，便于问题追踪与监控。

6. **高覆盖率单元测试**  
   - 测试覆盖率 ≥ 90%  
   - CI/CD 流水线每次提交自动运行测试，保障重构安全。

---

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 启动服务
```bash
python main.py
```

---

## API 调用说明

服务通过 **Kafka 消息队列** 异步通信：

- **请求主题**：`atri-crawler-input`  
- **响应主题**：`atri-crawler-output`

### 请求格式（JSON）

```json
{
    "request_id": "唯一请求ID",
    "school": "HNUCM",
    "action": "login|get_profile|get_grades|get_course_schedule",
    "username": "学号",
    "password": "密码",
    "params": {
        "semester": "2023-2024-1"
    }
}
```

> **说明**：
> - `request_id`：用于关联请求与响应，必须全局唯一。
> - `params.semester`：仅在 `get_grades` 和 `get_course_schedule` 动作中可选。

---

## 目录结构

```
atri_crawler/
├── adapter/               # 学校适配器（各学校教务系统实现）
│   └── hnucm_adapter/     # 湖南中医药大学（HNUCM）具体实现
├── schemas/               # Pydantic 数据模型定义
├── utils/                 # 工具模块（日志、OCR、Redis、Nacos 等）
├── tests/                 # 单元测试与集成测试
├── main.py                # 服务主入口
└── pyproject.toml         # 项目构建与依赖配置
```

---

## 配置说明

所有配置支持 **Nacos 配置中心** 管理，也可通过 **环境变量** 覆盖。

### 基础环境变量

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `ENVIRONMENT` | 运行环境 | `dev` / `test` / `prod` |
| `NACOS_SERVER_ADDR` | Nacos 服务地址 | `127.0.0.1:8848` |
| `NACOS_NAMESPACE_ID` | Nacos 命名空间 ID | `atri-crawler` |
| `NACOS_USERNAME` | Nacos 用户名 | `nacos` |
| `NACOS_PASSWORD` | Nacos 密码 | `nacos` |
| `NACOS_CACHE_DIR` | 本地配置缓存目录 | `./nacos_cache` |

> 💡 推荐做法：将敏感信息（如密码）通过 Nacos 或 Secret Manager 管理，避免硬编码。

---

## 运行测试

```bash
# 全量测试（静默模式）
pytest -q

# 指定测试文件（详细输出）
pytest tests/test_adapter_login.py -v
```

确保提交前通过所有测试：
```bash
make test
```

---

## 支持的学校

- ✅ 湖南中医药大学（`HNUCM`）
- ➕ 更多学校适配中……

---

## 贡献指南

1. Fork 本仓库
2. 创建特性分支：  
   ```bash
   git checkout -b feat/add-new-school
   ```
3. 提交代码前确保：  
   - 代码风格统一（使用 `ruff` / `black`）  
   - 单元测试通过（`make test`）
4. 发起 Pull Request  
   - CI 流水线全绿即可合并

---

## 许可证

本项目采用 [MIT License](LICENSE) ——  
✅ 允许商用  
✅ 允许修改  
✅ 请保留原始版权声明

--- 

> 🌟 欢迎贡献！任何 issue、PR 或建议都备受感激。