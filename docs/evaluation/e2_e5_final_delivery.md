# E2–E5 最终工程交付

本记录接续 E0/E1、E2–E5 初次交付和 PostgreSQL 阻塞修复；历史报告与失败日志保留。当前交付的是可运行的评测系统，证据模式为确定性集成与离线 Mock。真实模型质量、人工校准和生产效果仍需相应实际运行，不能用本记录替代。

## 交付范围

| 阶段 | 当前能力 | 验收边界 |
|---|---|---|
| E2 | 19 条版本化用例；真实 LangGraph 多轮执行；每轮重建 Router；工具选择/提交参数、节点、重试次数；session、draft、submission 独立读回及身份一致性；确认、幂等、未知提交结果恢复 | SQLite、PostgreSQL checkpoint+SQLite 投影、全 PostgreSQL 投影/模拟提交。数据均为开发/安全回归数据，非独立 holdout。 |
| E3 | Correctness / Faithfulness / Completeness 独立 rubric；Pydantic 判定与证据 ID；PASS/FAIL/UNKNOWN；预算、超时、无自动重试的 live provider；人工标签对照与 agreement/kappa/false pass/false fail | Prompt 更新至 semantic-judge-v2。默认 Mock 判定 UNKNOWN；实际人工样本 0，未校准 Judge 不能独立阻断部署。 |
| E4 | 输入/身份/语料/工具指纹一致性；使用实际工作流断言审查参评资格；版本名不进 Judge；随机盲序、全量换序、胜率/平局/Unknown/顺序一致性及 case-level bootstrap | 裸 `safety_passed=true` 已不能参评；检查断言值、判定与必需状态证据。外部报告的来源仍需由调用方可信地产生，本地 JSON 不是执行真实性的密码学认证。至少 20 个不同 case 才输出区间，重复次数不充当独立样本。 |
| E5 | 故障归因和执行 ID、待审核卡、数据/执行指纹绑定的审核 CLI、holdout 隔离、禁止覆盖既有回归文件、回归差异、CI 与 JSON/Markdown/JUnit 产物 | 审核命令只读取操作者提供的明确决定；本轮没有代替人工批准任何样本。独立故障注入示例验证闭环，标签保留 pending/null。 |

原 RAG Recall/MRR/nDCG 继续使用原 Runner；没有新增一套重复检索指标。回答语义 Judge 接收显式授权证据，确定性安全用例另外检查引用源、无证据弃答与未授权内容隔离。它们不能等价于真实模型的语义准确率。引用源检查目前按 source ID，不宣称已覆盖所有文档版本错误。

工具参数准确率当前覆盖提交身份/确认/会话/幂等键约束；草稿金额/日期等通过独立字段断言验证。请假余额、完整工作日算法和住宿标准等未存在的业务规则未被新增或虚构为已验收能力。

## 数据与安全/恢复覆盖

- `workflow_cases.jsonl`：7 条基础业务/安全回归。
- `workflow_challenge_cases.jsonl`：4 条多轮补齐、请假/差旅提交重放及金额矛盾阻断。
- `workflow_safety_cases.jsonl`：8 条授权拒绝、混合授权检索、过期证据、受控无命中、注入证据隔离、意图超时重试、模型超时重试、提交已落库但回执丢失后恢复。

安全 fixture 使用合成 Chunk 和固定测试身份，经过实际授权过滤与上下文构建；不在正常链路探测未授权正文。权限违规以检索 ID、上下文与回复 sentinel 检查；原因来自受控 fixture 元数据，应用不会据此编造对外拒绝原因。重试用例验证实际 attempts，未知提交结果用例验证 mutation 首次调用仅一次、重放返回原记录。

安全数据集已测分母：授权边界 7，弃答 7，引用源 7，恢复 3。报告逐项保留实际 n；这不是生产成功率。工作流完成率定义为符合预期终止/拒绝行为及持久化 phase；任务成功率还要求全部工具、规则、安全和状态断言通过。

## 一条命令验收

```powershell
.venv/Scripts/python.exe -m scripts.run_evaluation_delivery --output-dir artifacts/evaluation-delivery
```

执行三个工作流数据集，再执行离线语义与配对链路。配对输入绑定本次实际工作流结果。另生成独立 `injected-fault-demo.jsonl`，仅把示例预期提交数从 1 改为 2，用来确认评测器能识别错误并挖出待审核卡；原数据完全不改。此示例带 `injected_expectation_fault/not_ground_truth` 标签，不计入 19 条正常用例。

输出 `delivery-summary.json/.md`、`delivery-junit.xml`，各数据集 JSON/Markdown、待审核卡、Judge 与配对报告。故障注入示例报告失败是预期结果；总入口只有成功识别它并生成卡片才通过。JUnit 区分 deterministic_integration、offline_mock、fault_injection，不把 Mock UNKNOWN 记为模型质量通过。

原单项命令、可选 Langfuse 发布、真实模型预算和人工校准参数仍见 [运行说明](e2_e5_delivery.md)。默认不发送外部遥测或真实模型请求。`live-evaluation.yml` 仍仅支持人工启动与明确预算配置；本次未触发远端 workflow。

## 人工审核到回归数据集

先审阅源 dataset、失败报告和 `pending-review.jsonl`。操作者创建 decisions JSONL，包含 `case_id`、卡片中的 `source_sha256`、`reviewer`、`decision=approve/reject`、`rationale`。随后运行：

```powershell
.venv/Scripts/python.exe -m scripts.review_workflow_regressions --dataset <source.jsonl> --report <workflow-report.json> --decisions <human-decisions.jsonl> --output <new-regression.jsonl>
```

程序核对报告的数据集指纹、审核绑定的执行指纹与源 split，拒绝 holdout 晋升、重复决定、覆盖源文件/既有输出。批准后生成 workflow-regression-v1 数据及 `.review.json` 回执；原业务预期不被 Judge 改写。拒绝决定不晋升用例。程序不能判断提供的 reviewer 是否真为人类，需由操作者保证审核来源；本轮真实审核数为 0。

## 文件改动与当前验证

新增：`app/evaluation/workflow_fixtures.py`、`scripts/run_evaluation_delivery.py`、`scripts/review_workflow_regressions.py`、`tests/evaluation/workflow_safety_cases.jsonl`、`tests/evaluation/test_evaluation_delivery.py`。

扩展：`workflow_evaluation.py`、`judge.py`、`pairwise.py`、`failure_pipeline.py`、两个既有评测 CLI、评测升级测试、PostgreSQL 集成测试、配对 smoke fixture 与现有 CI。PostgreSQL 的阻塞修复继续由仓储回归覆盖；生产激活限制未变。

验收证据目录：`artifacts/evaluation-e2-e5-final/`。

- 最终合并回归：223 项通过，无跳过、无 XFAIL；包含 API、ACL、注入、恢复、持久化、原评测 Runner 与真实 PostgreSQL 集成。一个现有 Starlette/httpx 弃用提示。
- 统一离线入口：19/19 工作流通过；3 个 Mock 语义结果 UNKNOWN；1 个配对、2 次 Mock 判断，胜者 UNKNOWN；故障注入成功被检出。
- 原黄金集 30/30，原检索集 20 条、199 Chunks，门禁通过。
- 本轮 12 个 Python 文件 Ruff check / format check 通过；CI 配置校验通过。

具体测试命令保存在同目录 `acceptance-command.txt`；JUnit、CLI 日志和文件哈希清单可独立检查。不宣称全仓历史评测、远端 Actions 或生产环境已全部验收。

简历可描述：构建多轮 Agent 确定性状态评测、带预算和 UNKNOWN 状态的语义 Judge、结合执行证据的盲序配对与偏差检查，以及经审核后进入回归数据集的闭环；不得写成独立模型质量提升、已完成人工校准或线上成功率。
