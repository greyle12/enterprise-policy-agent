# Agent 评测与观测代码发布

本目录收录本轮 E0–E10 评测工程、操作说明与有边界的开发验收记录。代码在 GitHub 已有 master 基线上整理；未包含本地其他生成评测实验、前端开发、模型文件、环境、凭据或原始运行日志。

## 新 checkout 的离线复现

安装项目依赖和可选观测依赖：

```powershell
python -m pip install -e '.[dev,observability]'
python -m scripts.run_evaluation_delivery --output-dir artifacts/local-evaluation
python -m scripts.run_prompt_comparison --output-dir artifacts/local-prompt-comparison
python -m scripts.verify_optional_tracing --output-dir artifacts/local-tracing-probe
python -m scripts.run_traced_workflow_evaluation --output-dir artifacts/local-traced-workflow
```

这些命令默认使用离线分类器、Scripted/Mock provider 或内存导出器，不需要 LLM key，不进行真实审批或外部遥测发送。每次输出到新目录，不覆盖旧记录。离线 Mock 的 UNKNOWN 不表示语义质量通过。

原始历史产物继续留在本地，不作为此次公开代码的组成部分。公开汇总见 [evidence-summary.json](evidence-summary.json)，保留实际用量、断言结果和原报告哈希；它不代替原始回执，也不是外部执行真实性的密码学证明。历史文档中的 artifacts 路径描述原工作区记录，新 checkout 需要生成自己的输出和源码指纹。

`prepare_evaluation_pilot` 是历史验收快照助手，要求提供原验收 manifest/XML；新 checkout 的启动不依赖这些私有本地快照，不应删除它的指纹检查来绕过缺失记录。

## 能力与验收边界

- [E2–E5 最终交付](e2_e5_final_delivery.md)：多轮执行、轨迹/参数/持久化断言、预算化语义 Judge、盲序换序配对与经审核的回归入口。
- [真实提示词开发试验](prompt_ab_live_delivery.md)：42 次 DeepSeek 调用，两侧各 19 条工作流和 449 项断言通过；三对回答只有一个独特语义场景，不输出总体质量优胜。
- [助手辅助审核](assistant_assisted_review.md)：助手建议与人工标签分层，未确认保留 null，不自动冻结标签。
- [完整工作流本地追踪](workflow_tracing_delivery.md)：轮次与 span、失败审核卡关联，固定元数据导出。
- [本地 Langfuse 验收](local_langfuse_acceptance.md)：独立 Docker fixture、回环访问、合成探针回读与临时资源清理；不等于生产可用性认证。

真实模型模式必须另外提供明确的模型、接口、允许发送的数据范围及预算配置；凭据只通过进程环境传入。可选 live CI 仅由 workflow_dispatch 手动触发，不在普通 push/PR 流程中发送收费请求。已完成批次的授权不自动覆盖新调用。

## 发布检查

发布前在干净工作目录运行本阶段回归和独立 PostgreSQL 集成检查，结果见 [publication-validation.json](publication-validation.json)。全量 pytest 在已有语义 Facet 测试中遇到缺失的 `docs/gate_v3/semantic-facet-challenge-v1-draft/cases.json`，该测试与文件缺失状态均已存在于发布基线，完整门禁尚未全绿。原有三份脚本还存在格式检查问题，详见同一记录；本阶段不修改这些旧实验文件或跳过对应测试。
