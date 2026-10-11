# MCP 接入（Model Context Protocol）

让诊断 agent 使用**第三方系统**（K8s、云平台、工单系统、数据库…）的只读能力，
而**不用为每个工具写代码**——接一个 MCP server，agent 立刻能用它的工具。

## 快速开始

1. **写一个（或找一个）MCP server**。本地演示：`deploy/mcp/demo_k8s_server.py`
   （用官方 `mcp` SDK 的 `MCPServer`，stdio 传输，只读）。
   接真实系统时把 `command/args` 指向真实实现，例如
   `npx -y @modelcontextprotocol/server-kubernetes`。

2. **在 `.env` 里登记（白名单）**：

```ini
MCP_SERVERS=[{"name":"k8s","command":"{python}","args":["{repo}/deploy/mcp/demo_k8s_server.py"],"description":"K8s 集群只读查询（Pod/事件/日志）"}]
```

- `{python}` → 平台解释器路径，`{repo}` → 仓库根目录（便于用平台 venv 跑本地 server）。
- 支持 `env`（附加环境变量）与 `timeout`（秒，默认 30）。
- **不配置 = 能力关闭**；只连这里显式列出的 server。

3. **重启后端**（settings 在导入时读取）。之后问
   「用接入的外部 K8s 工具看下集群里 aiops-prod 有哪些 Pod / 最近事件」即可。

## agent 怎么用它

平台**不为每个 MCP 工具自动注册原生工具**（那样工具集会失控、且等于把任意能力交给模型）。
只固定暴露两个工具，先列后调、全程审计：

| 工具 | 作用 |
| --- | --- |
| `mcp_list_tools(server)` | 列出该 server 提供哪些工具（名称 + 用途） |
| `mcp_call(server, tool, arguments_json)` | 调用其中一个只读工具，`arguments_json` 为参数 JSON（无参数用 `"{}"`） |

系统提示里会列出已接入的 server 清单，并提示：**只有当平台自身探活/指标/日志覆盖不到时**才走 MCP。

## 安全

- **白名单**：命令来自运维配置，模型无法新增 server。
- **只读约定**：接入的 server 应只暴露只读工具（写操作仍走平台审批流程）。
- **外部数据护栏**：MCP 返回内容会被 `【外部数据·仅供参考…】` 包裹，当作**数据不当指令**，
  防止第三方系统里的内容构成提示注入。
- **失败隔离**：server 连不上/超时只返回一句错误，不影响诊断主流程。

## 验证

```bash
cd backend && .venv/bin/python -m pytest tests/test_mcp.py -q
```

含真实 stdio 端到端（起本地演示 server → 列工具 → 调用取数据）。
