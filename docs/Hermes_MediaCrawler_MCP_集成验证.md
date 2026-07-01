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
```

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

## 6. 飞书结果路径验证

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

## 7. 图片路径预验证

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

## 8. 验收标准

- Hermes 新会话能发现 `mediacrawler` MCP tools。
- `ping/create_dataset/list_datasets/get_dataset` 可直接调用成功。
- 对已有 raw 数据集，`normalize_dataset/query_dataset/generate_report/get_report` 可调用成功。
- 未登录时 `start_collection` 返回 `need_login`，不会长时间卡住。
- 飞书用户能收到报告摘要和报告文件路径。
- 如果飞书不能直接使用本地路径或图片路径，约束记录到本文档。
