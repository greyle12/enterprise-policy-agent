# 门控决策层诊断

新增scripts/evaluate_gate_decisions.py、tests/evaluation/test_gate_decisions.py。旧门控、规则、标签、生产窗口不改。
这一步脱离真实排名，以“合法来源锚点存在、目标缺失但在授权候选内”为假设，直接测试query/rule的允许或拒绝。
不是重做真实检索，不是绕过运行时权限检查，也不是新的Recall@5结果。不计模型推理或延迟成绩。

显式PAIRS关联查询和规则；expected_allow复用用户确认的supplement_needed。
规则配对是助手派生，未单独人工复核，因此仅作为探索性诊断，不写成简历成绩。
CHG-005直接查询城市名单，其主要证据就是城市分类，不能将原“无需补充”错当作拒绝目标；
因不适用假设而明确排除，原标签不删除、不修改。总计11+8=19对，分集合报告，不选择性只留成功案例。

正例=应允许补充：TP正确允许、FN误拒绝；负例=应拒绝补充：TN正确拒绝、FP漏拦。
accuracy=(TP+TN)/N；needed_allow_recall=TP/(TP+FN)；unnecessary_block_recall=TN/(TN+FP)；
allow_precision=TP/(TP+FP)。分母为0输出null。这些是决策分类指标，不是检索指标。

|集合|版本|N|正确允许|正确拒绝|漏拦|误拒绝|决策正确率|
|---|---|---:|---:|---:|---:|---:|---:|
|原挑战开发集|v1|11|3|2|2|4|45.45%|
|原挑战开发集|v2|11|7|2|2|0|81.82%|
|后续8条已观察集|v1|8|4|1|3|0|62.50%|
|后续8条已观察集|v2|8|3|1|3|1|50.00%|

后续8条v2误拒绝V2U-003：“这是否意味着不用再交财务复核？”
它是在问能否免除，不是要求省略复核内容。真实检索已含目标所以原验收没有触发门控；
本决策层诊断暴露了潜在错误，不能声称该错误已经导致真实Top5下降。
v2在后续集漏拦V2U-001/006/008。全部决策、子句轨迹、理由、排除与错误列表保存在报告。
本次保持v2不变，不继续拟合本批结果。

## PowerShell

将新增文件按ZIP相对路径添加，同名文件已存在先比较，不覆盖不同内容。依赖此前冻结和确认文件。

```powershell
python -X utf8 -m unittest tests.evaluation.test_gate_decisions tests.evaluation.test_coverage_gate_v2 tests.evaluation.test_coverage_gate -v
$resultDir = ".\artifacts\gate-decision-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.evaluate_gate_decisions --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "决策评测失败" }
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

预期9项测试通过；decision-report.json status=completed，11条与8条分开统计，结果如表。
无需torch和模型，耗时不报告。冻结校验失败不绕过；输入确认记录及代码指纹入报告。
旧数据已经观察过，本诊断不再称独立泛化验收；缺少第三方配对标注核验。
未提交、推送或部署。建议提交：test(retrieval): diagnose direct coverage gate decisions
