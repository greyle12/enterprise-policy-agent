# 企业制度 Agent 检索评测报告

- 运行模式：`offline`
- Embedding：`deterministic_hashed_lexical_v1`
- Reranker：`deterministic_lexical_overlap_v1`
- 外部模型推理：`false`
- 数据集 SHA-256：`db7b25617690afc26bbf1339650a4aa1c273f8d8476aad69d016df6481033b95`
- 语料 SHA-256：`5260c50ae8f0d4fddc4bd5ad7d1d325bd53c76487fa13b91e7aefaee7a445737`
- 查询数：6
- 候选池：Top 20
- 门禁阈值：Recall@5 ≥ 80.00%，MRR@5 ≥ 80.00%，nDCG@5 ≥ 80.00%
- 质量门禁：**未通过**

## 消融指标

| 检索通道 | Recall@1 | Recall@3 | Recall@5 | MRR@5 | nDCG@5 | 平均耗时 | 错误 | 门禁 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `vector` | 8.33% | 30.56% | 36.11% | 27.78% | 26.56% | 7.546 ms | 0 | 仅对照 |
| `bm25` | 33.33% | 38.89% | 69.44% | 58.89% | 51.76% | 1.312 ms | 0 | 仅对照 |
| `hybrid` | 25.00% | 36.11% | 44.44% | 45.00% | 39.28% | 8.178 ms | 0 | 未通过 |
| `reranked` | 25.00% | 38.89% | 61.11% | 51.39% | 46.02% | 8.908 ms | 0 | 未通过 |

## 查询明细

| 用例 | 分级标注 | 通道 | Recall@5 | nDCG@5 | 首个相关排名 | Top 5 | 错误 |
|---|---|---|---:|---:|---:|---|---|
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `vector` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_023`<br>`TRAVEL_POLICY_001__v1_0__article_010`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_020`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_009` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `bm25` | 50.00% | 33.74% | 1 | `TRAVEL_POLICY_001__v1_0__article_007`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_014` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `hybrid` | 50.00% | 13.05% | 5 | `TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_023`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_007` |  |
| `RET-021` 口语住宿额度 | `TRAVEL_POLICY_001__v1_0__article_008` (G3)<br>`TRAVEL_POLICY_001__v1_0__article_007` (G2) | `reranked` | 50.00% | 21.28% | 2 | `TRAVEL_POLICY_001__v1_0__article_016`<br>`TRAVEL_POLICY_001__v1_0__article_007`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_023`<br>`TRAVEL_POLICY_001__v1_0__article_004` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `vector` | 0.00% | 0.00% | — | `LEAVE_POLICY_001__v1_0__article_037`<br>`LEAVE_POLICY_001__v1_0__article_029`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_034`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_031`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_001` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `bm25` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_020`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_014` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `hybrid` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_005`<br>`LEAVE_POLICY_001__v1_0__article_037`<br>`TRAVEL_POLICY_001__v1_0__article_006`<br>`LEAVE_POLICY_001__v1_0__article_029` |  |
| `RET-022` 凭证丢失口语问法 | `TRAVEL_POLICY_001__v1_0__article_018` (G3) | `reranked` | 0.00% | 0.00% | — | `TRAVEL_POLICY_001__v1_0__article_006`<br>`TRAVEL_POLICY_001__v1_0__article_020`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`TRAVEL_POLICY_001__v1_0__article_004`<br>`TRAVEL_POLICY_001__v1_0__article_005` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `vector` | 100.00% | 50.00% | 3 | `PROCUREMENT_POLICY_001__v1_0__article_005`<br>`TRAVEL_POLICY_001__v1_0__article_016`<br>`PROCUREMENT_POLICY_001__v1_0__article_018`<br>`TRAVEL_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_017` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `bm25` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_024`<br>`LEAVE_POLICY_001__v1_0__article_038` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `hybrid` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`TRAVEL_POLICY_001__v1_0__article_018`<br>`TRAVEL_POLICY_001__v1_0__article_016` |  |
| `RET-023` 精确条款与阿拉伯数字 | `PROCUREMENT_POLICY_001__v1_0__article_018` (G3) | `reranked` | 100.00% | 100.00% | 1 | `PROCUREMENT_POLICY_001__v1_0__article_018`<br>`PROCUREMENT_POLICY_001__v1_0__article_028`<br>`PROCUREMENT_POLICY_001__v1_0__article_017`<br>`TRAVEL_POLICY_001__v1_0__article_018`<br>`TRAVEL_POLICY_001__v1_0__article_016` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `vector` | 66.67% | 48.06% | 3 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_005`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_014`<br>`TRAVEL_POLICY_001__v1_0__article_019` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `bm25` | 66.67% | 50.44% | 3 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`TRAVEL_POLICY_001__v1_0__article_013`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_004` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `hybrid` | 66.67% | 61.29% | 2 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_010`<br>`TRAVEL_POLICY_001__v1_0__article_012` |  |
| `RET-024` 跨制度逾期报销 | `TRAVEL_POLICY_001__v1_0__article_019` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` (G3)<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_025` (G2) | `reranked` | 66.67% | 50.44% | 3 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_002`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027`<br>`TRAVEL_POLICY_001__v1_0__article_019`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_010` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `vector` | 0.00% | 0.00% | — | `PROCUREMENT_POLICY_001__v1_0__article_020`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_024`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_016`<br>`PROCUREMENT_POLICY_001__v1_0__article_002`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_027` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `bm25` | 100.00% | 38.69% | 5 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_043`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_005`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_042`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_033`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_009` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `hybrid` | 0.00% | 0.00% | — | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_042`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_016`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_032`<br>`LEAVE_POLICY_001__v1_0__article_002`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_002` |  |
| `RET-025` 换一种说法询问账号共享 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_009` (G3) | `reranked` | 100.00% | 43.07% | 4 | `INFORMATION_SECURITY_POLICY_001__v1_0__article_043`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_005`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_033`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_009`<br>`INFORMATION_SECURITY_POLICY_001__v1_0__article_003` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `vector` | 50.00% | 61.31% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_016`<br>`LEAVE_POLICY_001__v1_0__article_027`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_033`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_001` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `bm25` | 100.00% | 87.72% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`PROCUREMENT_POLICY_001__v1_0__article_015`<br>`LEAVE_POLICY_001__v1_0__article_012`<br>`LEAVE_POLICY_001__v1_0__article_023` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `hybrid` | 50.00% | 61.31% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_003`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_034` |  |
| `RET-026` 跨制度专项证明材料 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015` (G3)<br>`LEAVE_POLICY_001__v1_0__article_012` (G3) | `reranked` | 50.00% | 61.31% | 1 | `EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_006`<br>`PROCUREMENT_POLICY_001__v1_0__article_015`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_032`<br>`EXPENSE_REIMBURSEMENT_GUIDE_001__v1_0__article_003` |  |

> `offline` 使用确定性哈希词法向量和词项重排，仅验证数据集、指标、
> 授权边界与 CI 回归链路；它不代表真实 BGE 语义质量，必须使用 `--mode bge` 重新测量。
