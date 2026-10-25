# E8 助手复核对照与当前交接

2026-10-24（Asia/Shanghai）。在用户要求助手审阅之后，将已保存的助手意见接入离线校准入口，单独输出诊断对照。原始问题、Judge 响应和人工待审标签保留，本轮模型调用为 0。

## 当前对照

| 用例 | 维度 | 助手复核 | 保存的 Judge 判定 | 是否一致 |
|---|---|---|---|---|
| SEM-001 | 正确性 | PASS | PASS | 是 |
| SEM-002 | 忠实性 | FAIL | FAIL | 是 |
| SEM-003 | 完整性 | FAIL | FAIL | 是 |

三条助手意见均已接触原 Judge 结果，只属于辅助诊断。没有新增人工标签；human_reviewed_samples=0，人工 agreement/kappa/false pass/false fail 均为 null。不得将三条一致表述为 Judge 准确率 100%、独立盲审或人工校准通过。

## 实现与复现

复用 `scripts/calibrate_saved_judge.py`，增加可选 `--assistant-review`。原人工标签入口及统计保持原行为，助手对照写入独立的 `assistant_comparison`。

```powershell
.venv/Scripts/python.exe -m scripts.calibrate_saved_judge --report artifacts/evaluation-e7-live-judge-smoke/judge-report.json --input tests/evaluation/semantic_smoke.jsonl --labels artifacts/evaluation-e8-saved-calibration/human-labels.pending.jsonl --assistant-review artifacts/evaluation-e8-assistant-review/assistant-review.json --output-dir artifacts/evaluation-e8-assistant-comparison/report
```

输出目录已存在，复现时请使用新目录。报告位于 `artifacts/evaluation-e8-assistant-comparison/report/calibration-report.json`。

助手包必须匹配输入与实际报告指纹，case/repetition 唯一并存在于报告中，rubric/证据 ID 与输入匹配，来源为 assistant_reviewed，human 与 reviewer_decision 为空，eligible_for_human_calibration=false。拒绝错误绑定、重复、混入人工标签、声称人工计分资格及引用未提供证据。UNKNOWN 或 Judge 接口失败不算一致或分歧。

新增 8 项测试，加上原 15 项保存报告校准、21 项评测升级及 2 项基线准备测试，合计 **46 项通过**。两份修改的 Python 文件 Ruff check / format check 通过。JUnit：`artifacts/evaluation-e8-assistant-comparison-tests/tests.xml`。

以前的 E8 证据清单对应当时的源码快照。本轮扩展了校准脚本和测试，另存新的清单，不回写历史验收或哈希。

## 后续可继续项

| 事项 | 当前状态 | 下一项具体工作 |
|---|---|---|
| E2–E5 工程交付 | 已验收 | 继续保留确定性与离线 Mock 的结论边界 |
| E6 基线 | 已准备 | 新一轮真实 Agent 运行前绑定新代码/数据指纹 |
| E7 Judge smoke | 三次真实调用已完成 | 现有授权仅覆盖这三条合成样例 |
| E7 实际 Agent 输出 | 待完成 | 为现有 Router 增加评测用模型注入、整轮调用预算及可追溯输出采集；先通过本地模拟验证 |
| E8 人工校准 | 待完成 | 取得具体人工判定与理由后离线汇总；助手对照不能替代 |
| E9 真实 A/B | 待完成 | 有实际候选输出、同条件指纹和安全资格证据后再执行 |
| E10 外部观测 | 待完成 | 验证 Langfuse 目标、脱敏范围及真实接收回执 |

接下来优先推进实际 Agent 输出采集的本地实现与模拟验证。三条 Judge smoke 的旧预算与数据范围不能自动扩展到 19 条工作流或语料外发。人工复核可以独立进行，不阻止本地采集功能开发。
