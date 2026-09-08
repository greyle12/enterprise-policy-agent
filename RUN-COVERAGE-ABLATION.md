# 安装及运行说明

本包只增加补充证据覆盖实验，不修改已有检索运行时或原标签。详细设计、结果、失败案例、限制、测试及 Git 建议见 docs/retrieval_coverage_ablation.md。

本次使用已有真实 BGE 排名进行确定性后处理，没有新模型推理。开发集 Recall@5 从 92.50% 到 100%，MRR@5 保持 0.9500。规则源自已观察失败，不能将这个 100% 写成独立测试/生产成绩。原简历数值保持此前真实 BGE 结果。

## PowerShell 安装

先把 ZIP 下载到 Downloads，在项目根目录运行（按实际下载位置调整）：

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\enterprise-policy-agent-retrieval-coverage-20260908.zip"
$patchDir = Join-Path $env:TEMP ("retrieval-coverage-" + [guid]::NewGuid().ToString("N"))
Expand-Archive -Path $zip -DestinationPath $patchDir
python -X utf8 "$patchDir\apply_patch.py" --repo (Get-Location).Path
if ($LASTEXITCODE -ne 0) { throw "Patch conflict; no files installed" }
python -X utf8 "$patchDir\apply_patch.py" --repo (Get-Location).Path --apply
if ($LASTEXITCODE -ne 0) { throw "Patch installation stopped" }
```

安装器只安装清单中的 6 个新增文件。同名文件内容一致时跳过，不同则全量预检失败，不覆盖。代码和测试、规则、文档、报告均保持项目相对路径。安装器与本说明仅在解压目录中使用。

## 测试及重放

```powershell
python -X utf8 -m unittest discover `
  -s tests/evaluation -p test_retrieval_coverage_ablation.py -v
if ($LASTEXITCODE -ne 0) { throw "Coverage tests failed" }
$baseline = ".\artifacts\retrieval-bge-20260906-133548.zip"
$resultDir = ".\artifacts\retrieval-coverage-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.run_retrieval_coverage_ablation `
  --baseline-zip $baseline `
  --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "Coverage replay failed; inspect failure.json" }
Get-Content "$resultDir\coverage-report.md" -Encoding utf8
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

若原始证据 ZIP 在 Downloads，请修改 `$baseline`。使用你上次回传的完整 BGE 结果 ZIP，不能使用本次代码包作为输入。

预期 13 个测试通过；重放返回 completed、fresh_model_inference=false、numeric_resume_ready=false。只需现有 Python 标准库，不需要安装 torch、下载模型或访问数据库。新输出目录不能预先存在。

本次实际验证：13 项 unittest 通过，Ruff 0.14.14 检查及格式检查通过；26 条历史真实排名逐项重算并完成消融。没有执行全项目回归或新的端到端模型评测。所有逐条结果，包括无收益修改与未解决失败，见包内报告。

建议提交信息：test(retrieval): add evidence coverage replay ablation。精确的 git add 命令见项目文档；先审查已有暂存区，避免混入 Phase 38 工作。本包不包含密钥、虚拟环境、模型缓存或原始输入 ZIP，不执行提交/推送。
