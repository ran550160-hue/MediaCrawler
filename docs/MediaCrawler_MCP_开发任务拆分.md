# MediaCrawler MCP 开发任务拆分

## 1. 拆分依据

本文基于以下已确认信息拆分开发任务：

- 第一版只做小红书 `xhs`。
- MCP 服务形态使用 stdio。
- Hermes 服务器已验证 `mcp==1.26.0`，支持 `FastMCP` 和 `mcp.run("stdio")`。
- Hermes MCP 配置方式详见 [HERMES服务器MCP配置方式](HERMES服务器MCP配置方式.md)。
- 数据目录默认使用 `~/.mediacrawler-mcp/`。
- 本地开发可通过环境变量把数据目录切到项目内，例如 `D:\WorkSpace\MediaCrawler\data\mcp`。
- 第一阶段优先支持用户手动扫码完成本地登录。
- 二维码图片发飞书的闭环保留设计，放到后续 Hermes 集成验证阶段。

## 2. 当前无需继续确认的问题

以下事项已经可以进入开发，不再作为阻塞项：

- MCP SDK 版本和 FastMCP 写法。
- Hermes stdio MCP 配置格式。
- 第一版平台范围。
- 数据目录位置。
- MCP 与飞书 SDK 的边界。
- stdout 不能写普通日志的约束。

开发过程中仍需验证的问题：

- 现有 MediaCrawler 输出文件如何最小成本归档到 dataset raw 目录。
- 小红书登录态检查如何稳定判断。
- 小红书二维码 PNG 输出需要等后续登录模块阶段验证。

## 3. 版本范围

### v1 目标

打通小红书单平台的最小可用链路：

```text
Hermes -> stdio MCP -> create_dataset
                  -> start_collection
                  -> get_task_status
                  -> normalize_dataset
                  -> query_dataset
                  -> generate_report
```

### v1 不做

- 抖音。
- 多平台并行采集。
- HTTP MCP。
- Web UI。
- MCP 内置飞书 SDK。
- 复杂 LLM 总结。
- 多账号池。
- 高并发采集。

### v1.1 再做

- 小红书二维码 PNG 输出。
- Hermes 将二维码发到飞书。
- 登录成功后自动恢复采集任务。

## 4. 里程碑总览

| Sprint | 目标 | 主要交付 |
|---|---|---|
| Sprint 0 | 依赖和骨架准备 | FastMCP server、配置、日志、目录 |
| Sprint 1 | 数据集元数据 | SQLite、Dataset CRUD、MCP tools |
| Sprint 2 | 离线数据查询闭环 | JSONL normalize、DuckDB、query_dataset |
| Sprint 3 | 报告生成 | Markdown、HTML、summary JSON |
| Sprint 4 | 小红书采集任务 | subprocess runner、task 状态、raw 归档 |
| Sprint 5 | 登录态基础能力 | get_login_status、manual login 指引、cookie import |
| Sprint 6 | Hermes 集成验证 | Hermes 配置、工具发现、飞书结果链路 |
| Sprint 7 | 二维码登录增强 | start_login、wait_login、qr_image_path |

建议实际开发顺序：

1. Sprint 0
2. Sprint 1
3. Sprint 2
4. Sprint 3
5. Sprint 4
6. Sprint 5
7. Sprint 6
8. Sprint 7

这样即使二维码登录延后，Hermes 也能先使用已有数据集做查询和报告。

## 5. Sprint 0：依赖和骨架准备

目标：项目具备可启动的 stdio MCP server，不污染 stdout，日志写入文件。

### MCP-0001：确认并新增依赖

内容：

- 在项目依赖中加入 `mcp>=1.26.0`。
- 加入 `duckdb`。
- 确认 `aiosqlite` 已存在。
- 暂不加入飞书 SDK、Celery、RQ、pyarrow。

验收：

- 本地环境可导入：

```python
from mcp.server.fastmcp import FastMCP
import duckdb
```

### MCP-0002：创建 `mediacrawler_mcp` 包

内容：

新增：

```text
mediacrawler_mcp/
  __init__.py
  server.py
  config.py
  errors.py
  utils.py
```

验收：

- `python -m mediacrawler_mcp.server` 可启动。
- 启动时不向 stdout 输出普通日志。
- 日志写入 `~/.mediacrawler-mcp/logs/server.log`。

### MCP-0003：FastMCP server skeleton

内容：

- 使用 `FastMCP("mediacrawler")`。
- 注册临时 `ping` tool。
- `if __name__ == "__main__": mcp.run("stdio")`。

验收：

- Hermes 或本地 MCP client 能发现 `ping`。
- stdout 不出现非 MCP 协议日志。

### MCP-0004：配置加载

内容：

- 实现 `config.py`。
- 支持环境变量：

```text
MEDIACRAWLER_MCP_HOME
MEDIACRAWLER_MCP_BROWSER_MODE
MEDIACRAWLER_MCP_CDP_ENDPOINT
MEDIACRAWLER_MCP_MAX_CONCURRENT_TASKS
MEDIACRAWLER_MCP_DEFAULT_TIMEOUT_SECONDS
```

验收：

- 默认 home 为 `~/.mediacrawler-mcp`。
- 设置 `MEDIACRAWLER_MCP_HOME` 后所有目录切换到指定路径。

## 6. Sprint 1：数据集元数据

目标：Hermes 能创建、列出、读取数据集，但暂不触发采集。

### MCP-0101：SQLite 初始化

内容：

- 新增 `storage.py`。
- 初始化 `metadata.sqlite`。
- 开启 WAL。
- 创建表：
  - `datasets`
  - `tasks`
  - `login_sessions`
  - `reports`
  - `accounts`

验收：

- 首次启动自动创建数据库。
- 重复启动不会破坏已有表。
- 有单元测试覆盖 schema 初始化。

### MCP-0102：Dataset 模型

内容：

- 新增 `models.py`。
- 定义 Dataset、Task、Report、LoginSession 的基础模型。
- 第一版可用 dataclass 或 Pydantic。

验收：

- Dataset 可序列化为 JSON。
- Dataset 字段包含 `dataset_id`、`name`、`platforms`、`keywords`、`status`、`dataset_dir`。

### MCP-0103：数据集目录创建

内容：

- 新增 `dataset_service.py`。
- 创建目录：

```text
datasets/<dataset_id>/
  dataset.json
  raw/
  logs/
  reports/
```

验收：

- 调用 service 后目录完整存在。
- `dataset.json` 内容和 SQLite 记录一致。

### MCP-0104：MCP tool `create_dataset`

内容：

- 注册 `create_dataset`。
- 第一版只允许 `platforms=["xhs"]`。
- 如果传入 `dy`，返回 `UNSUPPORTED_PLATFORM`。

验收：

- Hermes 调用可返回 `dataset_id`、`dataset_dir`、`dataset_json_path`。
- 参数错误返回结构化错误，不抛出裸异常。

### MCP-0105：MCP tools `list_datasets`、`get_dataset`

内容：

- `list_datasets(keyword?, platform?, status?, limit?)`
- `get_dataset(dataset_id)`

验收：

- 能列出刚创建的数据集。
- 能读取 dataset.json 和 SQLite 元数据。
- 不存在的数据集返回 `DATASET_NOT_FOUND`。

## 7. Sprint 2：离线数据查询闭环

目标：不接爬虫，先用 fixture/raw JSONL 跑通 DuckDB 标准化和查询。

### MCP-0201：准备小红书 fixture 数据

内容：

- 在 tests fixture 中准备小型 xhs contents/comments JSONL。
- 覆盖字段：
  - `note_id`
  - `note_url`
  - `title`
  - `desc`
  - `liked_count`
  - `collected_count`
  - `comment_count`
  - `share_count`
  - `source_keyword`

验收：

- fixture 可用于 normalizer 测试。

### MCP-0202：实现 `normalizer.py`

内容：

- 读取 dataset raw 下的 JSONL。
- 小红书字段映射到标准字段。
- 数字字段安全转换。
- 保留 `raw_json`。
- 写入 `analysis.duckdb`。

验收：

- DuckDB 中生成 `contents` 和 `comments` 表。
- `source_keyword` 进入每条记录。
- 缺失字段、空值、字符串数字不导致崩溃。

### MCP-0203：MCP tool `normalize_dataset`

内容：

- 注册 `normalize_dataset(dataset_id, force=False)`。
- 若没有 raw 文件，返回 `NORMALIZE_FAILED` 和明确说明。

验收：

- 对 fixture dataset 调用成功。
- 返回 `duckdb_path`、`content_count`、`comment_count`。

### MCP-0204：实现 `query_engine.py`

内容：

- 支持 `target=contents|comments`。
- 支持 `query` 文本关键词。
- 支持 `platform`、`source_keyword`。
- 支持 `limit`。
- 支持 `sort_by=like_count|engagement_count|publish_time`。

验收：

- 能查询评论中的指定关键词。
- 能按点赞或互动排序。
- limit 生效。

### MCP-0205：MCP tool `query_dataset`

内容：

- 注册 `query_dataset`。
- 如果 DuckDB 不存在，提示先调用 `normalize_dataset`。

验收：

- Hermes 可基于已有 dataset 查询历史评论。
- 返回结果包含 `platform`、`source_keyword`、`content_id`、`text`、`like_count`、`url`。

## 8. Sprint 3：报告生成

目标：复用当前 `analysis` 包，生成 Hermes 可读的报告产物。

### MCP-0301：报告服务基础

内容：

- 新增 `report_service.py`。
- 从 DuckDB 读取数据。
- 输出 `reports/summary.json`。
- 输出 `reports/report.md`。
- 输出 `reports/report.html`。

验收：

- fixture dataset 可生成三类报告文件。

### MCP-0302：复用评论词频能力

内容：

- 复用或迁移 `analysis/reports.py` 中的 jieba/wordcloud 逻辑。
- 生成：
  - `comment_word_freq.json`
  - `comment_word_cloud.png`

验收：

- 有评论时生成词频。
- 无评论时不崩溃，报告说明无评论数据。

### MCP-0303：广告/引流规则标记

内容：

- 第一版使用简单关键词规则：
  - 私信
  - 进群
  - 课程
  - 训练营
  - 资料包
  - 领取
  - 加我
  - 变现

验收：

- summary 中包含疑似广告/引流内容数量。
- 报告中列出候选内容，不做绝对判断。

### MCP-0304：MCP tools `generate_report`、`get_report`

内容：

- 注册 `generate_report(dataset_id, report_type="topic_research", top_n=20)`。
- 注册 `get_report(dataset_id, report_id=None)`。

验收：

- 返回 `report_md_path`、`report_html_path`、`summary_json_path`。
- Hermes 可直接拿路径回复用户。

## 9. Sprint 4：小红书采集任务

目标：通过 subprocess 调用现有 MediaCrawler，完成小红书搜索数据采集并归档到 dataset raw。

### MCP-0401：Task Manager

内容：

- 新增 `task_manager.py`。
- 支持创建任务、启动任务、查询任务、取消任务。
- 状态：
  - `accepted`
  - `running`
  - `normalizing`
  - `ready`
  - `failed`
  - `cancelled`
  - `interrupted`

验收：

- 任务状态写入 SQLite。
- `get_task_status` 可查询。

### MCP-0402：Crawler Runner

内容：

- 新增 `crawler_runner.py`。
- 通过 subprocess 调用现有命令。
- 第一版只支持 `platform=xhs`、`type=search`、`save_data_option=jsonl`。
- stdout/stderr 写入 dataset logs，不写 MCP stdout。

验收：

- 能启动一次 xhs search。
- 日志落到 dataset logs。
- subprocess 失败时任务状态为 `failed`。

### MCP-0403：raw 文件归档

内容：

- 采集完成后找到新生成的 xhs contents/comments JSONL。
- 复制或移动到：

```text
<dataset>/raw/xhs_contents.jsonl
<dataset>/raw/xhs_comments.jsonl
```

验收：

- dataset raw 下存在标准命名文件。
- dataset.json 更新 raw 文件路径。

### MCP-0404：多关键词串行采集

内容：

- 对 dataset keywords 串行执行。
- 每个关键词保留 `source_keyword`。

验收：

- 两个关键词采集后，raw 或 normalized 数据能区分 `source_keyword`。
- 如果现有 MediaCrawler 输出没有稳定 `source_keyword`，在归档/normalize 阶段补齐。

### MCP-0405：MCP tools `start_collection`、`get_task_status`、`cancel_task`

内容：

- `start_collection` 返回 task_id 后立即结束，不同步等待爬虫跑完。
- `get_task_status` 返回状态、进度、日志路径。
- `cancel_task` 终止 subprocess。

验收：

- Hermes 可以提交任务并轮询状态。
- 长任务不会阻塞 MCP 连接。

## 10. Sprint 5：登录态基础能力

目标：先支持“用户手动扫码登录后复用登录态”，二维码发飞书暂缓。

### MCP-0501：登录状态检查

内容：

- 新增 `login_manager.py`。
- 实现 `get_login_status(platform="xhs")`。
- 第一版可采用轻量探测或 profile/cookie 存在性判断。

验收：

- 未登录时返回 `logged_out` 或 `unknown`。
- 已登录时返回 `logged_in`。
- 检查失败不影响 MCP server。

### MCP-0502：手动登录指引

内容：

- 当 `start_collection` 检测未登录，返回 `need_login`。
- message 明确提示用户先本地执行手动扫码登录流程。

验收：

- 未登录时不进入长时间卡住状态。
- 返回可读恢复建议。

### MCP-0503：cookie import

内容：

- 实现 `import_cookies(platform="xhs", cookie_string)`。
- 第一版可先保存完整 cookie string 到受控文件或 SQLite。
- 后续再接入浏览器 context 注入。

验收：

- cookie 不打印到日志。
- config/storage 文件权限尽量限制为当前用户。

## 11. Sprint 6：Hermes 集成验证

目标：把 MCP server 接入 Hermes，确认工具可被发现和调用。

### MCP-0601：Hermes 配置样例

内容：

- 新增部署说明或在 README/文档中记录：

```yaml
mcp_servers:
  mediacrawler:
    command: /path/to/mediacrawler/.venv/bin/python
    args:
      - /path/to/MediaCrawler/mediacrawler_mcp/server.py
    enabled: true
    timeout: 300
    connect_timeout: 60
```

验收：

- Hermes 新会话可发现工具。

### MCP-0602：Hermes smoke test

内容：

- 在 Hermes 中测试：
  - `create_dataset`
  - `list_datasets`
  - `get_dataset`
  - `query_dataset`
  - `generate_report`

验收：

- 工具名形态类似：

```text
mcp_mediacrawler_create_dataset
mcp_mediacrawler_query_dataset
```

### MCP-0603：飞书结果路径验证

内容：

- 验证 Hermes 能在飞书回复中引用报告路径。
- 验证本地图片路径在飞书侧的媒体上传能力，为二维码阶段做准备。

验收：

- 飞书用户能收到报告摘要和路径。
- 如果图片路径展示有限制，记录约束。

## 12. Sprint 7：二维码登录增强

目标：实现服务器二维码 PNG 输出，并通过 Hermes 发到飞书。

### MCP-0701：二维码输出抽象

内容：

- 抽象 `QrCodeSink`。
- 第一版实现 `file png`。

验收：

- 能将二维码保存到：

```text
~/.mediacrawler-mcp/login_qrcodes/
```

### MCP-0702：MCP tool `start_login`

内容：

- 启动小红书登录流程。
- 保存二维码 PNG。
- 创建 `login_session`。
- 返回 `qr_image_path` 和 `expires_at`。

验收：

- Hermes 能拿到本地二维码图片路径。

### MCP-0703：MCP tool `wait_login`

内容：

- 轮询登录会话状态。
- 成功后更新 account/profile 状态。
- 过期后返回 `LOGIN_QR_EXPIRED`。

验收：

- 用户扫码成功后返回 `success`。
- 超时返回 `expired`。

### MCP-0704：飞书二维码闭环测试

内容：

- Hermes 调用 `start_login`。
- Hermes 将 `qr_image_path` 发到飞书。
- 用户扫码。
- Hermes 调用 `wait_login`。
- 登录成功后重新调用 `start_collection`。

验收：

- 飞书用户无需接触服务器，即可完成小红书登录。

## 13. 横向任务

### MCP-X001：统一错误结构

所有 tool 返回：

```json
{
  "status": "success|accepted|running|need_login|failed",
  "message": "...",
  "error": null
}
```

失败时：

```json
{
  "status": "failed",
  "message": "...",
  "error": {
    "code": "LOGIN_REQUIRED",
    "detail": "..."
  }
}
```

### MCP-X002：日志规范

要求：

- stdio MCP server 不向 stdout 写普通日志。
- 普通日志写文件。
- subprocess stdout/stderr 写 dataset logs。
- cookie、二维码 token、用户隐私信息不得写日志。

### MCP-X003：测试规范

每个 Sprint 至少包含：

- service 单元测试。
- MCP tool 输入输出测试。
- fixture 数据集测试。

真实平台采集只做手工验证，不纳入 CI。

### MCP-X004：文档同步

每完成一个 Sprint，同步更新：

- 工程设计文档。
- 开发任务拆分文档。
- Hermes 配置说明。

## 14. 第一批建议开工任务

建议第一批只做这 8 个任务：

1. MCP-0001：确认并新增依赖。
2. MCP-0002：创建 `mediacrawler_mcp` 包。
3. MCP-0003：FastMCP server skeleton。
4. MCP-0004：配置加载。
5. MCP-0101：SQLite 初始化。
6. MCP-0102：Dataset 模型。
7. MCP-0103：数据集目录创建。
8. MCP-0104：MCP tool `create_dataset`。

这一批完成后，Hermes 就可以先发现 MediaCrawler MCP，并创建第一个数据集。

## 15. v1 验收清单

v1 完成标准：

- Hermes 能发现 MediaCrawler MCP tools。
- `create_dataset` 可创建 xhs 数据集。
- `list_datasets` 和 `get_dataset` 可用。
- `start_collection` 可启动小红书采集任务。
- `get_task_status` 可轮询任务状态。
- 采集日志不污染 MCP stdout。
- raw JSONL 被归档到 dataset raw。
- `normalize_dataset` 可写入 DuckDB。
- `query_dataset` 可查询历史评论。
- `generate_report` 可输出 Markdown、HTML、summary JSON。
- 未登录时返回 `need_login`，不长时间卡住。

## 16. 后续 Backlog

- 接入抖音。
- 多平台对比报告。
- 多关键词质量评分。
- HTTP MCP 适配层。
- Web UI。
- 多账号隔离。
- 增量采集。
- Embedding 检索。
- Hermes 自动选择最近相关 dataset。
