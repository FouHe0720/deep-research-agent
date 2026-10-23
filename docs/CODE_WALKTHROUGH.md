# 代码导览

建议先执行 `make campus-demo`，从一次Run的请求、计划、来源、报告和事件记录开始阅读。下列路径均位于 `src/researchos/`。

## 请求与范围

`domain/models.py` 定义ResearchRequest、Budget、SourceRecord、DocumentOrigin和Run。校园资料通过 `corpus=campus` 选择；校区、文档年度和明确适用年级独立保存。仅有文档版号时，年级保持为空。

`adapters/campus.py` 根据已知专业和实验班关键词选择资料，再应用范围筛选。`data/campus/manifest.json` 保存原DOCX哈希、摘录哈希、相对路径与段落号；程序正常运行只读取仓库内摘录。`scripts/verify_campus_sources.py` 可在另行提供原库路径时核对原文件。

## 有限调度

`services/engine.py` 持有运行状态，显式调用Planner、Researcher、Synthesizer和Reviewer。角色定义位于 `services/roles.py`，在同一进程运行，控制权由Engine保留。

每轮受 `max_rounds`、`max_sources`、`max_tool_calls`、`max_seconds` 约束。Reviewer返回缺少证据的子问题；有剩余额度时再检索。超时、工具失败或取消时，已经采集的证据和事件仍然保存。

`services/planner.py` 为校园案例生成三个业务方面：资料范围、条件对照与尚缺证据。通用模式使用三个固定方面，deep模式增加评测复现。计划模板及子问题绑定不构成语义覆盖评价。

## 对照表与引用

`services/campus_report.py` 读取 `data/campus/comparisons.json` 中的字段、原文段号及短摘录。每段短摘录须出现在对应的原文段落中，才可写入表格。例如，数据科学实验班的推免依据保持其自身原文，计算法学的30%不会用于其他班。

`services/writer.py` 为来源建立Claim并输出引用。`services/evidence.py` 检查主张、来源摘录、子问题、编号及参考文献的一致性；校园模式额外重建期望对照表，检查数字与适用范围是否被更改。引用结构检查不判断资料的时效性或一般语义蕴含。

报告HTML禁用来源中的原始HTML与危险链接。表格使用局部滚动容器，手机页面可以横向查看列，正文不会被表格撑宽。

## 来源去重与证据查询

`services/memory.py` 按规范化URL、DOI和摘录哈希去重，合并来源关联的子问题。`infra/store.py` 用SQLite保存Run快照、来源和事件，FTS5提供词项检索；连续汉字中的短语未命中时，按字面查询标题及摘录。查询绑定run_id，并转义LIKE通配符。

可选RAGAdapter只取得候选chunk作为辅助上下文，不直接建立网络来源或可信引用。校园离线模式禁用外部RAG和模型调用。

## 公网读取与模型预算

`adapters/http.py` 校验每跳URL与全部DNS结果，固定连接到已验证的公网IP，同时保留原Host、SNI及TLS验证。重定向逐跳检查，跨源移除认证头。响应字节、请求总数、超时、节流与重试都有上限。

`adapters/integrations.py` 对操作者配置的Gateway、OpenAI-compatible和RAG端点使用独立协议。模型调用要求显式模型、价格与正费用预算；费用预检使用保守输入估计。返回usage缺失时保留null，计费观测只使用服务报告的token数据。模型辅助分析与原文引用分别标注。

## API、事件与历史

`api/app.py` 接受异步研究任务，提供SSE、来源、报告、取消和历史入口。`infra/jobs.py` 管理单进程并发与等待队列；重启后恢复历史状态，但不能继续中断的外部调用。多个Uvicorn worker不共享队列。

`infra/trace.py` 用单调时钟记录角色执行区间。AgentOpsExporter输出允许的字段，维护父span先于子span的树结构，未知token与费用保持为空。只读MCP提供已保存Run、sources及memory查询，不创建研究任务。

测试入口为 [test_campus.py](../tests/test_campus.py)、[test_limits_memory.py](../tests/test_limits_memory.py)、[test_network.py](../tests/test_network.py) 和 [test_jobs.py](../tests/test_jobs.py)。运行结果见 [VALIDATION](VALIDATION.md)，详细边界见 [ARCHITECTURE](ARCHITECTURE.md) 与 [SECURITY](SECURITY.md)。
