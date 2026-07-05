# MediaCrawler MCP 开发任务拆分

## 1. 拆分依据

本文基于以下已确认信息拆分开发任务：

- 第一版只做小红书 `xhs`。
- MCP 服务形态使用 stdio。
- Hermes 服务器已验证 `mcp==1.26.0`，支持 `FastMCP` 和 `mcp.run("stdio")`。
- Hermes MCP 配置方式详见 [HERMES服务器MCP配置方式](HERMES服务器MCP配置方式.md)。
- 数据目录默认使用 `~/.mediacrawler-mcp/`。
- 本地开发可通过环境变量把数据目录切到项目内，例如 `D:\WorkSpace\MediaCrawler\data\mcp`。
- 2026-07 调整后，第一阶段优先支持桌面端真实浏览器采集后的 dataset bundle 导入。
- 默认 MCP Server 只启用 `dataset` profile。
- 服务器二维码、login、cookie、server collection 工具必须通过 `experimental_collection` profile 显式启用，不作为 v1 主验收。

## 2. 当前无需继续确认的问题

以下事项已经可以进入开发，不再作为阻塞项：

- MCP SDK 版本和 FastMCP 写法。
- Hermes stdio MCP 配置格式。
- 第一版平台范围。
- 数据目录位置。
- MCP 与飞书 SDK 的边界。
- stdout 不能写普通日志的约束。
- 小红书首次登录、滑块和风险验证放在桌面真实浏览器中由用户接管。

开发过程中仍需验证的问题：

- 现有 MediaCrawler 输出文件如何最小成本归档到 dataset raw 目录。
- 桌面端导出的 dataset bundle 如何最小成本同步到 Hermes 服务器。
- 小红书登录态检查和二维码 PNG 输出仅作为服务器采集实验路径验证。

## 3. 版本范围

### v1 目标

打通小红书单平台的最小可用链路：

```text
Desktop Collector -> dataset bundle
Hermes -> stdio MCP -> register_dataset
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
- 小红书服务器首次扫码登录作为生产主链路。
- Hermes 自动反复触发服务器实时采集。

### v1.1 再做

- 桌面采集导出脚本。
- dataset bundle 同步脚本。
- 更完整的 bundle 校验和修复。

### v1.2 实验能力

- 小红书服务器二维码 PNG 输出。
- Hermes 将二维码发到飞书。
- 服务器采集任务的可恢复错误提示。
- 以上能力仅通过 `experimental_collection` profile 显式启用。

## 4. 里程碑总览

| Sprint | 目标 | 主要交付 |
|---|---|---|
| Sprint 0 | 依赖和骨架准备 | FastMCP server、配置、日志、目录 |
| Sprint 1 | 数据集元数据 | SQLite、Dataset CRUD、MCP tools |
| Sprint 2 | 桌面数据集导入 | register_dataset、validate_dataset_bundle、import_raw_files、sync_dataset_manifest |
| Sprint 3 | 离线数据查询闭环 | JSONL normalize、DuckDB、query_dataset |
| Sprint 4 | 报告生成 | Markdown、HTML、summary JSON |
| Sprint 5 | 桌面采集导出闭环 | 本机真实浏览器采集、dataset bundle 规范、同步说明 |
| Sprint 6 | Hermes 集成验证 | Hermes 配置、工具发现、飞书结果链路 |
| Sprint 7 | 服务器采集实验能力 | start_collection、cookie import、QR worker 可恢复错误 |

建议实际开发顺序：

1. Sprint 0
2. Sprint 1
3. Sprint 2
4. Sprint 3
5. Sprint 4
6. Sprint 5
7. Sprint 6
8. Sprint 7

这样即使服务器二维码登录不可用，Hermes 也能先使用桌面端采集出的数据集做查询和报告。

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
MEDIACRAWLER_MCP_TOOL_PROFILE
MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION
```

验收：

- 默认 home 为 `~/.mediacrawler-mcp`。
- 设置 `MEDIACRAWLER_MCP_HOME` 后所有目录切换到指定路径。
- 默认 `MEDIACRAWLER_MCP_TOOL_PROFILE=dataset`。
- 默认 `MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION=false`。

### MCP-0005：Tool profile 注册门控

内容：

- `server.py` 按 profile 注册 MCP tools。
- 默认只注册 dataset tools。
- `experimental_collection` tools 只有显式配置后才注册。

验收：

- 默认 Hermes 工具列表不包含 `start_collection`、`start_qrcode_login`、`import_cookies` 等工具。
- 显式启用 `MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION=true` 后才出现实验工具。

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

## 7. Sprint 2：桌面数据集导入

目标：不接服务器爬虫，先让 Hermes 能登记桌面端导出的 dataset bundle。

### MCP-0201：定义 dataset bundle 规范

内容：

- 固化目录结构：

```text
<dataset_id>/
  dataset.json
  raw/
    xhs_contents.jsonl
    xhs_comments.jsonl
  media/
  logs/
```

- 明确 `dataset.json` 最小字段。
- 约定 raw 文件命名和平台标识。

验收：

- 文档和 fixture 中存在一个可登记的 bundle 示例。

### MCP-0202：实现 `dataset_importer.py`

内容：

- 新增 `dataset_importer.py`。
- 支持校验 dataset bundle。
- 支持 `copy` 和 `link` 导入模式。
- 缺少非关键字段时返回 warning。

验收：

- 可将 `data/inbox/<dataset_id>` 登记到 MCP home。
- 不合规目录返回结构化错误。

### MCP-0203：MCP tool `register_dataset`

内容：

- 注册 `register_dataset(dataset_dir, import_mode="copy")`。
- 写入 SQLite 元数据。
- 同步或生成 `dataset.json`。

验收：

- Hermes 可登记一个桌面端导出的数据集。
- 返回 `dataset_id`、`dataset_dir`、`dataset_json_path`。

### MCP-0204：MCP tool `validate_dataset_bundle`

内容：

- 注册 `validate_dataset_bundle(dataset_dir)`。
- 检查 manifest、raw 文件和平台字段。

验收：

- 合法 bundle 返回 `valid=true`。
- 缺少评论文件时返回 warning，而不是直接失败。

### MCP-0205：MCP tool `import_raw_files`

内容：

- 注册 `import_raw_files(dataset_id, platform, contents_path?, comments_path?, source_keyword?)`。
- 将散落 raw JSONL 复制或链接到 dataset raw 目录。

验收：

- 可向已有 dataset 导入 `xhs_contents.jsonl` 和 `xhs_comments.jsonl`。
- dataset.json 更新 raw 文件路径。

### MCP-0206：MCP tool `sync_dataset_manifest`

内容：

- 注册 `sync_dataset_manifest(dataset_id)`。
- 将 `dataset.json`、SQLite 元数据和 raw 文件路径重新同步。
- 对缺少非关键字段的 manifest 做可恢复修复。

验收：

- 可修复桌面端导出的最小 manifest。
- 返回 `updated` 和 `warnings`。

## 8. Sprint 3：离线数据查询闭环

目标：用 fixture/raw JSONL 跑通 DuckDB 标准化和查询。

### MCP-0301：准备小红书 fixture 数据

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

### MCP-0302：实现 `normalizer.py`

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

### MCP-0303：MCP tool `normalize_dataset`

内容：

- 注册 `normalize_dataset(dataset_id, force=False)`。
- 若没有 raw 文件，返回 `NORMALIZE_FAILED` 和明确说明。

验收：

- 对 fixture dataset 调用成功。
- 返回 `duckdb_path`、`content_count`、`comment_count`。

### MCP-0304：实现 `query_engine.py`

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

### MCP-0305：MCP tool `query_dataset`

内容：

- 注册 `query_dataset`。
- 如果 DuckDB 不存在，提示先调用 `normalize_dataset`。

验收：

- Hermes 可基于已有 dataset 查询历史评论。
- 返回结果包含 `platform`、`source_keyword`、`content_id`、`text`、`like_count`、`url`。

## 9. Sprint 4：报告生成

目标：复用当前 `analysis` 包，生成 Hermes 可读的报告产物。

### MCP-0401：报告服务基础

内容：

- 新增或调整 `report_service.py`。
- 从 DuckDB 读取数据。
- 输出 `reports/summary.json`。
- 输出 `reports/report.md`。
- 输出 `reports/report.html`。

验收：

- fixture dataset 可生成三类报告文件。

### MCP-0402：复用评论词频能力

内容：

- 复用或迁移 `analysis/reports.py` 中的 jieba/wordcloud 逻辑。
- 生成：
  - `comment_word_freq.json`
  - `comment_word_cloud.png`

验收：

- 有评论时生成词频。
- 无评论时不崩溃，报告说明无评论数据。

### MCP-0403：广告/引流规则标记

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

### MCP-0404：MCP tools `generate_report`、`get_report`

内容：

- 注册 `generate_report(dataset_id, report_type="topic_research", top_n=20)`。
- 注册 `get_report(dataset_id, report_id=None)`。

验收：

- 返回 `report_md_path`、`report_html_path`、`summary_json_path`。
- Hermes 可直接拿路径回复用户。

## 10. Sprint 5：桌面采集导出闭环

目标：让用户能在桌面真实浏览器中完成采集，并把结果稳定交给服务器 MCP。

### MCP-0501：桌面采集运行说明

内容：

- 记录推荐桌面采集方式：
  - 现有 MediaCrawler CLI。
  - 本机 Chrome CDP。
  - headful Playwright。
- 明确登录、扫码、滑块和风险验证由桌面用户接管。

验收：

- 文档中有从桌面采集到 raw JSONL 的最短命令或流程。

### MCP-0502：dataset bundle 导出脚本

内容：

- 新增或规划脚本，将现有 `data/` 输出整理为标准 bundle。
- 生成 `dataset.json`。
- 复制 raw JSONL 到 `raw/`。

验收：

- 脚本可从一次 xhs 采集结果生成 bundle。
- bundle 可通过 `validate_dataset_bundle` 校验。

### MCP-0503：同步到服务器说明

内容：

- 记录手工复制、rsync、SFTP 或共享目录方式。
- 约定服务器 inbox 目录。
- 说明同步后调用 `register_dataset`。

验收：

- 用户能把桌面 bundle 放到 Hermes 服务器，并完成 MCP 登记。

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

## 12. Sprint 7：服务器采集实验能力

目标：通过 `experimental_collection` profile 保留服务器实时采集、cookie import 和二维码登录作为实验/兜底路径，但默认不注册、不作为 v1 主验收。

### MCP-0701：登录状态检查

内容：

- 保留或实现 `get_login_status(platform="xhs")`。
- 支持本地 cookie/profile 轻量检查。
- 可选远程校验时返回明确错误。

验收：

- 检查失败不影响 MCP server。
- 未登录时返回可恢复错误。
- 默认 profile 下该 tool 不存在。

### MCP-0702：cookie import

内容：

- 实现或保留 `import_cookies(platform="xhs", cookie_string)`。
- 保存完整 cookie string。
- 不在日志中打印 cookie。

验收：

- cookie 可用于后续服务器采集实验。
- 文件权限尽量限制为当前用户。
- 默认 profile 下该 tool 不存在。

### MCP-0703：服务器采集 task

内容：

- 保留 `start_collection`、`get_task_status`、`cancel_task`。
- 通过 subprocess 调用现有 MediaCrawler。
- 未登录、风控、验证码时快速失败并返回恢复建议。

验收：

- 长任务不会阻塞 MCP 连接。
- 失败不会影响已登记数据集查询和报告。
- 默认 profile 下这些 tools 不存在。

### MCP-0704：二维码登录实验

内容：

- 保留 `start_qrcode_login`、`get_qrcode_login_status`、`cancel_qrcode_login`。
- 输出 `qr_image_path`。
- 明确标注为实验能力。

验收：

- Hermes 能拿到本地二维码图片路径。
- 扫码后若触发风控，返回明确终态和建议。
- 失败不作为 MCP 集成失败。
- 默认 profile 下这些 tools 不存在。

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
8. MCP-0005：Tool profile 注册门控。

这一批完成后，Hermes 就可以先发现 MediaCrawler MCP，并开始围绕标准 dataset bundle 做导入和分析。

## 15. v1 验收清单

v1 完成标准：

- Hermes 能发现 MediaCrawler MCP tools。
- `create_dataset` 可创建 xhs 数据集。
- `list_datasets` 和 `get_dataset` 可用。
- `register_dataset` 可登记桌面端导出的 dataset bundle。
- `validate_dataset_bundle` 可返回结构化校验结果。
- `import_raw_files` 可向已有 dataset 导入 raw JSONL。
- `sync_dataset_manifest` 可同步或修复 manifest。
- raw JSONL 被归档到 dataset raw。
- `normalize_dataset` 可写入 DuckDB。
- `query_dataset` 可查询历史评论。
- `generate_report` 可输出 Markdown、HTML、summary JSON。
- 没有合适数据集时，Hermes 提示用户用桌面端采集。
- 默认工具列表不包含 QR、login、cookie、server collection 相关工具。

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
