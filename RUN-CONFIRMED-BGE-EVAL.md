# 安装与运行说明

本 ZIP 只增加已确认 COV 定向评测脚本、测试和文档；不包含模型缓存、密钥、虚拟环境、旧结果或 Phase 38 文件，也不会自动提交/推送。

## PowerShell 安装

在项目根目录执行。安装器先做全量冲突检查，再按清单写入；已有不同内容的文件不会被覆盖：

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\enterprise-policy-agent-retrieval-coverage-confirmed-20260908.zip"
$patchDir = Join-Path $env:TEMP ("retrieval-confirmed-" + [guid]::NewGuid().ToString("N"))
Expand-Archive -Path $zip -DestinationPath $patchDir
python -X utf8 "$patchDir\apply_patch.py" --repo (Get-Location).Path
if ($LASTEXITCODE -ne 0) { throw "冲突检查失败，未安装任何文件" }
python -X utf8 "$patchDir\apply_patch.py" --repo (Get-Location).Path --apply
if ($LASTEXITCODE -ne 0) { throw "安装中止" }
```

## 先跑保护性测试

```powershell
python -X utf8 -m unittest tests.evaluation.test_retrieval_coverage_confirmed_eval -v
```

## 真实 BGE 评测

`$modelLock` 必须指向上一轮真实 BGE 结果中的 `models.lock.json`，并且权重仍在本机 Hugging Face 缓存；不要手工改锁文件：

```powershell
$reviewDir = ".\artifacts\retrieval-coverage-review-v1"
$modelLock = ".\artifacts\retrieval-bge-20260906-133548\models.lock.json"
$resultDir = ".\artifacts\retrieval-coverage-confirmed-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

python -X utf8 -m scripts.run_retrieval_coverage_confirmed_eval `
  --review-dir $reviewDir `
  --model-lock $modelLock `
  --mode bge `
  --device cpu `
  --threads 4 `
  --warmups 1 `
  --output-dir $resultDir

if ($LASTEXITCODE -eq 2) { throw "评测被阻止；查看 $resultDir\failure.json" }
Get-Content "$resultDir\coverage-report.md" -Encoding utf8
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

成功时输出 `status=completed`、`real_model_inference=true`、`query_count=16`，但 `numeric_resume_ready` 仍为 `false`。这 16 条是用户确认的 AI 辅助开发集，不是独立人工测试集。缺少 torch、模型锁或权重时只生成 `failure.json`，不会回退到 offline 分数。
