# E7 首批真实 Judge 试运行

用户明确授权沿用现有应用模型和接口，仅发送 3 条合成 Judge 样例，费用上限 3 USD。本次已执行完毕，没有扩大数据范围或自动重跑。

## 实际结果

| 用例 | Rubric | Judge 判定 | 接口状态 | Token |
|---|---|---|---|---|
| SEM-001 | correctness | PASS | ok | 253 |
| SEM-002 | faithfulness | FAIL | ok | 257 |
| SEM-003 | completeness | FAIL | ok | 256 |

三次均正常结束，返回的证据 ID 全部属于输入证据。这里的 PASS/FAIL 是真实 Judge 对预先编写的合成答案的判定，尚无人工校准标签。三条结果不能作为 Agent 准确率、Judge 校准准确率或独立泛化结果。

请求沿用应用配置名 `deepseek-v4-flash`；实际 response.model 为 `deepseek-flash`。官方价格页说明旧名映射至 DeepSeek-V4.1-Flash-1017。本记录保留请求模型名和响应模型名，实际不可变权重版本未通过接口独立认证。

## 用量和预算

实际 3 次请求，输入共 598 Token，输出共 168 Token，总计 766 Token。按最高输入/输出单价统一取 1.2 USD / 百万 Token，保守费用估计上界为 **0.0009192 USD**，低于用户给定的 3 USD。它是 API usage 与已核实单价的计算结果，供应商账单尚未核对。

本次限制为：3 次调用、无自动重试、单次 60 秒、每次最多输出 1,000 Token、总预留 20,000 Token。总 Token 限制对应保守费用上界 0.024 USD。按 [DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/) 的峰值价格计算，不依赖缓存命中或谷时优惠。

使用 [DeepSeek 官方 Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 的 `max_tokens`；固定 `reasoning_effort=none`，启用 JSON 格式。适配仅作用于本次 smoke 脚本，现有应用和通用 Judge Runner 未改动。凭据从已有进程环境或本地应用 .env 读取，未写入产物。

## 证据与审核交接

产物目录：`artifacts/evaluation-e7-live-judge-smoke/`。

- `run-config.json`：模型/接口、输入/Runner/Judge 指纹、预算及计价依据。
- `provider-receipts.json`：实际请求中的合成内容、响应 ID、模型、finish_reason、usage 和最终 JSON；不保存密钥、请求头、异常正文或推理内容。
- `partial-results.json`、`judge-report.json`：三条真实 Judge 结果、延迟、证据引用检查、Token 和估计成本。
- `judge-blind-review.jsonl`：绑定本次报告 SHA-256 的待审材料，含 rubric、问题、答案、reference、授权证据；不展示 Judge 判定、理由或模型名。人工字段保留 null。
- `evidence-manifest.json`：本次相关源码、文档及运行产物哈希。

人工审核时应先独立阅读盲审材料，再填写 reviewer、human、rationale、review_status；不能把本报告中的判定直接复制成“人工标签”。本次 `human_calibration_samples=0`，agreement/kappa/false pass/false fail 均保持 null。

当前待审模板绑定已有报告，不应为了人工对照重新调用模型。现有通用 `--calibration-labels` 会触发新一轮判定；后续应在独立离线 join 中校验报告和输入指纹，再对照人工结果。

## 后续边界

本轮新增适配测试 5 项，与既有 Judge/配对/工作流升级测试及 E6 准备测试合并运行，28 项全部通过。两份新增 Python 文件的 Ruff check / format check 通过；JUnit 见 `artifacts/evaluation-e7-live-smoke-tests/final-tests.xml`。未重跑全仓或原 223 项验收。

本次完成 E7 的 Judge 接口小批量验证。实际 Agent 输出采集、19 条工作流的真实模型评测、独立测试集审核/冻结、人工校准和 Langfuse 外部接收仍待完成。下一项建议先完成这三条结果的独立人工盲审，以及绑定既有报告的离线校准汇总；后续真实 Agent 调用另行明确数据和预算范围。

Runner 命令用于记录本次运行方法；再次执行会产生新调用，需有对应运行范围与预算，且必须使用新目录：

```powershell
.venv/Scripts/python.exe -m scripts.run_live_judge_smoke --cost-budget 3 --output-dir artifacts/evaluation-e7-live-judge-smoke
```

本次目录已存在，脚本会在创建客户端前拒绝覆盖。数据哈希或模型/接口与本次核实范围不符也会阻止调用。
