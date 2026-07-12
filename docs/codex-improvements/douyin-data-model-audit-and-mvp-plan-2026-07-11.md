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

## 附录：2026-07-12 真实冒烟验证结果

以下结论由 2026-07-12 真实 CLI 冒烟确认（抖音标准 Playwright 模式 + 小红书 CDP 模式），均在 `b174451 fix(storage): isolate legacy crawler outputs by run id` 之上验证：

- 真实抖音搜索成功：关键词 `Cursor编辑器`，1 页返回 13 条视频，无风控、无验证码、无接口异常。
- 一级评论采集成功并与 `aweme_id` 关联：34 条一级评论全部 `aweme_id` 可关联本次 contents，`parent_comment_id == "0"` 判定一级。
- 一级评论采集成功并与 `aweme_id` 关联：34 条一级评论全部 `aweme_id` 可关联本次 contents，`parent_comment_id == "0"` 判定一级。评论按 aweme 分布：11 个视频各 3 条、1 个视频 1 条、1 个视频 0 条（`7654979792168586539`），评论覆盖 12/13 个视频，无 orphan 评论。
- 二级评论 `parent_comment_id` 结构已验证：detail 模式 `--get_sub_comment yes` 得 2 条一级 + 27 条二级；二级评论 `parent_comment_id` 指向一级评论 cid，保存记录中不存在 `reply_id`、`root_comment_id`、`reply_to_comment_id`，因此当前只能区分一级/二级，无法还原 reply-to-reply 更深线程。
- 二级评论无分页重复：detail 模式 29 行 29 个唯一 `comment_id`，0 重复；2 条一级 + 27 条二级，每个非零 `parent_comment_id` 均可匹配同一文件中的一级评论；`sub_comment_count` 字段值与实际唯一二级数量不严格相等，但不作为采全率依据。
- run_id 目录真实可用：抖音输出位于 `data/douyin/<run_id>/jsonl/search_contents.jsonl`，contents/comments 共享同一 run_id，`run_metadata.json` 与实际任务一致；小红书 CDP 同构 `data/xhs/<run_id>/...`。
- URL 可由 `aweme_id` 构造：`https://www.douyin.com/video/{aweme_id}` 13/13 稳定且与 id 匹配，无需 cookie/token/签名进数据集。
- 评论没有 `source_keyword`：抖音评论保存记录 0/34 不含 `source_keyword`，但 34/34 可由 `aweme_id` 关联父视频继承；小红书评论同理（0/6，可由 note_id 继承）。
- `play_count`/`duration`/`hashtags`/`challenges` 在当前 store 保存记录中不存在；本轮未保存或审计完整远端原始响应，因此无法判断远端响应中是否存在。不据此扩保存模型。
- `ip_location` 在 contents 中不存在（0/13），但在评论中存在（34/34）；fixture 脱敏必须删除 `ip_location`。
- 互动字段类型：contents 互动字段（liked/comment/collected/share_count）均为整数字符串；评论 `like_count` 为整数。归一化阶段需处理这两类形态。
- `aweme_type` 在本次保存记录中为字符串 `"0"`（0/13），非整数。
- aweme `7457151414150696219` 互动值：liked_count=`"52067"`、comment_count=`"1312"`、collected_count=`"48956"`、share_count=`"10405"`（均为 str），`create_time=1736253384`（int，秒级 Unix）。
- 数据安全：保存 JSONL 与日志中均未发现 `cookie`/`token`/`device`/`verifyFp`/`msToken`/`a_bogus`/`signature` 泄漏；`sec_uid` 在抖音 contents/comments 中存在，fixture 必须脱敏。
- MCP 回归修复（`921047a fix(mcp): discover run-isolated crawler outputs`）：`archive_outputs` 现可发现 run-isolated 输出并拒绝多 run 静默合并，旧日期共享路径作为 fallback 并以 `output_layout=legacy_shared` 标记。

保留限制（仅代表本次账号与时间点）：

- 无法还原 reply-to-reply 的更深线程。
- 媒体输出目录隔离尚未验证（本轮 `ENABLE_GET_MEIDAS=False`，媒体 store 仍走平台共享路径 `data/{platform}/images|videos`）。
- 登录与风控结果仅代表本次账号与时间点；抖音复用了 Playwright 持久 profile（`browser_data/dy_user_data_dir`）中的历史登录态（标准模式，非 CDP），未本轮扫码。小红书 6/27 标准登录态已失效，改用 CDP 模式复用已登录的 Chrome 后成功。CDP 登录模式下的抖音采集留到后续 MCP/Agent PR 验证。
- CDP 登录模式不是 PR 1 的前置条件；raw/bundle 接入不依赖具体登录方式。

## 附录：PR 1 实现状态（2026-07-12）

PR 1（抖音 raw + bundle 接入）已实现并独立提交（`feat(mcp): add Douyin raw dataset bundle support`）：

- `DatasetBundleExporter.export_douyin_bundle`：忠实复制 contents（必需）与 comments（可选）至 `raw/douyin_contents.jsonl`、`raw/douyin_comments.jsonl`，不补写 `source_keyword`、不转 int/str、不改 `parent_comment_id`、不计算 engagement。
- manifest 记录 platform=douyin、run_id、output_layout、crawler_type、declared keywords、collection_started_at、source contents/comments path、raw content/comment count，缺失值写 null/unavailable。
- `capability` 块明确记录：source_keyword 仅存在于 contents、comments 需在归一化经 `aweme_id` 继承、二级评论 `parent_comment_id` 指向根一级、不存在 reply-to 字段。
- `capability` 块新增四个显式字段：`comments_source_keyword = absent_in_raw`、`comments_source_keyword_derivation = inherit_from_parent_by_aweme_id_in_normalization`、`reply_model = root_and_second_level_only`、`reply_to_reply_chain = unavailable`。
- `_verify_aweme_linkage` 拒绝 contents/comments 无任何 aweme_id 交集（防止跨 run 静默合并）。
- 严格脱敏 fixture 位于 `tests/fixtures/douyin/`：2 条 contents + 1 条一级 + 2 条二级评论；删除 `sec_uid`/`user_signature`/`avatar`/`ip_location`/`cover_url`/`video_download_url`/`music_download_url`/`cookie`/`token`/`verifyFp`/`msToken`/`a_bogus`/`signature` 及 CDN 域名；正文/标题/评论为不可搜索合成文本；ID 保持 `comment.aweme_id == content.aweme_id`、`reply.parent_comment_id == root.comment_id`，不伪造 `reply_id`/`root_comment_id`/`reply_to_comment_id`；递归安全测试禁止 `sec_uid`/`user_signature`/`ip_location`/`byteimg`/`douyinvod`/`cookie`/`token`/`device`/`verifyfp`/`mstoken`/`a_bogus`/`signature`。
- 未新增 `exposure_count`/`duration_ms`/`hashtags`/`challenges`，未修改 DuckDB schema，未实现抖音 MCP 入口、归一化、报告与媒体下载隔离。
- 报告 URL 安全修复（`d9fe28a fix(report): strip sensitive query parameters from evidence URLs`）：`strip_sensitive_url_params` 工具函数从 report_service 和 topic_research 输出的 URL 中移除 `xsec_token`/`token`/`cookie`/`verifyFp`/`msToken`/`a_bogus`/`signature` query 参数，保留稳定页面路径，raw JSONL 不变。summary JSON、Markdown、HTML 三种输出均不含敏感参数。
