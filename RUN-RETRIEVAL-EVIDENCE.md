# 检索评测增量补丁

请先解压到临时目录，再用附带的 Python 安装器核对并安装；不要直接强制解压覆盖项目。
安装器核对全部目标文件哈希，发现与预期不同的本地修改就停止；只更改清单文件、备份旧文件，
不提交、不推送。包内为 7 个代码/数据/文档变更文件，以及实际运行报告；没有模型、缓存或密钥。

Windows PowerShell（已在项目根目录、虚拟环境已激活）：

```powershell
$zipPath = Join-Path $env:USERPROFILE "Downloads\enterprise-policy-agent-retrieval-evidence-20260906.zip"
$patchDir = Join-Path $env:TEMP ("retrieval-evidence-" + [guid]::NewGuid().ToString("N"))
Expand-Archive -LiteralPath $zipPath -DestinationPath $patchDir
python -X utf8 (Join-Path $patchDir "apply_patch.py") --repo . --apply
if ($LASTEXITCODE -ne 0) { throw "补丁冲突或校验失败，尚未执行评测" }

python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
python -X utf8 -m pytest tests/evaluation/test_retrieval_evidence.py `
  tests/evaluation/test_retrieval_runner.py tests/evaluation/test_retrieval_runtime.py -q
if ($LASTEXITCODE -ne 0) { throw "评测测试失败" }

$modelLock = ".\artifacts\retrieval-models.lock.json"
if (-not (Test-Path -LiteralPath $modelLock)) {
  python -X utf8 -m scripts.run_retrieval_evidence --prepare-models --model-lock $modelLock
  if ($LASTEXITCODE -ne 0) { throw "真实模型准备失败" }
}
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$resultDir = ".\artifacts\retrieval-bge-$runId"
python -X utf8 -m scripts.run_retrieval_evidence --mode bge --device cpu `
  --model-lock $modelLock --threads 4 --warmups 1 --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "真实评测未完整成功，请回传失败报告" }
Get-Content -Encoding utf8 (Join-Path $resultDir "evidence.md")
Compress-Archive -Path "$resultDir\*" -DestinationPath ".\retrieval-results-$runId.zip"
```

请回传上述新生成的 `retrieval-results-*.zip`。预期 `status=completed`、`mode=bge`，
开发集 N=20、探索集 N=6；分数和提升方向不预设。

本次交付环境缺少 torch，真实 BGE **未完成**。已经实际运行 BM25 和四通道 offline 接线评测，
主开发集 BM25 Recall@5=90.00%、MRR@5=0.9250；其余通道使用词法替身，不是 BGE 成绩。
原 20 条标签不变；新增 6 条是未人工复核探索样本，分别出报告。

完整结果、限制与测试边界：`artifacts/retrieval-evidence-20260906/RUN-RESULTS.md`。
完整设计、指标公式、版本、来源、复现命令、面试口径：`docs/retrieval_quality_evidence.md`。
本次 80 项检索相关测试、Ruff 和两项 offline verifier 通过；更广的 FastAPI 测试受缺失 libpq 阻塞。
没有声称全量回归通过，也没有生成未经实测的 BGE 简历成绩。

Git 提交建议（先检查 diff，只提交这一步，不要 git add .）：

```powershell
git diff -- docs/retrieval_evaluation.md
git add app/evaluation/retrieval_evidence.py scripts/run_retrieval_evidence.py `
  tests/evaluation/test_retrieval_evidence.py tests/evaluation/retrieval_evidence_protocol.json `
  tests/evaluation/retrieval_exploratory_cases.jsonl docs/retrieval_quality_evidence.md `
  docs/retrieval_evaluation.md
git diff --cached --stat
# 确认暂存区没有混入既有 Phase 38 文件后再提交：
git commit -m "feat(evaluation): add reproducible retrieval quality evidence"
```

本次源代码基于本地 c4b76cc 和实际工作区；审计时远端 main 是 2a033a5，检索相关文件未发生变化。
安装器不更新分支、不覆盖既有 Phase 38 修改。模型准备仅首次联网，锁定 commit 和文件哈希后，
推理只读取本地模型缓存。完整模型链路的兼容性和质量仍以你本机回传的真实结果为准。
