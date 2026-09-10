# Semantic Request Facet Sidecar Adapter

状态：`offline_adapter_only`。

本步骤提供一个只读的离线 adapter，把已确认的 SemanticParseRecord v1.0 JSONL 与 `semantic-request-facets-v0.1-confirmed-1` sidecar 联合读取。它不调用模型、不访问外部 API、不执行检索、不改变 Gate 选择器，也不接入 FastAPI 或 Agent Runtime。

## 输入边界

adapter 同时读取四类文件：

- `semantic-dev-v1-confirmed/records.jsonl`：v1.0 语义记录；
- `semantic-request-facets-v1-confirmed/accepted.json`：Facet 定义、原文 Span 和七条确认映射；
- `semantic-request-facets-v1-confirmed/confirmation.json`：用户确认范围、版本和 source records 哈希；
- `semantic-request-facets-v1-confirmed/manifest.json`：source/deliverable 文件哈希。

联合读取必须通过以下检查：

1. records JSONL 每条记录通过 v1.0 解码和契约校验；
2. confirmation 中的 `source_records_sha256` 等于实际 records 文件哈希；
3. accepted、confirmation 和 manifest 的 dataset version、source dataset version、样本数和 Facet 实例数一致；
4. case ID、物理 JSONL 行号和 query 一一对应；
5. 每个 Facet Span 都能回查到对应 query，且使用半开区间；
6. `preserved_unresolved_output_types` 与原记录的 `missing_outputs` 完全一致；
7. confirmation 的 accepted mapping 与 accepted sidecar 逐条一致；
8. manifest 中列出的 19 个文件全部存在且 SHA-256 一致。

空 Facet 列表仍表示 `MISSING`，`UNBOUND` 仍保留为不唯一绑定。adapter 不会把 Facet 确认转换为模型预测，也不会把 `PARTIAL` 记录升级为 `COMPLETE`。

## PowerShell 命令

在项目根目录运行：

```powershell
Set-Location D:\Ai_agent_program\demo1

& 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m scripts.check_semantic_request_facets `
  --records .\docs\gate_v3\semantic-dev-v1-confirmed\records.jsonl `
  --accepted .\docs\gate_v3\semantic-request-facets-v1-confirmed\accepted.json `
  --confirmation .\docs\gate_v3\semantic-request-facets-v1-confirmed\confirmation.json `
  --manifest .\docs\gate_v3\semantic-request-facets-v1-confirmed\manifest.json `
  --project-root .
```

成功退出码为 0，失败退出码为 1。两种结果都输出单行 JSON；失败结果包含 `code`、`path` 和 `message`，不输出 traceback。

成功结果只表示数据结构和血缘校验通过，不能解释为语义模型准确率、检索提升或端到端答案质量。

## Python API

```python
from pathlib import Path

from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle

bundle = load_semantic_request_facet_bundle(
    Path("docs/gate_v3/semantic-dev-v1-confirmed/records.jsonl"),
    Path("docs/gate_v3/semantic-request-facets-v1-confirmed/accepted.json"),
    Path("docs/gate_v3/semantic-request-facets-v1-confirmed/confirmation.json"),
    manifest_path=Path("docs/gate_v3/semantic-request-facets-v1-confirmed/manifest.json"),
    project_root=Path("."),
)
```

当前确认版预期结果是 7 条 source records、7 条 Facet cases、8 个 Facet 实例和 7 条带 `missing_outputs` 的 `PARTIAL` 记录。这个 adapter 是数据读取准备工作；下一步才可以讨论是否为 v1.1 设计序列化或 adapter 版本演进。
