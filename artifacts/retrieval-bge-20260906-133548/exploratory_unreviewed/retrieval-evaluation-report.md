# 企业制度 Agent 检索评测报告

- 运行模式：`bge`
- Embedding：`BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620`
- Reranker：`BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- 外部模型推理：`true`
- 数据集 SHA-256：`db7b25617690afc26bbf1339650a4aa1c273f8d8476aad69d016df6481033b95`
- 语料 SHA-256：`5260c50ae8f0d4fddc4bd5ad7d1d325bd53c76487fa13b91e7aefaee7a445737`
- 查询数：6
- 候选池：Top 20
- 门禁阈值：Recall@5 ≥ 80.00%，MRR@5 ≥ 80.00%，nDCG@5 ≥ 80.00%
- 质量门禁：**未通过**

## 消融指标

| 检索通道 | Recall@1 | Recall@3 | Recall@5 | MRR@5 | nDCG@5 | 平均耗时 | 错误 | 门禁 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `vector` | 50.00% | 80.56% | 80.56% | 83.33% | 78.00% | 21.648 ms | 0 | 仅对照 |
| `bm25` | 33.33% | 38.89% | 69.44% | 58.89% | 51.76% | 2.307 ms | 0 | 仅对照 |
| `hybrid` | 41.67% | 61.11% | 61.11% | 58.33% | 60.21% | 23.438 ms | 0 | 未通过 |
| `reranked` | 63.89% | 86.11% | 86.11% | 91.67% | 89.68% | 11561.281 ms | 0 | 通过 |

## 查询明细

| 用例 | 分级标注 | 通道 | Recall@5 | nDCG@5 | 首个相关排名 | Top 5 | 错误 |
|---|---|---|---:|---:|---:|---|---|
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `vector` | 50.00% | 78.72% | 1 | `TRAVEL_POLICY_001__v1_0__article_008`<br>`TRAVEL_POLICY_001__v1_0__article_014`<br>`TRAVEL_POLICY_001__v1_0__article_009`<br>`TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_010` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `bm25` | 50.00% | 33.74% | 1 | `TRAVEL_POLICY_001__v1_0__article_007`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_014` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `hybrid` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_014`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_013` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `reranked` | 50.00% | 49.66% | 2 | `TRAVEL_POLICY_001__v1_0__article_009`<br>`TRAVEL_POLICY_001__v1_0__article_008`<br>`TRAVEL_POLICY_001__v1_0__article_005`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_014` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `vector` | 100.00% | 100.00% | 1 | `TRAVEL_POLICY_001__v1_0__article_018`<br>`TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_011`<br>`TRAVEL_POLICY_001__v1_0__article_015`<br>`TRAVEL_POLICY_001__v1_0__article_006` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `bm25` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_020`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_014` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `hybrid` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`TRAVEL_POLICY_001__v1_0__article_014`<br>`TRAVEL_POLICY_001__v1_0__article_020` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `reranked` | 100.00% | 100.00% | 1 | `TRAVEL_POLICY_001__v1_0__article_018`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_020`<br>`TRAVEL_POLICY_001__v1_0__article_017`<br>`TRAVEL_POLICY_001__v1_0__article_021` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `vector` | 100.00% | 63.09% | 2 | `PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_019`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_014` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `bm25` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_024`<br>`LEAVE_POLICY_001__v1_0__article_038` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `hybrid` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_024`<br>`PROCUREMENT_POLICY_001__v1_0__article_027` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `reranked` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_027`<br>`PROCUREMENT_POLICY_001__v1_0__article_016` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `vector` | 33.33% | 34.19% | 2 | `TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`TRAVEL_POLICY_001__v1_0__article_014`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_021` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `bm25` | 66.67% | 50.44% | 3 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_004` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `hybrid` | 66.67% | 61.29% | 2 | `TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_002` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `reranked` | 66.67% | 88.39% | 1 | `TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_023`<br>`TRAVEL_POLICY_001__v1_0__article_005`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_002` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `vector` | 100.00% | 100.00% | 1 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_019`<br>`LEAVE_POLICY_001__v1_0__article_040`<br>`LEAVE_POLICY_001__v1_0__article_036`<br>`LEAVE_POLICY_001__v1_0__article_037` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `bm25` | 100.00% | 38.69% | 5 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_043`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_005`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_042`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_033`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_009` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `hybrid` | 100.00% | 100.00% | 1 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_033`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_018`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_032`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_016` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `reranked` | 100.00% | 100.00% | 1 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_003`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_015`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_016`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_033` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `vector` | 100.00% | 91.97% | 1 | `LEAVE_POLICY_001__v1_0__article_012`<br>`LEAVE_POLICY_001__v1_0__article_013`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`LEAVE_POLICY_001__v1_0__article_011` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `bm25` | 100.00% | 87.72% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`PROCUREMENT_POLICY_001__v1_0__article_015`<br>`LEAVE_POLICY_001__v1_0__article_012`<br>`LEAVE_POLICY_001__v1_0__article_023` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `hybrid` | 100.00% | 100.00% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`LEAVE_POLICY_001__v1_0__article_012`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`LEAVE_POLICY_001__v1_0__article_013`<br>`LEAVE_POLICY_001__v1_0__article_011` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `reranked` | 100.00% | 100.00% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`LEAVE_POLICY_001__v1_0__article_012`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_018` |  |

> `offline` 使用确定性哈希词法向量和词项重排，仅验证数据集、指标、
> 授权边界与 CI 回归链路；它不代表真实 BGE 语义质量，必须使用 `--mode bge` 重新测量。
