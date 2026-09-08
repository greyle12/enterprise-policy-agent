# 检索质量证据与复现（2026-09-06）

## 1. 真实仓库审计

本次工作区为 `main`，HEAD 为 `c4b76cc5201f246f055c71adad033e9928e5666e`。
通过 GitHub 插件检查远端 `main` 为 `2a033a5804347b50ebc2023d51fb57b3a856fb76`：
只领先一个 PostgreSQL Checkpointer 提交，未修改检索器、评测代码、语料或检索标签。
本地还有 Phase 38 的未提交修改，本次未合并、覆盖、提交或推送它们。

| 检查项 | 真实实现与条件 | 能证明什么 |
|---|---|---|
| 文档加载 | `app/rag/document_loader.py`，Markdown、PyMuPDF 原生 PDF、python-docx、可选 Tesseract OCR；元数据和 OCR 质量检查 | 代码及格式测试存在；本次语料仅 Markdown，不能声称测了 PDF/OCR 检索质量 |
| 制度解析/知识库 | `policy_parser.py` + `policy_chunker.py`；5 份演示制度、199 个条款 Chunk；稳定 ID，标题/章节/正文组成 retrieval_text | 本次解析得到的实际数量；不是企业真实文档规模 |
| 向量 | `BGEEmbeddingProvider`；默认 BAAI/bge-small-zh-v1.5，查询加中文 instruction、归一化；评测为内存精确余弦检索 | 真实 BGE 需 sentence-transformers、PyTorch、模型权重；本次不是 pgvector/ANN 实验 |
| BM25 | 实际 `InMemoryBM25Index`，NFKC、中文双字切分，k1=1.2、b=0.75，授权范围内计算统计量 | 无需模型即可实跑，词法规则和数字表达存在局限 |
| RRF | `fusion.py`，排名贡献 1/(60+rank)，去重，确定性平局排序 | 实际融合算法；输入向量若是替身，融合结果也不是 BGE 成绩 |
| 重排 | `BGERerankingProvider`，默认 BAAI/bge-reranker-v2-m3 CrossEncoder 批量打分 | 真实模型需额外权重；应用默认 Reranker disabled，显式评测开启不代表运行时已启用 |
| 评测集 | `tests/evaluation/retrieval_test_cases.jsonl`，20 条、30 个正相关 Query–Chunk 判断 | 23 个 Grade 3、6 个 Grade 2、1 个 Grade 1；文档称单开发者标注，无独立复核凭据 |
| 指标/消融 | `retrieval_runner.py`，Vector/BM25/Hybrid/Reranked，Recall@1/3/5、MRR@5、nDCG@1/3/5 | 真实实现，不是功能推断；单元测试中的 fake 排名只是公式/契约断言 |
| 历史运行证据 | Phase 31/32 文档有 offline 回归快照；CI 配置运行 offline；仓库受版本控制的 report 只有 portfolio demo | 未找到可核验的历史 BGE 检索报告、模型 revision 与硬件快照，不能断言以前完成了 BGE 实验 |

原始评测集字节 SHA-256（仓库 LF）：
`3b4343f681ed13c530d257394058245b2dfa42b1a7ae35c185216f540fa06789`。
`docs/retrieval_evaluation.md` 原表为 Phase 31 二元标签快照；本次补充版本说明，避免把旧分母成绩
套用到 Phase 32。当前 graded 快照记录于 `docs/graded_relevance_ndcg.md`。

## 2. 本次变更与边界

- 新增 `app/evaluation/retrieval_evidence.py`：模型锁、输入指纹、环境、候选记录、差值和失败证据。
- 新增 `scripts/run_retrieval_evidence.py`：复用现有 Runtime、Runner 和报告，不改评分、融合或运行时 Provider。
- 新增 `tests/evaluation/retrieval_evidence_protocol.json`：固定候选窗口、数据版本、标注来源和已知争议。
- 新增 `tests/evaluation/retrieval_exploratory_cases.jsonl`：6 条助手生成的探索样本。
- 新增 `tests/evaluation/test_retrieval_evidence.py`：指标接线、候选捕获不改排名、数据漂移、模型锁、失败边界测试。
- 更新本文件和 `docs/retrieval_evaluation.md`。

没有修改原 20 条标签，没有筛除失败样本，没有调权重、窗口或阈值。本次不做 Phase 38 持久化开发。

## 3. 公平比较协议

所有方案使用同一组已解析 Chunk、相同 `retrieval_text`、相同权限身份：
`RETRIEVAL-EVAL-001 / 评测部门 / EMPLOYEE / internal / 中国大陆 / test_fixture`，
固定制度有效日期 `2026-08-20`。当前 199 个 Chunk 全部可见，这不等于验证了复杂权限隔离效果。

| 方案 | 第一级候选 | 处理 | 最终 K |
|---|---|---|---:|
| Vector | 全部可见 Chunk 精确评分，最多取 20 | 截断 | 5 |
| BM25 | 全部可见 Chunk 词法评分，最多取 20 个正分结果 | 截断 | 5 |
| RRF | Vector 20 + BM25 20，去重并集最多 40 | RRF 常数 60，取融合前 5 | 5 |
| RRF + Reranker | 完全相同的每路候选窗口 | RRF 前 20 个做 CrossEncoder 重排，再截断 | 5 |

原 Runner 对 Vector/BM25 直接取前 5；证据适配器取前 20 再截断，目的是保存候选排名。
精确检索下前 5 顺序不变；测试逐查询比较现有四路结果，确认没有改变排序。
嵌入 batch=32，重排 batch=8；固定窗口 20，不在最终评测上选最优窗口。

**指标定义**：每个查询相关集 R 为该查询所有 Grade 1/2/3 的 Chunk ID 集合。
Recall@K=|R∩TopK|/|R|；然后对查询宏平均。主开发集 N=20，正相关判断总数 30；不能把总命中数除
以 30 的微平均冒充本指标，也不能把“至少命中一个”当 Recall。
RR@5=前 5 中第一个已标注正相关 Chunk 排名的倒数，未命中为 0；MRR@5=20 个 RR 的算术平均。
所有通道、Recall@1/3/5、MRR@5 的有效 N 相同。nDCG 保留原实现作为补充，不替换主指标。
差值为 `(方案指标−Vector 指标)×100` 个百分点，保留负号；没有把相对增幅混进来。

**特殊样本**：当前是有答案且可见的正例排名评测。无答案、无权限和无标注查询数量各为 0；
指标不适用于这些查询，不能用空分母打 1 分。加载器拒绝空标签，Runtime 在推理前拒绝缺失或不可见
的相关 Chunk，不静默剔除。需要拒答/权限质量时，应另建并报告对应安全套件。
执行异常保留在 N 中、该查询该通道记 0，并标记评测不完整。未标注返回项按未命中计算；
**未标注不代表人工判无关**，这套 judgments 不是穷尽标签。

## 4. 样本来源与局限

主集 `legacy_dev` 的 20 条长期用于 CI 和候选窗口实验，属于开发集，未建立独立保留测试集。
原查询覆盖 5 个制度领域和多条款问题，但原标签中没有跨不同制度的正相关组合。
RET-003 涉及北京，标签却未像 RET-001 一样包括城市分类条款，这是标注完整性争议；本次只记录问题。

探索集 `exploratory_unreviewed` 的 6 条为助手在查看条款后生成，未看这些新查询的结果再改标签。
RET-021/022 覆盖口语，RET-023 覆盖精确条款及数字表达，RET-024/026 为跨制度信息需求，
RET-025 覆盖表达差异。每条标签 rationale 指明待人工复核，输出 `judgment-review.json`
附原文供人工核验。它们与开发集共享信息需求，不独立、不混入原 20 条的分数，不用于生产或最终简历结论。

只有 20 个开发问题、5 份演示 Markdown 制度和单一权限视角；缺少真实匿名日志、OCR 噪声、
无答案、复杂 ACL、独立标注一致性、分层大样本及显著性检验。2.5 个百分点的 Recall 变化可能只来自
一个有两条标签的查询少/多找回一个 Chunk，不能轻率解释为普遍提升。

## 5. Windows PowerShell 完整复现

在项目根目录、已激活的 Python 3.12 虚拟环境运行。依赖安装如失败就停止；不要把失败换成 offline
后仍称为 BGE。首次模型准备需要网络、磁盘和足够内存；默认 M3 重排模型在 CPU 上可能较慢。
本入口不用数据库、Redis、LLM API key，也不读取 Provider 配置来切换服务。

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

python -X utf8 -m pytest tests/evaluation/test_retrieval_evidence.py `
  tests/evaluation/test_retrieval_runner.py tests/evaluation/test_retrieval_runtime.py -q
if ($LASTEXITCODE -ne 0) { throw "评测契约测试失败" }

# 首次准备：解析模型 main 到不可变 commit SHA，下载后逐文件做 SHA-256。
# 已有锁时直接复用，改模型实验要新建锁文件；程序不会覆盖既有锁。
$modelLock = ".\artifacts\retrieval-models.lock.json"
if (-not (Test-Path -LiteralPath $modelLock)) {
  python -X utf8 -m scripts.run_retrieval_evidence --prepare-models --model-lock $modelLock
  if ($LASTEXITCODE -ne 0) { throw "真实模型准备失败" }
}

# 推理阶段只读已锁定本地缓存，不下载，不回退到替身。
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$resultDir = ".\artifacts\retrieval-bge-$runId"
python -X utf8 -m scripts.run_retrieval_evidence --mode bge `
  --model-lock $modelLock --device cpu --threads 4 --warmups 1 --output-dir $resultDir
if ($LASTEXITCODE -ne 0) { throw "真实评测未完整成功，请回传 failure.json 或错误报告" }

Get-Content -Encoding utf8 (Join-Path $resultDir "evidence.md")
Compress-Archive -Path "$resultDir\*" -DestinationPath ".\retrieval-results-$runId.zip"
```

成功预期：`status: completed`、`mode: bge`、`real_model_inference: true`，开发/探索分别 N=20/6。
**不预设分数、提升方向或 0.80 门禁必须通过**。原报告仍保留门禁字段；新入口退出 0 表示完整执行，
不是成绩达到阈值。退出 1 是通道错误，退出 2 是配置/数据/依赖/模型等失败；失败不会生成可用简历成绩。
`numeric_resume_ready` 只指主开发集有完整真实推理数据，使用时仍需保留数据与标注局限，不能用探索集。

普通离线接线核对（不生成 BGE 成绩）：

```powershell
python -X utf8 -m scripts.run_retrieval_evidence --mode offline --warmups 0
python -X utf8 -m scripts.verify_retrieval_evaluation
python -X utf8 -m scripts.verify_graded_relevance
python -X utf8 -m ruff check app/evaluation/retrieval_evidence.py `
  scripts/run_retrieval_evidence.py tests/evaluation/test_retrieval_evidence.py
```

每次输出目录必须是新目录，避免覆盖旧报告。模型文件指纹由实际下载内容生成；Hub `main` 只在
首次准备时解析，以后按 40 位 revision 和文件哈希校验。锁文件本身不含权重和密钥。
依赖的快照下载语义见 [Hugging Face 官方文档](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download)。

## 6. 报告和证据路径

| 文件（相对本次输出目录） | 用途 |
|---|---|
| `evidence.json` / `evidence.md` | 两个独立 cohort 的四方案表、带符号差值、全部失败与候选排名；不只选有利案例 |
| `legacy_dev/retrieval-evaluation-report.json` | 复用既有报告结构；每条完整 Top-5、Recall@1/3/5、RR、错误 |
| `exploratory_unreviewed/retrieval-evaluation-report.json` | 明确未人工复核的探索结果 |
| 各 cohort 的 `dataset.jsonl` / `judgment-review.json` | 精确输入快照和标签原文复核包 |
| `models.lock.json` | 两个模型名、commit revision、选用权重及 tokenizer/config 文件哈希；仅 bge 成功准备后存在 |
| `environment.json` / `requirements-observed.txt` | 实际 Python、OS、CPU/GPU、线程与安装依赖版本；后者是观测清单，可能含平台专属或无关包，不直接作为跨平台安装锁 |
| `source-manifest.json` | 实际 app/scripts/pyproject 内容哈希，配合 HEAD 和本次补丁还原未提交代码版本；不打包 .env |
| `protocol.json` / `corpus.json` | 协议快照和全部 Chunk，指纹包括增强文本、状态、有效期、权限；弥补旧语料哈希只覆盖 ID/正文的问题 |
| `failure.json` | 模型或环境等阻塞时的失败证据；不能当成实测完成 |

计时在建库后进行：Vector/RRF/Reranked 包含每次 query embedding，BM25 没有 embedding；
Reranked 包含候选召回、融合、模型打分和排序。默认每组全查询预热 1 遍，正式每个查询每路 1 次，
固定顺序，保留原 Runner 的诊断耗时。未做固定负载重复统计、批量吞吐或显著性检验，**本次不推荐延迟数字**。

## 7. 简历与面试口径

拿到真实 bge 报告后，优先选择两个结果：

1. 固定 20 条开发集上 RRF+Reranker 的 Recall@5 与 MRR@5，同时说明标注规模与相关集定义。
2. 同一报告相对纯 BGE 向量的绝对差值（百分点）；没有提升就明确持平或下降，不能从多个窗口中选最高值。

在真实 BGE 回传前，仅可使用不含模型质量数字的项目描述：

> 为企业制度 RAG 建立条款级检索评测，统一权限与候选窗口对比向量、BM25、RRF 和重排，
> 固化模型版本、语料指纹与逐查询失败证据，支持检索质量复现和回归定位。

面试解释：

- **怎么测**：对固定可信身份下可见的条款检索，不生成答案；按查询计算相关 Chunk 找全比例和首相关排名，再宏平均。
- **基线是什么**：真实 bge 模式中的单独 BGE 向量；offline 的哈希词法向量只验证工程接线，不能充当语义基线。
- **为何可能有效**：BM25 补精确术语，向量补表达差异，RRF 合并排名，CrossEncoder 对候选做 query–document 交互评分；这是机制假设，是否有效以消融结果为准。
- **失败怎么看**：相关条款不在 RRF 前 20 属候选召回/融合问题；在其中但掉出最终前 5 才定位到重排窗口内排序；有多条证据时 MRR=1 仍可 Recall=0.5。
- **限制是什么**：小型自建开发集、非穷尽且无独立复核的标签、演示语料、单权限视角；不能宣传生产准确率、答案准确率或显著提升。

提交建议：确认补丁与报告后仅暂存本次列出的代码/文档/测试文件，使用
`feat(evaluation): add reproducible retrieval quality evidence`。不要 `git add .` 混入 Phase 38 修改。
报告可另行作为实验记录提交；模型缓存、虚拟环境、密钥、环境文件不进入提交或 ZIP。
