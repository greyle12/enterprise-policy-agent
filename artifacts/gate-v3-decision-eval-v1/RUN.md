# v3 第 2 步运行说明

当前确认状态：9 条 `V3U` 样本已由用户确认，四值报告已生成于
`decision-report-confirmed/four-value-report.json`。`review-draft.json` 保留为确认前原始草案，
正式输入为 `review.json`，并由 `confirmed-lock.json` 绑定。

## 当前（确认前）

PowerShell：

```powershell
python -X utf8 -m scripts.verify_confirmed_gate_contract
python -X utf8 -m scripts.verify_gate_v3_decision_review
python -X utf8 -m unittest tests.evaluation.test_gate_v3_decision_eval -v
```

预期：既有 v1 契约校验通过；草案校验通过但状态仍为
`pending_user_confirmation`；单元测试通过。此阶段不产生四值评测成绩。

## 用户确认后

确认后由维护者创建 `review.json`、`user-confirmation.json` 和
`confirmed-lock.json`，再运行：

```powershell
python -X utf8 -m scripts.evaluate_gate_v3_decisions `
  --contract-dir .\artifacts\gate-decision-contract-v1 `
  --unresolved-review .\artifacts\gate-v3-decision-eval-v1\review.json `
  --unresolved-confirmation .\artifacts\gate-v3-decision-eval-v1\user-confirmation.json `
  --unresolved-lock .\artifacts\gate-v3-decision-eval-v1\confirmed-lock.json `
  --output-dir .\artifacts\gate-v3-decision-eval-v1\decision-report
```

评测脚本会在确认文件、哈希或状态不完整时 fail-closed，不输出部分数值。
输出只属于决策层：每条记录保存解析意图、证据 span、四值结果、错误和混淆矩阵。
它不读取检索结果，不调用模型，不改变运行时。
