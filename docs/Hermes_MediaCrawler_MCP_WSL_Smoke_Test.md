# Hermes WSL Smoke Test 指南

本文用于把 Hermes/WSL 侧测试固化成一个可重复验收流程。目标不是测试小红书登录，也不是测试服务器实时爬取，而是确认 MCP 的主价值链路可用：

```text
桌面采集数据
  -> 导入数据集
  -> normalize_dataset
  -> query_dataset
  -> generate_report / get_report
```

Smoke test 默认只验证 `dataset` profile。QR、login、cookie、server collection 相关工具不应出现在默认工具列表里。

## 1. 适用场景

建议在以下时机运行：

- Hermes WSL 环境首次接入 MediaCrawler MCP。
- 每次拉取新代码或切换分支后。
- 桌面端导出新的 dataset bundle 后。
- 发现 Hermes 查询或报告结果异常时，用来定位是导入问题、标准化问题还是报告生成问题。

脚本不会启动 Playwright，不会打开浏览器，不会扫码登录小红书。

## 2. WSL 环境准备

进入项目目录，例如：

```bash
cd /mnt/d/WorkSpace/MediaCrawler
```

推荐使用 `uv`：

```bash
uv sync
```

如果不用 `uv`，用 WSL 自己的 Linux venv，不要复用 Windows `.venv`：

```bash
python3.11 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install -r requirements.txt
```

确认关键依赖：

```bash
uv run python -c "import mcp, duckdb, pandas; print('ok')"
```

## 3. 离线 Fixture 验收

这是最小自检，不依赖真实采集数据：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-smoke
```

脚本会自动创建一个小型 dataset bundle，然后执行：

```text
mcp.list_tools
ping
validate_dataset_bundle
register_dataset
list_datasets
get_dataset
normalize_dataset
query_dataset
generate_report
get_report
```

成功输出里应看到：

```json
{
  "status": "success",
  "mode": "fixture_bundle",
  "profile": {
    "experimental_tools_hidden": true
  },
  "counts": {
    "contents": 2,
    "comments": 3,
    "query_results": 3
  }
}
```

说明：

- `comments` 为 3 是因为 fixture 里包含 2 条顶层评论和 1 条 `sub_comments` 子评论。
- `experimental_tools_hidden: true` 表示默认 dataset profile 没有暴露 QR/login/start_collection。
- 不传 `--query` 时，脚本会用空查询读取样本结果，适合通用 smoke。

如果想确认文本过滤也能工作：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-smoke-filter \
  --query feedback
```

## 4. 真实 Bundle 验收

桌面端导出 bundle 后，在 WSL 中直接指向该目录：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-real-bundle \
  --dataset-dir /mnt/d/WorkSpace/MediaCrawler/data/inbox/<dataset_id>
```

如果想保留源目录、不复制到 MCP home：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-real-bundle \
  --dataset-dir /mnt/d/WorkSpace/MediaCrawler/data/inbox/<dataset_id> \
  --import-mode link
```

真实数据的 `counts.contents` 应大于 0；`counts.comments` 可以为 0，但如果你本次采集启用了评论，应该大于 0。

可以传入业务关键词测试查询：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-real-bundle \
  --dataset-dir /mnt/d/WorkSpace/MediaCrawler/data/inbox/<dataset_id> \
  --query "风险" \
  --target comments
```

## 5. Raw Files 验收

如果暂时没有 bundle，只想验证 `create_dataset + import_raw_files` 路径：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-raw \
  --name "AI编程副业调研" \
  --keyword "AI编程副业" \
  --contents /mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/search_contents_YYYY-MM-DD.jsonl \
  --comments /mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/search_comments_YYYY-MM-DD.jsonl
```

该模式会执行：

```text
create_dataset
import_raw_files
sync_dataset_manifest
normalize_dataset
query_dataset
generate_report
get_report
```

如果没有评论文件，可以只传 `--contents`：

```bash
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-raw \
  --contents /mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/search_contents_YYYY-MM-DD.jsonl \
  --target contents
```

## 6. 检查 Hermes Env 是否误开实验工具

默认脚本会强制使用 dataset profile，适合稳定验收。如果想检查当前 shell 环境是否误启用了实验采集工具，使用：

```bash
MEDIACRAWLER_MCP_TOOL_PROFILE=dataset \
MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION=false \
uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-smoke-env \
  --respect-profile-env
```

如果输出失败并提示 `visible_experimental_tools`，说明环境变量或 Hermes 启动参数误开了实验工具。

## 7. 通过标准

一次 smoke 通过应满足：

- `status` 为 `success`。
- `profile.experimental_tools_hidden` 为 `true`。
- `steps` 包含 normalize、query、generate_report、get_report。
- `outputs.duckdb_path`、`report_md_path`、`report_html_path`、`summary_json_path` 对应文件存在。
- 真实数据集下 `counts.contents > 0`。
- 如果本次采集包含评论，`counts.comments > 0`。

## 8. 常见问题

### 8.1 Windows `.venv` 在 WSL 不能用

WSL 里需要 Linux venv 或 `uv run`。不要执行 `/mnt/d/.../.venv/Scripts/python.exe`。

### 8.2 `Dataset already exists in MCP home`

使用新的 `--home`，或把 `--import-mode` 改成 `link`。Smoke 测试建议使用 `/tmp/mediacrawler-mcp-<name>` 这类临时目录。

### 8.3 `validate_dataset_bundle` 失败

检查目录结构：

```text
<dataset_id>/
  dataset.json
  raw/
    xhs_contents.jsonl
    xhs_comments.jsonl
```

至少需要 `raw/xhs_contents.jsonl` 或 `raw/xhs_comments.jsonl` 其中一个存在，并且每一行都是合法 JSON 对象。

### 8.4 查询结果为 0

不传 `--query` 会使用空查询，更适合通用验收。真实业务关键词可能确实匹配不到评论，可以改用：

```bash
--target contents --query "你的关键词"
```

### 8.5 Hermes 里仍看到 QR/login/start_collection

检查 Hermes MCP 配置：

```bash
MEDIACRAWLER_MCP_TOOL_PROFILE=dataset
MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION=false
```

启动参数也不要传：

```text
--enable-experimental-collection
```
