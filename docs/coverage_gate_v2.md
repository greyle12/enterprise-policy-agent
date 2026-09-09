# 门控v2：子句需求实验

新增scripts/coverage_gate_v2.py、scripts/replay_gate_v2.py、tests/evaluation/test_coverage_gate_v2.py。
按相对路径添加，若同名文件存在先比较内容。依赖旧冻结、确认和回放代码包。
不修改v1、两条规则、语料、标签、候选和生产默认值；不提交或推送。

## 设计

按标点及部分转折连接词切分子句，逐句记录need/veto；任一子句的正向需求优先于局部否决。
识别双重否定、城市类别疑问、已知类别与流程排除；未知情形允许原提案。
门控仅输入query和rule_id，标签仅用于评分。选择只接受或否决原唯一提案，否决恢复完整原Top5。
前4位、最多1补充及20/40候选边界仍由旧选择器保证。逐句判定日志保存到报告。
这是有限正则启发式，不是通用中文语义解析器。

## 实测回放

|集合|N|窗口|冻结规则Recall|v1 Recall|v2 Recall|
|---|---:|---:|---:|---:|---:|
|原开发集|16|20|82.0833%|82.0833%|82.0833%|
|原开发集|16|40|88.8542%|88.8542%|88.8542%|
|挑战集现转为回归集|12|20|87.50%|79.1667%|87.50%|
|挑战集现转为回归集|12|40|95.8333%|83.3333%|95.8333%|

MRR分别为原集0.96875，挑战20为0.8541667、40为0.9375，各方案不变。
v2相对v1和冻结规则没有已标注指标回归。无新增模型推理；只回放已捕获真实候选。
挑战控制补充20拦截1/2、40拦截2/3；两个窗口仍漏拦CHG-009：
类别已知在前一子句，金额需求在后一子句，当前没有跨句继承已知类别，后一需求优先导致放行。
不继续针对这批数据追加规则。CHG-001/002/007的已观测误拦得到修复；
无候选或无锚点的问题仍存在，不算门控成功。

报告包含所有窗口与案例、raw/frozen/v1/v2指标、实际补充机会、逐句解释、替换和回归。
legacy中needs组仅指非控制组，不等价每条都需要规则补充。未标注不是无关。
计时仅v2选择，单次未预热、排除模型/IO/评分，不作端到端或性能提升声明。
本批挑战样本已经指导v2设计，因此只能回归；若需泛化结论必须冻结v2后使用新未见样本。

## PowerShell

```powershell
python -X utf8 -m unittest tests.evaluation.test_coverage_gate_v2 tests.evaluation.test_coverage_gate tests.evaluation.test_coverage_window_replay tests.evaluation.test_gate_challenge -v
$resultDir = ".\artifacts\gate-v2-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.replay_gate_v2 `
  --legacy-zip ".\artifacts\retrieval-candidate-window-bge-20260908-202536.zip" `
  --challenge-zip ".\artifacts\gate-challenge-bge-20260909-090550.zip" `
  --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "v2回放失败" }
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

预期12 tests通过，gate-v2-report.json status=completed，质量指标如表。输入ZIP路径按实际位置修改。
报告记录两个输入ZIP、v2/回放代码哈希及本环境。冻结输入校验失败不绕过。
建议提交：test(retrieval): add clause-scoped coverage gate v2 replay
