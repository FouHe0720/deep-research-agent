# 面试讲解与追问

建议先运行离线 Demo，再从一次实际 Run 解释请求、计划、来源、引用、事件和最终状态。
这是以证据审计和失败边界为重点的工程作品，默认报告展示原始摘录；不要把它介绍成已经
解决开放领域语义研究质量的系统。

## 1. 为什么不用一个长 prompt 直接生成报告？

长 prompt 难以证明来源来自哪里，也难以定位失败。这里先落库 SourceRecord，再建立
claim → source → passage → citation 关联，Writer 输出后由 EvidenceChecker 检查一致性。
这解决可追溯性和结构完整性；原文真假、检索充分性和语义蕴含仍需评审。

## 2. Manager Pattern 在哪里？这是多个独立 Agent 吗？

`services/engine.py` 的 Orchestrator 控制状态，调用 Planner、Researcher、Synthesizer、
Reviewer 的显式契约。它们目前是同一进程内的角色类，默认没有各自独立的模型循环。
这样可以先验证有限状态、预算和取消行为，再考虑引入框架；角色名不等于自主智能体能力。

## 3. 为什么选择 agents-as-tools 思路，怎样避免 handoff 失控？

控制权始终属于 Manager。Reviewer 返回“缺哪些子问题”和是否补充检索，不将执行权转交
另一角色递归调用。循环被 max_rounds、max_sources、max_tool_calls、max_seconds 限制；
模型辅助分析至多一次。相比开放 handoff，可明确说明谁负责停止和保留部分结果。

## 4. Planner 是否真的理解每个研究主题？

当前是确定性模板：定义路线、代表论文/实现、优势局限；deep 再加评测复现。
关键词包含主题，Live provider 使用主题查询官方 API。它能演示任务结构和受控执行，
并没有经过开放领域规划质量评测。更强 Planner 需要独立题集和针对计划质量的标注。

## 5. Memory 和 RAG 各自做了什么？

本地 Memory 用 SQLite 保存运行快照、来源、摘录、事件，FTS5 检索文本；URL/DOI/hash
用于去重。可选 RAGAdapter 请求兄弟服务 `/query` 的 candidates chunks，保留定位信息。
检索结果只是辅助上下文，不因命中就成为新抓取的网页或可信事实。缺少 embedding/model
不会阻塞本地核心路径。

## 6. 引用检查能消除幻觉吗？

不能。它验证引用存在、摘录位于来源片段中、子问题和编号正确、参考文献内容未被替换。
它不会判断来源可信度或一般语义蕴含。默认用原文摘录减少新断言；可选 LLM 分析明确
标注未验证。引用链完整率与 Faithfulness 需要分别报告，不能互相代替。

## 7. 为什么只在请求前检查域名还不够？

检查时解析到公网、连接时重新解析到内网会留下 DNS rebinding 空隙。SafeHTTP 校验全部
A/AAAA 地址，再连接其中已验证的 IP，使用原始 Host 和 SNI 做 TLS 认证；重定向每跳重新
验证，跨源移除认证信息。用户 URL 与操作者配置的本机 Gateway/RAG 地址采用不同信任边界。

## 8. 怎样处理工具失败、超时和重试？

总研究截止时间覆盖角色调用；底层 HTTP 有字节、时间、请求总数、节流和有限重试。
429/503 按限制处理，网络失败不会偷偷替换成 Fixture。已获取的来源仍保留，最终可为
partial；取消也保留可审计记录。模型请求不自动重试，以免上游已执行后重复收费。
asyncio 取消是协作式控制，不能当作进程隔离或任意代码沙箱。

## 9. 费用预算如何实现，为什么未知 usage 是 null？

模型调用前必须有显式模型、价格和本次额度。UTF-8 字节数加 framing allowance 形成保守
输入估计，再计入输出上限；超额或缺价格时请求前拒绝。这个规划额度不是任意兼容服务的
账单保证。返回后仅使用服务报告的 tokens；缺失时 null，不能把字符估计写成真实 usage。
费用来自已报告输入/输出 tokens × 配置价格，也不是供应商账单。

## 10. MCP 在这里解决什么问题，如何控制权限？

ResearchOS 自身的 MCP 只读已保存的 run、sources 和 memory，不提交新研究任务。
Workspace 兄弟项目提供另一套 stdio 文件工具契约，而非通用 Research HTTP 服务。其读取必须
绑定授权 workspace、固定服务程序和工具 allowlist，不能让资料正文指定命令或扩大目录。
默认研究流程不需要文件系统工具。写文件与覆盖文件是额外权限；只读接入不应自动启用它们。

## 11. Trace 和评测怎样避免“看起来很科学”的假数字？

本地角色 spans 用单调时钟测量实际代码执行区间，工具事件记录真实成功或失败。
AgentOpsExporter 只输出协议允许字段，并维护一棵父先子的 span 树；未知 token/cost
保留 null。离线 30 题复用 Agent Memory 教学资料，只证明软件回归行为；网络可用性、
主题研究质量和模型质量需要各自真实验证。端到端延迟也不等于生产性能基准。

## 12. 当前部署的主要限制和下一步是什么？

单进程内存队列配合 SQLite 适合本机演示。历史可以持久化，但多个 workers 不共享调度，
重启不能继续正在执行的外部调用。下一步应先实现持久队列、幂等任务、身份和租户隔离，
再增加主题相关 gold 和人工语义评审；扩展角色数量不应早于可验证的质量收益。

核心阅读顺序：`domain/models.py` → `services/engine.py` → `adapters/http.py` →
`services/evidence.py` → `api/app.py`。辅助阅读 `services/roles.py`、
`adapters/integrations.py` 和 [VALIDATION](VALIDATION.md)，避免讲出超出验证范围的结论。
