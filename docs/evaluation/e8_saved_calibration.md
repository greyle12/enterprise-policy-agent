# E8 已保存 Judge 结果的离线校准

已完成本地校准流程，并对 E7 的三条实际结果执行待审状态汇总。本轮没有新增模型调用。实际人工复核仍为 0，校准结论保持 pending。

## 运行

```powershell
.venv/Scripts/python.exe -m scripts.calibrate_saved_judge --report artifacts/evaluation-e7-live-judge-smoke/judge-report.json --input tests/evaluation/semantic_smoke.jsonl --labels artifacts/evaluation-e7-live-judge-smoke/judge-blind-review.jsonl --output-dir artifacts/evaluation-e8-saved-calibration/pending-calibration
```

命令只读取已有报告、输入与审核文件，输出新的 `calibration-report.json/.md`，不创建模型客户端。每次使用新目录，已存在目录会在读取/写入前被拒绝。

## 绑定与统计

- 报告绑定输入文件和证据哈希；人工标签必须绑定这份报告的 SHA-256 与输入 SHA-256。
- 核对 case/repetition 的唯一性与完整报告覆盖；人工审核可只完成部分条目，其余计为 pending/missing。
- 审核材料中的问题、答案、reference、rubric、证据必须与原输入相同；标签不得提供或覆盖 Judge 判定。
- 已完成审核必须明确提供 human=PASS/FAIL/UNKNOWN、reviewer、rationale、review_status=human_reviewed 及 judge_visibility=seen/not_seen。程序只校验输入契约，不认证 reviewer 身份或独立性。
- 待审字段保持 null；接口错误统一按 Judge UNKNOWN 排除二分类计分。人工 UNKNOWN 也不进入二分类分母，但仍计为已完成审核。
- 同时输出所有有效复核、未见过 Judge 结果的复核、已见过结果的复核三组统计。两次重复运行不增加独立样本数。

用户此前已看到三条 Judge 判定；如果用户本人审核，应记录 judge_visibility=seen。不能把这种复核描述成独立盲审。若其他未见结果的人审核，需由操作者明确填写 not_seen。

## 当前实际状态

本轮读取的是 E7 待审模板，并未填写人工标签：

| 项目 | 当前值 |
|---|---|
| 已保存的真实 Judge 结果 | 3 |
| 人工完成审核 | 0 |
| 待审核 | 3 |
| 二分类有效分母 | 0 |
| agreement / kappa / false pass / false fail | null |
| 新增模型调用 | 0 |

先前对人工标签问题的回复重复了复核说明，没有给出具体审核人、三个判定及理由，因此未转换为人工审核证据。

审核填写材料位于 `artifacts/evaluation-e8-saved-calibration/review-packet.md` 和 `human-labels.pending.jsonl`。复制 JSONL 为新的 completed 文件后填写人工字段，并保留指纹和原问题/证据。随后把运行命令的 `--labels` 指向 completed 文件、`--output-dir` 指向新目录。

## 验证

新增 15 项测试，覆盖待审不计分、可见性分组、UNKNOWN/接口失败排除、指纹错配、内容变更、重复/未知条目、不完整报告与禁止覆盖。与既有 21 项升级测试合并执行，**36 项全部通过**。新增两个 Python 文件 Ruff check / format check 通过。

测试中的审核人及标签全部是明确命名的 synthetic fixture；不进入实际人工样本数。JUnit 位于 `artifacts/evaluation-e8-calibration-tests/tests.xml`。

三个合成 smoke 的校准即使全部一致，也只能说明这三个例子的复核情况。实际 Agent 输出、独立数据、扩大样本和部署门禁仍需后续证据。
