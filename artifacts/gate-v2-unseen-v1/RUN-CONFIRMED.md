# v2新增8条：确认后的真实评测

用户2026-09-09回复“确认”，全部8条草案获确认。新增user-confirmation.json绑定原草案SHA；不覆盖草案、freeze和draft-lock。
新增独立scripts/run_gate_v2_unseen.py，复用现有模型加载、检索运行器和选择器。避免修改旧冻结实验入口。
脚本、测试及本目录新增文件按ZIP原路径加入；同名文件存在时先比较，不覆盖不同内容。
依赖前面v2、挑战集与冻结草案代码包；无需安装新评测依赖，使用原来成功执行BGE的venv。

```powershell
python -X utf8 -m unittest tests.evaluation.test_gate_v2_unseen tests.evaluation.test_coverage_gate_v2 tests.evaluation.test_coverage_gate tests.evaluation.test_coverage_window_replay -v
$resultDir = ".\artifacts\v2-unseen-bge-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.run_gate_v2_unseen --device cpu --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "评测失败，请回传 failure.json" }
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

预期12项测试通过。真实评测完成输出v2-unseen-report.json status=completed，两个窗口各8条。
raw-20/40.json保留原四检索通道和错误；candidates-20/40.json保留本批新问题候选。
最终报告比较raw/frozen/v1/v2，分别报告全部8条、4条controls和4条needs_supplement。
blocked指v2拦截，v1_blocked指v1拦截，opportunities指原冻结选择器实际提案数。
逐条包含全部排名ID、标注、指标、子句轨迹、替换和相对冻结方案回归；可以据此重算相对v1回归。

所有输入一致，每路候选与重排窗口20/40，RRF常数60，K=5，保留前4位、最多补1条。
报告保留模型锁、依赖、Git状态、源文件清单、评测入口指纹和门控冻结记录。
原草案status仍为pending，最终确认状态以独立confirmation为准；旧草案校验器不作授权判断。
任何通道错误保留原始证据并阻断最终成功报告，不排除失败样本计算漂亮成绩。

Recall@5为全部已标注相关Chunk的查询宏平均；MRR@5为首相关倒数，未命中0；nDCG使用grade。
单次固定顺序、无预热；selection_ms只含冻结选择及两门控，排除模型和评分，不当作端到端耗时。
当前环境缺少torch，已保存failure.json，没有本批推理成绩；这不代表用户Windows缺少torch。
本地单测只运行合成案例，不执行新8条选择器。推理后不据结果改标签、规则或删样本。
已知作者背景使其仍是定向未见问法集，而非独立第三方盲测或代表性生产集。
生产默认与CHG-009处理不改。未commit/push。
建议提交：test(retrieval): evaluate confirmed unseen v2 queries
