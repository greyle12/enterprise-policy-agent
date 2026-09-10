# Facet 预测输入包 v1

本步骤完成离线导出和实际运行验收。复用确认 sidecar adapter 校验来源，再通过 case_id/query 字段白名单构造模型输入；不序列化标签、语义图、缺失输出说明或历史判定。提示词基于确认的词汇定义，属于开发集实验提示词，不能证明独立泛化。

模型仅可接收 prompt.txt 和 requests.jsonl。manifest.json 是实验追溯文件，不发送给模型。输出目录必须不存在，防止覆盖既有实验。重复运行在来源、提示词、代码 HEAD 和 dirty 状态相同的条件下应逐字节一致。

## 实际验收

- 基础 HEAD：0bc04f3bc5698750a1976e648a4630189b9bd06b；工作区有修改，exporter SHA 记录于 manifest。
- 数据集：semantic-request-facets-v0.1-confirmed-1，源版本 semantic-dev-v1-confirmed-1。
- 实际导出 7/7 条，case ID 唯一、原始问句保持不变，模型输入仅含两个允许字段。
- 相关测试：24 passed；Ruff 检查通过。
- 实际分别导出 artifacts/semantic-facet-requests-v1 与 artifacts/semantic-facet-requests-v1-repeat；请求、提示词及 manifest 哈希逐一比较。
- requests SHA-256：b8c8e476b020c901b2b7168702d30c981595822ffeefb2e1262523036a97d2c4。
- prompt SHA-256：1e327a051e0f2f0cdd0137930e9931fa665a749bace653977f5adc42edf6af2a。
- 结果符合工程验收预期：输入可复现并与评分标签分离。模型推理尚未执行，model/revision 为 null；无语义效果或检索提升结果。

## PowerShell 复现

优先使用项目虚拟环境 Python；本机现有虚拟环境启动器不可用时使用下述已验证的运行时。输出目录应使用新的名称。

```powershell
Set-Location D:\Ai_agent_program\demo1
$pythonExe = 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $pythonExe -X utf8 -m scripts.export_semantic_facet_requests --output artifacts/semantic-facet-requests-v1-new
$oldPythonPath = $env:PYTHONPATH
try {
  $env:PYTHONPATH = (Resolve-Path .venv/Lib/site-packages).Path
  & $pythonExe -X utf8 -m pytest tests/evaluation/test_export_semantic_facet_requests.py tests/evaluation/test_semantic_request_facets_evaluation.py tests/evaluation/test_semantic_request_facets_adapter.py -q -p no:cacheprovider --basetemp .pytest_tmp-export-new
} finally {
  $env:PYTHONPATH = $oldPythonPath
}
```

预期：导出清单 sample_count=7；测试全部通过。当前数据固定 7 条仅是本次验收断言，导出器本身复用来源校验，不硬编码标签或成绩。

回归风险：提示词定义可能影响模型选择；字段隔离无法消除开发集已参与提示词设计带来的偏差。默认运行时和 Gate 没有接入导出器。

下一步建议：确定模型及固定 revision 后，对冻结输入运行一次预测，保留原始响应及失败记录，再用现有 evaluator 评分，逐例分析 Facet、绑定和覆盖率。模型调用参数与预算需在执行前明确。

建议提交：feat(evaluation): export label-free semantic facet requests。只暂存本步骤文件；ZIP 不纳入提交。
