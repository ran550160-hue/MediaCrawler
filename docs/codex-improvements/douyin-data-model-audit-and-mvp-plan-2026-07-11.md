# 抖音数据模型只读审计与最小闭环计划

## 审计范围与证据

本次未运行抖音采集，也未修改抖音业务代码。结论来自以下代码和本地既有 JSONL 的只读字段统计：

- `media_platform/douyin/core.py`：`DouYinCrawler.search`、`get_comments`。
- `media_platform/douyin/client.py`：`search_info_by_keyword`、`get_aweme_all_comments`、`get_sub_comments`。
- `store/douyin/__init__.py`：`update_douyin_aweme`、`update_dy_aweme_comment`。
- `store/douyin/_store_impl.py`：JSON/JSONL/CSV/DB store。
- `data/douyin/jsonl/search_contents_2026-06-27.jsonl`（28 行）和 `search_comments_2026-06-27.jsonl`（280 行）。

## 当前能力

| 能力 | 代码证据 | 审计结论 |
|---|---|---|
| 关键词搜索 | `DouYinCrawler.search` 调用 `search_info_by_keyword` | 支持；逐关键词、按页采集。 |
| 详情采集 | `get_specified_awemes` / `get_video_by_id` | 支持。 |
| 一级评论 | `get_aweme_all_comments` 调用 `get_aweme_comments` | 支持。 |
| 二级评论 | `get_aweme_all_comments(... is_fetch_sub_comments=True)` 调用 `get_sub_comments` | 代码路径支持；本地样本未验证真实二级评论。 |
| 保存格式 | `DouyinStoreFactory.STORES` | csv、db/postgres、json、jsonl、sqlite、mongodb、excel。 |
| 来源关键词 | `source_keyword_var` → `update_douyin_aweme` | 内容保存；评论保存项当前没有 `source_keyword`。 |
| 来源 URL | `aweme_url = https://www.douyin.com/video/{aweme_id}` | 内容保存稳定页面 URL；评论通过 `aweme_id` 可关联该 URL。 |
| MCP/Agent | `mediacrawler_mcp` 当前只实现 XHS 归一化和桌面 agent 流程 | 无抖音 MCP/Agent 入口。 |
| 独立文件输出 | 本轮 `AsyncFileWriter` run_id 修复 | json/jsonl/csv 的通用 writer 已隔离；抖音媒体 store 仍使用平台共享 `images/videos` 路径，尚未验证。 |

## 原始内容字段映射

下表描述 `update_douyin_aweme` 实际写入 JSONL 的路径，而非对远端响应的猜测。

| 统一语义 | 原始字段路径 | 稳定性 | 保存字段 / 备注 |
|---|---|---|---|
| content_id | `aweme_id` | 已在样本中存在 | `aweme_id` |
| title / content_text | `desc` | 已在样本中存在 | `title`、`desc` |
| content_type | `aweme_type` | 已在样本中存在 | `aweme_type`，需后续映射语义值 |
| author_id | `author.uid` | 代码读取 | `user_id` |
| author_name | `author.nickname` | 代码读取 | `nickname` |
| publish time | `create_time` | 已在样本中存在 | `create_time` |
| source URL | 构造 `https://www.douyin.com/video/{aweme_id}` | 代码确定 | `aweme_url` |
| source keyword | `source_keyword_var.get()` | 内容样本存在 | `source_keyword` |
| crawl time | `utils.get_current_timestamp()` | 内容样本存在 | `last_modify_ts` |
| likes | `statistics.digg_count` | 代码读取 | `liked_count` |
| comments | `statistics.comment_count` | 代码读取 | `comment_count` |
| collects | `statistics.collect_count` | 代码读取 | `collected_count` |
| shares | `statistics.share_count` | 代码读取 | `share_count` |
| exposure / plays | 未映射 | 未在保存样本中存在 | 不能推断为 engagement；后续需从真实响应确认。 |
| duration | 未映射 | 未在保存样本中存在 | 候选 `duration_ms`，仅在确认原始路径后新增。 |
| hashtags / challenges | 未映射 | 未在保存样本中存在 | 仅保留 raw JSON 后再决定标签提取。 |

`engagement_count` 的后续定义应是 `like + comment + collect + share`；播放量如可获得应单独映射为 `exposure_count`，不得并入互动。

## 评论与回复审计

`update_dy_aweme_comment` 写入：`cid → comment_id`、`aweme_id`、`text → content`、`user.uid → user_id`、`digg_count → like_count`、`create_time`、`reply_comment_total → sub_comment_count`、`reply_id → parent_comment_id`。评论入库前检查回调的 `aweme_id` 与评论自身 `aweme_id` 相等，因此有基础的视频关联保护。

本地 JSONL 的 280 条评论均有 `aweme_id`，没有空关联；155 条的 `sub_comment_count > 0`，但所有 `parent_comment_id` 都是根值。因此二级回复抓取**代码支持但未由真实样本验证**。`reply_id` 是否总能表达父评论、是否还需要 root/reply-to 字段，须在下一阶段用真实脱敏回复响应确认。

## 输出隔离与数据集适配

旧抖音 JSONL 同样采用日期文件名和通用 writer，因此受本轮 run_id 修复覆盖：新 CLI JSONL 路径为 `data/douyin/{run_id}/jsonl/search_contents.jsonl`（等价的任务隔离结构），同次内容/评论共享 run id。现存 `data/douyin/jsonl/search_*.jsonl` 是历史共享输出，不能直接视为干净任务数据。

现有 DuckDB `contents/comments` 可直接复用：dataset/platform/collection task/source keyword/content id/type/title/text/author/time/url/like/comment/collect/share/engagement/crawl/raw JSON。候选新增通用字段仅有 `exposure_count` 与 `duration_ms`；没有确认前不扩表。原始平台媒体、签名 URL、用户细节和未确认字段应留在 raw JSON，不应驱动通用模型扩张。

## 抖音最小闭环实施计划

### PR 1：raw 与 bundle 接入

实现抖音任务目录、`raw/douyin_contents.jsonl`、`raw/douyin_comments.jsonl`、manifest 血缘和文件识别；不生成报告。验收：同日独立运行不混写，bundle 可注册。

### PR 2：归一化 MVP

添加明确的抖音内容/评论字段映射、父子关系、互动与曝光分离、来源 URL、source keyword 回填和 DuckDB 测试。验收：固定脱敏真实内容/一级评论样本可归一化并可回溯；二级评论字段仅在真实样本确认后支持。

### PR 3：MCP/Agent 入口

按现有 XHS 任务约束提供抖音专用或最小参数化入口：start/status/cancel/retry/finalize。验收：模拟本地采集边界下，真实 register/normalize 核心可运行。

### PR 4：规则型报告适配与真实验证

复用范围、质量、去重、证据与标签/关键词确定性分组；保留抖音特有字段语义。验收：真实脱敏样本报告不把播放量计为互动，不声称趋势或语义聚类。

## 风险与未验证事项

- 未运行真实抖音采集：登录、账户权限、风控、反爬和网络稳定性均未验证；执行时需要人工确认登录态与风险。
- 有真实内容和一级评论保存样本，但没有真实二级回复保存样本。
- 未验证抖音 finalize，也不存在当前 MCP/Agent 入口。
- 原始保存样本没有 play_count、duration、hashtags/challenges，不能据此补全字段。
- run_id 通用 writer 修复不自动迁移历史共享文件；历史数据应按已知任务边界重新导出或隔离后再注册。

## 架构判断

当前应复用数据集注册、存储、归一化质量、证据报告、缩写数值解析、安全 URL 和 run_id 输出隔离；保留抖音原始路径、媒体、回复、内容类型和互动来源为平台特有适配函数。尚无两个以上稳定调用点证明需要 `PlatformAdapterFactory`、通用研究引擎或多层抽象。
