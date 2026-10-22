# 实际验证记录

本文件只记录已经执行的检查。待执行项不能作为通过声明；本地测试、真实网络、模型调用、
Docker 和远程 CI 分开。日期使用执行会话的用户时区，验证环境见 [ENVIRONMENT](ENVIRONMENT.md)。

## 已完成阶段

| 阶段 | 实际结果 | 范围 |
| --- | --- | --- |
| V0 | 3 tests passed；离线 Demo 生成 6 个 Fixture 来源和报告 | 最小引用闭环 |
| V1 | 58 tests passed；Ruff lint/format 通过 | 来源适配、安全网络、去重、边界与恢复 |
| V2 | 103 tests passed；Ruff lint/format 通过 | 有限角色循环、事件/Trace、可选集成 |

V1 实际无凭证 GitHub 搜索通过 SafeHTTP 返回 HTTP 200，得到 1 个公开仓库结果；
只发生 1 次外发请求。此检查没有验证 arXiv、Brave、LLM 的真实可用性。

## V3 最终验收

本轮验收日期：2026-10-22（Asia/Shanghai）。下面区分本地实测与待推送后的远程状态。

| 检查 | 命令/方法 | 实际结果 |
| --- | --- | --- |
| 锁文件安装 | `uv sync --extra dev --extra mcp --frozen` | 实际成功，项目内 Python 3.12.15 |
| Lint / format | `make lint` + `node --check src/researchos/static/app.js` | 全部通过，54 个 Python 文件格式通过 |
| 完整 pytest | `make test` | 120 passed in 13.09s（含可选 MCP），无失败 |
| 离线 Quick Start | `make demo` | completed，6 个 Fixture 来源，引用结构通过，生成 MD/HTML/Run/Trace |
| 30 题离线回归 | 已生成 `eval/results/offline.json` | 30/30 流程与引用结构通过，90/90 Fixture 工具结果成功，人工审核 0/30 |
| 100 题扩展检查 | 生成、人工审阅状态检查 | 实际生成 100 条 pending，严格审阅验证按预期退出 1；没有代替人工批准 |
| 本地 API / SSE / 导出 | 实际本地 TCP，临时数据库 | 202 Run.id、31 条 SSE、6 来源、MD/HTML、AgentOps 导出、memory 与兼容 POST 200；无外部网络/模型 |
| 取消路径 | 排队/执行中取消自动化与 HTTP 检查 | 9 项 JobManager 测试通过，API 取消正常；重启恢复及并发限制已验证 |
| 浏览器交互与截图 | 实际 Chromium + Playwright | 6 来源、31 事件、引用弹窗、MD/HTML 下载、Trace、FTS、历史重载；桌面 1440 / 手机 390 无溢出，0 JS errors；截图已保存 |
| 可选 MCP | 官方 SDK 2.3.0，内存协议 + 实际 stdio 子进程 | 2 项测试通过，工具发现/读取/参数校验通过；不创建研究任务 |
| Docker build / health | 实际 daemon 构建、只读根 FS + volume 运行 | 构建成功，UID 10001:10001，healthy，HTTP 202 到 completed、6 来源及 HTML 成功；重启后仍可读取报告 |
| GitHub Actions | 远程 workflow 状态 | 待推送后确认 |
| 隐私 / 暂存区审查 | tracked/staged 文件扫描 | 待发布前执行 |
| GitHub public 发布 | 核对账户、普通 push、可见性 | 待发布后确认 |

## 模型和兄弟服务

- 30 项专门的适配器测试通过：MockTransport 验证 Gateway/OpenAI 两种请求、缺失 usage、
  费用预检、超时、响应上限、RAG chunk 以及 AgentOps 字段/树结构。
- 实际本地跨仓库检查：Gateway 真实 FastAPI + Mock backend 通过 ASGITransport 接受
  `/v1/chat`；RAG 真实 `/query` 从临时合成 SQLite 返回 1 个 typed chunk；AgentOps 实际
  `_remote_response` validator 接受 5 spans。这些检查没有网络服务或真实模型调用。
- 真实 LLM：未调用；没有模型质量、生产成本或模型端性能结论。默认费用预算为 0，
  未观测 token/cost 为 null，不填写虚构的零 token 使用量。
- 实际 arXiv SafeHTTP smoke：HTTP 200，3692 bytes Atom，1 次请求。
- 实际混合联网 CLI：`RESEARCH_ALLOW_LIVE=true researchos "agent memory" --mode live --providers github arxiv`，completed；2 个 GitHub 项目和 2 篇 arXiv 论文摘要，2 次 HTTP 请求（缓存复用），引用结构通过。公开产物 `eval/results/live-mixed*` 保留真实 URL、采集时间、论文发布日期来源。没有阅读 PDF 或完整代码库，不是主题正确率评价。
- Brave：未提供 Key，协议/错误/网页读取安全逻辑经过 Mock 测试，没有真实 Brave Search 调用。

## 评测解释

离线题集复用自编 Agent Memory 材料。Task Completion 衡量执行是否达到自身完成条件，
Coverage 衡量子问题绑定或标注要点命中；二者都不等于开放领域内容准确率。
Source Validity 应区分可识别 Fixture 与真实网络来源，Citation Link Integrity 只检查引用链。
Latency 是本次程序观测，Tool Success 必须报告成功/总调用分母。Faithfulness 需要独立
人工评分，不从引用结构推出百分比。

公开产物必须由实际运行生成并审阅，不包含密钥、私人文件、数据库或机器绝对路径。
后续新增验证应同时保存命令、配置摘要和对应产物，避免截图与 Run/指标不一致。

浏览器系统库与 Noto CJK 字体仅在忽略的 `.runtime/` 中解包使用，没有 sudo、系统安装或仓库字体包。Git 作者使用已核验账户的 GitHub no-reply 邮箱。
