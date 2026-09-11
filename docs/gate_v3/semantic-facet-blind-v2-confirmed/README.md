# Semantic Facet Blind v2 Confirmed

状态：user_confirmed。该数据集是基于已有开发错误设计的定向盲评候选集，独立测试集标志保持 false。

records.jsonl 是用于 adapter 对齐的 PARTIAL fixture；accepted.json 保存确认的 Facet、binding 和原文 Span；confirmation.json 保存确认范围与 provenance。原始待审阅文件仍保存在 `docs/gate_v3/semantic-facet-blind-v2-draft/`。

本包包含 18 条 query、17 个 Facet 实例和两条空 Facet 真值。当前不包含模型推理、检索、运行时接入或语义准确率。
