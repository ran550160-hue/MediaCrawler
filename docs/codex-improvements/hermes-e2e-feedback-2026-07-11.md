# MediaCrawler MCP — Hermes 端到端使用反馈与改进建议

> 本文档记录 2026-07-11 通过 Hermes Agent 使用 `mediacrawler_desktop` MCP 工具爬取小红书热点的完整过程、发现的问题及改进建议。供 Codex 逐条认领优化。

---

## 一、背景

在 Hermes（WSL）中，通过 `mediacrawler_desktop` MCP profile 调用了以下工具链：

```
check_local_workbench
  → start_local_xhs_search(keywords=["热点","热门","热榜"], max_contents=20, max_comments_per_content=10)
    → get_local_xhs_search_status（轮询 150s）
      → finalize_local_xhs_search(normalize=true, generate_report=true)
```

最终产出数据集 `ds_20260711_132420_2026_07_11`：20 篇内容 + 200 条评论。

在此过程中观察到以下问题，按严重程度排序。

---

## 二、环境信息

| 项目 | 值 |
|------|-----|
| 项目路径 | `D:\WorkSpace\MediaCrawler`（WSL: `/mnt/d/WorkSpace/MediaCrawler`） |
| WSL venv | `/home/wangran/.mediacrawler-venv`（Python 3.12） |
| MCP profile | `desktop_agent`（23 tools） |
| Windows API | `http://127.0.0.1:8080`（uvicorn via `uv run`） |
| CDP 浏览器 | Chrome 150, port 9222, headless=false |
| 测试关键词 | `["热点", "热门", "热榜"]` |
| 搜索参数 | `max_contents=20, max_comments_per_content=10, timeout_seconds=600` |
| 数据集 ID | `ds_20260711_132420_2026_07_11` |

---

## 三、问题与改进建议

### 问题 1：like_count 为 0 但其他互动指标正常

**现象：**

`finalize_local_xhs_search` 生成的 report 中，`interaction_warnings` 板块标记了 5 篇内容 `like_count=0`，但它们的 collect/comment/share 合计均过万：

| content_id | title | like_count | collect_count | comment_count | share_count | 互动总量 |
|---|---|---|---|---|---|---|
| 6a3cee8a... | 深圳…你又开始癫了是吧！！ | 0 | 4,965 | 7,707 | 62,000 | 74,672 |
| 670df0cc... | 在日本图书馆看到这个我真的笑出了声 | 0 | 14,000 | 12,000 | 18,000 | 44,000 |
| 6a4fab50... | 你是我目前为止 见过最尊重台风之人！ | 0 | 8,216 | 4,457 | 4,805 | 17,478 |
| 6a3b9d03... | 男子到店连喝八杯水... | 0 | 11,000 | 954 | 4,377 | 16,331 |
| 6a27f818... | 深圳万象天地也太超前了 | 0 | 8,735 | 1,284 | 2,022 | 12,041 |

这明显不是数据缺失——点赞数大概率存在于 raw_json 的嵌套字段中（如 `interact_info.liked_count`、`note.interact_info`），但 normalizer 只在顶层取值，未做深度提取。

**建议：**

在 normalizer 中增加 like_count 的多路径提取逻辑。按优先级尝试以下字段路径：

```
1. raw_json.interact_info.liked_count
2. raw_json.note.interact_info.liked_count  
3. raw_json.note_card.interact_info.liked_count
4. raw_json.liked_count  （当前只试了这条）
```

取到第一个非空值为止。如果所有路径都为空，再保留 0 并触发 warning。

**验证方法：**

拿 `6a3cee8a000000002103ef1e` 的 raw_json 做对照——如果其中某处包含了真实 like_count，修复后该值应被正确提取。

---

### 问题 2：评论 jieba 分词未生效

**现象：**

report 中的关键词分布（`top_keywords`）显示的是"小红书"、"运营"、"网站"等整体词条，但 `comment_word_freq` 把整段评论当成一个 token，没有经过 jieba 分词。例如一篇高赞评论 "之前看到的这个给我笑死" 应该被分成 "之前/看到/这个/给/我/笑死"，而不是作为一个整体出现。

**建议：**

检查 normalizer 中 jieba 分词的调用位置和前置处理：

1. 确认 `jieba.cut(text)` 是否被正确调用，还是词频统计绕过了分词
2. 检查文本预处理——如果先做了去除空格/标点的操作导致句子粘连（如 "之前看到的这个给我笑死"），jieba 会把整串当成一个词。应保留必要分隔符
3. 加入停用词过滤（"的"、"了"、"是"等），提升关键词可读性

**验证方法：**

对任意数据集运行 `normalize_dataset(id, force=true)` 后，检查 report 中 `comment_word_freq` 是否出现了真正的分词结果（如 "笑死"、"马龙" 等独立词），而非整句。

---

### 问题 3：cancelled 任务 finalize 返回 HTTP 409

**现象：**

当采集任务被 `cancel_local_xhs_search` 取消后，调用 `finalize_local_xhs_search` 返回 HTTP 409，提示任务状态不允许 finalize。

**背景：**

`cancel` 后数据文件（JSONL）仍然保留，已采集的内容不会丢失。但目前用户必须手动走绕行路径：

```
cancel → create_dataset → import_raw_files → normalize_dataset → query_dataset
```

**建议：**

在 `finalize_local_xhs_search` 内部增加降级逻辑：

```python
if task_status == "cancelled" and jsonl_files_exist(data_root):
    # 自动降级到 import 路径
    dataset = create_dataset(name, platforms, keywords)
    import_raw_files(dataset_id, platform, contents_path=..., comments_path=...)
    normalize_dataset(dataset_id)
    generate_report(dataset_id)
    return success_response
```

这样对调用方透明——Hermes agent 无需感知 cancel 后的特殊处理。

---

### 问题 4：start_local_xhs_search 缺少搜索排序参数

**现象：**

目前搜索关键词语法固定，无法指定排序方式。小红书搜索结果页支持：
- 综合（默认）
- 最热
- 最新

不同场景需要不同排序——"最近热点"希望按最新排序，"热门话题"希望按最热排序。

**建议：**

在 `start_local_xhs_search` 增加 `sort_by` 参数：

```python
def start_local_xhs_search(
    keywords: list[str],
    sort_by: str = "general",  # "general" | "hot" | "latest"
    ...
)
```

对应 CDP 操作：在搜索后点击对应的排序标签。

---

### 问题 5：缺少自动发现热点的工具

**现象：**

用户说"爬一下最近热点"，Hermes agent 只能手动猜关键词（"热点"、"热门"、"热榜"），覆盖不全面。小红书有热榜/发现页/推荐流，如果能自动抓取这些页面的内容，用户体验会好很多。

**建议：**

新增 `discover_xhs_hot_topics` 工具：

```python
def discover_xhs_hot_topics(
    source: str = "hot_board",  # "hot_board" | "discover" | "recommend"
    max_count: int = 20,
    ...
) -> dict:
    """
    Returns list of trending topics with note_id, title, hot_score.
    """
```

该工具只返回热点列表（不爬详情和评论），用户确认后再用 `start_local_xhs_search` 针对感兴趣的话题深度采集。

**实现路径：** 小红书 Web 版热榜页面有 topic 卡片列表，CDP 提取即可，无需登录态。

---

### 问题 6：comments.source_keyword 为 NULL

**现象：**

原始评论 JSONL 不含 source_keyword 字段，normalizer 也未从关联 content 回填。导致按 `source_keyword` 过滤评论时会漏掉大量数据。

**建议：**

在 normalizer 中增加回填逻辑：对每条 comment，用 `content_id` JOIN contents 表，将 `content.source_keyword` 写入 `comment.source_keyword`。在 force re-normalize 时覆盖已有非空值。

---

### 问题 7：keywords 参数只接受 list

**现象：**

```python
# 只能用 list
start_local_xhs_search(keywords=["热点"])
# 不能直接用 string
start_local_xhs_search(keywords="热点")  # 报错
```

对单关键词场景不友好。

**建议：**

在工具入口做参数兼容：

```python
if isinstance(keywords, str):
    keywords = [keywords]
```

改动成本极低。

---

### 问题 8：report 未利用 LLM 生成自然语言洞察

**现象：**

当前 report 是模板化数据罗列：表格 + 词频 + warning。对非技术用户（如运营人员），期望看到的是"最近深圳天气话题热度飙升"、"711起诉耐克引发法律话题讨论"这样的自然语言总结。

**建议：**

在 `generate_report` 中增加 `ai_insights` 选项，当开启时，将 top contents/comments 的高层次数据传给 LLM 做摘要：
- 热点趋势归纳（什么话题在火、为什么）
- 情感倾向（正面/负面/中性占比）
- 舆情预警（争议性内容、负面情绪集中话题）

数据集规模小（20 篇）时可在工具内直接调用 LLM；规模大时考虑先做聚类再摘要。

---

### 问题 9：publish_time 格式不适合作时间序列分析

**现象：**

contents 表中 `publish_time` 是毫秒时间戳字符串（如 `"1783747360662"`），排序可工作但做日期过滤（如"最近 7 天"）需要在查询层手动计算，且 `>`, `<` 对比字符串语义不直观。

**建议：**

在 normalizer 中同时产出 `publish_datetime TIMESTAMP` 列，保留原 `publish_time TEXT` 向后兼容：

```python
publish_datetime = datetime.fromtimestamp(int(publish_time) / 1000)
```

query_dataset 的 `sort_by` 增加 `publish_datetime` 选项。

---

### 问题 10：子评论未展开到 comments 表

**现象：**

`start_local_xhs_search` 的 `include_sub_comments` 参数默认为 false。即使打开，子评论也作为嵌套 JSON 保留在 raw_json 中，comments 表只展平了顶层评论，无法被 `query_dataset` 全文搜索命中。

**建议：**

在 normalizer 中支持子评论递归展开：

```python
for sub in raw_json.get("sub_comment_list", []):
    comments_table.append({
        "comment_id": sub["id"],
        "content_id": note_id,
        "parent_comment_id": parent_id,  # 非 "0"
        "comment_text": sub["content"],
        "source_keyword": source_keyword,  # 从父级继承
        ...
    })
```

---

## 四、改进优先级汇总

| 优先级 | 编号 | 问题 | 改动位置 |
|--------|------|------|----------|
| 🔴 高 | #1 | like_count 深层提取 | normalizer |
| 🔴 高 | #2 | jieba 分词失效 | normalizer/report |
| 🔴 高 | #3 | cancel 后 finalize 降级 | server.py / desktop_agent_client |
| 🟡 中 | #4 | 搜索排序参数 | start_local_xhs_search |
| 🟡 中 | #5 | 自动发现热点工具 | 新工具 |
| 🟡 中 | #6 | source_keyword 回填 | normalizer |
| 🟢 低 | #7 | 关键词兼容 string | 工具入口 |
| 🟢 低 | #8 | LLM 生成报告洞察 | report_service |
| 🟢 低 | #9 | publish_datetime 列 | normalizer |
| 🟢 低 | #10 | 子评论展开 | normalizer |
