> 2026-07 状态说明：本文是早期选型讨论记录。最新主线已进一步收敛为“桌面真实浏览器采集 + 服务器数据集 MCP 查询分析”。默认 MCP Server 只启用 `dataset` profile，服务器二维码登录和实时采集工具默认不注册、默认不可见，仅作为 `experimental_collection` profile 的实验/兜底能力。正文里关于 Hermes 默认调用 `start_collection`、二维码登录或服务器实时采集的建议已不再作为当前架构依据。详见 [桌面采集与数据集 MCP 架构改造方案](桌面采集与数据集MCP架构改造方案.md)。

我的建议：**Codex 这套技术选型方向基本对，但我会做几个关键收敛**。你这个场景不是“实时搜索工具”，而是一个**本地数据集采集 + 查询 + 分析系统**，MCP 只是给 Hermes 暴露一个小而深的接口。

我会这样定：

## 总体结论

**推荐第一版：**

- **MCP 形态：stdio**
- **任务状态：SQLite**
- **采集执行：后台 subprocess / asyncio task，不要同步阻塞 MCP 调用**
- **原始数据：保留 JSONL**
- **查询分析：DuckDB**
- **登录态：Playwright persistent context 优先**
- **二维码：输出本地图片路径，Hermes 可以通过飞书发送**
- **报告：生成 Markdown + HTML，本地路径返回给 Hermes**
- **飞书集成：放在 Hermes 层，不放进 MediaCrawler MCP**

这套足够支撑你说的 A/B/C/D 四个场景，而且不会一开始就过度工程化。

---

# 1. MCP 服务形态：我也建议先 stdio

你说“部署到 Hermes 同一台服务器上”，那 **stdio 是最合适的第一形态**。

Hermes 原生支持 stdio MCP。配置大概是：

```yaml
mcp_servers:
  mediacrawler:
    command: "/path/to/venv/bin/python"
    args: ["/path/to/mediacrawler_mcp/server.py"]
    timeout: 300
    connect_timeout: 60
```

Hermes 启动后会发现工具，并注册成类似：

```text
mcp_mediacrawler_create_dataset
mcp_mediacrawler_start_collection
mcp_mediacrawler_get_task_status
mcp_mediacrawler_query_dataset
mcp_mediacrawler_generate_report
```

## stdio 的优点

- 不需要端口，不需要反代，不需要鉴权。
- 只有本机 Hermes 能调用，安全面更小。
- 部署和调试简单。
- 和 Hermes 的 MCP 客户端天然匹配。

## 什么时候再考虑 HTTP MCP？

只有这些情况出现时再上 HTTP：

1. 你想让多个 Agent / 多台机器共用同一个 MediaCrawler 服务。
2. 你想做 Web UI。
3. 你想让外部系统直接调用采集任务。
4. 你想让服务独立常驻，并和 Hermes 生命周期解耦。
5. 你要做 webhook / 远程任务队列 / 多用户。

否则第一版用 HTTP 反而增加很多复杂度：鉴权、端口、服务保活、日志、跨进程状态、反代、TLS、访问控制。

**所以我的建议：先 stdio。后面如果需要共享服务，再把核心逻辑抽成 Python package，外面同时套 stdio MCP 和 HTTP MCP 两层适配器。**

---

# 2. 长耗时任务：MCP 调用不能直接等爬虫跑完

这一点 Codex 说得对。采集小红书、抖音内容和评论可能几分钟到几十分钟，不能让一个 MCP tool call 一直卡住。

建议设计成两阶段：

## 采集任务接口

```text
create_dataset
start_collection
get_task_status
cancel_task
```

典型流程：

1. Hermes 调用 `create_dataset`
2. 返回 `dataset_id`
3. Hermes 调用 `start_collection`
4. 返回 `task_id`
5. 用户后续问“进度如何”，Hermes 调 `get_task_status`
6. 任务完成后，Hermes 调 `generate_report`

不要做成：

```text
crawl_and_analyze_everything_sync
```

这种一旦平台登录、验证码、限流、评论翻页卡住，Hermes 侧体验会很差。

## 任务状态管理建议

第一版我建议：

- **SQLite 存任务表、数据集表、运行日志**
- **后台 subprocess 执行具体 crawler**
- **SQLite WAL 模式**
- **不用 Celery / Redis / RQ**

比“后台线程”我更偏向 **subprocess**，原因是 MediaCrawler + Playwright 这类爬虫经常有浏览器进程、异步事件循环、登录态、页面崩溃等问题。放在同进程线程里，MCP server 容易被拖死。

推荐结构：

```text
MCP server process
  ├── SQLite: tasks / datasets / accounts
  ├── DuckDB: query / analytics
  └── spawn crawler subprocess
        └── MediaCrawler / Playwright
```

MCP server 负责调度和查询状态，爬虫 subprocess 负责实际采集。

---

# 3. 数据集存储：JSONL + DuckDB 是最合适的组合

我不建议只用文件目录，也不建议一上来全塞进 SQLite。

更好的组合是：

```text
~/.mediacrawler-mcp/
  datasets/
    ds_20260627_ai_coding_side_hustle/
      dataset.json
      raw/
        xiaohongshu_contents.jsonl
        xiaohongshu_comments.jsonl
        douyin_contents.jsonl
        douyin_comments.jsonl
      normalized/
        contents.parquet
        comments.parquet
      analysis.duckdb
      reports/
        report.md
        report.html
        charts/
```

## 为什么保留 JSONL？

JSONL 适合做原始归档：

- 爬虫输出直接追加
- 出错后容易恢复
- 可审计
- 不绑定数据库 schema
- 以后可以重新 normalize

## 为什么加 DuckDB？

你有这些需求：

- 按 `source_keyword` 对比
- 按平台拆分统计
- 热门内容排序
- 评论高频词
- 搜索历史数据
- 找“大家最担心什么风险”
- 过滤广告/引流内容
- 多关键词横向比较

这些用 DuckDB 很舒服。

例如：

```sql
SELECT source_keyword, platform, COUNT(*) AS content_count
FROM contents
GROUP BY source_keyword, platform;
```

```sql
SELECT source_keyword, COUNT(*) AS comment_count
FROM comments
WHERE comment_text LIKE '%被骗%'
   OR comment_text LIKE '%接不到单%'
   OR comment_text LIKE '%不会报价%'
GROUP BY source_keyword;
```

SQLite 更适合存任务元数据，DuckDB 更适合做分析查询。

---

# 4. 一定要把 `source_keyword` 当成一等字段

你的场景 C 很重要。多关键词对比如果后面再补，会很麻烦。

建议所有核心表都保留这些字段：

## contents 表

```text
dataset_id
platform
source_keyword
content_id
author_id
author_name
title
desc
content_text
url
publish_time
like_count
comment_count
share_count
collect_count
crawl_time
raw_json
```

## comments 表

```text
dataset_id
platform
source_keyword
content_id
comment_id
parent_comment_id
user_id
user_name
comment_text
like_count
publish_time
crawl_time
raw_json
```

## tasks 表

```text
task_id
dataset_id
task_type
platforms
keywords
status
progress
started_at
finished_at
error
log_path
```

`source_keyword` 不要只存在 dataset 配置里，要进入每条内容和每条评论。否则后续对比统计会很痛苦。

---

# 5. MCP 工具接口建议：少而深，不要暴露太多细碎命令

我建议第一版暴露这些工具就够：

## 数据集

```text
create_dataset(name, description?, platforms, keywords, options?)
list_datasets()
get_dataset(dataset_id)
```

## 登录

```text
get_login_status(platform)
start_login(platform, account_name?)
```

`start_login` 返回：

```json
{
  "status": "qr_required",
  "platform": "xiaohongshu",
  "qr_image_path": "/tmp/mediacrawler-login/xhs-xxx.png",
  "expires_at": "2026-06-27T15:30:00+08:00",
  "message": "请扫码登录"
}
```

## 采集任务

```text
start_collection(dataset_id, platforms?, keywords?, crawl_comments=true, max_items?, max_comments?)
get_task_status(task_id)
cancel_task(task_id)
```

## 查询历史数据

```text
query_dataset(dataset_id, query, platform?, source_keyword?, limit?)
```

这里的 `query` 可以先做简单关键词搜索，后续再升级成 SQL template 或 embedding search。

## 分析报告

```text
generate_report(dataset_id, report_type, group_by_keyword=true)
get_report(dataset_id, report_id?)
```

`generate_report` 返回：

```json
{
  "report_md_path": "/root/.mediacrawler-mcp/datasets/.../reports/report.md",
  "report_html_path": "/root/.mediacrawler-mcp/datasets/.../reports/report.html",
  "summary": {
    "top_concerns": ["接不到单", "报价困难", "被骗稿", "没有案例"],
    "top_keywords": [...],
    "high_engagement_posts": [...]
  }
}
```

这样 Hermes 可以直接把摘要发到飞书，并附报告路径。

---

# 6. Hermes + 飞书能不能发二维码图片？

**可以。**

当前 Hermes 在飞书环境里支持发送本地媒体文件。只要最终回复里包含：

```text

```

飞书侧会把图片作为媒体上传并展示。

所以二维码建议返回 **本地图片路径**，例如：

```json
{
  "qr_image_path": "/tmp/mediacrawler-login/douyin-login-20260627.png"
}
```

然后 Hermes 可以回复用户：

```text
请用抖音扫码登录：


```

## 是否需要 base64？

可以支持，但我建议优先本地文件路径。

理由：

- Hermes 发图片最自然的是本地路径。
- base64 会让 MCP 返回变大。
- LLM 上下文不适合塞大 base64。
- 文件路径更容易调试、复用、过期清理。

最佳设计：

```json
{
  "qr_image_path": "...",
  "qr_image_base64": null,
  "expires_in_seconds": 120
}
```

base64 作为可选字段，不作为主路径。

---

# 7. 登录态管理：默认 persistent context，CDP 和 cookie import 做补充

我赞同 Codex 的判断。

## 推荐优先级

### 第一优先：Playwright persistent context

目录类似：

```text
~/.mediacrawler-mcp/browser_profiles/
  xiaohongshu/default/
  douyin/default/
```

优点：

- 登录态持久化自然。
- 二维码登录后能复用。
- 不需要用户手动复制 cookie。
- 对小红书、抖音这类平台更现实。

### 第二优先：cookie import

作为服务器兜底能力。

例如：

```text
import_cookies(platform, cookie_json_path)
```

适合：

- 无法扫码
- 需要从本地浏览器导出 cookie
- 服务器没有图形环境
- 登录验证复杂

### 第三优先：CDP

CDP 是高级模式，适合复用已经打开的 Chrome 会话。

你当前服务器上其实已经有 OpenClaw 的 Chrome/CDP 环境：

```text
127.0.0.1:9222
```

但我不建议第一版强依赖它。原因是：

- 和 OpenClaw 生命周期耦合。
- 多平台登录态可能混在同一个 profile 里。
- 任务隔离不如 persistent context 清晰。
- 爬虫崩了可能影响其他浏览器自动化任务。

可以作为高级配置：

```yaml
browser:
  mode: persistent_context # default
  cdp_endpoint: http://127.0.0.1:9222 # optional
```

---

# 8. 报告与分析栈：pandas + jieba + DuckDB 就够第一版

我建议第一版不要急着上复杂可视化。

## 第一版报告应该包含

### 主题调研报告

- 数据集信息
- 平台范围
- 关键词范围
- 采集时间
- 内容数量
- 评论数量
- 高互动内容 Top N
- 高频评论词
- 用户主要关切
- 典型正面反馈
- 典型负面反馈
- 疑似广告/引流内容
- 值得人工复看的链接

### 多关键词对比

按 `source_keyword` 拆：

| 关键词 | 内容数 | 评论数 | 平均互动 | 高频风险词 | 广告感比例 |
|---|---:|---:|---:|---|---:|

### 竞品/口碑分析

按类别拆评论：

- 夸什么
- 骂什么
- 问什么
- 担心什么
- 是否像广告
- 是否像真实用户反馈

## 分析方式

第一版可以先用规则 + LLM 综合：

- DuckDB 做结构化统计
- jieba 做中文分词
- 规则识别广告/引流：
  - 私信
  - 进群
  - 课程
  - 训练营
  - 副业项目
  - 带你做
  - 资料包
  - 领取
  - 加我
- Hermes/LLM 做最终归纳总结

不要把所有智能分析都写死在 MCP 里。MCP 负责生成结构化统计和候选样本，Hermes 负责自然语言分析会更灵活。

---

# 9. 历史数据检索：不要重新爬，必须围绕 dataset_id

你的场景 D 本质是“数据集记忆”。

用户问：

> 上次抓到的数据里，大家最担心接单的什么风险？

Hermes 应该能做：

1. 找最近相关 dataset
2. 调 `query_dataset`
3. 调 `analyze_dataset`
4. 直接基于已有数据回答

所以 dataset 元数据要认真设计。

## dataset.json 建议

```json
{
  "dataset_id": "ds_20260627_ai_coding_side_hustle",
  "name": "AI 编程副业真实反馈调研",
  "description": "小红书和抖音关于 AI 编程副业、程序员接单的内容和评论",
  "platforms": ["xiaohongshu", "douyin"],
  "keywords": ["AI编程副业", "程序员接单", "AI编程接单"],
  "created_at": "2026-06-27T15:00:00+08:00",
  "last_crawled_at": "...",
  "status": "ready",
  "content_count": 123,
  "comment_count": 4567
}
```

还可以做一个 `dataset_aliases`：

```text
上次AI编程副业调研
AI编程副业
程序员接单
```
 (1/2)
回复 用户554313: 
GitHub - ran550160-hue/MediaCrawler: 小红书笔记 | 评论爬虫、抖音视频 | 评论爬虫、快手视频 | 评论爬虫、B 站视频 ｜ 评论爬虫、微博帖子 ｜ 评论爬虫、百 我想基于这个项目开发mcp工具给当前服务器的hermes使用，核心场景大致如下：2.2 核心场景场景 A：主题调研用户说：“帮我调研 AI 编程副业在小红书和抖音上的真实反馈。”Agent 应能： 1. 创建小红书和抖音搜索数据集。 2. 抓取内容和评论。 3. 分析高互动内容、评论高频词、用户关切。 4. 返回报告路径和关键摘要。 5. 后续用户追问时复用该数据集。场景 B：竞品/口碑分析用户给出品牌、产品或关键词，Agent 创建数据集，分析： - 热门内容是谁发的。 - 评论区在夸什么、骂什么、问什么。 - 哪些内容像广告或课程引流。 - 哪些真实用户反馈值得进一步查看。场景 C：多关键词对比用户希望比较多个关键词，如： - 程序员接单 - Python接单 - AI编程接单 - vibe coding接单系统应保留 source_keyword，并在报告中支持按关键词拆分统计。场景 D：历史数据检索用户问：“上次抓到的数据里，大家最担心接单的什么风险？”Agent 应能在已有数据集里查询评论和内容，而不是重新爬取。  关于codex给的技术选型方案：我想听听你的建议，具体如下：MCP 服务形态 - 本地 stdio 还是 HTTP MCP。 - 如果接 Hermes/飞书，后续大概率需要 HTTP 或网关模式。 - 但开发早期可以先 stdio，简单、好调试。任务执行与状态管理 - 采集任务是长耗时任务，不能同步卡住 MCP 调用。 - 需要选：内存任务表、SQLite、Redis、Celery/RQ。 - 个人/单机阶段我建议先 SQLite + 后台线程/asyncio task，别一开始上 Celery。数据集存储 - 继续用文件目录，还是引入 DuckDB/SQLite。 - 当前 jsonl/csv 可以保留为原始数据。 - 查询分析层建议选 DuckDB，适合本地分析和结构化查询。登录态管理 - Playwright persistent context、CDP、cookie 导入，怎么组合。 - 我的建议是：默认 Playwright persistent context；CDP 作为可选高级模式；cookie import 作为服务器兜底能力。 - 二维码输出要抽象成 file/base64，方便 Hermes 发飞书。报告与分析栈 - 现在 pandas + jieba + wordcloud + HTML 足够。 - 后续如果加更强查询，用 DuckDB。 - 如果加图表，再考虑 Plotly/ECharts，不急。Hermes/飞书集成边界 - MediaCrawler MCP 不直接依赖飞书 SDK。 - MCP 只返回二维码文件/base64、任务状态、报告路径。 - Hermes 负责飞书消息、图片上传、交互卡片。mcp服务形态我觉得应该stdio吧，毕竟我打算就部署到hermes的服务器上，登录态这块，hermes + 飞书能发二维码图片吗？其他的我就不太了解了，看下你的建议
这样 Hermes 后续更容易找到历史数据。
10. MediaCrawler MCP 不要直接依赖飞书 SDK
这一点 Codex 也对。
MCP 服务只负责：
登录二维码文件路径
任务状态
数据集路径
报告路径
查询结果
结构化摘要
Hermes 负责：
飞书消息发送
二维码图片上传
报告文件发送
用户交互
后续追问理解
这样边界干净。
如果 MCP 里直接引入飞书 SDK，后面会变成：
MediaCrawler MCP = 爬虫 + 分析 + 任务系统 + 飞书机器人
这会越来越难维护。
11. 我会稍微调整 Codex 方案的地方
调整 1：后台线程不如 subprocess 稳
Codex 说 SQLite + 后台线程/asyncio task。可以，但我更建议：
SQLite + asyncio subprocess
尤其是 Playwright 爬虫，subprocess 隔离更稳。
调整 2：DuckDB 可以早一点上
Codex 说“后续如果加更强查询，用 DuckDB”。
我建议第一版就引入 DuckDB，但只用于分析层，不影响原始数据存储。
因为你的场景 C/D 天然需要结构化查询和历史复用，早上 DuckDB 可以少走弯路。
调整 3：二维码不要主要用 base64
保留 base64 选项可以，但主通道用 qr_image_path。Hermes/飞书发本地图片路径更合适。
调整 4：MCP 接口要围绕“数据集”，不是围绕“爬一次”
核心抽象应该是：
Dataset
Task
Report
Query
LoginSession
而不是：
crawl_xhs
crawl_douyin
crawl_comments
analyze_csv
前者更适合你后续追问和历史复用。
12. 推荐的第一版开发顺序
我建议按这个顺序做：
Phase 1：MCP 壳 + 数据集元数据
实现：
create_dataset
list_datasets
get_dataset
SQLite 初始化
数据集目录创建
先不接爬虫。
Phase 2：接 MediaCrawler 单平台最小闭环
先选一个平台，比如小红书：
start_login
get_login_status
start_collection
get_task_status
输出 raw JSONL
目标是跑通：
创建数据集 → 扫码登录 → 抓内容 → 抓评论 → 写 JSONL
Phase 3：DuckDB normalize + 查询
实现：
raw JSONL → DuckDB 表
query_dataset
按关键词、平台、互动排序
Phase 4：报告生成
实现：
generate_report
Markdown 报告
HTML 报告
高频词
高互动内容
典型评论
广告/引流规则识别
Phase 5：多平台、多关键词
加入：
抖音
多关键词并行/串行
source_keyword
平台对比
13. 最终我建议的架构图
Feishu 用户
   ↓
Hermes Agent
   ↓ MCP stdio
MediaCrawler MCP Server
   ├── SQLite
   │     ├── datasets
   │     ├── tasks
   │     ├── login_sessions
   │     └── reports
   │
   ├── DuckDB
   │     ├── contents
   │     └── comments
   │
   ├── raw JSONL files
   │     ├── xiaohongshu_contents.jsonl
   │     ├── xiaohongshu_comments.jsonl
   │     ├── douyin_contents.jsonl
   │     └── douyin_comments.jsonl
   │
   ├── Playwright persistent profiles
   │     ├── xiaohongshu/default
   │     └── douyin/default
   │
   └── crawler subprocesses
         └── MediaCrawler
14. 对你当前问题的直接回答
mcp 服务形态我觉得应该 stdio 吧？
是，我建议 stdio。
因为你部署在 Hermes 同一台服务器，第一版没有必要上 HTTP。后续如果要多人/多 Agent 共用，再加 HTTP 适配层。
Hermes + 飞书能发二维码图片吗？
能。
MCP 返回二维码图片本地路径，Hermes 回复里带：

飞书就可以展示图片。
所以登录二维码设计成 qr_image_path 最合适。
其他的你建议？
我的核心建议是：
围绕 dataset 设计，不要围绕单次 crawl 设计。
任务异步化，MCP 调用只提交任务和查状态。
SQLite 管任务，DuckDB 管分析，JSONL 管原始数据。
Playwright persistent context 做默认登录态。
二维码用文件路径，base64 可选。
MCP 不碰飞书 SDK。
第一版用 subprocess 跑爬虫，比线程更稳。
source_keyword 必须进入每条内容和评论。
如果你要我选一个“最小可行版本”的范围，我会定成：
stdio MCP
+ 小红书单平台
+ 扫码登录
+ 创建数据集
+ 关键词搜索内容
+ 抓评论
+ JSONL 原始存储
+ DuckDB 查询
+ Markdown 报告
+ Hermes 可发送二维码和报告路径
这个跑通后，再接抖音、多关键词对比和历史检索。 (2/2)
