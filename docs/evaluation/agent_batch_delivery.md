# Agent 评测批次交付

2026-10-24（Asia/Shanghai）。本批次完成外部模型适配器、本地接口契约验证、19 条工作流的输出采集与状态评测、实际回答到语义 Judge 的输入导出，以及统一离线运行和 CI 接入。

## 已完成能力

| 能力 | 当前实现 |
|---|---|
| 外部模型适配 | DeepSeekCaptureProvider：显式配置、SDK 自动重试关闭、max_tokens 输出上限、reasoning_effort=none、响应模型与 usage 回执、异步释放 |
| HTTP 契约 | HTTP MockTransport 经真实 OpenAI SDK 序列化，验证请求字段、401/429/500 不重试、截断/空响应拒绝、usage 缺失保留未知和超时取消；没有外部请求 |
| 完整工作流采集 | 原 7 基础 + 4 挑战 + 8 安全/恢复用例，共 19 条、39 轮，复用原业务预期和持久化断言 |
| 模型注入 | 实际 IntentClassifier 和 PolicyAnswerService 接受共享预算客户端；保留原 ACL/过期/注入/重试/回执丢失 fixture 语义 |
| 中途证据 | 每完成一轮写 capture-partial.json；用 capture_complete 区分未完成 case；正常结束保存完整报告 |
| 独立状态读回 | 每轮查询 SQLite session、draft、submission、audit，记录问题/回复、节点/工具、调用及授权检索 ID |
| 语义输入导出 | 只对已完成且有授权证据的制度回答导出 faithfulness；从实际发送的上下文提取证据和文档版本；绑定执行 ID 与答案哈希 |
| 失败处理 | 工作流失败生成 pending-review 卡，reviewer_decision=null；无合格回答则跳过语义阶段并记录原因 |
| CI | 原质量 job 增加该批次离线运行及新契约测试，产物沿用原工作流评测上传目录；未触发远端 Actions |

当前全量本地运行：19/19 工作流通过，39 轮完整采集，21 次 scripted Provider 调用，导出 3 条忠实性 Judge 输入。它们来自 WF-013、WF-017、WF-018 的实际 Router 回复，绑定实际授权上下文；其他 36 轮给出明确跳过原因。没有参考答案时不虚构 correctness/completeness 标签。

随后使用原语义 Runner 的 Mock Provider 对三条导出输入执行结构验证，均为 UNKNOWN。22 项批次 JUnit 是 19 条工作流断言门禁加 3 条 Mock 语义链路检查，不是 22 个独立模型质量样本。

## 一条命令运行

```powershell
.venv/Scripts/python.exe -m scripts.run_agent_evaluation_batch --output-dir artifacts/agent-batch-new
```

每次使用新目录。该统一入口固定离线模式，不读取模型凭据、不发布 Langfuse、不产生实际审批。最终本地证据：`artifacts/evaluation-agent-batch-20261024/delivery-final/`。

主要产物：

- `summary.json/.md`、`batch-junit.xml`：批次结果和分层工程门禁。
- `workflow/capture-report.json`：39 轮原始执行及状态断言、预算、实际调用模式、源码/数据/语料指纹。
- `workflow/capture-partial.json`：逐轮进度快照。
- `workflow/semantic-inputs.jsonl`、`semantic-bindings.json`、`semantic-skipped.json`：可消费的 Judge 输入、源执行绑定和逐轮跳过原因。
- `workflow/pending-review.jsonl`：仅失败时写待审卡，不自动批准或改写原标签。
- `semantic/judge-report.json`、`calibration-pending.jsonl`：本次导出回答的离线 Mock 检查和待审模板。
- `live-config.pending.json`：包含当前源码、数据和语料指纹；模型、接口、预算和外发范围尚空，默认不能用于收费调用。

## 真实采集入口

`scripts.run_agent_workflow_capture` 支持显式 live 模式，仅采集 Agent 工作流，不另外调用语义 Judge。操作者必须提供与当前源码/输入/语料指纹一致的配置及进程环境 EVAL_API_KEY。不会自动沿用 .env 或剩余历史预算。

配置必须包含 model、base_url、api_key_env、JudgeBudget 全部字段、dataset_sha256、corpus_sha256、source_sha256，以及 approved_data_scope=workflow_development_and_synthetic_fixtures。预算按全批次共享，工具级重试也计入实际 Provider 调用预算。SDK 层不重试。live 适配器只接受经过验证的 DeepSeek HTTPS 主机及根路径或 /v1。

```powershell
.venv/Scripts/python.exe -m scripts.run_agent_workflow_capture --mode live --live-config <explicit-config.json> --output-dir <new-directory>
```

适配器未擅自发送请求。真实试运行需要对应的数据范围和预算授权；原先三条合成 Judge smoke 的授权已执行完毕。此前三次真实 Judge 结果仍是本项目这条升级路径中已验证的真实模型调用证据。

## 源码与证据边界

新增 `app/evaluation/capture_provider.py`、两个批次脚本和两份契约测试。扩展 `agent_capture.py`；对 `workflow_evaluation.py` 只增加可选 runtime_fixture/on_turn 参数及回调，默认行为保留。没有改动业务 Router、生产 LLM 客户端、审批规则或原 19 条数据标签。

原 E2–E5 的交付清单中，workflow_evaluation.py 与 CI 文件因本批次扩展发生变化，其余清单文件应保持一致；差异另存新验收清单，不覆盖旧证据。E6 准备脚本对旧 21 文件指纹的严格检查会按设计检测到这些变化，不能把旧清单作为当前源码的唯一运行身份。

本地 scripted 结果、SDK Mock 契约、真实 PostgreSQL 结果和三条历史真实 Judge 结果分别保留。人工审核数仍为 0，质量结论为 null；没有独立 holdout、生产多实例效果或全仓/远端 CI 全绿声明。

## 验收入口

本次合并回归 **286 项通过，无失败、错误或跳过**，其中真实 PostgreSQL 使用本轮一次性 PostgreSQL 17 容器，完成后已移除。一个现有 Starlette/httpx 弃用提示保留。黄金集 30/30，检索集 20 条/199 Chunks 门禁通过，七份 Python 文件 Ruff check / format check 和 CI 配置检查通过。

本轮合并 pytest、真实 PostgreSQL、黄金集、检索和 CI 配置检查的最终结果见 `artifacts/evaluation-agent-batch-20261024/final-acceptance.json`。完整测试命令、JUnit、日志及新文件哈希与历史报告并存。
