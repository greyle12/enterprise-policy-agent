# Agent 输出采集：本地实现与模拟验证

2026-10-24（Asia/Shanghai）。已实现注入评测 Provider 的 Agent 输出采集，当前 CLI 固定使用 scripted Provider，不读取模型凭据，也没有 live 选项。

## 本次实际执行

运行 4 个场景、7 轮 Router：

| 用例 | 场景 | 当前记录 |
|---|---|---|
| CAP-001 | 授权制度问答 | 实际分类与回答组件执行，返回 S1 引用；检索结果仅 fixture-allowed |
| CAP-002 | 无授权证据 | 实际分类后检索无命中，确定性弃答；没有调用回答模型 |
| CAP-003 | 输入注入 | PromptInjectionBlockedError；没有模型调用或会话写入 |
| CAP-004 | 采购草稿、确认、提交、重复提交 | SQLite 独立读回 awaiting_confirmation → confirmed → submitted → submitted；提交记录相同，重放标记为 true |

共 4 次 scripted Provider 调用，真实模型调用 0。实际执行的是现有 IntentClassifier、PolicyAnswerService、AgentRouter/LangGraph、业务规则、草稿生成及 SQLite checkpoint/仓储/模拟审批；模型输出由测试脚本提供。没有新增真实模型质量结论。

## 实现

- `app/evaluation/agent_capture.py`：采集 Schema、共享预算客户端、Router 组装与逐轮采集。
- `scripts/capture_agent_pilot.py`：仅本地的执行入口及源代码/制度指纹。
- `tests/evaluation/agent_capture_cases.jsonl`：4 条合成开发用例，与原 19 条工作流及原标签分开保存。
- `tests/evaluation/test_agent_capture.py`：状态、授权与预算边界验证。

每个 case 使用独立的临时 SQLite 数据库；每轮重新创建 Router，共享该 case 的 checkpoint 和投影。读取持久化 session、draft、submission 和 audit，记录完整结构化结果、问题/回复、节点和工具轨迹、调用请求/响应、延迟、错误类型及检索 ID。Router 重建验证不等于进程重启或生产多实例验收。原 19 条确定性评测仍由原 Runner 承担。

模型客户端调用实际分类器的 JSON 解析路径，不替换分类器；回答同样经过实际授权检索与上下文构建。复用合成 ACL fixture 的检索部分；fixture 自带的离线分类器/回答器不参与当前执行。草稿时钟固定为 2026-08-07，fixture 检索截至 2026-08-18；这是一组固定开发环境条件。

## 预算与失败

一个 BudgetedCaptureClient 在所有 case、轮次、分类和回答间共享预算；预算预留按请求 UTF-8 字节数、协议余量及输出上限计算。调用数、Token 与估算费用任一不满足即在 Provider 调用前拒绝；并发竞争由锁保护。失败、超时和取消不退还预留；异常 usage 耗尽剩余 Token 预算；不保存异常正文。

Provider 契约为 `complete(messages, max_output_tokens) -> (text, total_tokens_or_none)`，实际外部适配器必须执行输出上限。当前 scripted Provider 不提供真实 usage，保留 null，不报告模拟消耗为真实费用。工具执行器 max_attempts=1，不自动重试。预算只约束模型调用；已有确定性确认/提交逻辑仍由正常业务门禁控制。

执行状态为 unavailable 或输入被阻断时，采集器仍保存状态及错误记录。采集完成不等于任务成功，报告 `quality_conclusion=null`。人工审核数仍为 0。完整请求/回复仅存于本地评测产物，没有接入外部遥测或自动发布。

## 复现

```powershell
.venv/Scripts/python.exe -m scripts.capture_agent_pilot --output-dir artifacts/agent-capture-new
```

每次使用新目录，脚本拒绝覆盖已有产物。可用 `--dataset` 指定仓库内 CaptureCase 格式的数据，输入 case ID 唯一且问题非空。临时数据库在采集完成后清理，持久化状态快照保留在 JSON 中。

本轮最终产物：`artifacts/evaluation-agent-capture-offline-final/capture-report.json`。初次运行保留在 `artifacts/evaluation-agent-capture-offline/`，两次均为本地 scripted 执行。

## 验证与后续

专项 12 项测试通过，覆盖授权/弃答/注入、多轮提交幂等、全局调用/Token/费用上限、并发预算、失败/超时保留预算、usage 异常、取消以及预算失败后状态采集。合并回归及当前验收见同目录 `acceptance.json`，JUnit 见 `artifacts/evaluation-agent-capture-tests/final-tests.xml`。

下一步可为该 Provider 契约增加具体外部 API 适配器、验证超时/输出上限/usage 与模型身份，再依据明确的数据范围和预算执行真实采集。现有 3 条 Judge smoke 授权已经完成；本次没有扩大其外发范围。当前也未运行全 19 条安全/恢复数据集的真实模型版本，未完成 Agent 语义评测或人工校准。
