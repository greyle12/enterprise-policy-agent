# Semantic Parse Interface

状态：contract_only。

本文件和对应代码只定义语义解析的结构化边界，不代表已经接入语义模型，也不代表已经获得语义准确率。当前没有默认 SemanticParser、模型调用、外部 API 调用或生产运行时接入。

## 目标与边界

未来的语义解析器需要把一条用户问法输出为一个可审计的 SemanticParseRecord。记录必须保留：

- 用户当前发出的请求与被引用、转述的说法；
- 节点、关系边和作用域；
- 每个语义对象对应的原文 Span；
- 无法解析、歧义或缺失的输出。

本步骤不改变 v2/v3.1 Gate 的选择逻辑，不替换生产默认，不连接 FastAPI/Agent Runtime，也不产生端到端效果结论。

## JSONL 记录格式

检查器接收 UTF-8 JSONL，每行一个记录。顶层字段如下：

| 字段 | 含义 |
| --- | --- |
| schema_version | 当前必须为 1.0 |
| query | 原始用户问句，作为所有 Span 的唯一坐标系 |
| status | COMPLETE、PARTIAL 或 FAILED |
| parser_id | 可选的解析器标识；当前仅保留元数据 |
| parser_revision | 可选的解析器版本或 revision；当前仅保留元数据 |
| nodes | 语义节点数组 |
| edges | 节点之间的有向关系数组 |
| scopes | 作用域数组 |
| missing_outputs | 未能安全输出的语义字段数组 |

示例：

~~~json
{
  "schema_version": "1.0",
  "query": "有人说“普通费用不需要财务复核”，请核实这项说法。",
  "status": "COMPLETE",
  "parser_id": null,
  "parser_revision": null,
  "nodes": [
    {
      "node_id": "assertion-1",
      "node_type": "REPORTED_ASSERTION",
      "text": "普通费用不需要财务复核",
      "source_spans": [
        {"start": 4, "end": 15, "text": "普通费用不需要财务复核"}
      ],
      "act": "REPORT_ASSERTION",
      "scope_id": "scope-quoted"
    },
    {
      "node_id": "request-1",
      "node_type": "USER_REQUEST",
      "text": "请核实这项说法",
      "source_spans": [
        {"start": 17, "end": 24, "text": "请核实这项说法"}
      ],
      "act": "VERIFY_ASSERTION",
      "scope_id": "scope-request"
    }
  ],
  "edges": [
    {
      "edge_id": "edge-1",
      "source": "request-1",
      "target": "assertion-1",
      "relation": "TARGETS",
      "source_spans": []
    }
  ],
  "scopes": [
    {
      "scope_id": "scope-quoted",
      "scope_type": "REPORTED_ASSERTION",
      "source_spans": [
        {"start": 4, "end": 15, "text": "普通费用不需要财务复核"}
      ],
      "node_ids": ["assertion-1"],
      "parent_scope_id": null
    },
    {
      "scope_id": "scope-request",
      "scope_type": "USER_REQUEST",
      "source_spans": [
        {"start": 17, "end": 24, "text": "请核实这项说法"}
      ],
      "node_ids": ["request-1"],
      "parent_scope_id": null
    }
  ],
  "missing_outputs": []
}
~~~

## 用户请求与引用说法

NodeType 是安全边界的一部分：

| node_type | 允许的 act | 语义 |
| --- | --- | --- |
| USER_REQUEST | VERIFY_ASSERTION | 用户要求核实一条说法 |
| USER_REQUEST | DENY_REQUEST | 用户明确拒绝或排除一个请求 |
| REPORTED_ASSERTION | REPORT_ASSERTION | 用户正在转述、引用或报告他人的说法 |
| ENTITY、CONDITION | null | 被请求或被引用的对象，不直接代表用户动作 |

REPORT_ASSERTION 不能挂在 USER_REQUEST 上；VERIFY_ASSERTION 和 DENY_REQUEST 不能挂在 REPORTED_ASSERTION 上。这样可以把“有人说不用审批，请核实”与“我要求不用审批”保留为不同的结构，避免引用内容被静默升级为用户指令。

## 节点、关系和作用域

节点必须有唯一的 node_id、非空 text 和至少一个 source_spans。当前允许的关系为：

- TARGETS
- REFERS_TO
- SUPPORTS
- DENIES
- SCOPED_BY
- QUOTES

每条边的 source 和 target 必须指向已声明节点，否则是悬空关系。当前契约要求节点关系图无有向环；作用域的 parent_scope_id 也必须指向已声明作用域且不能形成环。检查器会分别报告 DANGLING_*、RELATION_CYCLE 和 SCOPE_CYCLE。

作用域必须有唯一的 scope_id、scope_type、至少一个 source span 和 node_ids。一个节点可以通过 scope_id 归属一个作用域。

## Span 与缺失输出

Span 使用半开区间 [start, end)，坐标按 Python 字符串索引解释。检查器会验证：

1. start/end 是整数；
2. 0 <= start < end <= len(query)；
3. query[start:end] 与 Span 的 text 完全一致。

节点和作用域必须保留至少一个原文 Span。边和 missing output 的 Span 可以为空，因为关系或缺失字段有时没有单独的连续表述；一旦提供，也必须通过同样的校验。

如果解析器无法安全输出字段，必须把它放在 missing_outputs，同时把 status 设为 PARTIAL 或 FAILED。COMPLETE 记录不能带 missing_outputs；PARTIAL 和 FAILED 记录不能静默省略 missing_outputs。当前契约只保留 output_type、reason 和可选原文 Span，不允许消费者自行把缺失值推断成确定结论。

## 离线检查

在项目根目录运行：

~~~powershell
Set-Location D:\Ai_agent_program\demo1

.\.venv\Scripts\python.exe -X utf8 -m scripts.check_semantic_parse_records .\records\semantic.jsonl
~~~

检查器只输出 JSON 摘要并以状态退出：

- 退出码 0：所有记录通过；
- 退出码 1：存在 JSON、解码或契约校验错误；
- 摘要包含 record_count、valid_record_count、invalid_record_count、records_with_missing_outputs 和逐项 errors。

契约测试：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\evaluation\test_semantic_parse_contract.py -q
~~~

## 后续接入限制

未来接入真实语义模型时，模型名称、revision、代码 HEAD、依赖环境、数据集版本和哈希必须作为实验记录保存；这些元数据不能被当作本契约已经完成的模型效果。需要另行设计解析器实现、标注集、独立测试集、准确率口径和生产降级策略后，才能讨论语义模型效果。
