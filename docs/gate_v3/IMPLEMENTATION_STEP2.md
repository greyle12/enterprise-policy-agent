# 门控 v3 第 2 步：冻结 UNRESOLVED 复核集与四值评测入口

本步骤建立一批用户确认前的语义复核草案，并实现独立的四值决策评测脚本。
它不修改 `scripts/coverage_gate_v2.py`、`scripts/experiment_coverage_gate.py`、
已确认的 20 条契约、旧选择器、FastAPI、检索数据或生产 Provider。

## 为什么先复核再评测

Step 1 已经让解析器在信息不足、指代未绑定和城市条件冲突时返回
`UNRESOLVED`。如果直接把助手编写的标签当作真值，评测只能证明代码复现了自己的假设。
因此本步骤先保存 9 条小批草案，状态为 `pending_user_confirmation`；没有用户确认时，
评测脚本拒绝运行并且不生成数值报告。

## 四值指标约定

- `overall_accuracy`：所有样本中 `actual_decision == expected_decision` 的比例；错误样本计入分母。
- `known_decision_accuracy`：只在期望值不是 `UNRESOLVED` 的样本上计算精确匹配率，单独报告分母。
- `four_value_output_coverage`：实际输出四个合法值（而不是运行错误）的样本比例。
- `known_decision_coverage`：实际输出确定值（`ALLOW`、`DENY` 或 `NOT_APPLICABLE`）的比例；`UNRESOLVED` 不算确定决策。
- `unresolved_recall`：期望 `UNRESOLVED` 的样本中实际返回 `UNRESOLVED` 的比例。
- `unresolved_precision`：实际返回 `UNRESOLVED` 的样本中期望也是 `UNRESOLVED` 的比例。
- 混淆矩阵保留 `ALLOW`、`DENY`、`UNRESOLVED`、`NOT_APPLICABLE` 四行四列，运行错误另列。

这些指标评估的是关系门控决策，不是 Recall@K、MRR、答案正确率或生产效果。
`NOT_APPLICABLE`（例如目标条款本身就是主要问题）保留在总体四值分母中。

## 运行与边界

```powershell
python -X utf8 -m scripts.verify_gate_v3_decision_review
python -X utf8 -m unittest tests.evaluation.test_gate_v3_decision_eval -v
```

用户确认草案后，按 `artifacts/gate-v3-decision-eval-v1/RUN.md` 创建确认锁并运行评测。
评测逐条调用 `scripts.gate_v3_intent.evaluate`；预期标签只在评测后用于计分，不能驱动决策。
每条结果带原查询 span 与 reason code，解析异常保留为错误并计入总体分母。

下一步才是使用冻结候选进行实验回放；本步骤没有 `select_v3`，也没有质量成绩。
