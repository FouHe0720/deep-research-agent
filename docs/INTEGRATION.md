# 可选服务集成与边界

ResearchOS 独立运行；启动、Fixture Demo 和回归测试不需要兄弟仓库、API Key、GPU
或模型下载。适配器只连接操作者配置的固定服务地址，不接受网页内容指定的新模型地址。
地址允许本机 HTTP 服务；这是可信配置边界，与公开网页 Reader 的 DNS/SSRF 边界分开。
所有 HTTP 集成都关闭环境代理和自动重定向，具有总超时、响应字节上限和安全错误码。
模型调用不自动重试，避免一次超时引发重复推理和潜在重复费用。

## 项目 1：LLM Gateway / OpenAI-compatible

`GatewayClient` 有两种明确协议，模型名称必须由操作者提供，没有私人地址或默认模型。

| `kind` | `base_url` 约定 | 实际请求 | 文本结果 |
| --- | --- | --- | --- |
| `gateway` | Gateway 服务根地址或 `/v1` | `POST /v1/chat` | `answer` |
| `openai` | 兼容服务 API base，通常含 `/v1` | `POST {base}/chat/completions` | `choices[0].message.content` |

Gateway 的公开请求是 `model`、`messages` 与可选生成参数；这里发送 `max_tokens=400`。
消息只允许 system/user/assistant；单条不超过 16,000 字符，总计不超过 32,000 字符。
Gateway 通常要求 `Authorization: Bearer ...`，只在开发环境且仅启用 Mock 时允许禁用认证。
OpenAI-compatible 路径发送 `max_completion_tokens=400`，它包含可见输出和模型推理 token，
遵循当前 [OpenAI Chat Completions 参考](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。
不同兼容服务可能不支持该参数；本项目不在错误后偷偷改协议重发。两个协议都支持显式无 Key
的自建服务；任何真实 Key 只从运行环境读取，不写入报告、Trace、仓库或错误正文。

配置 `RESEARCH_LLM_BASE_URL`、`RESEARCH_LLM_MODEL`、`RESEARCH_LLM_KIND=openai|gateway`、
可选 `RESEARCH_LLM_API_KEY`。Gateway 也可通过 `RESEARCH_GATEWAY_URL` 和
`RESEARCH_GATEWAY_API_KEY` 指定。OFFLINE 强制禁止外部 RAG/LLM；LIVE 辅助模型分析只有在正费用预算、完整模型配置和明确价格
同时存在时才允许请求；未配置时保留本地抽取式报告。

`generate(messages, max_cost_usd=..., max_completion_tokens=400)` 必须传入本次费用额度。
价格由 `RESEARCH_LLM_INPUT_USD_PER_MILLION` 和 `RESEARCH_LLM_OUTPUT_USD_PER_MILLION`
显式指定；免费本机服务可明确设为 0。预检按 prompt 的 UTF-8 字节数，加每条 128 和固定
256 的 framing allowance 估计输入 token 上界，再加完整输出 token 上限计算费用。
这对常见按字节编码的 tokenizer 是保守的规划额度，不是任意第三方服务的账单保证。
价格未知或估算超额度时，在发送 HTTP 之前拒绝。

`LLMResult` 分离 `text`、请求的 `model`、`usage` 和 `cost_usd`。
usage 缺失时为 null，不用字符数或预检估算冒充真实使用量；只报告 total_tokens 时无法
计算输入/输出分项费用，cost 仍为 null。费用是服务报告的输入/输出 tokens 乘操作者配置
价格，非服务账单。辅助模型文本不自动成为经核验的 claim，也不改变原始来源摘录绑定。

## 项目 2：Structured LLM Workflow

公开接口是 `POST /v1/analyze`，JSON `{text, name}`；文件入口为
`POST /v1/analyze/file`，multipart 字段 `file`。它是固定步骤的文档抽取服务，不是自主搜索
或工具执行 Agent，当前没有入站认证。

ResearchOS 当前使用自身抽取式 Writer 和 EvidenceChecker，没有强行调用该服务，也没有
声称文档抽取结果等于来源真实性或语义蕴含验证。这是可扩展边界，不是已接通的集成。

## 项目 3：RAG Knowledge Engine

`RAGAdapter.query(question, top_k=5)` 向 `RESEARCH_RAG_URL` 的 `/query` 发送：

```json
{"question":"Agent memory?","strategy":"hybrid","top_k":5,"rerank":"noop","rewrite":false}
```

公开 schema 允许 top_k 1–100，但服务运行配置还要求不超过 `RAG_TOP_N`，默认 20。
没有 `/retrieve` 接口，也没有服务内入站认证。操作者可在自己的反向代理增加认证；适配器
允许可选 Bearer Key，但不假装原服务已经实现用户隔离。

只读取响应 `candidates[].chunk` 的正文和定位字段，以及 candidate 的 score/rank；
忽略上游生成的 `answer`。返回严格的 `RetrievalHit`，标记 `provenance=rag_retrieval`。
`chunk.source` 可以是本地文档路径；获取检索结果不证明其对应网页刚刚被访问。
这些结果用于辅助上下文，不伪装成 `SourceRecord(provenance=network)`，不自动变成可信引用。
不调用 `/documents` 的写入或删除入口。

## 项目 4：Workspace Agent / MCP

兄弟仓库实际提供 stdio MCP server，而非 HTTP Research API。其 MCP 工具是
`list_files`、`read_file`、`search_files`、`write_file`；参数为严格 schema，读写限制共享
原生 sandbox。写入默认只预览，真实写入需要显式授权，覆盖现有文件另需授权。

本项目当前不启动 Workspace subprocess，不隐式读取用户工作目录。未来 MCP 接入应明确
指定授权 workspace，只允许所需的读工具，使用固定可执行程序和受控环境，不把任意网页
指令转为 shell 命令。未声称存在通用 Workspace HTTP 入口，也没有复制其整个仓库。

### ResearchOS 自身的只读 MCP

本仓库另提供 `researchos.mcp_server`，通过 stdio 读取已持久化的研究记录。安装可选
`mcp` extra 后，使用 `.venv/bin/python -m researchos.mcp_server` 由 MCP client 启动。
服务基于官方 MCP Python SDK 的 `MCPServer`，工具为 `get_run`、`list_sources`、
`search_memory`；只读现有记录，不创建研究、不发起新外部请求、不暴露任意文件操作。
默认数据库来自 `RESEARCH_DATA_DIR`，与 HTTP 服务使用同一配置。
该 server 及其工具与 Workspace 兄弟项目独立，不能把它的只读能力理解为已启动兄弟文件服务。
协议参考：[MCP Python SDK](https://py.sdk.modelcontextprotocol.io/run/)。

## 项目 5：AgentOps Evaluation Platform

`AgentOpsExporter.from_events(run, events)` 导出项目 5 的 `ResearchAgentAdapter` 可接受响应：

```json
{"answer":"本次报告","trace":[{"span_id":"research-root","event_type":"agent","status":"ok"},{"span_id":"research-final","parent_span_id":"research-root","event_type":"final","status":"ok"}]}
```

非空 Trace 只有一个根，父 span 总在子 span 之前；工具结果关联到相应工具调用。
仅导出 `span_id`、`parent_span_id`、`event_type`、`tool_name`、`status`、`duration_ms`、
`token_usage`、`cost` 等已定义字段，不把本地 timestamp、任意 metadata、prompt、header、
来源正文或工具输出塞进 HTTP spans。工具调用失败状态由真实 tool_result 更新；未知
token/cost 为 null。最多 1,000 spans；answer 超过 20,000 字符时明确截断，完整报告仍在本地。

项目 5 的适配器对操作者指定的 `RESEARCH_AGENT_ENDPOINT` 发 POST，请求包含
`task`、`category=retrieval`、`fixtures`、`seed`、`config={planner,tool_descriptions,tool_retries}`。
本仓库 `/api/agentops/research` 实现此离线兼容入口，支持 `planner=true`、
`tool_descriptions=improved`、`tool_retries=0|1`；最后一项对应 1–2 轮有限补检。
外部 fixtures、关闭 planner、basic 描述或超出上限的重试配置会明确返回 422；
不把未实现的配置消融悄悄当作生效。seed 被接收，内置固定流程没有随机模型。
项目 5 接收后会校验整棵树，并重新映射 run/span IDs，不信任外部时间戳。

项目 5 当前没有公开 HTTP Trace 导入端点；其 API 提供 `/runs`、`/metrics`、`/compare`
等读取与人工 annotations 写入。平台自身 `export_record` 输出完整 RunRecord JSON 和
带 case_id/repeat 的 JSONL；它与本节的 HTTP 响应不是同一种格式。
ResearchOS 的兼容响应可由平台现有远程适配器消费，但不直接修改其数据库或文件。

## 验证范围

`tests/test_integrations.py` 使用 `httpx.MockTransport` 验证两种模型协议、显式 Key/无 Key、
缺失 usage、费用预检在 I/O 前拒绝、超时、字节上限、安全错误、RAG 来源边界和 AgentOps
Trace tree/字段限制。30 项适配器测试全部通过；没有调用真实模型或付费 API。
通过 MockTransport 说明协议和失败边界可执行，不代表兄弟服务实际部署连通。

另已实际执行三项本地跨仓库协议检查：在显式 lifespan 下通过 `httpx.ASGITransport`
调用兄弟 Gateway 的真实 FastAPI `/v1/chat`，后端使用 Mock provider；通过相同方式调用
兄弟 RAG 的真实 `/query`，从临时 SQLite 合成语料得到一个类型正确的检索 chunk；直接
调用兄弟 AgentOps 的实际 `_remote_response` validator，成功接受五个 spans 的导出。
这些检查没有修改兄弟源码、启动网络服务、下载模型或请求真实模型；与真实网络集成分别说明。
