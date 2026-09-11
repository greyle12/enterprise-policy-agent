# Semantic Request Facet Offline Evaluation

状态：`offline_facet_agreement_only`。

本步骤提供一个离线 evaluator，把外部生成的预测 JSONL 与已确认的
`semantic-request-facets-v0.1-confirmed-1` sidecar 对齐。evaluator 不调用模型、不访问外部 API、
不执行检索，也不接入 Gate 或生产运行时。

## 预测输入格式

预测文件使用 UTF-8 JSONL，每个非空行对应一个 case：

```json
{
  "schema_version": "1.0",
  "case_id": "COV-007",
  "predicted_facets": [
    {"facet": "MATERIAL", "binding": "BOUND"},
    {"facet": "RESPONSIBLE_ROLE", "binding": "BOUND"}
  ]
}
```

`predicted_facets` 可以为空数组，表示该 case 没有输出 Facet。缺失整条 case 记录则表示
`prediction_present=false`，会在报告中列出并降低 prediction coverage。一个 case 内不能重复
Facet；预测 Facet 必须来自确认词汇表，binding 只能是 `BOUND` 或 `UNBOUND`。

evaluator 会拒绝未知字段、未知 Facet、非法 binding、重复 case ID、重复 Facet、未知 source
case ID、非法 JSON、非法 UTF-8 和空预测文件。有效但错误的 Facet 或 binding 会进入逐例差异，
不会被删除或改写。

## 计分口径

已确认 sidecar 中 `EXPRESSED` 的 Facet 都参与 Facet 名称统计，`BOUND` 与 `UNBOUND` 都是
可比较的表达结果。没有 accepted Facet 的 case 作为空期望集合保留；`MISSING` 不生成一个
可预测的 Facet 标签。

报告提供：

- Facet label 的 micro precision、recall、F1，以及 TP、FP、FN；
- 在 Facet 名称匹配后计算的 binding match、mismatch 和 accuracy；
- case 级 exact match rate；
- prediction coverage 和缺失 prediction case ID；
- 每个 case 的 expected、predicted、FP、FN、binding mismatch 和 excluded unresolved outputs。

这些指标没有阈值，也不产生 quality gate。报告显式保留
`independent_test_set=false`、`numeric_resume_ready=false` 和 `semantic_accuracy=null`。
当前 7 条 case、8 个 Facet 实例来自用户确认的开发集，不能解释为独立测试集语义准确率。

## 失败与零分母口径

输入或对齐契约失败时，CLI 返回码为 1，报告的 `status` 为 `failed`、`metrics` 为 `null`，
并在 `errors` 中保留结构化错误。指定 `--output` 时，标准输出和落盘文件写入同一份报告。
预测输入合法但得分较低时，CLI 返回码仍为 0，报告的 `status` 为 `passed`；低分结果通过逐例
`false_positive_facets`、`false_negative_facets` 和缺失 case ID 保留，不由 evaluator 转换为输入错误。

Facet label 使用 micro 统计：当预测数为 0 时，precision 为 JSON `null`；当存在期望 Facet
但没有命中时，recall 为 `0.0`；只要 precision 或 recall 无法定义，F1 为 JSON `null`。当没有
任何名称匹配的 Facet 时，binding accuracy 也为 JSON `null`。这些 `null` 表示分母为 0，
不表示 evaluator 失败，也不构成质量阈值。

## 可追溯信息

报告记录：

- confirmed source dataset version、records SHA-256、proposal SHA-256 和 manifest 校验结果；
- prediction JSONL SHA-256、记录数、case 数和 prediction source；
- `human_fixture`、`offline_mock` 或 `real_model` prediction kind；
- model ID 和 model revision。人工或离线替身预测使用 `none` 明确表示没有真实模型推理；
- evaluator 脚本 SHA-256、Git HEAD、branch、工作区是否有修改和依赖环境。

evaluator 自身的 `model_inference_performed_by_evaluator` 始终为 `false`。`real_model` 只记录
预测来源声明，不代表本程序执行了模型调用。

## PowerShell 命令

在项目根目录运行。`predictions.jsonl` 由外部预测过程或测试夹具提供：

```powershell
Set-Location D:\Ai_agent_program\demo1

$localSitePackages = (Resolve-Path -LiteralPath '.\.venv\Lib\site-packages').Path
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $localSitePackages
try {
  & 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m scripts.evaluate_semantic_request_facets `
    --records .\docs\gate_v3\semantic-dev-v1-confirmed\records.jsonl `
    --accepted .\docs\gate_v3\semantic-request-facets-v1-confirmed\accepted.json `
    --confirmation .\docs\gate_v3\semantic-request-facets-v1-confirmed\confirmation.json `
    --manifest .\docs\gate_v3\semantic-request-facets-v1-confirmed\manifest.json `
    --predictions .\predictions.jsonl `
    --project-root . `
    --prediction-kind human_fixture `
    --prediction-source manual-review-fixture `
    --model-id none `
    --model-revision none `
    --output .\artifacts\evaluation\semantic-request-facets-evaluation.json
} finally {
  $env:PYTHONPATH = $previousPythonPath
}
```

退出码 0 表示输入结构和对齐过程通过；正确率或 F1 较低仍然属于有效评测结果。退出码 1
表示输入或对齐契约失败，报告会保留结构化 `errors`。成功和失败报告都使用 UTF-8 JSON，
父目录会自动创建。

## 测试

```powershell
Set-Location D:\Ai_agent_program\demo1

$localSitePackages = (Resolve-Path -LiteralPath '.\.venv\Lib\site-packages').Path
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $localSitePackages
try {
  & 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m pytest .\tests\evaluation\test_semantic_request_facets_evaluation.py -q
  exit $LASTEXITCODE
} finally {
  $env:PYTHONPATH = $previousPythonPath
}
```
