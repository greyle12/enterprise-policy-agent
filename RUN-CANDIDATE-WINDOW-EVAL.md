# 运行候选窗口 20/40 消融

本包只增加候选阶段诊断，不切换应用运行时。先把本包文件按相对路径复制到仓库根目录，
然后在 Windows PowerShell 执行：

```powershell
$reviewDir = ".\artifacts\retrieval-coverage-review-v1"
$modelLock = ".\artifacts\retrieval-bge-20260906-133548\models.lock.json"
$resultDir = ".\artifacts\retrieval-candidate-window-bge-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

python -X utf8 -m scripts.run_retrieval_coverage_candidate_window_eval `
  --review-dir $reviewDir `
  --model-lock $modelLock `
  --mode bge `
  --device cpu `
  --threads 4 `
  --warmups 1 `
  --embedding-batch-size 32 `
  --reranker-batch-size 8 `
  --candidate-windows 20 40 `
  --output-dir $resultDir

if ($LASTEXITCODE -eq 2) { throw "评测被阻止；查看 $resultDir\failure.json" }
Get-Content "$resultDir\candidate-window-report.json"
Compress-Archive -Path "$resultDir\*" -DestinationPath "$resultDir.zip"
```

真实 BGE 需要锁文件和本机 Hugging Face 缓存权重。若当前 Python 环境不能导入
`langgraph`/`torch`/`sentence-transformers`，会得到退出码 2 和 `failure.json`；这不应使用
offline 结果替代。纯数据保护测试不需要模型：

```powershell
python -X utf8 -m unittest tests.evaluation.test_retrieval_coverage_candidate_window_eval -v
```

把生成的 ZIP 或 `candidate-window-report.json` 返回后，再根据实际数据分析窗口 20/40 的
Recall@5、MRR@5、重排回归和耗时；脚本本身不会生成简历数字。
