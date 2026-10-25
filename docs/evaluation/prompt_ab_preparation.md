# E9 同模型回答提示词实验准备

2026-10-25（Asia/Shanghai）。按用户选择，候选为同一模型下的回答提示词改进。本轮完成候选实现、两次独立离线执行、请求指纹核验、真实 A/B 输入资格检查及 CI 接入；尚未执行新的收费模型或 Judge 调用。

后续用户已明确授权并完成 [真实双侧采集交付](prompt_ab_live_delivery.md)：42 次模型调用，两侧各 19/19 条工作流通过，无 Judge 或外部遥测。以下保留准备阶段结果与待确认配置的历史状态。

## 实验变量

应用默认提示词保持原样。`BudgetedCaptureClient` 只在采集入口识别与原回答系统提示词完全一致的消息，将以下固定补充追加到 system 消息；不改变用户问题、授权证据、分类器、检索、工具、权限、审批模拟或状态断言。

> 作答前逐项核对证据中的适用对象、前提、例外、必需材料和审批条件，只保留影响当前问题结论的项目。
> 不得遗漏会改变结论的条件，也不得把不同条款拼接成证据未规定的新要求。
> 证据没有覆盖的部分明确说明无法确定；不要把“未提及”表述为“允许”或“不需要”。
> 答案先给结论，再列必要条件；每项事实紧随支持它的来源。只输出答案，不输出内部核对过程。

候选标识 `completeness-v1`，基线标识 `baseline`。这只是待评估的假设：补充核对要求可能减少重要条件遗漏，也可能使答案更长；本轮不能证明质量提升。没有根据这 3 条答案改写预期标签或加入针对某一题的答案。

实际系统提示词保存为每侧的 `answer-system-prompt.txt`。`capture-report.json` 的 `manifest.answer_prompt_sha256` 绑定实际文本；比较时逐一核对保存的回答请求，声明哈希与实际请求不符会拒绝构建配对输入。原始调用消息先复制再修改，不改调用方数据。Live 配置还须与 CLI 选择的提示词变体一致，否则在构造 provider 前拒绝。

## 独立离线执行

```powershell
.venv/Scripts/python.exe -m scripts.run_prompt_comparison --output-dir artifacts/prompt-comparison
```

输出目录必须不存在。该入口没有 live 开关，固定使用 Scripted provider 和 Mock Judge。执行期间临时禁用外部 tracing，结束或异常时恢复原环境设置；作为独立 CLI 使用，不在并发业务进程中调用。

本轮产物：`artifacts/evaluation-prompt-ab-20261025/`。

- `offline-verified/baseline/`、`offline-verified/completeness-v1/`：各 19 条工作流、39 轮、449 项业务断言通过；两侧执行 ID 独立，源码、数据、语料一致，实际回答提示词指纹不同。
- `offline-verified/comparison/`：以保存的两份报告执行最终比较检查。19/19 条工作流均有资格，3 对回答、6 次盲序/换序 Mock 调用，结果为 3 个 Unknown；quality_winner=null，不输出小样本置信区间。
- `historical-baseline-audit.json`：原真实基线与当前源码的差异明细。首次核对已存在 1 个新增文件与 4 个源码变化；本轮实现又加入了采集提示词支持，最终差异以该文件为准。数据及语料仍相同。原基线报告和哈希不改写，不放宽原有源码一致性检查。
- `live-run-plan.json`、`baseline-config.pending.json`、`candidate-config.pending.json`：具体的后续真实采集计划。scope_authorized=false，配置的 approved_data_scope=null，因此不能直接用于 live 执行；无凭据。
- `acceptance-final.xml`、`acceptance.json`、`delivery-manifest.json`：最终回归、交付检查及文件指纹；较早的 focused.xml 是阶段记录。

首次提示词落盘字节核验未通过：Windows 自动换行为 CRLF，而实际请求和清单使用 LF。`offline-final/` 与 `prompt-file-audit.json` 保留该记录；修复固定 LF 写入后重新生成 `offline-verified/`，并增加保存文件字节哈希必须等于实际请求提示词哈希的回归。没有改写旧采集报告或其指纹。

`real_ab_input_ready` 与工程门禁分开。两份离线报告可以通过工程门禁，但真实 A/B 输入资格必须为 false。真实资格检查要求：两侧 live_llm、有完整且不重复的本地 provider 回执、不同执行 ID、不同报告、提示词绑定、明确实验变量、所有工作流具备资格、存在配对答案；同模型实验还核对实际 response_model 集合一致，并拒绝两侧重复的 provider response ID。它只检查本地证据一致性，不是外部执行的密码学认证，也不是质量优胜结论。

CI 的 fast gate 增加本轮测试，并运行上述固定离线命令，沿用 workflow-evaluation artifact 上传；未新增 secrets 或自动收费调用，未触发远端 Actions。

相关合并回归 216/216 通过，无失败、错误或跳过。覆盖 E2–E5、采集预算和状态、辅助审核、配对比较、提示词选择与绑定、可选 tracing/receipt、部署/CI 契约、ACL、注入、恢复及回答服务。提示词换行修复后另外复验 3/3 条针对性回归；不将重复用例累加为 219 个独立用例。8 个本轮 Python 文件的 Ruff check/format check 与本地 CI 配置校验通过；没有重跑全仓历史测试或 PostgreSQL 集成。

## 下一次真实采集的待确认范围

需要在当前同一份源码条件下重采基线和候选两侧，避免把旧基线的源码变化误归因为提示词效果。每侧 19 条开发工作流、39 轮，审批只在本地模拟；同一 `deepseek-v4-flash` 配置及 DeepSeek 接口，仅回答提示词不同。

拟议两侧合计最多 60 次模型调用、30 万 Token、3 USD；每侧分别限制 30 次、15 万 Token、1.5 USD，不跨侧挪用或自动追加额度；不追加语义 Judge 或外部遥测。预算预检发现基线的离线预留量已达 100195 Token，原拟议每侧 10 万不足，因此在请求授权前修正为每侧 15 万，检查保存在 budget-preflight.json。先前完成的一批授权不覆盖这两次新采集，所以本轮只生成待确认配置。

已于 2026-10-25 核对 [DeepSeek 官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)：旧模型名仍作为当前 Flash 的别名接受，最高输入/输出单价中取 1.2 USD/百万 Token 作为保守上界；在 30 万 Token 的预留上限下对应 0.36 USD。实际调用前仍须刷新价格和源码指纹。模型别名并不证明底层版本永久不变，必须核对两侧回执中的 response_model。

真实回答产生后可由助手继续给出审核建议；不代填人工确认，不用 Mock 结果选出优胜者，不自动冻结候选或部署到应用。3 对回答只适合小规模开发验证，不构成独立测试或稳定质量增益证明。
