# 候选阶段诊断与 20/40 窗口消融

## 本步骤解决的问题

本步骤只研究候选阶段，不改变 FastAPI 运行时、Provider、SQLite 数据、LangGraph
Checkpoint、标签或覆盖选择器。它复用冻结的
`artifacts/retrieval-coverage-review-v1`，在同一授权语料、查询顺序、相关性标签和
模型锁下比较 candidate window 20 与 40。

每个窗口组内四路条件完全一致：

- Vector `top_k=window`；
- BM25 `top_k=window`；
- RRF 使用 Vector/BM25 的窗口列表、固定 `rank_constant=60`，先保留最多 `2×window`
  的 RRF 并集，再截断到 `window`；
- Reranker 接收同一 RRF `window`，最终返回固定 Top-5；
- Embedding batch size、Reranker batch size、设备、查询顺序和 warm-up 也相同。

窗口 20/40 是本命令唯一允许的自变量。命令不会根据结果自动改生产配置。

## 排名诊断定义

`CandidateTracingRetriever` 捕获实际运行的窗口候选。为回答“相关条款在截断前是否已
出现”，评测在测量 pass 之后，用同一个授权 retriever 请求
`allowed_chunk_count` 条 Vector/BM25 结果；这次全量扫描单独计时，不混入通道耗时。

每个 `window-*/candidate-diagnostics.json` 的相关条款记录包含：

- Vector：全量排名（窗口截断前）、窗口排名（截断后）、最终 Top-5 排名；
- BM25：同上；
- RRF：由实际窗口 Vector/BM25 列表计算的最多 `2×window` 并集排名（RRF 截断前）、
  实际 RRF 窗口排名（截断后）、最终 RRF Top-5 排名；
- Reranker：RRF 输入排名和最终 Top-5 排名；
- relevance grade、rationale、每路候选覆盖率、每路 Recall@5/MRR@5/nDCG@5、耗时和错误。

另外保存 `rrf_from_full_source_diagnostic`，它只是把全量 Vector/BM25 排名用于诊断的
假设性 RRF 并集，不是生产链路分数；生产链路的 RRF 结果始终以窗口列表为输入。

## 指标和有效样本

Recall@5 是每条查询 Top-5 命中的已标注正相关 Chunk 数除以该查询全部已标注正相关
Chunk 数，再做查询宏平均。MRR@5 是 Top-5 中首个正相关项排名的倒数，未命中为 0。

报告同时给出：

- `effective_n`：指标分母中的查询数；
- `successful_n`：没有该通道运行错误的查询数；
- `error_count`：通道错误数。错误样本保留在 N 中并按现有 Runner 记零，不静默删除；
- 候选覆盖的 `effective_n`：成功完成对应候选扫描的查询数。

重排回归逐查询比较 Reranked 与 RRF 的 Recall@5、MRR@5、nDCG@5；窗口对比给出
`40 - 20` 的绝对差值和百分点差值，不只保留较好的窗口。

## 运行命令（Windows PowerShell）

真实 BGE 评测需要与冻结 review 完全一致的 `models.lock.json` 和本机缓存权重。若锁文件
在上一轮结果目录，请修改 `$modelLock`，不要手工编辑其内容。

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

成功摘要应包含：`status=completed`、`windows=[20,40]`、`query_count=16`、
`real_model_inference=true` 和 `numeric_resume_ready=false`。这次会对两个窗口分别构建
同一语料的检索器并运行一次 warm-up；模型加载、索引建立和全量诊断扫描不计入每路
`average_duration_ms`。当前机器若缺少应用依赖、锁文件或模型缓存，命令会退出 2 并写
`failure.json`，不会将 offline 结果冒充真实 BGE。

如只需检查纯数据/指标保护逻辑：

```powershell
python -X utf8 -m unittest tests.evaluation.test_retrieval_coverage_candidate_window_eval -v
python -X utf8 -m unittest discover -s tests/evaluation -p 'test_retrieval_coverage*.py' -q
```

## 输出文件

```text
candidate-window-report.json       # 两窗口、四路指标、回归、耗时和 provenance
candidate-window-report.md         # 人工审阅摘要（不自动推荐窗口）
comparison.json                    # 40 - 20 逐通道和逐查询差值
candidate-diagnostics.json         # 两窗口全部逐查询诊断行
window-20/
  retrieval-evaluation-report.json/.md
  candidate-traces.json             # 实际 Vector/BM25/RRF 窗口候选
  full-source-traces.json           # 授权全量 Vector/BM25 诊断及单独耗时
  candidate-diagnostics.json
  window-summary.json
window-40/                          # 同上
```

报告还复制冻结查询、标签、权限语料快照、规则、环境、源文件指纹和模型锁，便于面试
复核。标签仍然是用户确认的 AI 辅助开发集，不是独立人工测试集；任何数值都必须以实际
`candidate-window-report.json` 为准，不能从旧报告或离线替身推断。

## 局限与风险

- 16 条查询规模小，且尚未完成全授权语料的独立双人 pool judging；不能外推线上分布。
- `average_duration_ms` 是单次、固定顺序的诊断值，受 CPU/GPU、缓存和并发负载影响；不
  作为生产 SLA 或简历延迟承诺。
- 增大窗口可能提高候选覆盖，却因 RRF 竞争或 Reranker 排序误差使最终 Top-5 回归；这
  正是本步骤保留所有窗口、逐查询变化和回归清单的原因。
- 全量源排名用于定位“相关条款在哪个截断点消失”，不改变实际检索链路，也不构成另一
  个可直接上线的 RRF 配置。
