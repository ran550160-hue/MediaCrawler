# 小红书主题研究最小闭环实施记录

## 阶段 1：当前实现审计

### 代码事实

| 能力 | 位置 | 已有事实 | 缺口 |
|---|---|---|---|
| 本地搜索与任务状态 | `api/routers/agent.py` 的 `_start_agent_task`、`finalize_agent_task` | 任务保存关键词、请求参数、起止时间和输出目录；有取消后可 finalize 的降级路径 | 导出的 dataset manifest 未写入 task id、搜索排序或实际采集范围 |
| MCP 原子入口 | `mediacrawler_mcp/server.py` | `create_dataset`、`import_raw_files`、`normalize_dataset`、`query_dataset`、`generate_report` 均可单独调用 | 没有面向已归一化数据集的主题研究入口 |
| 数据集与 raw 文件 | `dataset_service.py`、`dataset_importer.py`、`dataset_bundle_exporter.py` | `dataset.json` 保存 dataset id、平台、关键词、options；raw JSONL 与报告目录固定 | collection task id 仅存在任务存储，未进入 bundle；raw 行的发现/请求数量没有统一记录 |
| 归一化 | `normalizer.py` 的 `DatasetNormalizer` | 内容与评论写入 DuckDB；评论有 `content_id`、`parent_comment_id`；有精确 id 去重和保留 `raw_json` | 内容互动数只查顶层字段，缺少嵌套 `interact_info` 路径；归一化表缺少 collection task id / 内容类型；无质量统计 |
| 报告 | `report_service.py` 的 `ReportService.generate_report` | 输出 JSON、Markdown、HTML；包括词频、榜单、互动警告 | `report_type=topic_research` 只是名称，仍是通用词频榜单，没有主题、观点、证据链或限制说明 |
| 测试 | `tests/test_mediacrawler_mcp_normalize_query.py`、`tests/test_mediacrawler_mcp_report.py`、`tests/test_api_workbench.py` | 覆盖 JSONL→归一化→查询、报告文件输出，以及部分本地任务 finalize | 现有样本为手写 fixture；没有“原始响应→归一化→质量→主题研究→证据”全链路验证 |

### 关系、血缘与互动字段

- 内容以 `content_id` 标识；评论以 `content_id` 关联内容，回复以 `parent_comment_id` 关联父评论。`_flatten_comment_items` 会展开 `sub_comments`、`sub_comment_list`、`comments`、`replies`。
- 已保存的血缘：dataset id（表与 manifest）、平台、原始关键词（manifest 与 `source_keyword`）、raw JSON、内容链接、采集时间（raw 的 `crawl_time` 或归一化时刻）。
- 尚未可靠保存：collection task id、搜索排序方式、发现/请求/解析各阶段计数；因此这些字段必须明确降级而非虚构。
- 内容互动：like、collect、comment、share、汇总 engagement；评论互动：like。回复计数还未作为独立字段归一化。

### 设计取舍

保留采集、数据集、归一化、查询和现有通用报告。延后万能 `research_xhs(question)`、自动热点发现、调度/历史趋势判断与 LLM 叙事。新增一个深模块：其小 interface 只接收已归一化的 `dataset_id`，内部完成质量统计、确定性主题组织、观点分类与证据选择；其实现细节不泄漏给 MCP 调用方。

## 最小实现方案

1. P0：归一化时补齐嵌套互动字段、内容类型和 collection task id；从 manifest 读取数据集级血缘；输出可计算的数据质量统计及明确的 unavailable 指标。
2. P1：新增独立的主题分析原语，使用确定性文本标准化、精确/近似重复检测、评论父帖上下文和规则型观点分类，避免 LLM 参与统计。
3. P2：新增 `generate_topic_research_report(dataset_id, top_n=...)` MCP 工具与 JSON/Markdown/HTML 报告。报告仅表述当前样本的高频/高互动主题，明确没有历史基线时不能判断趋势。
4. 验证：使用固定的脱敏 XHS 风格 JSONL fixture，断言嵌套互动字段、父子评论、质量指标、证据回溯、顺序稳定性与“无趋势结论”。

### 预计修改

- `mediacrawler_mcp/normalizer.py`：schema、嵌套字段、manifest 血缘、归一化统计。
- `mediacrawler_mcp/topic_research.py`：质量、主题、观点、证据原语与模板服务。
- `mediacrawler_mcp/server.py`：新增显式研究模板入口。
- `api/routers/agent.py`、`mediacrawler_mcp/dataset_bundle_exporter.py`：把本地任务血缘写入 bundle。
- `tests/test_mediacrawler_mcp_topic_research.py` 与现有归一化/工作台测试：单元和端到端固定样本。

### 风险与兼容性

新增列只在重建 `analysis.duckdb` 时出现；`normalize_dataset(..., force=True)` 是明确迁移路径。旧 bundle 没有新血缘字段时报告会返回 `unavailable` 与限制说明，不猜测值。现有采集、数据集和通用报告接口保持可用。

## 阶段 2：P0 数据可靠性

### 本阶段目标

让归一化数据能保留任务血缘、展开回复关系，并从 XHS 常见的嵌套互动对象提取计数；对尚未采集的流水线指标明确标记为不可获得。

### 实际修改文件

- `mediacrawler_mcp/normalizer.py`
- `mediacrawler_mcp/dataset_bundle_exporter.py`
- `api/routers/agent.py`
- `tests/test_mediacrawler_mcp_topic_research.py`
- `tests/test_mediacrawler_mcp_dataset_bundle_exporter.py`

### 关键设计决策

- `collection_task_id`、任务起止时间写入 bundle 的 `dataset.json.options`；归一化时写入内容和评论表。旧 bundle 没有这些值时保持空值，研究报告显示 `unavailable`。
- 互动字段以 `interact_info`、`note.interact_info`、`note_card.interact_info` 及历史顶层字段的明确顺序提取，第一项非零值优先，避免顶层 `0` 覆盖嵌套真实值。
- 子评论保留 `parent_comment_id`，研究报告仅把能 JOIN 到父帖的评论纳入主题，无法关联者计入质量问题。

### 测试结果

固定脱敏 XHS 风格 JSONL 覆盖嵌套互动、子评论、丢失链接和孤儿评论。相关测试通过（41 passed，1 个第三方 deprecation warning）。

### 尚未解决的问题

- 原始爬虫尚未记录 discovered/requested/parsed 三段计数，也未将搜索排序作为显式入参；报告因此显示这些值不可获得。
- 互动路径来自已知 XHS 响应形态；新的平台响应形态仍需通过真实脱敏样本补充。

## 阶段 3：P1/P2 主题分析与固定模板

### 本阶段目标

在不发起采集、不使用 LLM 的前提下，为一个已归一化数据集生成可重算、可回溯的主题研究报告。

### 实际修改文件

- `mediacrawler_mcp/topic_research.py`
- `mediacrawler_mcp/server.py`
- `tests/test_mediacrawler_mcp_topic_research.py`

### 关键设计决策

- `TopicResearchService.generate_topic_research_report(dataset_id, top_n)` 是固定研究模板的唯一外部 interface；其内部的文本规范化、精确/近似重复计数、观点分类、主题指标和证据选择均可独立测试。
- 主题按帖子标签优先、原始搜索关键词降级归组，并用规范化键合并 `AI 编程` 与 `AI编程`。统计全部由 DuckDB 行重算。
- 每条评论证据附父帖 ID、父评论 ID（回复）、父帖摘要和父帖链接；报告不输出任何没有历史基线支撑的趋势结论。
- `evidence_strength` 仅输出 `high`、`medium`、`low`，并返回代码中同一条明确规则；不产生伪精确百分比。

### 测试结果

端到端固定样本验证：raw JSONL → 归一化 DuckDB → 质量指标 → 主题/观点 → JSON、Markdown、HTML 报告；同时验证记录顺序不影响统计、证据可回溯、缺失字段进入质量报告以及无趋势声明。

### 下一阶段计划

可选地在真实采集链路记录 discovered/requested/parsed 和搜索排序，并通过多期 dataset snapshot 后再实现趋势比较；LLM 如接入，只能消费本报告的统计和 evidence，不得写回或改写统计结果。

## 阶段 4：准确性与完成度修正

### 已完成

- 成果准确命名为“**可追溯的规则型主题分组与证据报告 MVP**”。分组是首个标签优先、搜索关键词降级的 deterministic topic grouping，不是语义主题聚类。
- 代表帖子与代表评论分别选择后才组成 evidence；帖子少于三个时，评论不会进入代表帖子列表。
- 内容互动字段状态写入归一化表：`present`、`present_zero`、`missing`、`parse_error`。真实零互动不再被当作字段缺失。
- 异常样本按唯一内容/评论记录计数，同时提供按问题类型的计数；同一内容的多个问题不会重复扩大 `anomaly_sample_count`。
- 评论观点改为多维规则结果：sentiment、是否提问、是否有行动意图和命中标记可同时存在；正负面示例独立选择，不宣称是同一属性的严格反例。
- 文本重复统计移入数据质量：明确区分归一化 ID 去重、文本精确重复诊断和 `SequenceMatcher` 近似重复诊断。近似重复只诊断，不删除。
- Markdown/HTML 展示数据范围、任务/时间、质量、不可获得指标、分组限制、证据和趋势限制。HTML 仅链接 `http`/`https` URL，其他协议按纯文本处理且所有动态文本转义。
- `finalize_local_xhs_search` 新增兼容的 `report_type`：`topic_research`（默认）、`generic`、`none`；原有通用报告保持可选。

### 真实结构 fixture

- `tests/fixtures/xhs/real_capture_sanitized_*.jsonl` 来自本地 XHS 搜索的真实响应结构，经可审计脚本脱敏。
- 当前本地采集文件只有一级评论，没有可安全提交的真实回复 payload；因此 fixture README 明确记录此限制，回复关系仍由单独的边界样本覆盖，不把合成回复描述为真实采集结果。

### 明确限制 / 待实现

- 尚未实现语义主题聚类、属性级观点对齐、历史快照趋势检测、LLM 洞察或通用用户研究引擎。
- discovered/requested/successful_parse 和搜索排序尚未由原始采集链路记录；报告会将其列为 unavailable。
- 近似重复检查最多比较 300 个唯一文本，结果可能截断，仅适合作为质量提示。

### 验收结果

相关单元、规则、真实结构 fixture 集成、finalize→注册→归一化→主题报告，以及通用报告兼容测试均通过：`55 passed, 1 warning`。执行命令：

```powershell
.\.tmp\uv-agent-venv\Scripts\python.exe -m pytest tests/test_mediacrawler_mcp_topic_research.py tests/test_mediacrawler_mcp_desktop_agent.py tests/test_mediacrawler_mcp_normalize_query.py tests/test_mediacrawler_mcp_report.py tests/test_mediacrawler_mcp_dataset_bundle_exporter.py tests/test_api_workbench.py -q
```

## 阶段 5：提交前统计、fixture 与接口修正

### 已完成

- XHS 数字解析支持 `万 / w / W / 千 / k / K`、小数、逗号和尾随 `+`。尾随 `+` 解析为可确定下限，并将字段标为 approximate。
- 归一化保留 `interaction_approximate_fields` 与 `interaction_parse_error_fields`；质量状态区分 `present`、`present_zero`、`missing`、`parse_error`、`partial_parse_error`、`present_approximate`。
- 真实结构 fixture 已重新递归脱敏：移除位置、token、cookie、设备标识和媒体/CDN URL；标题、正文、评论和标签均替换为合成文本；脚本先验证原始帖子/评论的真实 `note_id` 关联后才伪名化。
- 一级评论的 `parent_comment_id` 在 JSON、Markdown 和 HTML 中统一为 `null` / 不展示；采集和数据时间统一显示 ISO 8601。
- 报告同时输出 declared 与 observed source keywords。空 ID 异常使用行号与 raw JSON 哈希构造稳定记录键，避免不同空 ID 行被合并。
- `generate_report` 的分发规则固定为 `topic_research → TopicResearchService`、`generic → ReportService`、`none → 不生成`；`ReportService.generate_report` 默认回归 generic，避免同名 report type 表达不同语义。

### 验收结果

完整指定测试集通过：`67 passed, 1 warning`。基于重新脱敏 fixture 的样例报告已验证帖子互动 `116331`、主题总互动 `120957`、ISO 采集时间和实际 observed keyword。
