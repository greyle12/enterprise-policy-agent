# 用户确认后的挑战集运行

2026-09-09用户回复“确认”，确认原12条草案。原草案、冻结文件不改写，新增 user-confirmation.json 绑定草案哈希。
新增 scripts/run_gate_challenge.py、tests/evaluation/test_gate_challenge.py。同名文件若存在先比较，勿覆盖不同修改。
依赖前两步代码包和原冻结语料，新增文件按ZIP相对路径添加。

```powershell
python -X utf8 -m unittest tests.evaluation.test_gate_challenge tests.evaluation.test_coverage_gate tests.evaluation.test_coverage_window_replay -v
$resultDir = ".\artifacts\gate-challenge-bge-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.run_gate_challenge --device cpu --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "真实挑战集评测失败，请回传 failure.json" }
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

预期8项测试通过；推理完成生成 challenge-report.json status=completed，两个窗口各12条。
复用本地既有锁定模型，不需重新调参。无offline替身模式。
原 verify_gate_challenge_freeze 是草案阶段只读脚本，仍报告待确认；本次运行入口读取独立确认记录判断授权。

指标为全部标注相关Chunk的按查询宏平均Recall@5、首相关倒数MRR@5及nDCG@5。
报告含all/controls/needs_supplement分组、实际补充机会、拦截数、完整替换和回归。
raw-20/40.json 与 candidates-20/40.json 保存四方案原始报告及新候选。
任何通道错误保留原始错误报告、退出非零，不静默排除样本生成验收成绩。
selection_ms仅选择与门控，排除推理；原始通道耗时不作为性能成绩。0预热、单次固定顺序。
模型、依赖环境、Git和代码指纹保存在最终报告。本集合为用户确认AI辅助定向挑战集，非盲测。

本环境缺少torch，真实运行已阻断，失败报告附包；不代表你的Windows环境缺少torch。
本地校验/合成单测不等于真实BGE集成通过，需回传真实报告后验收。
不修改规则、标签、生产默认值；不提交或推送。
建议提交：test(retrieval): evaluate confirmed gate challenge with fresh BGE candidates
