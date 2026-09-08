# 补充必要性门控实验 v1

只新增实验脚本和测试；冻结规则、标签、候选、旧选择器和生产默认值均不修改。
依赖上一包 scripts/replay_coverage_windows.py 及本地冻结评审目录。
ZIP 内 scripts/tests/docs 按项目相对路径添加；若同名文件已存在，先比较，勿覆盖不同内容。

## 原因与设计

COV-004 合住问题因前2名出现住宿标准而触发城市分类补充；COV-008 只问时限、
明确排除审批，仍因超时报销锚点触发特殊费用审批补充。旧规则描述条款关系，未判断查询需求。

两个版本在运行前固定，仅输入查询文本和规则名，不读取标签、案例编号或控制标签：
- explicit_veto：住宿规则在合住/同住/明确一二三类城市时否决；费用规则在明确不需要审批/流程时否决。
- positive_intent：先使用相同否决，再要求住宿金额/限额或审批/复核/说明等正向词。
完整正则保存在脚本 POLICY 并随报告记录。两组窗口使用同一 POLICY。

门控只接受或否决原选择器唯一提案；否决时恢复完整原始 Top5，不寻找第二个补充。
原来的前2位锚点、前4位保留、最多1条补充、权限和20/40窗口校验由冻结回放执行。

这是观察控制查询后设计的开发集实验，没有独立测试集，不宣称泛化收益或简历新成绩。
例如“一类城市怎么判断”也可能被宽泛否决；口语表达、否定作用域和复合问题尚未独立验证。

## 实测（16条；每组7条控制）

|窗口|版本|Recall@5|MRR@5|nDCG@5|控制补充数|相对冻结规则回归|
|---|---|---|---|---|---|---|
|20|冻结规则|82.0833%|0.96875|0.88935|1|基线|
|20|explicit_veto|82.0833%|0.96875|0.88935|0|无|
|20|positive_intent|78.9583%|0.96875|0.88119|0|COV-009|
|40|冻结规则|88.8542%|0.96875|0.90933|2|基线|
|40|explicit_veto|88.8542%|0.96875|0.90933|0|无|
|40|positive_intent|85.7292%|0.96875|0.90117|0|COV-009|

两个窗口的原始 reranked Recall 都是73.75%。显式否决保留全部原收益；
正向意图均损失3.125个百分点，因为“这种超时怎么处理”没有显式审批词但标注需要审批条款。
控制计数是原有控制标签上的选择变化，不是另行人工验证的误补充率。

报告包含所有查询的原始/冻结/门控结果、原替换事件、实际被替换条款与标注、
全部指标回归及独立控制统计。未标注不代表无关。冻结回放完整嵌入 baseline_evidence。
计时仅门控选择，单次、固定顺序、未预热（含首次正则编译）；排除旧选择、推理、IO、评分。
禁止比较这些微小时差来宣称性能提升。环境、脚本哈希、输入ZIP和冻结标签规则哈希均有记录。

## PowerShell 复现

```powershell
python -X utf8 -m unittest tests.evaluation.test_coverage_gate tests.evaluation.test_coverage_window_replay -v
$resultDir = ".\artifacts\coverage-gate-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.experiment_coverage_gate `
  --evidence-zip ".\artifacts\retrieval-candidate-window-bge-20260908-202536.zip" `
  --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "门控回放失败" }
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

预期5项测试通过，gate-report.json status=completed，质量指标如表；耗时随环境变化。
输入必须为完整候选证据ZIP，不能仅用汇总replay-report.json。失败写failure.json并返回非零。

建议只提交三个新增脚本/测试/文档文件：
`test(retrieval): compare necessity gates on frozen candidate replay`
不要夹带现有Phase38修改；本步骤无运行时切换、提交或推送。
