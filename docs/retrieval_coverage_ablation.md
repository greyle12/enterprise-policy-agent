# 检索补充证据覆盖：第一步离线消融

本步骤解决“相关辅助条款已进入候选，但在 Top-5 中丢失”的问题。只新增实验脚本、规则、测试和报告，不接入 FastAPI 检索链路，不修改分块、Embedding、BM25、RRF、Reranker 或原评测标签。

## 输入、规则与边界

输入为 `run_retrieval_evidence` 生成的完整 BGE 证据 ZIP。本次使用用户回传的 `retrieval-bge-20260906-133548.zip`。脚本直接读取 ZIP，兼容 PowerShell 的反斜杠成员路径，不需要解压模型或原代码。

真实模型调用发生在原始运行中：Embedding 为 `BAAI/bge-small-zh-v1.5`，Reranker 为 `BAAI/bge-reranker-v2-m3`。本次**没有重新调用模型**，是对真实模型保存排名的确定性后处理重放，不能称为新一轮端到端 BGE 验收。模型 revision、原环境、原代码指纹仍以输入证据为准，报告保留模型锁和原代码标识。

本地代码 HEAD 为 `c4b76cc5201f246f055c71adad033e9928e5666e`；2026-09-08 查到 GitHub main 为 `2a033a5804347b50ebc2023d51fb57b3a856fb76`。用户原始报告 HEAD 为 `2779d0d555af045167526fd92891e96e6980dfce`，工作区有未提交修改。这些并非同一源代码快照，因此不覆盖用户的检索实现，使用已保存语料及排名固定实验输入。新脚本的 HEAD 和内容 SHA-256 单独记录；HEAD 本身不能证明未提交脚本内容。

规则在观察原失败案例后提出，属于开发假设，尚无独立人工复核。规则文件不含评测 case ID、查询文本或相关性标签：

1. 住宿标准表按“城市类别”给出金额 → 同制度、同版本的“城市分类”条款。
2. “超时报销”要求说明原因及部门审批 → 同制度、同版本的“特殊费用审批”中超时费用的财务复核要求。

脚本根据条款标题与内容生成关系，保存关系两端文本指纹；同一规则匹配多个目标时停止要求复核。不能据此推断所有制度关系均已覆盖。

只有原重排前两名可以触发关联。保留前四名原顺序；关联目标尚未在原 Top-5、且位于同一授权 RRF Top-20 候选池时，最多替换第五名一次。不扩大候选池、不扩充最终 K、不合并 Chunk。目标已在 Top-5 则保持原排名；目标在候选池外则记录跳过。选择函数不接收标签，标签只在选择完成后用于评分。

所有候选仍限制在历史 `allowed_chunk_ids`，并校验历史有效日期与制度状态。这里重放的是原访问上下文；不能用历史权限快照判断今天某个员工的实时访问权限，也不构成线上 ACL 验收。

## 指标与本次结果

Recall@5 的分母为每条查询全部已标注相关 Chunk 数，再按查询宏平均；Grade 1/2/3 均为正相关。MRR@5 为首个正相关排名倒数的宏平均，未命中为 0。另报告指数增益 nDCG@5，以观察辅助证据是否挤掉高等级证据。

原始四个方案的逐条 Recall/MRR/nDCG 会独立重算并与原报告校验。数据、语料、协议、源文件清单及模型锁不一致时停止；原报告含运行错误时阻止完整消融结果，不删除该样本继续报成功。无答案、无权限、无标注查询在本次原始正例套件中均为 0 条。未标注项不等于人工判定无关。

|样本|方案|N|Recall@5|MRR@5|nDCG@5|
|---|---|---:|---:|---:|---:|
|原开发集|原 RRF+BGE Reranker|20|92.50%|0.9500|0.9196|
|原开发集|增加覆盖规则|20|100.00%|0.9500|0.9392|
|未人工复核探索集|原 RRF+BGE Reranker|6|86.11%|0.9167|0.8968|
|未人工复核探索集|增加覆盖规则|6|94.44%|0.9167|0.9185|

开发集改变 4 条，3 条 Recall 增加；探索集改变 1 条，1 条 Recall 增加。按现有标注，没有查询的上述三项指标下降。脚本不会把这一点作为必须达到的断言，也不会隐藏后续回归。完整四基线、实验方案、所有查询的前后排名、候选和关联事件见 `artifacts/retrieval-coverage-20260908/coverage-report.json`。

开发集的 +7.50 个百分点是**针对已观察失败案例的事后消融**，不能作为独立测试集成绩或生产精确率。报告固定 `numeric_resume_ready: false`。当前简历继续使用此前真实 BGE 开发集的 Recall@5=92.50%、MRR@5=0.9500，并说明 20 条开发集范围。

## 尚未解决的问题与回归风险

- RET-001、RET-002 补入城市分类，RET-014 补入财务复核。RET-003 也补入城市分类，但原标签不包含该条，既不将它记为增益，也不修改标签。
- RET-024 的财务复核条款不在 RRF Top-20 内；规则跳过，最终仍缺失。解决它需要另一步候选召回或查询拆分实验。
- RET-021 的“我不是领导”仍可能把负责人住宿条款排第一。本步骤保留前四名，因此不能修正身份否定理解错误；MRR 不提高。
- 保留前四名仍可能把正确第五名挤走。测试明确构造该回归，验证会计入指标损失。后续语料和查询必须继续报告这类退化。
- 规则未理解城市已明确分类、问题只询问期限等情境，可能补入不必要背景。原标注不完整，当前无指标下降不等于实际无噪声。
- 20 条长期使用的开发集和 6 条未复核样本，均不适合证明泛化；100% 只表示命中了这 20 条的全部现有正例标签。

下一步应先冻结当前规则，再新增独立人工复核查询，特别覆盖无须补充、第五名是关键证据、跨制度同名条款、否定身份、多条件组合；通过未见样本后再决定是否接入运行时。不要继续围绕这 20 条调到“满分”。

## Windows PowerShell 复现

在项目根目录、现有 `.venv` 中运行。仅使用 Python 标准库，无需安装或加载 torch，不访问外部服务。将 `$baseline` 指向原始完整证据 ZIP；不要拿本次增量代码 ZIP 当输入。

```powershell
git status --short
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

预期：13 个单元测试通过；JSON 为 `status: completed`、`fresh_model_inference: false`、`numeric_resume_ready: false`。同一输入 ZIP 与规则应复现以上指标。输出目录已存在时拒绝覆盖；失败保留 `failure.json`。生成时间、平台和实验代码 HEAD 可不同，比较时以输入 ZIP、脚本和规则指纹为准。

建议只提交以下新增实验文件，避免混入 Phase 38 未提交工作：

```powershell
git add -- scripts/run_retrieval_coverage_ablation.py `
  tests/evaluation/test_retrieval_coverage_ablation.py `
  tests/evaluation/retrieval_coverage_rules.json `
  docs/retrieval_coverage_ablation.md
git diff --cached --stat
# 确认暂存区只有本次预期内容后：
git commit -m "test(retrieval): add evidence coverage replay ablation"
```

本次仅运行新增实验测试和真实排名重放，未执行全项目回归或新模型推理。原运行时文件未修改。
