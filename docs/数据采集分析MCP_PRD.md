# 数据采集分析 MCP 产品需求文档

## 1. 产品概述

### 1.1 产品名称

暂定名：MediaCrawler Research MCP

### 1.2 产品定位

MediaCrawler Research MCP 是一个面向 AI Agent 的垂直社媒数据采集与分析能力层。它基于 MediaCrawler 现有的小红书、抖音等平台采集能力，将一次性的爬取脚本升级为可被 Agent 调用的数据集生成、分析、检索和报告工具。

它不替代 Agent Reach。Agent Reach 更适合即时联网检索、网页阅读、社媒单条内容读取和工具路由；本产品更适合围绕一个研究主题持续沉淀数据集，并提供可复用的离线分析能力。

### 1.3 背景

当前项目已经具备多平台数据采集能力，并新增了第一版离线分析模块，可读取 `jsonl/json/csv` 内容与评论数据，生成统计摘要、Top 内容、Top 评论、词频和词云。

技术选型和架构决策详见 [技术选型与架构决策](技术选型与架构决策.md)。

工程落地设计详见 [MediaCrawler MCP 工程设计文档](MediaCrawler_MCP_工程设计文档.md)。

开发任务拆分详见 [MediaCrawler MCP 开发任务拆分](MediaCrawler_MCP_开发任务拆分.md)。

但现阶段仍存在几个问题：

- 数据采集、数据集管理、分析查询之间缺少统一产品抽象。
- 分析模块主要面向人工 CLI 使用，还不能自然接入 Hermes Agent。
- 服务端采集缺少登录态管理闭环，未登录时无法把二维码通过飞书等渠道交给用户扫码。
- 搜索结果质量依赖关键词策略，缺少任务级配置、去重、筛选和质量评估。
- 历史数据没有稳定的数据集 ID、元信息、版本和可追踪产物。
- Agent 只能“临时查几条”，还不能围绕同一主题反复追问、复用历史数据。

### 1.4 产品目标

将 MediaCrawler 改造成 Hermes Agent 可调用的研究型数据工具，使 Agent 能够：

- 按主题创建社媒搜索数据集。
- 对数据集进行可解释的统计分析。
- 对历史数据集进行结构化查询。
- 获取报告、榜单、词频、评论洞察等分析产物。
- 在不重复爬取的情况下复用已有数据。

### 1.5 非目标

v1 不做以下事情：

- 不做发帖、评论、点赞、关注等写操作。
- 不绕过平台风控、验证码或访问限制。
- 不承诺大规模、商业化、持续高频采集。
- 不做复杂情感分析、主题聚类、LLM 自动结论生成。
- 不替代 Agent Reach 的通用联网检索能力。
- 不直接提供面向终端用户的 SaaS 管理后台。

## 2. 用户与使用场景

### 2.1 目标用户

- Hermes Agent：主要调用方，需要通过 MCP 工具完成调研任务。
- 个人研究者：通过 CLI 或 MCP 创建数据集并查看报告。
- 开发者：基于数据集结果继续做二次分析、模型实验或业务插件。

### 2.2 核心场景

#### 场景 A：主题调研

用户说：“帮我调研 AI 编程副业在小红书和抖音上的真实反馈。”

Agent 应能：

1. 创建小红书和抖音搜索数据集。
2. 抓取内容和评论。
3. 分析高互动内容、评论高频词、用户关切。
4. 返回报告路径和关键摘要。
5. 后续用户追问时复用该数据集。

#### 场景 B：竞品/口碑分析

用户给出品牌、产品或关键词，Agent 创建数据集，分析：

- 热门内容是谁发的。
- 评论区在夸什么、骂什么、问什么。
- 哪些内容像广告或课程引流。
- 哪些真实用户反馈值得进一步查看。

#### 场景 C：多关键词对比

用户希望比较多个关键词，如：

- `程序员接单`
- `Python接单`
- `AI编程接单`
- `vibe coding接单`

系统应保留 `source_keyword`，并在报告中支持按关键词拆分统计。

#### 场景 D：历史数据检索

用户问：“上次抓到的数据里，大家最担心接单的什么风险？”

Agent 应能在已有数据集里查询评论和内容，而不是重新爬取。

## 3. 产品范围

### 3.1 v1 范围

v1 聚焦“本地可用、Agent 可调用、分析可信”：

- 支持平台：小红书 `xhs`、抖音 `dy`。
- 支持采集类型：关键词搜索。
- 支持数据类型：内容 `contents`、评论 `comments`。
- 支持输入来源：现有 MediaCrawler 落盘文件。
- 支持分析输出：HTML 报告、JSON 摘要、CSV 明细、词频 JSON、词云 PNG。
- 支持 MCP wrapper：通过 MCP 暴露数据集创建、分析、查询、报告获取工具。

### 3.2 v1.5 范围

- 增加数据集元信息文件。
- 支持多关键词批量任务。
- 支持简单质量过滤。
- 支持基于 SQLite 的本地索引。
- 支持更好的评论文本清洗。

### 3.3 v2 范围

- 支持更多平台，如 B 站、微博、贴吧、知乎、快手。
- 支持创作者维度分析。
- 支持趋势对比和历史增量。
- 支持 LLM 辅助总结，但保留可追溯证据。
- 支持 WebUI 或 Hermes 内部可视化面板。

## 4. 功能需求

### 4.1 数据集创建

#### 4.1.1 功能描述

系统提供创建搜索数据集的能力。调用方输入平台、关键词、采集数量、评论数量等参数，系统执行爬取并生成可追踪的数据集目录。

#### 4.1.2 输入参数

- `platforms`: 平台列表，v1 支持 `xhs`、`dy`。
- `keywords`: 关键词列表，至少 1 个。
- `crawler_type`: 默认 `search`。
- `max_contents`: 每个平台最多采集内容数，默认 20。
- `max_comments_per_content`: 每条内容最多采集评论数，默认 10。
- `include_comments`: 是否采集评论，默认 true。
- `include_sub_comments`: 是否采集二级评论，默认 false。
- `headless`: 是否无头浏览器，默认 false。
- `save_format`: 默认 `jsonl`。

#### 4.1.3 输出结果

- `dataset_id`
- `status`
- `platforms`
- `keywords`
- `created_at`
- `data_paths`
- `log_path`
- `warnings`

#### 4.1.4 行为要求

- 采集过程必须遵守现有项目的频控配置。
- 采集失败时需要返回明确错误，不吞掉异常。
- 如果某个平台失败，其他平台成功，数据集状态为 `partial_success`。
- 数据集目录必须可复用，不依赖一次性终端上下文。

### 4.2 数据集列表与详情

#### 4.2.1 列表功能

系统应能列出历史数据集，包括：

- `dataset_id`
- 主题或关键词
- 平台
- 创建时间
- 状态
- 内容数
- 评论数
- 分析产物是否存在

#### 4.2.2 详情功能

系统应能返回某个数据集的完整元信息，包括：

- 采集参数
- 数据文件路径
- 分析文件路径
- 原始记录数与去重后记录数
- 错误与警告

### 4.3 数据集分析

#### 4.3.1 内容分析

系统应输出：

- 总内容数
- 去重内容数
- 重复内容数
- 作者数
- 关键词分布
- 平台分布
- Top 内容排行

Top 内容字段：

- `content_id`
- `platform`
- `title`
- `nickname`
- `url`
- `source_keyword`
- `liked_count`
- `collected_count`
- `comment_count`
- `share_count`
- `engagement_count`

#### 4.3.2 评论分析

系统应输出：

- 总评论数
- 去重评论数
- 评论用户数
- 平均评论点赞数
- Top 评论
- 评论最多的内容
- 评论词频
- 评论词云

Top 评论字段：

- `comment_id`
- `platform`
- `content_id`
- `nickname`
- `content`
- `like_count`

#### 4.3.3 去重规则

内容去重：

- 小红书使用 `note_id`。
- 抖音使用 `aweme_id`。
- 重复内容保留 `engagement_count` 最高的一条。

评论去重：

- 使用 `comment_id`。
- 重复评论保留 `like_count` 最高的一条。

#### 4.3.4 文本清洗

v1 文本清洗要求：

- 去除空评论。
- 保留中英文、数字和常见技术词。
- 使用 `docs/hit_stopwords.txt` 作为停用词来源。
- 使用 `config.CUSTOM_WORDS` 注入自定义词。

后续版本增加：

- 表情过滤。
- 广告词过滤。
- 招生/课程/引流识别。
- 疑问句提取。

### 4.4 数据集查询

#### 4.4.1 功能描述

Agent 可对已有数据集进行轻量查询，返回相关内容或评论。

#### 4.4.2 查询能力

v1 支持：

- 按关键词全文匹配标题和评论。
- 按平台过滤。
- 按 `source_keyword` 过滤。
- 按互动数排序。
- 按评论点赞数排序。

#### 4.4.3 示例问题

- “找出评论里提到接单风险的内容。”
- “哪些评论在质疑这个副业不靠谱？”
- “找出和 Python 接单最相关的高赞评论。”
- “列出抖音里互动最高的 AI 编程副业内容。”

### 4.5 报告获取

系统应能返回数据集报告产物：

- `summary.json`
- `report.html`
- `top_contents.csv`
- `top_comments.csv`
- `comment_word_freq.json`
- `comment_word_cloud.png`

MCP 返回时不直接传输大文件内容，默认返回本地路径和简短摘要。

## 5. MCP 工具设计

### 5.1 工具列表

#### 5.1.1 `create_search_dataset`

创建搜索数据集。

输入：

```json
{
  "platforms": ["xhs", "dy"],
  "keywords": ["程序员接单", "AI编程接单"],
  "max_contents": 20,
  "include_comments": true,
  "max_comments_per_content": 10,
  "include_sub_comments": false,
  "headless": false
}
```

输出：

```json
{
  "dataset_id": "20260627_221500_ai_coding_side_job",
  "status": "success",
  "data_paths": {
    "xhs_contents": "data/datasets/.../xhs_contents.jsonl",
    "xhs_comments": "data/datasets/.../xhs_comments.jsonl",
    "dy_contents": "data/datasets/.../dy_contents.jsonl",
    "dy_comments": "data/datasets/.../dy_comments.jsonl"
  }
}
```

#### 5.1.2 `list_datasets`

列出历史数据集。

输入：

```json
{
  "platform": "xhs",
  "keyword": "接单",
  "limit": 20
}
```

输出：

```json
{
  "datasets": [
    {
      "dataset_id": "...",
      "created_at": "...",
      "platforms": ["xhs", "dy"],
      "keywords": ["程序员接单"],
      "status": "success"
    }
  ]
}
```

#### 5.1.3 `analyze_dataset`

对数据集生成或刷新分析产物。

输入：

```json
{
  "dataset_id": "20260627_221500_ai_coding_side_job",
  "top_n": 20
}
```

输出：

```json
{
  "status": "success",
  "summary_path": ".../summary.json",
  "report_path": ".../report.html",
  "metrics": {
    "total_contents": 46,
    "total_comments": 434
  }
}
```

#### 5.1.4 `query_dataset`

查询已有数据集。

输入：

```json
{
  "dataset_id": "20260627_221500_ai_coding_side_job",
  "query": "接单 风险 不靠谱",
  "target": "comments",
  "platform": "xhs",
  "limit": 10,
  "sort_by": "like_count"
}
```

输出：

```json
{
  "results": [
    {
      "platform": "xhs",
      "content_id": "...",
      "comment_id": "...",
      "content": "...",
      "like_count": 224,
      "source_url": "..."
    }
  ]
}
```

#### 5.1.5 `get_dataset_report`

返回数据集报告位置和摘要。

输入：

```json
{
  "dataset_id": "20260627_221500_ai_coding_side_job"
}
```

输出：

```json
{
  "report_path": ".../report.html",
  "summary_path": ".../summary.json",
  "top_contents_path": ".../top_contents.csv",
  "top_comments_path": ".../top_comments.csv",
  "brief": "共分析 46 条内容、434 条评论..."
}
```

### 5.2 MCP 设计原则

- 工具输入必须稳定，不暴露过多底层爬虫参数。
- 输出必须结构化，便于 Agent 继续推理。
- 大文件只返回路径，不直接塞入上下文。
- 所有工具必须返回可读错误。
- 涉及平台登录时，必须提示用户完成扫码或 cookie 配置。

## 6. 数据与目录设计

### 6.1 推荐目录结构

```text
data/
  datasets/
    <dataset_id>/
      dataset.json
      raw/
        xhs_contents.jsonl
        xhs_comments.jsonl
        dy_contents.jsonl
        dy_comments.jsonl
      analysis/
        summary.json
        report.html
        top_contents.csv
        top_comments.csv
        comment_word_freq.json
        comment_word_cloud.png
      logs/
        crawl.log
        analysis.log
```

### 6.2 `dataset.json`

```json
{
  "dataset_id": "20260627_221500_ai_coding_side_job",
  "created_at": "2026-06-27T22:15:00+08:00",
  "updated_at": "2026-06-27T22:20:00+08:00",
  "status": "success",
  "platforms": ["xhs", "dy"],
  "keywords": ["程序员接单", "AI编程接单"],
  "crawler_type": "search",
  "params": {
    "max_contents": 20,
    "include_comments": true,
    "max_comments_per_content": 10
  },
  "files": {
    "raw": {},
    "analysis": {}
  },
  "metrics": {
    "raw_total_contents": 0,
    "total_contents": 0,
    "raw_total_comments": 0,
    "total_comments": 0
  },
  "warnings": [],
  "errors": []
}
```

### 6.3 字段标准化

统一内容字段：

- `platform`
- `content_id`
- `title`
- `desc`
- `nickname`
- `user_id`
- `url`
- `source_keyword`
- `liked_count`
- `collected_count`
- `comment_count`
- `share_count`
- `engagement_count`

统一评论字段：

- `platform`
- `comment_id`
- `content_id`
- `content`
- `nickname`
- `user_id`
- `like_count`
- `parent_comment_id`
- `create_time`
- `ip_location`

## 7. 质量策略

### 7.1 关键词策略

系统应鼓励使用窄关键词，而不是单个泛词。

推荐：

- `程序员接单`
- `Python接单`
- `AI编程接单`
- `vibe coding接单`
- `编程接单避坑`

不推荐单独使用：

- `副业`
- `赚钱`
- `AI赚钱`

### 7.2 数据质量指标

报告应展示：

- 原始内容数
- 去重内容数
- 重复内容移除数
- 原始评论数
- 去重评论数
- 重复评论移除数
- 每个平台数据量
- 每个关键词数据量

### 7.3 噪声识别方向

后续版本可增加：

- 广告/课程引流识别。
- 标题党识别。
- 低相关内容过滤。
- 只包含表情或空文本的评论过滤。
- 与关键词无关的泛流量内容标记。

## 8. 非功能需求

### 8.1 稳定性

- 单个平台失败不应导致整个多平台任务完全失败。
- 分析任务必须能处理缺字段、空值和字符串数字。
- 数据集目录已存在时不能静默覆盖，应创建新版本或提示冲突。

### 8.2 性能

v1 目标：

- 100 条内容、1000 条评论以内，分析在 30 秒内完成。
- HTML 报告生成在 10 秒内完成。
- 查询 5000 条评论以内，响应在 5 秒内完成。

### 8.3 可维护性

- 采集、加载、标准化、分析、MCP wrapper 分层实现。
- 平台字段映射集中维护。
- MCP 工具输入输出有测试覆盖。

### 8.4 安全与合规

- 默认只读，不做写操作。
- 不用于商业化大规模采集。
- 不保存用户主账号敏感信息到数据集目录。
- Cookie、登录态和浏览器数据沿用现有项目配置，不复制到报告产物。
- 报告中可能包含公开评论内容，分享前需要人工确认是否脱敏。

## 9. 验收标准

### 9.1 v1 CLI 验收

- 可对 `xhs` 和 `dy` 的已有 `jsonl` 数据生成报告。
- 支持显式路径和自动发现两种模式。
- 自动发现 `dy` 时能兼容 `data/douyin` 目录。
- 内容和评论自动去重。
- 缺少内容文件时可只分析评论。
- 缺少评论文件时可只分析内容。
- 两类文件都缺失时返回明确错误。

### 9.2 v1 MCP 验收

- Hermes Agent 能成功调用 `list_datasets`。
- Hermes Agent 能成功调用 `analyze_dataset`。
- Hermes Agent 能成功调用 `query_dataset` 并获得结构化结果。
- `get_dataset_report` 能返回报告路径和关键指标。
- 错误场景返回可读错误，不抛出未处理异常。

### 9.3 报告验收

报告至少包含：

- 总览指标。
- Top 内容表。
- Top 评论表。
- 关键词分布。
- 重复数据移除统计。
- 评论词频文件。
- 评论词云图片。

## 10. 开发路线图

### Milestone 1：离线分析模块稳定化

状态：进行中。

目标：

- 完成 `analysis` 包。
- 完成去重、字段标准化、报告生成。
- 补齐小红书、抖音测试。

### Milestone 2：数据集抽象

目标：

- 引入 `dataset_id`。
- 引入 `dataset.json`。
- 将现有平台输出复制或归档到统一数据集目录。
- 支持 `list_datasets` 的本地实现。

### Milestone 3：MCP wrapper

目标：

- 新增 MCP server。
- 暴露 `list_datasets`、`analyze_dataset`、`get_dataset_report`。
- 先不暴露自动爬取，降低复杂度。

### Milestone 4：采集任务 MCP 化

目标：

- 暴露 `create_search_dataset`。
- 支持小红书、抖音。
- 支持任务状态和错误追踪。

### Milestone 5：查询能力

目标：

- 支持 `query_dataset`。
- 支持按平台、关键词、目标类型、排序字段过滤。
- 引入 SQLite 或 DuckDB 索引。

### Milestone 6：洞察增强

目标：

- 增加问题抽取。
- 增加广告/课程引流标记。
- 增加平台和关键词对比视图。
- 可选接入 LLM 总结，但必须附证据。

## 11. 与 Agent Reach 的关系

Agent Reach 是 Hermes Agent 的通用互联网接入层，适合：

- 临时搜索。
- 阅读网页。
- 读取单条社媒内容。
- 诊断和路由上游工具。

MediaCrawler Research MCP 是研究数据层，适合：

- 批量采集。
- 数据集沉淀。
- 离线分析。
- 历史数据复用。
- 主题研究和口碑洞察。

推荐协作方式：

- Hermes 先用 Agent Reach 做快速探索，确定关键词和平台。
- Hermes 再用 MediaCrawler Research MCP 创建数据集。
- 后续问题优先查询数据集，必要时再增量采集。

## 12. 风险与应对

### 12.1 平台风控风险

风险：平台登录、验证码、接口签名变化导致采集失败。

应对：

- 保持低频采集。
- 明确提示用户登录状态。
- 服务端登录态与飞书二维码闭环详见 [服务器登录态与飞书二维码方案](服务器登录态与飞书二维码方案.md)。
- 单平台失败不影响其他平台。
- MCP 返回可读错误和恢复建议。

### 12.2 数据质量风险

风险：关键词太泛导致结果噪声高。

应对：

- 在 PRD 和 CLI/MCP 提示中鼓励窄关键词。
- 报告中展示关键词分布与重复率。
- 后续增加低相关内容过滤。

### 12.3 合规风险

风险：用户将工具用于商业化或大规模采集。

应对：

- 文档中明确非商业学习研究用途。
- 默认限制采集数量。
- 不提供高并发批量采集默认配置。

### 12.4 Agent 误用风险

风险：Agent 在未确认用户意图时频繁创建采集任务。

应对：

- MCP 工具描述中强调成本和频率。
- 大于默认数量的任务需要显式确认。
- 优先复用已有数据集。

## 13. 成功指标

### 13.1 工程指标

- v1 分析模块测试通过率 100%。
- MCP 工具核心路径有自动化测试。
- 数据集分析失败率低于 5%。

### 13.2 产品指标

- 用户能在 10 分钟内完成一次“采集到报告”的闭环。
- Agent 能基于已有数据集回答至少 5 类追问。
- 重复采集减少，历史数据集复用率逐步提升。

### 13.3 质量指标

- 报告能明确显示重复数据移除数量。
- Top 内容和 Top 评论可追溯到原始 URL 或内容 ID。
- 查询结果包含足够证据字段，便于 Agent 引用。

## 14. 开放问题

以下问题中的 MCP 形态、任务执行、数据集存储、登录态边界和飞书集成边界，已在 [技术选型与架构决策](技术选型与架构决策.md) 中形成第一版结论。

- 数据集目录是否直接使用现有 `data/<platform>`，还是迁移到 `data/datasets/<dataset_id>`？
- MCP server 是否与 MediaCrawler 同仓库维护，还是拆成独立包？
- Hermes Agent 的 MCP 部署方式是本地 stdio、HTTP，还是通过统一网关？
- 是否需要为不同平台配置独立账号和采集限额？
- 是否需要引入 DuckDB 作为分析查询层？
- LLM 总结能力由 Hermes 自己完成，还是由本 MCP 内置？

## 15. 当前建议

短期优先级：

1. 继续打磨现有 `analysis` 包。
2. 引入数据集元信息与统一目录。
3. 先实现只读 MCP：`list_datasets`、`analyze_dataset`、`get_dataset_report`、`query_dataset`。
4. 在开放服务端 `create_search_dataset` 前，先完成登录态状态检查与飞书二维码登录闭环。
5. 最后再开放 `create_search_dataset`，避免一开始就把登录、浏览器、风控、异步任务全部压进 MCP。

这条路径能最快让 Hermes Agent 获得“可复用研究数据”的能力，同时避免和 Agent Reach 的即时联网检索能力重复。
