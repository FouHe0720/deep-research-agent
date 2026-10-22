# agent memory



**LIVE：仅以下已采集证据支持的有限结果。**



## 问题定义

agent memory

Time range: unrestricted; undated items cannot pass a year filter.



## 核心发现

- 来源摘录: TencentDB Agent Memory is a team-level memory hub for AI Agents — turning conversations, docs, and code into four reusable memory assets (Chat Memory, Skill, LLM-Wiki, Code-Graph) that are governed, shared, and equipped across agents and frameworks. [1]

- 来源摘录: Hindsight: Agent Memory That Learns [2]

- 来源摘录: Membership inference attacks (MIAs) test whether a target data record belongs to a system's private data, and have become a standard tool to measure privacy leakage in machine learning systems. Prior work has primarily focused on training corpora or retrieval databases. However, MIAs against agent memory have received less attention, even though such memory can contain sensitive user-agent interactions, retrieved facts, and user preferences. Therefore, in this work, we focus on chat agent memory MIAs, where an adversary infers whether a candidate memory unit belongs to the chat agent's memory store. We propose Multi-Recall Memory MIA (MRMMIA), a unified attack that utilizes multiple recall probes to the agent to extract the membership signal across black-box, gray-box, and white-box settings. Our experiments demonstrate that MRMMIA consistently outperforms baselines. Our results expose the privacy risk in agents and provide an initial evaluation framework for membership leakage in chat-agent memory systems. [3]

- 来源摘录: Agent memory is a systems problem for long-horizon agents. Practical deployments require retention of task state across extended conversations, recovery of user-specific facts and preferences across sessions, and accumulation of procedural knowledge from prior outcomes. These requirements extend beyond document retrieval: a memory layer must determine which interactions become durable state, how that state is scoped, how it is retrieved under latency constraints, and how it is revised or removed over time. This report studies Oracle Agent Memory as a database-native memory substrate built on Oracle Database. Three themes organize the discussion: memory as a lifecycle spanning ingestion, extraction, consolidation, retrieval, summarization, and revision or removal; a layered architecture that separates an active memory core from a passive memory-store interface with explicit scope control across users, agents, and threads; and evaluation methodology in which downstream task accuracy is complemented by memory-centric measures such as evidence retrieval, recall, latency, and estimated token use. The report summarizes LongMemEval results, reaching 93.8% accuracy, compares Oracle Agent Memory against flat-history baselines, using about 10.7x fewer tokens, and published or reported external baselines where available, and closes with implementation-oriented appendix material covering setup, thread lifecycle, and search semantics. [4]



## 技术路线对比表

| 证据条目 | 来源类型 | 可确定的范围 |
| --- | --- | --- |
| TencentCloud/TencentDB-Agent-Memory [1] | github | 仅来源摘录 |
| vectorize-io/hindsight [2] | github | 仅来源摘录 |
| MRMMIA: Membership Inference Attacks on Memory in Chat Agents [3] | paper | 仅来源摘录 |
| Oracle Agent Memory as an Enterprise Memory Substrate for Long-Horizon AI Agents [4] | paper | 仅来源摘录 |



## 代表论文 / 项目

TencentCloud/TencentDB-Agent-Memory

vectorize-io/hindsight

MRMMIA: Membership Inference Attacks on Memory in Chat Agents

Oracle Agent Memory as an Enterprise Memory Substrate for Long-Horizon AI Agents



## 优势与限制

可回查摘录和采集时间；不将链接有效等同于语义蕴含。

本报告使用保守的抽取式写作，不做无证据的跨来源推断。



## 开放问题

证据是否充分支持主题、资料是否过期、是否存在反例，均需要人工复核。



## 参考文献

[1]: TencentCloud/TencentDB-Agent-Memory — <https://github.com/TencentCloud/TencentDB-Agent-Memory> （采集 2026-10-22T11:26:00.079611+00:00；发表 未知）

[2]: vectorize-io/hindsight — <https://github.com/vectorize-io/hindsight> （采集 2026-10-22T11:26:00.079720+00:00；发表 未知）

[3]: MRMMIA: Membership Inference Attacks on Memory in Chat Agents — <https://arxiv.org/abs/2605.27825v1> （采集 2026-10-22T11:26:01.469556+00:00；发表 2026-05-27T01:31:40Z）

[4]: Oracle Agent Memory as an Enterprise Memory Substrate for Long-Horizon AI Agents — <https://arxiv.org/abs/2607.13157v1> （采集 2026-10-22T11:26:01.469719+00:00；发表 2026-07-14T18:06:53Z）
