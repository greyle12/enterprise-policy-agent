# Semantic Facet Challenge v1 Confirmed

状态：user_confirmed。该数据集是基于已有开发错误设计的定向挑战集，不是独立测试集。

本目录的 records.jsonl 是用于 adapter 对齐的 PARTIAL challenge fixture；accepted.json 保存用户确认的 Facet 与 source spans；confirmation.json 保存确认范围与 provenance。原始草案仍保存在 `docs/gate_v3/semantic-facet-challenge-v1-draft/`。

本数据集包含 12 条 query、6 组对照和 14 个 Facet 实例，覆盖时间条件/时间询问、明确对象/省略对象、明确候选流程/模糊指代。当前不包含模型推理、检索、运行时接入或语义准确率。
