# 门控 v3 候选回放报告

状态：`completed`；模式：`captured_bge_ranking_replay`。
本报告重放已完成的真实 BGE 候选排名；本步骤没有重新执行 embedding、向量检索或 reranker 推理。
标签来自用户确认的 COV 开发集，未经过独立双人复核，因此 `numeric_resume_ready=false`，不能当作独立测试集成绩。

## 配置与计时边界

两个窗口使用同一语料、权限范围、标签、候选记录、RRF 常数 60 和最终 K=5；选择策略固定为前 2 个锚点、保留前 4 条、最多补充 1 条。
选择耗时只覆盖冻结选择器和 v3 选择器本身，排除归档读取、模型加载、向量化、检索、RRF 和重排。

## 窗口汇总

| 窗口 | 阶段 | N | Recall@5 | MRR@5 | nDCG@5 | 改变数 | 回归数 | 平均选择耗时 (ms) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 20 | baseline | 16 | 0.7375 | 0.9688 | 0.8599 | 0 | 0 | — |
| 20 | frozen | 16 | 0.8208 | 0.9688 | 0.8893 | 4 | 0 | 0.007 |
| 20 | v3 | 16 | 0.7896 | 0.9688 | 0.8812 | 3 | 1 | 0.209 |
| 20 | v3 - frozen | — | -0.0312 | +0.0000 | -0.0082 | — | — | — |
| 40 | baseline | 16 | 0.7375 | 0.9688 | 0.8599 | 0 | 0 | — |
| 40 | frozen | 16 | 0.8885 | 0.9688 | 0.9093 | 8 | 0 | 0.007 |
| 40 | v3 | 16 | 0.8573 | 0.9688 | 0.9012 | 6 | 1 | 0.042 |
| 40 | v3 - frozen | — | -0.0312 | +0.0000 | -0.0082 | — | — | — |

## 决策与回归诊断

关系决策计数（两个窗口合并）：`{"ALLOW": 14, "DENY": 6, "UNRESOLVED": 44, "NOT_APPLICABLE": 0}`。
v3 事件计数（两个窗口合并）：`{"already_present": 6, "denied_by_gate": 3, "outside_authorized_candidate_window": 5, "supplemented": 9}`。
每条查询的候选池、基线、两种选择结果、事件、被替换条款和指标均保存在 `replay-report.json` 的 `cases` 中；控制查询单独保存在各窗口的 `controls` 中。

## 限制

- COV labels are a user-confirmed AI-assisted development set, not an independent holdout.
- This replay measures evidence selection on captured rankings; it does not measure new model quality or production latency.
- UNRESOLVED is intentionally replayed as the frozen supplement fallback and is reported separately from ALLOW/DENY success.
