# 离线评测与人工审阅

这里的 30 个问题覆盖 10 个类别，含 Agent Memory、RAG、数据库、能源、教育等。
它们由开发代理整理，全部保留 `reviewed=false`；当前没有人工审阅背书。
其它领域的问题用于测试不同输入能否走完程序流程，**不是这些领域的研究质量基准**。
所有离线运行都使用同一组本项目编写的 Agent Memory 教学材料，报告会明确提示主题相关性限制。

```bash
.venv/bin/python scripts/evaluate.py
```

脚本为每个问题创建新的临时 SQLite 数据库，执行实际 Engine，测量耗时并读取事件。
关闭 `.env` 加载，同时显式覆盖环境中的模型、Gateway、RAG 和联网设置；不会调用网络或收费模型。
输出 `results/offline.json` 和一个真实生成的 `demo.md`、`demo.html`、`run.json`、`trace.json`。
临时数据库在运行结束后删除，导出只包含公开教学材料与允许的 Trace 字段。
结果中的 UTC 生成时间、数据集 SHA256 和约束说明用于辨认该次运行，耗时随机器负载变化。

| 指标 | 实际含义 | 未覆盖的内容 |
| --- | --- | --- |
| task_completion | 状态 completed、完整计划绑定、非空主张、重新校验通过且存在 finished 事件 | 主题是否被正确回答 |
| source_validity | 离线始终为 null | 真实互联网来源的存在性、权威性与时效性 |
| fixture_source_integrity | 来源 ID、地址、标题、摘录与哈希和受控 Fixture 一致的比例 | 真实来源有效率 |
| citation_link_integrity | 有主张时重新运行 EvidenceChecker；全部结构检查通过记 1，否则 0 | 证据是否在语义上支持主张 |
| coverage | 已绑定来源的计划子问题比例 | 研究主题语义覆盖率 |
| annotated_keypoint_coverage | 仅 Agent Memory 有标注问题的摘录关键词匹配比例 | 语义蕴含、真实答案完整性 |
| latency_ms | 单次实际 Engine 运行的墙钟耗时 | 真实模型/网络端到端耗时 |
| tool_success | 实际 tool_result 事件中 status=ok 的比例；无事件为 null | 联网检索质量 |
| llm_tokens / cost_usd | 来自已记录 usage，未产生时为 null | 将缺失使用量错误视为零成本实测 |
| faithfulness | null，需要人工或独立语义评审 | 自动宣称消除幻觉 |

`aggregate_means` 仅对非空值求均值。Keypoint 的总体均值只来自 3 个有标注的 Agent Memory 问题，
其它 27 个问题为 null；这不是 30 个主题的语义覆盖率。现有标注本身也等待人工复核。
离线结构指标即使全部为 1，也不能解读为真实研究任务正确率 100%。

## 扩展到 100 条并保留审阅门槛

```bash
.venv/bin/python scripts/expand_eval.py --generate eval/candidates.jsonl
.venv/bin/python scripts/expand_eval.py --validate eval/candidates.jsonl
```

生成是确定性的：10 个类别各扩展 10 条，恰好 100 条，所有新候选均为 `reviewed=false`，没有自动批准。
`--validate` 要求至少 30 条、唯一 ID、合法结构，且每条均有显式人工审阅记录。
因此对当前基线或刚生成的候选执行严格验证会以非零退出码报告 `human review pending`，这是预期门槛。
离线结构回归允许待审阅问题，但会原样报告 `human_reviewed_count=0`。

人工实际读过问题、类别及 Keypoint 标注，修正后，才可逐条记录：

```bash
.venv/bin/python scripts/expand_eval.py --dataset eval/candidates.jsonl \
  --mark-reviewed candidate-001 --reviewer human-review-2026-10
```

该命令只记录调用者的明确确认，不会判断审阅是否真的发生，也不会批量猜测其它条目的状态。
后续可以按相同 JSONL 结构添加人工标准答案和真实来源，但应另行设计语义评测，不能复用 Fixture 的高分作证明。

## Faithfulness 人工评分表（尚未执行评分）

逐条阅读 `claim → source → passage`，再填写 [human_review_template.csv](human_review_template.csv)。
评分对象是主张与摘录的关系；来源的声誉不能替代对该关系的判断。

| 维度 | 0 分 | 1 分 | 2 分 |
| --- | --- | --- | --- |
| Faithfulness | 摘录不支持、矛盾或主张虚构 | 支持部分内容，但缺条件、扩大范围或遗漏重要限制 | 摘录明确支持主张，条件、时间和不确定性一致 |
| Source quality | 来源无法回查或归属不明 | 可回查但只是二手介绍/项目自述 | 合适的一手材料，并有明确版本或时间信息 |
| Topic coverage | 该子问题没有相关证据 | 只覆盖子问题的一部分 | 覆盖预先标注要点并记录可能的反例 |

记录每项理由、相关原文、reviewer 和实际审阅时间。空白表示未评分，不计为 0 或 2。
建议两名评审独立打分，再复核分歧；只有填写完成并核对分母后才汇总分布。Fixture 上的
评分仍不能视为真实互联网研究能力。当前 CSV 没有任何评分，评测 JSON 的 faithfulness 仍为 null。
