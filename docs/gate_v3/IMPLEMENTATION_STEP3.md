# 门控 v3 第 3 步：冻结候选回放

本步骤把已完成的真实 BGE 候选窗口归档作为输入，在不重新调用模型的前提下，回放
v3 四值关系门控，并与未加 v3 门控的冻结选择器逐条比较。

## 范围

- 使用 `retrieval-candidate-window-bge-*.zip` 中已经捕获的 20/40 候选排名。
- 标签、权限、语料快照、候选池、RRF 常数 60、最终 K=5 和原有两条冻结规则保持不变。
- 选择策略保持“检查基线前 2 条锚点、保留前 4 条、最多补充 1 条”。
- v3 决策只影响实验回放；`scripts/replay_coverage_windows.py`、生产选择器和 FastAPI
  Provider 不修改、不切换。
- `UNRESOLVED` 明确记为 `fallback_applied=true`，沿用冻结补充行为，不能被误读为门控识别成功。

## 运行

在仓库根目录执行（PowerShell）：

```powershell
$out = "artifacts/gate-v3-replay-v1"
python -X utf8 -m scripts.replay_gate_v3 `
  --evidence-zip .\upload\retrieval-candidate-window-bge-20260908-202536.zip `
  --output-dir $out
```

若输出目录已存在，请换一个新的目录名；脚本不会覆盖已有证据。

只运行单元测试：

```powershell
python -X utf8 -m unittest tests.evaluation.test_replay_gate_v3 -v
```

## 输出与解释

- `replay-report.json`：每条查询、每个窗口的基线/冻结/v3 文档 ID、门控事件、被替换证据、
  Recall@5、MRR@5、nDCG@5、回归和控制查询分组。
- `replay-report.md`：汇总表和计时边界。
- 若冻结锁、归档指纹、标签、规则、语料或模型锁不一致，脚本 fail-closed 写出
  `failure.json`，不生成部分数值报告。

报告中的耗时只覆盖两个选择器的本地选择过程，排除归档读取、模型加载、embedding、检索、
RRF 和 reranker；不能据此声称新的端到端延迟。

## 证据边界

该归档的源实验确实使用真实 BGE 推理，但本步骤没有新模型推理，且当前 COV 标签是用户确认的
AI 辅助开发集、不是独立留出集。因此报告用于验证门控选择与回归，不用于生成新的生产质量或
简历数字。
