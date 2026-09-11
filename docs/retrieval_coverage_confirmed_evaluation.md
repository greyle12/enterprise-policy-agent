# 已确认 COV 定向检索评测

## 本步骤解决的问题

用户已确认 COV-002 至 COV-016 共 15 条 AI 辅助相关性标注草案，COV-001 的两条标签由用户先前直接提供并保留。现在需要在同一语料、权限快照、查询和标签下，实际运行四路检索并检查覆盖选择器是否改善 Top-5 覆盖。

这不是对原有 20 条 `legacy_dev` 结果的重跑，也不是独立人工测试集。16 条查询来自冻结规则后的定向开发集；当前 `review.user-confirmed.json` 明确记录了“用户确认 AI 辅助标注”，尚未完成全部授权 Chunk 的独立人工复核。因此本命令始终输出 `numeric_resume_ready: false`。

## 运行边界

- 不切换 FastAPI Provider，不迁移 SQLite，不修改 LangGraph Checkpoint。
- 复用现有 `RetrievalEvaluationRunner` 和 `AccessControlledPolicyRetriever`。
- 四路均使用同一授权上下文、199 个冻结 Chunk、同一 `PolicyChunk.retrieval_text` 和同一查询顺序。
- Vector、BM25、Hybrid、Reranked 的候选配置固定为：每路候选 Top-20、RRF 常数 60、Rerank 窗口 20、最终 Top-5。
- 覆盖选择器只读取制度内容派生的固定链接规则：保留重排 Top-4，最多用一个授权 Hybrid Top-20 候选补充第 5 位；不读取查询或相关性标签。标签只在选择完成后计算指标。
- Recall@5 是每条查询 Top-5 命中的已标注相关 Chunk 数除以该查询全部已标注相关 Chunk 数，再做宏平均。MRR@5 是 Top-5 中首个正相关项排名的倒数，未命中为 0。
- 延迟仅作为诊断字段，不作为简历结论；包含查询向量化，排除模型加载和建库。

## 文件与输出

新增脚本：

```text
scripts/run_retrieval_coverage_confirmed_eval.py
```

输入目录必须包含冻结资料：

```text
artifacts/retrieval-coverage-review-v1/
  freeze.json
  queries.json
  corpus.json
  rules.json
  review.user-confirmed.json
  user-confirmation.json
```

脚本会先校验冻结指纹、查询顺序、全部正例标签、授权 Chunk 和模型锁；任一漂移都 fail-closed。成功目录包含：

```text
coverage-report.json                 # 基线 + 覆盖选择结果，机器可读
coverage-report.md                   # 人工审阅摘要
retrieval-evaluation-report.json     # 现有四路评测格式
retrieval-evaluation-report.md
target-dataset.jsonl                 # COV 映射为 RET-901..RET-916 的审计输入
judgment-review.json                 # 标签与对应 retrieval_text，便于逐条复核
corpus.json / frozen-corpus.json
source-manifest.json / protocol.json / environment.json
models.lock.json                     # bge 模式下的不可变模型锁副本
rules.json / queries.json / review.user-confirmed.json / user-confirmation.json
```

如果依赖、模型、语料、冻结查询或规则不满足条件，只写 `failure.json`，绝不用 offline 结果冒充 BGE 分数。

## Windows PowerShell

先确认你已有上一轮真实 BGE 运行产生的 `models.lock.json`，并且模型权重仍在本机 Hugging Face 缓存中。若锁文件在上一次结果目录，直接指向它；不要手工修改锁内容。

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
Get-Content "$resultDir\coverage-report.json"
```

预期成功摘要形如：

```json
{
  "status": "completed",
  "mode": "bge",
  "real_model_inference": true,
  "numeric_resume_ready": false,
  "query_count": 16
}
```

若本机没有 `torch`、`sentence-transformers`、Hugging Face 权重或模型锁，预期是退出码 2 和 `failure.json`，这表示没有可写入简历的数值，不是可以用 offline 替代的情况。

如需检查脚本的纯数据保护逻辑（不调用模型），可以运行：

```powershell
python -X utf8 -m unittest tests.evaluation.test_retrieval_coverage_confirmed_eval -v
```

## 结果解释

`coverage-report.json` 的 `baseline.summaries` 是 16 条定向开发集上的 Vector/BM25/Hybrid/Reranked 实测；`coverage.cases` 展示每条查询的 Hybrid Top-20 候选、重排 Top-5、覆盖选择后的 Top-5、候选覆盖率和所有替换事件。`coverage.summary` 只有在四路无错误且 16 条都完成时才生成。

即使 BGE 运行成功，也只能在面试中如实描述为“冻结规则后的用户确认 AI 辅助开发集定向评测”。不能与原 20 条开发集合并、不能称为独立测试集，也不能把覆盖选择器的离线差值或未复核标签写成生产质量提升。若结果无提升，应保留失败案例并分析候选窗口、标签完整性和规则适用范围。

## Git 建议

本步骤只提交脚本、测试和本文档；不要把运行生成的模型输出、模型缓存或既有 Phase 38 工作树修改混入提交：

```powershell
git add -- scripts/run_retrieval_coverage_confirmed_eval.py `
  tests/evaluation/test_retrieval_coverage_confirmed_eval.py `
  docs/retrieval_coverage_confirmed_evaluation.md
git diff --cached --stat
git commit -m "test(retrieval): evaluate confirmed coverage queries with real BGE"
```
