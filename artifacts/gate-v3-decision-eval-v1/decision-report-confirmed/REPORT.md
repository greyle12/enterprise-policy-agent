# 门控 v3 四值决策评测报告

状态：`completed`。这是查询意图决策层的离线一致性评测，不是检索质量、答案正确率或生产效果。

## 结果

| 套件 | N | ALLOW | DENY | UNRESOLVED | NOT_APPLICABLE | ERROR | 精确匹配 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 已确认 v1 契约 | 20 | 11 | 8 | 0 | 1 | 0 | 20/20 |
| v3 UNRESOLVED | 9 | 0 | 0 | 9 | 0 | 0 | 9/9 |
| 合计 | 29 | 11 | 8 | 9 | 1 | 0 | 29/29 |

合计 `overall_accuracy=100%`，`known_decision_accuracy=100%`（已知值样本 N=20），
`unresolved_recall=100%`（UNRESOLVED 样本 N=9）。这些数字只说明当前确定性解析器
复现了经用户确认的标签，不能作为独立泛化能力或检索提升的证据。

## 计算边界

- 预期标签不参与决策，只在解析完成后用于混淆矩阵。
- `NOT_APPLICABLE` 保留在总体分母；运行异常保留为 `ERROR` 并计为错误。
- 没有调用 LLM、Embedding、Vector Search、BM25、RRF 或 Reranker。
- v1/v2 规则、选择器、FastAPI 和生产默认配置未改变。

完整逐条 intent、证据 span、reason code 和哈希见同目录的
`four-value-report.json`。
