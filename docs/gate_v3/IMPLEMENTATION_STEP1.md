# 门控 v3 第 1 步：结构化意图与四值决策

本步骤实现 `scripts/gate_v3_intent.py`，并新增
`tests/evaluation/test_gate_v3_intent.py`。它是独立的评估组件，不修改
`scripts/experiment_coverage_gate.py`、`scripts/coverage_gate_v2.py`、两条
冻结规则、已确认标签、FastAPI 或生产选择器。

## 解决的问题

v1/v2 以关键词是否出现决定是否补充，无法可靠区分：

- “不问同住，但仍问住宿金额”的局部话题排除；
- “是否可以免除复核”的豁免要求；
- “不是不需要审批”的双重否定；
- “类别已知”跨句条件；
- 指代、冲突和信息不足。

本实现把解析和决策分开，输出可回溯到原查询字符位置的对象。

## API

```python
from scripts.gate_v3_intent import evaluate

intent, result = evaluate(
    "目的地已确认是二类城市。普通员工每晚住宿上限是多少？",
    "city_category_for_lodging_table",
)
assert result.decision.value == "DENY"
```

主要对象：

- `QueryIntent`：原始查询、带 `[start, end)` 偏移的 `QueryClause`、
  `QueryRequest`、`QueryCondition`、`QueryAmbiguity`。
- `RequestAct`：`ASK_REQUIREMENT`、`ASK_EXEMPTION`、`EXCLUDE_TOPIC`。
- `ConditionStatus`：`GIVEN`、`QUESTIONED`、`UNKNOWN`、`CONFLICTED`。
- `DecisionResult`：`ALLOW`、`DENY`、`UNRESOLVED`、`NOT_APPLICABLE`、
  `reason_code`、证据 spans 和不确定原因。
- `relation_for`：只允许当前冻结的两个规则 ID，不接受自造关系。

解析器只实现有限、可解释的中文模式，不调用 LLM，不访问语料、标签、
案例编号、检索结果或外部服务。无法可靠绑定的指代和冲突返回
`UNRESOLVED`；不把不确定性默认改成 `DENY`。直接询问目标条款（如只列一类
城市名单）返回 `NOT_APPLICABLE`，不删除目标主要证据。

## 选择器边界

本步骤没有 `select_v3`，也没有把四值结果接入原选择器。后续实验设计中：

- `ALLOW` 接受冻结选择器的唯一补充；
- `DENY` 恢复原始 Top-5；
- `UNRESOLVED` 暂保留冻结提案并单独统计；
- `NOT_APPLICABLE` 不执行该补充关系。

这些行为尚未在本步骤实现或测量。前四位、最多一条补充、最终 K=5 和
20/40 候选限制继续由旧实验保持。

## 验证

```powershell
python -X utf8 -m unittest tests.evaluation.test_gate_v3_intent -v
python -X utf8 -m scripts.gate_v3_intent `
  --rule-id city_category_for_lodging_table `
  --query "目的地已确认是二类城市。普通员工每晚住宿上限是多少？"
```

预期 12 项单元测试通过；命令输出完整 intent 和 decision JSON，决策为
`DENY`、原因 `CITY_CATEGORY_ALREADY_GIVEN`。PowerShell 字符串可替换为其他
问法，规则 ID 必须是两个冻结 ID 之一。

建议回归命令（不会执行模型推理）：

```powershell
python -X utf8 -m unittest `
  tests.evaluation.test_gate_v3_intent `
  tests.evaluation.test_gate_decisions `
  tests.evaluation.test_coverage_gate_v2 `
  tests.evaluation.test_coverage_gate `
  tests.evaluation.test_coverage_window_replay `
  tests.evaluation.test_gate_challenge -v
```

当前环境若没有可执行的项目 venv，使用项目已有 Python 运行上述
`unittest`；未安装 pytest 不代表集成评测通过或失败。本步实际验证为
26 项 unittest、Ruff；没有新的 BGE 推理或质量结果。

## 风险和下一步

规则模式仍可能漏掉未覆盖的中文表达；结构化字段不等于通用语义理解。
跨句条件只在有明确邻接证据时继承，多实体或冲突保持不确定。
下一步必须先为 `UNRESOLVED` 增加人工确认样本，再单独做决策层评测；
之后才可在不改变冻结输入的实验回放中验证 v3 的质量和回退成本。

建议提交信息：`feat(retrieval): add structured gate v3 intent decisions`。
本步骤未提交、推送或接入运行时。
