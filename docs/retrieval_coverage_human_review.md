# 覆盖规则冻结与人工复核

## 本步骤解决的问题

上一步开发集 Recall@5 达到 100%，但覆盖规则源自已观察到的失败案例。继续在同一批题上调规则无法证明泛化。本步骤只冻结规则、准备 16 条尚未运行检索的新查询和人工复核入口，不做新模型推理、不接入运行时、不更新简历成绩。

这些查询由助手依据原制度及已知薄弱点提出，不是真实流量，也不是独立作者设计的测试集。应称为“冻结规则后的定向验证候选集”。后续即使人工核验通过，也不能自动升级为独立测试集。

覆盖场景：城市分类依赖、已知城市类别而无需补充、同一住宿条款的其他问题、否定身份、期限与金额条件、跨制度、多条款、补充内容挤占关键证据的风险，以及餐补、交通、票据等对照问题。某条查询是否真的触发第五位回归，必须由之后的检索结果判断，不能从标签或题目类别推断。

## 新增文件与冻结边界

- `tests/evaluation/retrieval_coverage_review_queries.json`：16 条新查询，没有建议相关性标签。
- `scripts/manage_retrieval_coverage_review.py`：生成复核包、校验冻结及人工声明。
- `tests/evaluation/test_retrieval_coverage_review.py`：8 个新测试。
- 本文档和 `artifacts/retrieval-coverage-review-v1/` 下待复核资料。

现有重排实验脚本、覆盖规则、原 20+6 样本和运行时均不修改。

本包依赖上一步 `enterprise-policy-agent-retrieval-coverage-20260908.zip` 已安装。下载本包后，在项目根目录用 PowerShell 安装；同名不同内容会触发冲突检查，不覆盖已有人工编辑：

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\enterprise-policy-agent-coverage-review-20260908.zip"
$patchDir = Join-Path $env:TEMP ("coverage-review-" + [guid]::NewGuid().ToString("N"))
Expand-Archive -Path $zip -DestinationPath $patchDir
python -X utf8 "$patchDir\apply_patch.py" --repo (Get-Location).Path --apply
if ($LASTEXITCODE -ne 0) { throw "安装停止，请检查文件冲突" }
```

`freeze.json` 固定查询集、规则、选择脚本、完整语料/权限快照、基线 ZIP 指纹、模型锁与配置。脚本指纹按 LF 归一化，允许 Windows CRLF，不允许逻辑修改。校验器同时检查冻结副本和项目中当前文件。任一漂移、缺题、改题、非法标签都会阻止放行，不会静默删题。

该锁是可审计的文件指纹，不是签名或不可篡改账本。请在首次推理前保留或提交原冻结资料；不要重写冻结记录掩盖调参。人工身份、是否看过输出、是否完整检查语料，都依赖人工声明，软件无法独立证明。

## 你需要完成的人工复核

打开 `artifacts/retrieval-coverage-review-v1/review.json`，对照同目录 `corpus-review.md`。后者包含原访问条件下的全部 199 个 Chunk，不含模型排名、分数或建议标签。不要先运行这 16 条新问题的检索再决定标注。

逐条操作：

1. 阅读查询并检查所有授权条款，不只检查规则两端或标题相近的条款。
2. 能由语料回答时，将 `answerability` 改为 `answerable`。不能回答或题意有歧义时，保留问题、将 `review.status` 设为 `needs_revision` 并填写 notes；不要删除题目或随意标正例。
3. 为所有判为正相关的条款添加 `judgments`：`chunk_id`、整数 `relevance` 和具体 `rationale`。3=直接回答，2=支持解释，1=相关背景。仅主题相似不等于正相关；所有 Grade 1/2/3 都会进入 Recall 分母。非相关条款不加入这个正例列表。
4. 填真实复核者标识、带时区的时间；完成全部语料检查后将 `checked_all_authorized_chunks` 改为 true。`viewed_retrieval_outputs` 仅在没看过这些新问题的输出时保持 false。
5. 认可当前标注后将 `review.status` 改为 `approved`。不要更改题目、ID、tags 或 freeze_sha256。

一条 judgment 的格式（占位符不能直接作为标签）：

```json
{
  "chunk_id": "从 corpus-review.md 复制实际 Chunk ID",
  "relevance": 3,
  "rationale": "具体解释此条款回答了问题的哪个部分"
}
```

复核时间可用 PowerShell 获取：

```powershell
(Get-Date).ToString("o")
```

不要让助手替你把所有状态批量改成 approved。可以请助手解释原文，但若使用生成标签，必须披露辅助过程并逐条人工核验。单一开发者的人工复核仍不是多人独立标注。

可以分批填写并保存，不需要一次完成。未完成的查询会继续阻止整体放行，不影响已填写内容。

## PowerShell：测试与校验

安装增量包后，在项目根目录运行：

```powershell
python -X utf8 -m unittest discover `
  -s tests/evaluation -p 'test_retrieval_coverage*.py' -v
if ($LASTEXITCODE -ne 0) { throw "Coverage tests failed" }

$reviewDir = ".\artifacts\retrieval-coverage-review-v1"
$report = Join-Path $reviewDir ("validation-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".json")
python -X utf8 -m scripts.manage_retrieval_coverage_review validate `
  --review-dir $reviewDir --report $report
```

预期：21 个测试通过（原 13 + 新 8）。未填标签时，校验退出码为 2，报告 `blocked_pending_human_review`，`blocked_case_count: 16`。**这是预期保护，不是程序故障。** 完整人工复核通过后，状态为 `review_ready_for_separate_evaluation`，退出码 0；仍有 `retrieval_executed: false`、`numeric_resume_ready: false`，不代表质量验收通过。

报告路径必须不存在，避免覆盖人工文件或历史结果。完成后回传：

```powershell
$delivery = ".\artifacts\coverage-review-return-$(Get-Date -Format 'yyyyMMdd-HHmmss').zip"
Compress-Archive -Path "$reviewDir\*" -DestinationPath $delivery
```

若要从你原来的完整 BGE 结果包重新生成一份**新的**冻结复核目录，而不是编辑包中附带的复核资料：

```powershell
$reviewDir = ".\artifacts\coverage-review-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -X utf8 -m scripts.manage_retrieval_coverage_review prepare `
  --baseline-zip ".\artifacts\retrieval-bge-20260906-133548.zip" `
  --output-dir $reviewDir
```

重新生成后应选定唯一版本再开始标注，不能混用两个目录的 review.json 与 freeze.json。原目录不会被覆盖。脚本仅使用 Python 标准库，不需要 torch、模型下载或数据库。

## 本次实际验证与未完成项

实际：生成 16 条空标签复核模板；使用原 BGE 语料快照生成 199 个授权 Chunk 的盲审材料；待复核校验按预期阻止放行。新增测试中的 approved 和 reviewer 是显式合成测试数据，不代表对真实问题进行了人工复核。

未完成：真实人工标签、16 条问题的 BGE 推理、指标对比与运行时验收。当前没有新的 Recall/MRR 数值。本阶段先在人工复核处停下；回传通过的资料后，下一步才扩展现有评测入口，在冻结配置下运行四基线与覆盖方案，报告所有收益和回归，不调规则。

主要风险：辅助标注的偏见、漏标导致 Recall 分母不完整、复核者受到既有失败案例影响、样本量小和主题相关性。现有语料全部授权，此套件不验证拒答/越权访问；这类测试仍应单独运行。若发现无答案问题，不自动移除，先记录并制定新版本/独立拒答指标协议。

## Git 建议

只加入本步骤文件，不将已有 Phase 38 修改混入提交：

```powershell
git add -- scripts/manage_retrieval_coverage_review.py `
  tests/evaluation/retrieval_coverage_review_queries.json `
  tests/evaluation/test_retrieval_coverage_review.py `
  docs/retrieval_coverage_human_review.md
git diff --cached --stat
# 确认暂存区没有无关内容后：
git commit -m "test(retrieval): freeze coverage rules and add human review gate"
```

冻结材料可另行审查后版本管理；不要提交复核者不希望公开的个人信息。本包安装工具不会提交、推送或部署。
