# 门控 v3 候选回放运行记录

## 输入

- 候选归档：`upload/retrieval-candidate-window-bge-20260908-202536.zip`
- 源实验提交：`2fa68aff35ca77d390754a86c5bbec78b1371d45`（归档记录为 dirty worktree）
- 本地 v3 冻结：`artifacts/gate-v3-decision-eval-v1/`
- 评测集：已确认的 `artifacts/retrieval-coverage-review-v1/` COV 开发集（16 条）

## PowerShell 命令

```powershell
$out = "artifacts/gate-v3-replay-v1-delivery"
python -X utf8 -m scripts.replay_gate_v3 `
  --evidence-zip .\upload\retrieval-candidate-window-bge-20260908-202536.zip `
  --output-dir $out
```

输出：`replay-report.json` 和 `replay-report.md`。

## 验证命令

```powershell
python -X utf8 -m unittest `
  tests.evaluation.test_gate_v3_intent `
  tests.evaluation.test_gate_v3_decision_eval `
  tests.evaluation.test_gate_decisions `
  tests.evaluation.test_coverage_gate_v2 `
  tests.evaluation.test_coverage_gate `
  tests.evaluation.test_coverage_window_replay `
  tests.evaluation.test_gate_challenge `
  tests.evaluation.test_replay_gate_v3 -v
```

本次运行结果：相关回归测试 38 passed；回放状态 `completed`；没有新的模型推理。

## 解释边界

归档的候选排序来自真实 BGE 实验，但本步骤只重放排序后的证据选择。计时只包含冻结选择器
和 v3 选择器，不包含模型加载、query embedding、检索、RRF 或 reranker；COV 标签不是独立
留出集，因此报告不提供可直接写成生产质量的数字。
