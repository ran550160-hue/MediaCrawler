# Hermes MediaCrawler MCP 集成验证

本文档对应 Sprint 6，目标是把 `mediacrawler_mcp` 以 stdio MCP server 的方式接入 Hermes，并验证 Hermes 能发现、调用工具，以及能把报告路径回传到飞书。

## 1. 前置条件

- Hermes 服务器已安装 MCP Python SDK，当前已确认 `mcp==1.26.0` 可用。
- MediaCrawler 项目依赖已安装，且当前虚拟环境可导入：

```bash
uv run python -c "from mcp.server.fastmcp import FastMCP; import duckdb; print('ok')"
```

- MCP server 不向 stdout 写普通日志。普通日志写入：

```text
<MEDIACRAWLER_MCP_HOME>/logs/server.log
```

## 2. 推荐部署目录

建议在 Hermes 服务器上固定项目目录和 MCP home：

```text
/opt/mediacrawler/MediaCrawler
/data/mediacrawler-mcp
```

其中：

- `/opt/mediacrawler/MediaCrawler`：代码目录。
- `/data/mediacrawler-mcp`：数据集、SQLite 元数据、账号 cookie、日志和报告目录。

## 3. Hermes 配置样例

Hermes 配置路径：

```text
/root/.hermes/config.yaml
```

配置示例：

```yaml
mcp_servers:
  mediacrawler:
    command: /opt/mediacrawler/MediaCrawler/.venv/bin/python
    args:
      - /opt/mediacrawler/MediaCrawler/mediacrawler_mcp/server.py
    enabled: true
    timeout: 300
    connect_timeout: 60
    env:
      MEDIACRAWLER_MCP_HOME: /data/mediacrawler-mcp
      PYTHONPATH: /opt/mediacrawler/MediaCrawler
```

`server.py` 会自动把 repo root 注入 `sys.path`，因此缺少 `PYTHONPATH` 时也可以启动；但部署配置中仍建议显式设置 `PYTHONPATH`，方便后续脚本、测试和 Hermes 子进程行为保持一致。

如果使用 Hermes CLI，可参考：

```bash
hermes mcp add mediacrawler \
  --command /opt/mediacrawler/MediaCrawler/.venv/bin/python \
  --args /opt/mediacrawler/MediaCrawler/mediacrawler_mcp/server.py
```

添加后需要开启新会话，或重启 Hermes gateway，确保新工具被注入。

## 4. 本地 smoke 验证

在正式接 Hermes 前，先在 MediaCrawler 项目目录内运行离线 smoke：

```bash
uv run python scripts/hermes_mcp_smoke.py
```

该脚本不会触发真实爬虫，不需要小红书登录。它会：

- 使用临时 `MEDIACRAWLER_MCP_HOME`。
- 调用 MCP tool 函数创建数据集。
- 写入小型 fixture raw JSONL。
- 调用 `normalize_dataset` 写入 DuckDB。
- 调用 `query_dataset` 查询评论。
- 调用 `generate_report` 和 `get_report` 生成并读取报告。

成功时输出类似：

```json
{
  "status": "success",
  "dataset_id": "ds_...",
  "tool_name_hint": [
    "mcp_mediacrawler_create_dataset",
    "mcp_mediacrawler_query_dataset",
    "mcp_mediacrawler_generate_report"
  ],
  "outputs": {
    "duckdb_path": ".../analysis.duckdb",
    "report_md_path": ".../reports/report.md",
    "report_html_path": ".../reports/report.html",
    "summary_json_path": ".../reports/summary.json"
  }
}
```

也可以指定固定 home：

```bash
uv run python scripts/hermes_mcp_smoke.py --home /tmp/mediacrawler-mcp-smoke
```

## 5. Hermes 会话内工具验证

Hermes 侧工具名通常会变成：

```text
mcp_mediacrawler_ping
mcp_mediacrawler_create_dataset
mcp_mediacrawler_list_datasets
mcp_mediacrawler_get_dataset
mcp_mediacrawler_get_login_status
mcp_mediacrawler_import_cookies
mcp_mediacrawler_start_qrcode_login
mcp_mediacrawler_get_qrcode_login_status
mcp_mediacrawler_cancel_qrcode_login
mcp_mediacrawler_start_collection
mcp_mediacrawler_get_task_status
mcp_mediacrawler_cancel_task
mcp_mediacrawler_normalize_dataset
mcp_mediacrawler_query_dataset
mcp_mediacrawler_generate_report
mcp_mediacrawler_get_report
```

建议在 Hermes 新会话中按以下顺序测试：

1. 调用 `mcp_mediacrawler_ping`，确认连接可用。
2. 调用 `mcp_mediacrawler_create_dataset`，参数：

```json
{
  "name": "Hermes 集成验证",
  "platforms": ["xhs"],
  "keywords": ["AI编程副业"],
  "description": "Hermes MCP smoke dataset"
}
```

3. 调用 `mcp_mediacrawler_list_datasets`，确认能看到刚创建的数据集。
4. 调用 `mcp_mediacrawler_get_dataset`，确认返回 `dataset_dir` 和 `dataset_json_path`。
5. 对已有 raw 数据集调用 `mcp_mediacrawler_normalize_dataset`。
6. 调用 `mcp_mediacrawler_query_dataset`。
7. 调用 `mcp_mediacrawler_generate_report`。
8. 调用 `mcp_mediacrawler_get_report`。

注意：`start_collection` 会检查小红书登录态。服务器未登录时，预期返回：

```json
{
  "status": "need_login",
  "error": {
    "code": "LOGIN_REQUIRED"
  }
}
```

这是正常行为，不应视为 MCP 接入失败。

## 6. 登录与浏览器模式现状

当前 Sprint 6 推荐优先使用 cookie 模式完成服务器采集：

1. 在浏览器中导出小红书 cookie。
2. 调用 `mcp_mediacrawler_import_cookies`，导入包含 `web_session` 的完整 cookie string。
3. 调用 `mcp_mediacrawler_get_login_status`。如果要确认 cookie 是否仍被小红书远端认可，可传 `verify_remote: true`。
4. 再调用 `mcp_mediacrawler_start_collection`。

导入 cookie 后，MCP 会用以下方式启动现有 MediaCrawler：

```bash
python main.py --platform xhs --lt cookie --cookies "<cookie_string>" ...
```

MCP 默认 `MEDIACRAWLER_MCP_BROWSER_MODE=persistent_context`，采集命令会显式传入：

```bash
--enable_cdp_mode false --cdp_connect_existing false
```

因此服务器 cookie 模式不需要手工修改 `config/base_config.py`。

如果将 `MEDIACRAWLER_MCP_BROWSER_MODE` 设置为 `cdp` 或 `cdp_existing`，MCP 会显式打开 CDP 并尝试连接已有浏览器；如果设置为 `cdp_launch`，则打开 CDP 但不强制连接已有浏览器。`MEDIACRAWLER_MCP_CDP_ENDPOINT` 当前仍属于预留项，尚未映射到现有 MediaCrawler 的 CDP 端口配置。

如后续要走 CDP，需要单独实现并验证：

- MCP 配置如何映射到 MediaCrawler 的 `ENABLE_CDP_MODE`、`CDP_DEBUG_PORT`、`CDP_CONNECT_EXISTING`。
- 服务器上是否存在可访问的 Chrome CDP 端口。
- 登录态 profile/cookie/localStorage 是否能被实际采集进程复用。

如果走 persistent browser profile，也需要让 `LoginManager` 检测到的 profile 与 `CrawlerRunner` 实际启动爬虫使用的 profile 保持一致。

小红书搜索接口一页通常返回 20 条。若 Hermes 调用 `start_collection(max_contents=1/3/5)`，当前 MCP 会把数量限制前移到小红书 detail 请求构造前，避免对整页 20 条内容发起 detail 请求；raw 归档阶段仍会做兜底裁剪。

### 二维码登录流程

如果 cookie 过期或不方便手动复制 cookie，可以让 Hermes 走二维码登录：

1. 调用 `mcp_mediacrawler_start_qrcode_login`。
2. MCP 返回：

```json
{
  "status": "waiting_scan",
  "login_task_id": "task_qrcode_login_xhs_...",
  "qr_image_path": "/data/mediacrawler-mcp/login_qrcodes/task_qrcode_login_xhs_....png",
  "qr_ready": true,
  "qr_image_exists": true,
  "expires_at": "..."
}
```

3. 只有当 `status == "waiting_scan"` 且 `qr_ready == true` 时，Hermes 才读取 `qr_image_path` 并上传到飞书，让用户扫码。
4. Hermes 轮询 `mcp_mediacrawler_get_qrcode_login_status(login_task_id)`。
5. 返回 `status=success` 后，MCP 已远程验证并保存 cookie，并且 Playwright persistent profile 已落到 `browser_data/xhs_user_data_dir`。建议再调用 `mcp_mediacrawler_get_login_status(verify_remote=true)` 做最终确认。
6. 用户取消或二维码过期时，调用 `mcp_mediacrawler_cancel_qrcode_login(login_task_id)` 或重新发起二维码登录。

二维码登录和小红书采集共用同一把 XHS browser profile 文件锁。同一时间只允许一个 XHS collection 或 QR login，避免多个 Playwright 进程抢占 `browser_data/xhs_user_data_dir` 导致 profile lock 或 launch timeout。不要在 MCP 任务运行时手工启动另一个直接使用同一 XHS profile 的 `main.py`。

更新 QR login 或 collection 相关代码后，需要在 Hermes/Gateway 侧执行 `/reload-mcp` 或重启进程，确保加载到最新 MCP server。

## 7. 飞书结果路径验证

当前 MCP 报告工具返回本地服务器路径：

- `report_md_path`
- `report_html_path`
- `summary_json_path`

Hermes 接飞书时，推荐第一版直接回复报告摘要和服务器路径，例如：

```text
报告已生成：
Markdown: /data/mediacrawler-mcp/datasets/ds_xxx/reports/report.md
HTML: /data/mediacrawler-mcp/datasets/ds_xxx/reports/report.html
摘要: /data/mediacrawler-mcp/datasets/ds_xxx/reports/summary.json
```

如果飞书侧不能直接打开服务器本地路径，Hermes 后续需要补一层文件读取或上传能力。MediaCrawler MCP 第一版只负责生成文件和返回路径，不直接依赖飞书 SDK。

## 8. 图片路径预验证

Sprint 7 会做二维码 PNG 输出。Sprint 6 先确认 Hermes/飞书是否能处理本地图片路径：

- 如果 Hermes 已有文件上传 skill 或飞书图片上传能力，验证能上传本地 PNG。
- 如果只能发送文本，则二维码阶段需要 Hermes 读取 `qr_image_path` 后主动上传图片。
- MediaCrawler MCP 只返回 `qr_image_path` 或后续可选的 base64，不直接发送飞书消息。

记录当前结论：

```text
飞书文本路径回传：待服务器验证
飞书本地图片上传：待服务器验证
二维码 PNG 上传：Sprint 7 验证
```

## 9. 验收标准

- Hermes 新会话能发现 `mediacrawler` MCP tools。
- `ping/create_dataset/list_datasets/get_dataset` 可直接调用成功。
- 对已有 raw 数据集，`normalize_dataset/query_dataset/generate_report/get_report` 可调用成功。
- 未登录时 `start_collection` 返回 `need_login`，不会长时间卡住。
- 飞书用户能收到报告摘要和报告文件路径。
- 如果飞书不能直接使用本地路径或图片路径，约束记录到本文档。
