# 简历表述

以下表述对应已实现代码；量化测试和部署结果以 [VALIDATION](VALIDATION.md) 最终记录为准。
离线题集、Mock 协议测试不应改写为真实互联网研究或模型准确率。

- 独立实现 ResearchOS 调研工作台，以 FastAPI、SQLite/FTS5 和显式 Manager 角色组织任务拆解、
  有限轮检索、去重、摘录式报告及持久化 Trace，支持离线无 Key 演示与可选实时来源适配。
- 建立 source → passage → claim → citation 证据链与确定性引用检查，区分 Fixture、网络来源和
  RAG 上下文；实现 DNS 固定连接、逐跳 URL 校验、响应/时间/请求预算及部分结果保留。
- 适配独立 LLM Gateway/OpenAI-compatible、RAG 和 AgentOps 协议，加入模型费用预检与缺失
  usage 的 null 语义，并通过类型约束、故障测试和本地跨仓库协议检查验证集成边界。
