> 2026-07 状态说明：本文主要记录 Hermes stdio MCP 配置方式，仍然有效。默认 MediaCrawler MCP 只启用 `dataset` profile；`start_collection`、QR、login、cookie 相关工具必须显式启用 `experimental_collection` profile 才会注册。最新主线是桌面真实浏览器采集后，通过 MCP 登记、查询和分析数据集。详见 [桌面采集与数据集 MCP 架构改造方案](桌面采集与数据集MCP架构改造方案.md)。

已在当前 Hermes 服务器上确认过了，这一项结论如下。
1. MCP Python SDK 已安装
服务器上的 Hermes Python 环境里已经有 MCP SDK：
Name: mcp
Version: 1.26.0
Location: /usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages
Hermes 版本：
Hermes Agent v0.15.1 (2026.5.29)
Project: /usr/local/lib/hermes-agent
Python: 3.11.15
也确认了当前 SDK 支持 FastMCP：
from mcp.server.fastmcp import FastMCP

并且 FastMCP.run() 支持：
run(transport='stdio' | 'sse' | 'streamable-http')
所以你的 server.py 第一版可以直接用 FastMCP 写 stdio server。
2. 当前 Hermes 配置里还没有 MCP server
当前配置路径是：
/root/.hermes/config.yaml
现在没有配置任何 MCP server：
No MCP servers configured.
也就是说后续 MediaCrawler MCP 做好后，需要新增配置。
3. Hermes stdio MCP 配置格式已验证
我临时写了一个最小 MCP server，并用 Hermes 实际测试了添加、发现工具、测试连接、移除，流程是通的。
临时 server 内容类似：
from mcp.server.fastmcp import FastMCP



mcp = FastMCP("probe")



@mcp.tool()

def ping(message: str = "ok") -> str:

    """Return a ping response."""

    return f"pong:{message}"



if __name__ == "__main__":

    mcp.run("stdio")

Hermes 能成功发现工具：
✓ Connected! Found 1 tool(s) from 'probe':
  ping    Return a ping response.
配置写入后的实际 YAML 形态是：
mcp_servers:

  probe:

    command: python3

    args:

      - /tmp/hermes_mcp_probe.py

    enabled: true

所以 MediaCrawler MCP 后续可以配置成类似：
mcp_servers:

  mediacrawler:

    command: /path/to/mediacrawler-mcp/.venv/bin/python

    args:

      - /path/to/mediacrawler-mcp/server.py

    enabled: true

    timeout: 300

    connect_timeout: 60

    env:

      MEDIACRAWLER_MCP_TOOL_PROFILE: dataset

      MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION: "false"

或者用 CLI 添加：
hermes mcp add mediacrawler \

  --command /path/to/mediacrawler-mcp/.venv/bin/python \

  --args /path/to/mediacrawler-mcp/server.py

添加时 Hermes 会先连接 server，发现工具，然后询问是否启用。
4. server.py 具体写法建议
基于当前服务器的 MCP SDK 版本，第一版建议用这个模式：
from mcp.server.fastmcp import FastMCP



mcp = FastMCP("mediacrawler")



@mcp.tool()

def create_dataset(

    name: str,

    platforms: list[str],

    keywords: list[str],

    description: str | None = None,

) -> dict:

    """Create a MediaCrawler dataset."""

    ...



@mcp.tool()

def register_dataset(dataset_dir: str, import_mode: str = "copy") -> dict:

    """Register a desktop-collected dataset bundle."""

    ...



@mcp.tool()

def query_dataset(

    dataset_id: str,

    query: str,

    platform: str | None = None,

    source_keyword: str | None = None,

    limit: int = 20,

) -> dict:

    """Query existing dataset without recrawling."""

    ...



@mcp.tool()

def generate_report(dataset_id: str, report_type: str = "topic_research") -> dict:

    """Generate markdown/html report for a dataset."""

    ...



if __name__ == "__main__":

    mcp.run("stdio")

5. 重要实现注意点
stdio MCP 有一个关键要求：
不要往 stdout 打普通日志。
因为 stdout 是 MCP 协议通道。如果你在 server.py 里随便：
print("starting crawler")

可能会污染 MCP 协议，导致 Hermes 连接失败。
建议：
普通日志写 stderr
或写文件，例如：
~/.mediacrawler-mcp/logs/server.log
MCP tool 返回结构化 dict / str
例如：
import logging

from pathlib import Path



log_dir = Path.home() / ".mediacrawler-mcp" / "logs"

log_dir.mkdir(parents=True, exist_ok=True)



logging.basicConfig(

    filename=log_dir / "server.log",

    level=logging.INFO,

    format="%(asctime)s %(levelname)s %(message)s",

)

6. 使用项目 venv 时要注意
虽然 Hermes 自己的 Python 环境里已经有 mcp==1.26.0，但如果 MediaCrawler MCP 用自己的 venv，例如：
/path/to/mediacrawler-mcp/.venv/bin/python
那这个 venv 里也要安装 MCP SDK：
pip install mcp

或者如果用 uv：
uv add mcp

建议项目依赖里固定：
mcp>=1.26.0
至少当前服务器验证过 1.26.0 可用。
7. Hermes 生效方式
添加 MCP server 后，Hermes 提示：
Start a new session to use these tools.
如果是在飞书网关里使用，通常需要：
/restart
或者重启 Hermes gateway，让新 MCP tools 被发现并注入。
工具命名会变成类似：
mcp_mediacrawler_create_dataset
mcp_mediacrawler_register_dataset
mcp_mediacrawler_query_dataset
也就是：
mcp_{server_name}_{tool_name}

默认配置下不应该出现：

```text
mcp_mediacrawler_start_collection
mcp_mediacrawler_get_task_status
mcp_mediacrawler_start_qrcode_login
mcp_mediacrawler_import_cookies
```

如果确实要调试服务器采集，需要显式启用：

```yaml
env:
  MEDIACRAWLER_MCP_TOOL_PROFILE: dataset
  MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION: "true"
```
最终确认结论
这一项已经确认清楚：
当前服务器 已安装 MCP Python SDK 1.26.0。
Hermes 原生支持 stdio MCP。
配置位置是：
/root/.hermes/config.yaml
配置 key 是：
mcp_servers:

stdio 配置格式是：
mcp_servers:

  mediacrawler:

    command: /path/to/python

    args:

      - /path/to/server.py

    enabled: true

    timeout: 300

    connect_timeout: 60

server.py 推荐用：
from mcp.server.fastmcp import FastMCP

...

mcp.run("stdio")

开发时要避免向 stdout 写日志，日志写 stderr 或文件。
配置后需要新会话或重启 Hermes gateway 才能用。
