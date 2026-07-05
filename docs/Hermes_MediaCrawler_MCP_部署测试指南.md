# Hermes MediaCrawler MCP 部署测试指南

本文用于把当前项目按“桌面真实浏览器采集 + Hermes MCP 数据分析”的主链路部署到 Hermes 测试环境。

默认 MCP Server 只启用 `dataset` profile。`QR/login/cookie/server collection` 相关工具默认不注册、默认不可见，不作为 Hermes 测试主流程。

## 1. 测试目标

验证以下闭环：

```text
Windows 桌面 Chrome CDP 采集
  -> 导出 dataset bundle
  -> 复制到 Hermes 服务器
  -> MCP register_dataset
  -> normalize_dataset
  -> query_dataset
  -> generate_report / get_report
```

验收重点：

- Hermes 默认只能看到稳定 dataset 工具。
- Hermes 默认看不到 `start_qrcode_login`、`get_login_status`、`import_cookies`、`start_collection` 等实验采集工具。
- 已采集数据能被登记、标准化、查询和生成报告。
- 整个测试不依赖服务器扫码登录小红书。

## 2. Windows 桌面采集

### 2.1 启动本地 Chrome CDP

先关闭所有 Chrome：

```powershell
taskkill /F /IM chrome.exe
```

用独立用户目录启动 Chrome：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\chrome-cdp-mediacrawler" `
  --no-first-run `
  --no-default-browser-check
```

如果 Chrome 安装在 x86 路径：

```powershell
& "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\chrome-cdp-mediacrawler" `
  --no-first-run `
  --no-default-browser-check
```

验证 CDP 端口：

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

能看到 `Browser`、`Protocol-Version`、`webSocketDebuggerUrl` 即可。

### 2.2 在 Chrome 中登录小红书

在上一步启动的 Chrome 中打开：

```text
https://www.xiaohongshu.com
```

手动完成扫码、滑块、风险验证等登录步骤。登录成功后不要关闭 Chrome。

### 2.3 运行 MediaCrawler 采集

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe main.py `
  --platform xhs `
  --lt qrcode `
  --type search `
  --keywords "AI编程副业" `
  --crawler_max_notes_count 15 `
  --max_comments_count_singlenotes 10 `
  --save_data_option jsonl `
  --enable_cdp_mode true `
  --cdp_connect_existing true `
  --cdp_debug_port 9222 `
  --headless false
```

预期产物：

```text
data/xhs/jsonl/search_contents_YYYY-MM-DD.jsonl
data/xhs/jsonl/search_comments_YYYY-MM-DD.jsonl
```

## 3. 导出 Dataset Bundle

采集完成后，导出 MCP 标准 bundle：

```powershell
.\.venv\Scripts\python.exe scripts/export_dataset_bundle.py `
  --name "AI编程副业调研" `
  --keyword "AI编程副业" `
  --output-dir data/inbox
```

预期产物：

```text
data/inbox/<dataset_id>/
  dataset.json
  raw/
    xhs_contents.jsonl
    xhs_comments.jsonl
  media/
  logs/
```

可用 Python 快速确认行数和 JSONL 是否正常：

```powershell
@'
import json
from pathlib import Path

bundle = max(Path("data/inbox").iterdir(), key=lambda p: p.stat().st_mtime)
for name in ["xhs_contents.jsonl", "xhs_comments.jsonl"]:
    path = bundle / "raw" / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(name, len(rows))
print(bundle)
'@ | .\.venv\Scripts\python.exe -
```

注意：PowerShell 控制台偶尔会显示中文乱码，这不一定代表文件损坏。以 Python 按 UTF-8 解析结果为准。

## 4. 复制 Bundle 到 Hermes 服务器

示例：

```powershell
scp -r data/inbox/<dataset_id> hermes-user@hermes-host:/data/mediacrawler-inbox/
```

服务器上应能看到：

```text
/data/mediacrawler-inbox/<dataset_id>/dataset.json
/data/mediacrawler-inbox/<dataset_id>/raw/xhs_contents.jsonl
/data/mediacrawler-inbox/<dataset_id>/raw/xhs_comments.jsonl
```

Hermes MCP 调用时必须使用服务器上的绝对路径，不要使用 Windows 本地路径。

## 5. Hermes 服务器 MCP 配置

### 5.1 环境变量

服务器默认只启用 dataset profile：

```bash
export MEDIACRAWLER_MCP_HOME=/data/mediacrawler-mcp
export MEDIACRAWLER_MCP_TOOL_PROFILE=dataset
export MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION=false
```

不需要 Redis。Redis 只用于原项目的缓存/动态代理池测试，不是 MCP dataset 分析主链路依赖。

### 5.2 MCP Server 启动命令

示例：

```bash
/path/to/MediaCrawler/.venv/bin/python \
  /path/to/MediaCrawler/mediacrawler_mcp/server.py \
  --profile dataset
```

### 5.3 Hermes MCP 配置示例

不同 Hermes 部署的配置格式可能略有差异，核心是 stdio command、args 和 env：

```yaml
mcp_servers:
  mediacrawler:
    command: /path/to/MediaCrawler/.venv/bin/python
    args:
      - /path/to/MediaCrawler/mediacrawler_mcp/server.py
      - --profile
      - dataset
    env:
      MEDIACRAWLER_MCP_HOME: /data/mediacrawler-mcp
      MEDIACRAWLER_MCP_TOOL_PROFILE: dataset
      MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION: "false"
    enabled: true
    timeout: 300
    connect_timeout: 60
```

## 6. Hermes 侧测试流程

### 6.1 工具可见性检查

默认应可见：

```text
ping
create_dataset
register_dataset
validate_dataset_bundle
import_raw_files
sync_dataset_manifest
list_datasets
get_dataset
normalize_dataset
query_dataset
generate_report
get_report
```

默认不应可见：

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

如果实验工具可见，说明误启用了 `experimental_collection`，需要检查 Hermes env 或启动参数。

### 6.2 数据集登记

先校验 bundle：

```text
validate_dataset_bundle("/data/mediacrawler-inbox/<dataset_id>")
```

预期：

```json
{
  "status": "success",
  "valid": true,
  "warnings": [],
  "errors": []
}
```

登记数据集：

```text
register_dataset("/data/mediacrawler-inbox/<dataset_id>", "copy")
```

记录返回的 `dataset_id`。

### 6.3 标准化和查询

标准化：

```text
normalize_dataset("<dataset_id>", true)
```

预期返回：

```json
{
  "status": "success",
  "duckdb_path": ".../analysis.duckdb",
  "content_count": 15,
  "comment_count": 150
}
```

查询评论：

```text
query_dataset("<dataset_id>", "风险", "comments", "xhs", null, 10, "like_count")
```

也可以查询内容：

```text
query_dataset("<dataset_id>", "AI", "contents", "xhs", null, 10, "engagement_count")
```

### 6.4 生成报告

```text
generate_report("<dataset_id>", "topic_research", 20)
get_report("<dataset_id>")
```

预期返回：

```text
reports/report.md
reports/report.html
reports/summary.json
```

Hermes/飞书回复中可以引用报告路径或读取摘要内容。

## 7. 常见问题

### 7.1 Chrome CDP 端口打不开

检查：

- 是否关闭了所有旧 Chrome 进程。
- 是否使用了独立 `--user-data-dir`。
- 是否端口被占用。
- `Invoke-RestMethod http://127.0.0.1:9222/json/version` 是否能返回 JSON。

### 7.2 采集文件为空

检查：

- 小红书是否在 CDP Chrome 中已经登录。
- 是否触发了滑块/风险验证。
- `--keywords` 是否正确。
- `--crawler_max_notes_count` 是否过小。
- 控制台日志是否出现登录失效或接口失败。

### 7.3 Bundle 校验失败

常见原因：

- 没有先运行 `scripts/export_dataset_bundle.py`。
- bundle 路径不是服务器上的绝对路径。
- `raw/xhs_contents.jsonl` 和 `raw/xhs_comments.jsonl` 缺失。
- JSONL 文件被手工编辑后格式损坏。

### 7.4 Hermes 看到了 QR/login/start_collection 工具

这不是默认主链路。检查：

```bash
echo $MEDIACRAWLER_MCP_TOOL_PROFILE
echo $MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION
```

应为：

```text
dataset
false
```

并确认启动参数没有传：

```text
--enable-experimental-collection
```

### 7.5 Redis 连接失败

对本 MCP dataset 测试可忽略。Redis 只影响原项目的 Redis cache 和动态代理池测试，不影响：

- `register_dataset`
- `normalize_dataset`
- `query_dataset`
- `generate_report`

## 8. 推荐验收口径

一次部署测试通过的标准：

- Windows 桌面采集得到 `contents > 0`、`comments >= 0`。
- 导出的 bundle 通过 `validate_dataset_bundle`。
- Hermes 默认工具列表不包含实验采集工具。
- `register_dataset` 返回成功。
- `normalize_dataset` 生成 `analysis.duckdb`。
- `query_dataset` 能返回内容或评论。
- `generate_report` 生成 markdown/html/json 报告。
