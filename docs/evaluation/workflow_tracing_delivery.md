# 完整工作流的本地观测与失败定位

本轮将单条 SDK 探针扩展到既有 19 条工作流、39 轮离线执行。复用原采集入口、真实 LangGraph、SQLite 状态检查和可选 Langfuse SDK，新增轮次到 trace 的本地关联与失败卡定位。默认业务采集行为保持兼容；新增入口固定使用离线 Scripted provider 和内存导出器，没有真实模型、语义 Judge 或外部遥测调用。

## 已完成

- `scripts.run_agent_workflow_capture.run` 增加可选的 `on_snapshot` 回调，默认不启用；原采集内容、状态断言和配置校验保持原有语义。
- 新入口在每轮快照完成后读取已经结束的 SDK span。校验一轮一个 `agent.request` 根节点、trace ID 不跨轮复用、span ID 不重复、子节点全部连接到根且无环、根节点状态与该轮异常信息一致。
- 对预期的安全拦截，完整的 error trace 仍算关联成功；span 返回状态不是业务办理成功率。LangGraph 的正常确认中断也不能据此认定为审批失败。
- 本地 `trace-index.json` 关联 case、execution、turn 和生成的 trace/span ID，并绑定原采集报告 SHA-256。原始问题、回答、身份和执行 ID 不作为 SDK 元数据发送。
- 复用既有 `failure_cards`，仅在新的本地审核卡上附加 trace ID、索引哈希、原采集哈希和关联完整性。卡片原有 source_sha256 保持原执行结果绑定，reviewer_decision 仍为 null，不自动晋升回归标签。
- 记录器启动、结束、flush 或 shutdown 失败时，业务结果独立保存；观测门禁报告失败。丢轮、错配、环、重复、已复用 trace 或不完整审核卡关联不会获得通过。

## 运行

```powershell
.venv/Scripts/python.exe -m scripts.run_traced_workflow_evaluation --output-dir artifacts/traced-workflow-check
```

输出目录必须不存在。需要安装项目 observability extra。该命令没有 live/export 开关；即使环境里配置了真实 Langfuse 地址和凭据，也只使用随机合成 SDK 配置与内存导出器。它临时替换进程内 tracing backend 并在 finally 中恢复，作为独立 CLI 运行，不用于并发业务服务内切换 tracing。

## 本轮结果与证据

目录：`artifacts/evaluation-workflow-tracing-20261024/`。

| 产物 | 用途 |
|---|---|
| delivery-final/workflow/capture-report.json | 19 条工作流、39 轮快照与 449 项业务断言；Scripted provider 的 21 次本地调用不是模型 API 调用 |
| delivery-final/trace-index.json | 39 轮各自绑定到生成的 trace ID，仅在本地保存 execution ID |
| delivery-final/trace-spans.json | 333 个真实 SDK 产生的内存 span，含名称、父子关系、状态、延迟和配置版本摘要 |
| delivery-final/trace-linked-pending-review.jsonl | 正常批次没有业务失败，文件为空 |
| delivery-final/trace-junit.xml | 19 条工作流及其轮次关联的工程门禁 |
| delivery-final/delivery-manifest.json | 该批次所有产物的文件哈希 |
| fault-input-final.jsonl、fault-injection.json | 独立的合成故障副本与说明，原数据集未改写 |
| fault-demonstration-final/ | 故障被检出后生成的 1 张待审核卡及对应本地 trace |
| acceptance.xml、acceptance.json | 本轮合并回归、源码与产物指纹 |

故障演示只将复制出的 WF-013 第 0 轮期望 authorization_violation 从 false 改为 true，实际授权检查仍返回 false。工程门禁正确检出这次故意制造的断言不符，并将待审核卡关联到该次 trace。这不是实际发生了权限泄漏；该样例不进入原回归集、不填写人工决定。

首个准备副本因使用不支持的 dataset_version 被 schema 拒绝，未执行业务；该失败准备产物保留为 fault-input.jsonl。最终副本沿用源数据的受支持版本号，通过 schema 后完成上述故障演示。

测试覆盖真实 SDK 的完整 19 条工作流、禁止网络发送、配置值和业务内容脱敏、旧采集入口兼容、记录器生命周期故障，以及合成的丢失/复用/错配/环状 trace。CI 保留原质量作业与 Actions 固定 SHA，新增默认离线的完整工作流观测步骤，并复用原 artifact 上传目录；本轮没有触发远端 Actions。

最终合并回归 153/153 通过，无失败、无跳过；本轮 5 个 Python 文件 Ruff check/format check 和本地 CI 配置校验通过。测试同时覆盖已有 E2–E5、辅助审核、配对比较、采集、ACL、注入、恢复及上一轮回执契约。未重跑 PostgreSQL 集成和全仓历史测试。原真实采集报告 SHA-256 仍为 75bd77832bd8e87639e27342d5bc86f30059339b57057c6cfedcab28b8cd5455。

## 结论边界

本轮完成本地业务执行到观测再到失败卡的关联工程。后续 [本地 Langfuse 真实接收验收](local_langfuse_acceptance.md) 已通过，范围为一条新合成探针的 8 个 observation；本页 19 条离线工作流仍为内存证据，原 19 条真实 LLM 采集也没有被补造历史 trace。E9 实际 A/B 比较仍待另一份满足相同实验条件的真实候选结果。原真实采集、助手建议、人工待确认状态和既有冻结/待审标签保持原状。
