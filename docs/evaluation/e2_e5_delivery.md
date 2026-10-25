# E2–E5 工程交付与验收

本文保留分阶段实施说明；最新的 19 条用例、执行证据参评资格、审核 CLI 与统一入口见 [最终交付](e2_e5_final_delivery.md)。

本轮在现有 `app/evaluation` 内增加可执行能力，复用 AgentRouter、黄金集离线分类器、业务规则、SQLite/PostgreSQL 仓储、checkpoint 和 `EvaluationAssertion`。后续 PostgreSQL 确认兼容修复见下文；没有产生真实审批。以下结果属于确定性集成或 Mock 链路验证。

## 实现与边界

| 阶段 | 实现 | 证据边界 |
|---|---|---|
| E2 | 版本化 JSONL、严格字段校验、多轮重建 Router、节点/工具轨迹、提交参数约束、独立持久化读回、确认及重复副作用断言 | 7 条开发用例，覆盖采购缺字段/确认/重复提交、请假日期、差旅总额、取消及注入。不是独立 holdout。权限和恢复复用现有回归测试；对应数据集指标保持 `n=0, rate=null`，不虚构覆盖率。 |
| E3 | 正确性/忠实性/完整性/配对 Judge 输入输出 Schema；PASS/FAIL/UNKNOWN；证据 ID 校验；调用/超时/Token/估计成本预算；Mock 和真实模型适配器；人工校准输入与统计 | 离线样例默认 UNKNOWN。实际人工校准样本为 0；agreement/kappa/false pass/false fail 保持 null。没有真实模型质量结论。 |
| E4 | 候选版本匿名化；一致数据/身份/语料/工具指纹；安全与最终状态资格前置；固定种子随机展示并全量交换顺序；胜率/平局/Unknown/顺序一致性/失败率；至少 20 条时 case-level bootstrap | 调用者必须提供可信确定性执行结果，资格字段不能由 LLM 判断。重复运行单独汇总，不当作独立样本。小样本不输出 CI。 |
| E5 | 本地失败卡、自动诊断归因、Pending Review、显式人工批准接口、禁止 holdout 转调优回归；相同数据集前后回归比较；现有 CI 扩展及手动 live workflow | 自动归因仅是诊断，不是人工根因结论；没有自动把 Judge 输出写成 Ground Truth。 |
| 观测适配 | 可选 Langfuse 本地实验结果发布，固定实验名，仅 case 摘要、版本摘要和确定性分数 | 不上传问题、制度正文、身份、工具参数。未执行外部发布，服务端接收未验证。 |

`workflow-report.json` 的通过条件是全部实际执行断言通过，最终回复的措辞不参与任务成功判断。指标报告包含真实分母；工具参数准确率目前仅覆盖提交参数约束，不代表所有工具参数均已评测。业务最终状态由仓储 API 读回，而不是从回复推断。

请假余额/工作日算法、住宿标准完整规则、重复报销业务检测等仍取决于现有业务能力。本轮没有新增这些业务功能，也不将简单草稿通过描述成这些规则已验收。

## 本地运行

```powershell
.venv/Scripts/python.exe -m scripts.run_workflow_evaluation --output-dir artifacts/workflow-evaluation
# 同一数据集的前后比较；先保留旧报告
.venv/Scripts/python.exe -m scripts.run_workflow_evaluation --baseline artifacts/previous/workflow-report.json --output-dir artifacts/current
```

每次运行创建独立临时 SQLite 数据库，每轮重建 Router。报告包括数据集、代码工作区指纹、模型/Prompt 标识、语料和 Git HEAD；临时数据库结束后清理，JSON/Markdown 与失败卡保留。`--baseline` 遇到数据集指纹变化会拒绝比较。

```powershell
.venv/Scripts/python.exe -m scripts.run_semantic_evaluation --mode offline --input tests/evaluation/semantic_smoke.jsonl --output-dir artifacts/judge --token-budget 20000 --cost-budget 1 --max-output-tokens 500 --max-usd-per-million 1 --timeout 10 --max-calls 6 --repetitions 1
.venv/Scripts/python.exe -m scripts.run_semantic_evaluation --mode offline --kind pairwise --input tests/evaluation/pairwise_smoke.jsonl --output-dir artifacts/pairwise --token-budget 20000 --cost-budget 1 --max-output-tokens 500 --max-usd-per-million 1 --timeout 10 --max-calls 6 --repetitions 1
```

真实模型运行需显式选择 `--mode live --model <model-id>`，并在进程环境设置 `EVAL_API_KEY`、`EVAL_BASE_URL`。预算参数均必填；客户端自动重试关闭。预算按输入 UTF-8 字节数加协议余量及输出上限预留 Token，按使用者给定的最高输入/输出单价约束估计成本；失败和超时不退回预留。实际 usage 缺失保留 null；异常超额 usage 会耗尽剩余预算。该成本是按配置估算，不是供应商账单认证。

`--calibration-labels labels.jsonl` 可读取人工标签，格式：

```json
{"case_id":"SEM-001","repetition":0,"human":"PASS","reviewer":"reviewer-name","review_status":"human_reviewed"}
```

只接受本次结果中的 case/repetition，重复标签报错；Judge 判断来自实际本次结果，不采用标签文件提供的 Judge 字段。UNKNOWN 和未审标签不进入二分类分母。当前并无已完成的人工校准。`calibration-pending.jsonl` 是待填模板。

失败卡位于 `pending-review.jsonl`，默认 `reviewer_decision=null`。`approve_regression(card, reviewer=..., decision='approve', rationale=...)` 是供显式人工审核流程调用的接口；返回审核卡，仍需编写并验证回归用例。它不自动生成业务标准答案。holdout 来源禁止进入此接口。

## CI 与发布

现有 `.github/workflows/ci.yml` 增加离线状态评测、安全/恢复测试及产物上传；PostgreSQL job 增加同一工作流数据集测试。原有黄金集、检索、完整测试和门禁保留。

新增 `live-evaluation.yml` 仅支持手动启动，使用 `evaluation` environment 中的凭据和固定合成样例。必须填写模型、Token/成本预算、最高单价、重复次数；调用上限 20，单次超时 30 秒，输出上限 500。输入通过环境变量传入，避免拼接 workflow 表达式到 shell 命令。没有对每个 PR 自动执行收费调用。

Langfuse 使用 E1 的配置和可选依赖；在已有离线工作流命令后加 `--publish-langfuse` 才发布结果。未启用或不可用时保留本地评测结果。SDK API 根据已安装 4.18.0 的签名验证，并参考 [Langfuse Experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)。真实模型适配器使用已有 OpenAI 依赖的 Chat Completions 接口；请求配置参考[官方 API](https://developers.openai.com/api/reference/resources/chat)。

## PostgreSQL 集成阻塞修复

在一次性 PostgreSQL 17 容器上实际发现：全 PostgreSQL 投影路径确认草稿时抛出 `DraftRevisionConflictError`。`workflow._confirm_draft` 更新 warnings，而 `postgres_runtime._draft_content_identity` 将 warnings 保留为同一 revision 的不可变内容。原始失败日志 `postgres.log` 保留。

后续已修复：仅从不可变业务内容比较中排除四条固定生命周期提示，其他风险提示与全部业务字段仍保持不可变，原事务、CAS 和合法状态迁移约束保留。原 XFAIL 已移除。当前两个模式均使用 11 条用例验证通过：PostgreSQL checkpoint + SQLite 投影、全 PostgreSQL checkpoint/投影/模拟提交。详见 [修复验收](postgres_unblock_acceptance.md)。运行时原有 Redis 协调激活限制仍保留，这些结果不等于生产多实例验收。

新增 `tests/evaluation/workflow_challenge_cases.jsonl`，版本 `workflow-challenge-v1`，4 条明确开发用途的用例：采购缺字段补齐、请假提交重放、差旅提交重放、采购金额矛盾禁止提交。原 `workflow-v1` 的 7 条用例及原报告保留。运行挑战集：

```powershell
.venv/Scripts/python.exe -m scripts.run_workflow_evaluation --dataset tests/evaluation/workflow_challenge_cases.jsonl --output-dir artifacts/workflow-evaluation/challenge
```

## 文件与产物

- `app/evaluation/workflow_evaluation.py`：数据集和真实状态执行器。
- `app/evaluation/judge.py`：Judge Schema、Provider、预算、校准统计。
- `app/evaluation/pairwise.py`：资格检查、盲评、换序与统计。
- `app/evaluation/failure_pipeline.py`：失败诊断、人工审核接口及回归比较。
- `app/evaluation/experiment_tracking.py`：安全元数据实验发布。
- `scripts/run_workflow_evaluation.py`、`run_semantic_evaluation.py`：可运行入口。
- `tests/evaluation/workflow_cases.jsonl`、`semantic_smoke.jsonl`、`pairwise_smoke.jsonl`：开发/合成数据，均非独立认证集。
- `tests/evaluation/test_evaluation_upgrade.py`、`tests/integration/test_workflow_evaluation_postgres.py`：正负、故障、状态和 PostgreSQL 验证。
- `.github/workflows/ci.yml`、`live-evaluation.yml`：CI 扩展。
- `artifacts/evaluation-e2-e5-20261023/`：报告、JUnit、首次失败日志及最终验收记录。

本轮可用于描述“多轮 Agent 状态评测、预算化 Judge、带顺序偏差检测的配对比较及人工审核回归闭环”的工程能力。不能写成已校准 Judge、独立泛化成绩或生产成功率。具体命令与最终测试数量见 `e2_e5_acceptance.md`。
