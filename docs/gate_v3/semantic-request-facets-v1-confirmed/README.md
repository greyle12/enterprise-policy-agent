# Semantic Request Facets v1 Confirmed

状态：`user_confirmed`。本目录固化了对 `semantic-request-facets-v0.1-draft-1` 的范围级确认：五个请求信息维度和七条开发集映射均被接受。

## 本步骤解决的问题

此前的 `docs/gate_v3/semantic-request-facets-v1/` 只是待审核草案。当前确认版把用户已接受的定义、绑定状态和原文 Span 记录为独立的、可审计的 sidecar 数据集，同时保留草案和原始 semantic records 不变。

确认版仍然不是 SemanticParseRecord v1.0 的新增字段，也不是模型输出。它不会被现有 Gate 选择器或生产运行时自动读取。

## 已确认范围

已确认的五个维度：

- `PROCEDURE`：询问如何处理、步骤或办理方式；
- `MATERIAL`：询问需要补充、提交或准备的说明、凭证或材料；
- `DEADLINE`：询问时间点、期限、天数阈值或逾期后的动作时间；
- `RESPONSIBLE_ROLE`：询问部门、岗位、人员或审批主体；
- `PROCESS_CHOICE`：在多个可能办理流程之间询问应选择哪一个。

绑定状态仍按草案定义解释：`BOUND` 表示目标对象可唯一回查，`UNBOUND` 表示维度出现但目标不唯一，`MISSING` 表示没有安全可标注的请求维度或现有契约没有对应表达。

七条确认映射如下：

| case_id | accepted facet | binding |
| --- | --- | --- |
| COV-007 | `MATERIAL`、`RESPONSIBLE_ROLE` | `BOUND` |
| COV-008 | `DEADLINE` | `BOUND` |
| COV-009 | `PROCEDURE` | `BOUND` |
| COV-010 | `DEADLINE`、`RESPONSIBLE_ROLE` | `BOUND` |
| V3U-004 | `PROCEDURE` | `UNBOUND` |
| V3U-006 | 无 | `MISSING` |
| V3U-009 | `PROCESS_CHOICE` | `UNBOUND` |

## 数据血缘和边界

- `accepted.json` 是确认后的 Facet sidecar；原文 query、Facet Span 和未解决输出类型均保留。
- 原始记录仍来自 `docs/gate_v3/semantic-dev-v1-confirmed/records.jsonl`，没有修改任何 `SemanticParseRecord`。
- 七条记录仍全部是用户确认的开发集记录，并且仍然是 `PARTIAL`；没有独立测试集、语义准确率或真实模型推理结果。
- `source_proposal_sha256`、`source_records_sha256` 和本步骤文件哈希写入 `manifest.json`。
- 不根据本次确认修改历史检索标签、v2/v3/v3.1 决策结果或生产默认行为。

## 验证命令

```powershell
Set-Location D:\Ai_agent_program\demo1
& 'C:\Users\Grey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 -m unittest `
  tests.evaluation.test_semantic_request_facets `
  tests.evaluation.test_semantic_request_facets_confirmed -q
```

测试只验证确认状态、定义和映射与草案一致、Span 可回查、原始 unresolved outputs 未丢失，以及 v1.0 记录契约未被修改；不代表语义模型效果。

确认后的下一步应另行设计 v1.1 序列化或 sidecar adapter。本步骤不接入 Parser、Gate 或生产运行时。
