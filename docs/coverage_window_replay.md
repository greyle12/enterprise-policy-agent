# 冻结规则候选回放

新增 scripts/replay_coverage_windows.py 与 tests/evaluation/test_coverage_window_replay.py。
原冻结选择器文件不修改，以免破坏既有 freeze 指纹。实验适配函数 select_window
显式接受 window=20/40，校验候选上限、去重、授权和 baseline 子集；其前2位锚点、
保留前4位、最多补充1条的算法与旧函数一致。20窗口逐查询与旧函数核对结果及事件。
标签和两条规则不修改。规则不读取查询或标签，标签仅用于事后评分。

安装时将 ZIP 解压到临时目录，再将 scripts、tests、docs 三个目录下的新增文件按相对
路径添加到项目；如目标文件已存在，请先比较，不覆盖不同内容。results 仅为本次回放证据。

PowerShell（项目根目录，使用前一步生成的完整证据 ZIP）：

```powershell
python -X utf8 -m unittest tests.evaluation.test_coverage_window_replay -v
$output = ".\artifacts\coverage-replay-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.replay_coverage_windows `
  --evidence-zip ".\artifacts\retrieval-candidate-window-bge-20260908-202536.zip" `
  --output-dir $output
if ($LASTEXITCODE -ne 0) { throw "回放失败，请查看 failure.json" }
Compress-Archive -Path "$output\*" -DestinationPath "$output.zip"
```

输出 replay-report.json，包含两窗口全部16条明细、替换事件、被移出条款及其标签、
三个指标的全部回归、7条控制查询的独立统计。成功 status=completed。
无新模型推理。计时仅包括选择函数及其输入校验，单次采样，排除文件读取、建链接、
模型推理和指标计算；不可作为新端到端耗时或生产性能。

本次实际回放：20窗口 Recall@5 73.75%→82.0833%；40窗口 73.75%→88.8542%。
两组 MRR@5 均保持0.96875，无已标注指标回归。控制组仍发生非必要补充：
20窗口 COV-008，40窗口 COV-004/008；零指标回归不等于零语义风险。
本集合是用户确认AI辅助开发集，不是独立测试集；不更改生产默认窗口。

Git建议：只提交新增脚本、测试、文档，排除现有Phase38修改和运行结果。
提交信息：test(retrieval): replay frozen coverage rules across candidate windows
