# 采集结果对比：工程交付与进度

2026-10-24（Asia/Shanghai）。本轮完成同条件版本对比的本地工程入口，并用已保存的真实采集基线执行自检。没有新增真实模型、收费 Judge 或外部遥测调用。

## 已完成

| 项目 | 本轮结果 |
|---|---|
| 基线绑定 | 锁定原采集报告 SHA-256、源码/数据/语料清单；不改原报告、不冻结数据标签 |
| 实验条件校验 | 校验输入文件哈希、问题/轮次/fixture/split、来源清单、身份与工具定义、授权证据及文档版本 |
| 独立快照校验 | 在执行断言之外检查保存的状态、提交数及确认信息；报告标记 passed=true 不能覆盖快照中的回归 |
| 受控源码差异 | 只有显式声明的 IntentClassifier / PolicyAnswerService 变更可作为当前实验变量；身份/安全/工具/持久化变化拒绝按同条件实验处理 |
| 后处理变更审计 | pairwise、failure_pipeline、experiment_tracking 三个后处理模块的变化单独列出，不混入实际 Agent 条件；源码清单完整保留 |
| 配对输入 | 保留 workflow_case_id，支持同一工作流下不同轮次的独立 pair ID；从真实执行恢复问题、答案和完整授权证据 |
| 审核衔接 | 读取绑定原报告与原答案的助手辅助审核，独立汇总建议，不计入人工校准或安全资格判定 |
| 回归汇总 | 列出资格回归、改进、缺失回答及不能参评的项；不静默丢弃失败 |
| 自动化 | 新增统一离线 A/B 准备/盲序换序自检入口，接入现有 CI 和原 artifact 上传目录 |

本次输入两侧均为同一份真实采集报告，明确标记 same_report_self_check。19 条工作流条件校验通过，3 对实际回答进入配对链路；各执行随机顺序和换序共 6 次本地 Mock 判断，结果均 UNKNOWN。小于 20 个不同配对 case，不输出置信区间。该自检没有比较两个实际候选版本，没有选择质量优胜者。

## 本地运行

```powershell
.venv/Scripts/python.exe -m scripts.run_capture_comparison --baseline <baseline/capture-report.json> --candidate <candidate/capture-report.json> --output-dir <new-output-directory>
```

可选参数：

- `--baseline-review` / `--candidate-review`：对应 capture 报告的 assisted-review.jsonl。
- `--allow-source-change app/rag/policy_answer_service.py`：显式声明回答组件的实验差异；分类器同理。默认不接受实际执行源码变化。

统一入口固定使用本地 Mock，不能因为加上这些参数就发送真实模型请求。构建输入时不把模型名或版本名写入 Judge 消费的 query/evidence/answer 字段；版本信息保留在外部汇总与源报告中。

输入格式是 run_agent_workflow_capture 生成的完整采集报告。数据集文件必须仍能按声明的哈希读回；若原数据已变，需先恢复对应快照，不能直接跨数据比较。报告文件哈希用于绑定来源，不是外部执行真实性的密码学认证。

## 产物

目录：`artifacts/evaluation-comparison-20261024/delivery-final/`。

- `baseline-lock.json`：基线位置、哈希与原清单，dataset_frozen=false。
- `pairwise-inputs.jsonl`：3 对带实际执行资格与完整证据的盲评输入。
- `comparison-summary.json`：同条件检查、资格回归、排除项和辅助审核统计。
- `offline-pairwise/judge-report.json`：随机/换序 Mock 结果。
- `delivery-summary.json/.md`：工程门禁；quality_winner 始终不由本地 Mock 推断。

源码：`scripts/prepare_capture_comparison.py`、`scripts/run_capture_comparison.py`；扩展 pairwise.compare_pair 的 workflow_case_id 参数，原调用方式兼容。新增测试使用明确的合成结构 fixture，不将模拟测试结果写为真实模型证据。

## 当前阶段进度

| 阶段 | 状态 |
|---|---|
| E2–E5 评测工程 | 已交付，继续由回归覆盖 |
| E6 可复现基线 | 已有指纹快照；本轮增加对比基线绑定 |
| E7 真实采集 | 已完成 19 条/39 轮，21 次真实调用，449 项断言通过；原记录保留 |
| E8 助手辅助人工审核 | 已完成 3 条真实回答的助手建议，均建议 PASS；人工确认字段独立保留 |
| E9 对比工程 | 条件校验、回归汇总、输入构建和本地自检已完成；后续[同模型提示词真实双侧采集](prompt_ab_live_delivery.md)已交付，42 次调用、两侧各 19/19 条通过；独特语义场景仅 1 个，不选质量优胜者 |
| E10 外部观测验收 | [本地 Langfuse 4.69.0 接收验收](local_langfuse_acceptance.md)已完成，8 个新合成 observation 匹配且资源清理通过；云端/生产环境尚未验证 |

本轮合并回归、最后的对比契约检查、CI 配置与当前文件清单见 `artifacts/evaluation-comparison-20261024/acceptance.json`。先前测试/真实采集/人工待审标签保持历史状态，不批量改写。
