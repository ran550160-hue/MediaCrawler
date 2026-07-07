# MediaCrawler MCP MVP 验收记录

## 1. 结论

本次 MVP 验收通过。

通过范围：

- `dataset` profile 主链路通过。
- 离线 fixture bundle smoke 通过。
- 真实桌面导出 bundle smoke 通过。
- Hermes/WSL 下 dataset 工具可见性通过。
- QR/login/cookie/server collection 实验采集工具默认隐藏通过。
- DuckDB、Markdown report、HTML report、summary JSON 输出文件均生成成功。
- 验收结束后 git 工作区干净，无 CRLF/LF 伪修改，无遗留 `uv.lock` 修改。

本次验收不覆盖服务器侧扫码登录、不覆盖小红书实时采集、不覆盖 `experimental_collection` profile。MVP 主价值被定义为：Hermes 分析已经采集好的数据，而不是由 Hermes 在服务器上直接登录和实时爬取。

## 2. 代码版本

| 项目 | 值 |
| --- | --- |
| branch | `codex/analysis-module` |
| commit | `82e4057bfe95e78c949d512072fc3f76fa8186ac` |
| git status | 干净 |

实际检查命令：

```bash
git status --short --branch
```

结果：

```text
codex/analysis-module...origin/codex/analysis-module
```

## 3. 验收环境

| 项目 | 值 |
| --- | --- |
| WSL 发行版 | `Ubuntu-24.04` |
| OS | `Ubuntu 24.04.4 LTS (Noble Numbat)` |
| 系统 Python | `/usr/bin/python3`, `Python 3.12.3` |
| uv | `/home/wangran/.local/bin/uv`, `uv 0.11.14` |
| repo 路径 | `/mnt/d/WorkSpace/MediaCrawler` |
| Hermes MCP home | `/home/wangran/.mediacrawler-mcp` |
| smoke 临时 home | `/tmp/mediacrawler-mcp-smoke`, `/tmp/mediacrawler-mcp-real-bundle` |

注意事项：

- 当前 shell 中没有 `python` 命令，只有 `python3`。
- 项目 `.python-version` 是 `3.11`。
- 本次成功 smoke 复用了 WSL 原生 venv：`/home/wangran/.mediacrawler-venv`，该 venv Python 版本为 `3.12.3`。
- 直接 `uv run` 曾使用 uv-managed Python `3.11.15` 创建 `.venv`，但依赖同步未完成。

Hermes MCP 配置片段：

```yaml
mcp_servers:
  mediacrawler:
    command: /home/wangran/.mediacrawler-venv/bin/python
    args:
      - /mnt/d/WorkSpace/MediaCrawler/mediacrawler_mcp/server.py
      - --profile
      - dataset
    env:
      MEDIACRAWLER_MCP_HOME: /home/wangran/.mediacrawler-mcp
      MEDIACRAWLER_MCP_TOOL_PROFILE: dataset
      MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION: "false"
      PYTHONPATH: /mnt/d/WorkSpace/MediaCrawler
```

## 4. 执行命令

### 4.1 初始命令

```bash
cd /mnt/d/WorkSpace/MediaCrawler
git status --short --branch
git pull

uv run python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-smoke
```

该直接命令遇到依赖下载问题，详见“已知问题与规避方式”。

### 4.2 离线 Fixture Smoke

实际成功命令：

```bash
cd /mnt/d/WorkSpace/MediaCrawler

env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
  UV_PROJECT_ENVIRONMENT=/home/wangran/.mediacrawler-venv \
  uv run --no-sync python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-smoke
```

### 4.3 真实 Bundle Smoke

真实 bundle 路径：

```text
/mnt/d/WorkSpace/MediaCrawler/data/inbox/ds_20260705_223621_ai
```

实际成功命令：

```bash
cd /mnt/d/WorkSpace/MediaCrawler

env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
  UV_PROJECT_ENVIRONMENT=/home/wangran/.mediacrawler-venv \
  uv run --no-sync python scripts/hermes_mcp_smoke.py \
  --home /tmp/mediacrawler-mcp-real-bundle \
  --dataset-dir /mnt/d/WorkSpace/MediaCrawler/data/inbox/ds_20260705_223621_ai
```

因为已有真实 bundle，本次没有执行 raw files smoke。

## 5. Smoke 输出摘要

### 5.1 离线 Fixture Smoke

| 字段 | 值 |
| --- | --- |
| `status` | `success` |
| `mode` | `fixture_bundle` |
| `profile.experimental_tools_hidden` | `true` |
| `counts.contents` | `2` |
| `counts.comments` | `3` |
| `counts.query_results` | `3` |
| `outputs.report_md_path` | `/tmp/mediacrawler-mcp-smoke/datasets/hermes_smoke_20260706_135723_12303/reports/report.md` |
| `outputs.summary_json_path` | `/tmp/mediacrawler-mcp-smoke/datasets/hermes_smoke_20260706_135723_12303/reports/summary.json` |

### 5.2 真实 Bundle Smoke

| 字段 | 值 |
| --- | --- |
| `status` | `success` |
| `mode` | `bundle` |
| `profile.experimental_tools_hidden` | `true` |
| `counts.contents` | `15` |
| `counts.comments` | `150` |
| `counts.query_results` | `5` |
| `outputs.report_md_path` | `/tmp/mediacrawler-mcp-real-bundle/datasets/ds_20260705_223621_ai/reports/report.md` |
| `outputs.summary_json_path` | `/tmp/mediacrawler-mcp-real-bundle/datasets/ds_20260705_223621_ai/reports/summary.json` |

## 6. MCP 工具可见性

默认 `dataset` profile 下，Hermes 可见以下 12 个工具：

```text
create_dataset
generate_report
get_dataset
get_report
import_raw_files
list_datasets
normalize_dataset
ping
query_dataset
register_dataset
sync_dataset_manifest
validate_dataset_bundle
```

以下实验采集工具默认不可见，且本次验收没有调用：

```text
get_login_status
import_cookies
start_qrcode_login
get_qrcode_login_status
cancel_qrcode_login
start_collection
get_task_status
cancel_task
```

验收证据：

```text
profile.experimental_tools_hidden = true
```

## 7. 已知问题与规避方式

### 7.1 WSL 代理导致 TUNA 下载 403

现象：

- 直接 `uv run` 时，TUNA PyPI 访问 `tzdata` wheel 返回 `403`。
- WSL 环境继承了代理变量，例如 `HTTP_PROXY`、`HTTPS_PROXY`、`all_proxy` 指向 `127.0.0.1:57189`。
- 清理代理变量后，同一个 TUNA `tzdata` URL 返回 `200`。

本次规避：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
  ...
```

### 7.2 直接同步完整依赖耗时过长

现象：

- 切换 `UV_DEFAULT_INDEX=https://pypi.org/simple` 后，uv 尝试重建/同步完整项目依赖。
- 下载 `opencv-python`、`playwright`、`duckdb`、`numpy`、`pandas` 等大包超过 600 秒。
- 后台下载进程已手动 kill。

本次规避：

```bash
UV_PROJECT_ENVIRONMENT=/home/wangran/.mediacrawler-venv \
uv run --no-sync python scripts/hermes_mcp_smoke.py ...
```

### 7.3 uv.lock registry URL 曾被改写

现象：

- 使用 `UV_DEFAULT_INDEX=https://pypi.org/simple` 后，`uv.lock` registry URL 被改写成 `pypi.org/files.pythonhosted.org`，产生大量非业务 diff。

处理：

```bash
git restore -- uv.lock
```

最终无遗留 `uv.lock` 修改。

### 7.4 Python 版本提示

现象：

- 项目 `.python-version` 是 `3.11`。
- 本次复用的 `/home/wangran/.mediacrawler-venv` 是 Python `3.12.3`。
- `uv run --no-sync` 会打印 incompatible environment warning。

结论：

- warning 不影响本次 dataset profile smoke 执行结果。
- 后续生产部署仍建议固定 Python 版本和 venv 构建流程。

## 8. 后续建议

优先级最高：

- 拆分 dataset profile 依赖和 crawler/experimental 依赖。
- 让 Hermes/WSL 跑 dataset smoke 时不需要拉取 `opencv-python`、`playwright` 等重依赖。
- 固化一套 Hermes 可复用的 Python venv 构建命令，避免依赖同步依赖临时 workaround。

下一阶段产品优化：

- 优化报告模板，使 Hermes 输出更适合飞书阅读。
- 增加评论主题聚类、诉求提取、爆款笔记对比等分析能力。
- 为真实 bundle smoke 增加可选的业务断言，例如最小内容数、最小评论数、指定关键词命中数。
