# E2–E5 验收记录（2026-10-23）

本文件保留首次交付时的历史结果。其后 PostgreSQL 阻塞已修复并移除 XFAIL，
当前结果见 [修复与挑战集验收](postgres_unblock_acceptance.md)。

首次交付状态：E2–E5 的本地执行器、Judge、配对统计、失败审核与 CI 入口已实施；当时全 PostgreSQL 工作流投影存在真实复现的 Phase 38 阻塞。真实模型、人工校准与外部 Langfuse 接收没有执行。

## 实际结果

| 验证 | 结果 | 产物 |
|---|---|---|
| 最终合并回归 | **93 passed，1 xfailed**，无跳过 | `combined.xml` / `combined.log` |
| 评测升级、原有 Golden/Retrieval Runner、CI 契约 | **36 passed**（与合并回归有重叠，不累加） | `contracts.xml` / `contracts.log` |
| 单独 PostgreSQL 数据集 | **1 passed，1 xfailed** | `postgres.xml` / `postgres-verified.log` |
| 新多轮工作流数据集 | **7/7**，全部确定性断言通过 | `workflow-report.json` / `.md` |
| 同数据集回归比较 | 7 unchanged，0 regressions | `regression-summary.json` |
| 原黄金集 | **30/30**，原门禁通过 | `golden/` |
| 原检索集 | **20 cases，199 chunks**，原门禁通过 | `retrieval/` |
| Semantic offline smoke | 3 个 Mock Judge 调用，判定全部 UNKNOWN | `judge/judge-report.json` |
| Pairwise offline smoke | 1 个配对、2 次 Mock 调用，胜者 UNKNOWN，CI=null | `pairwise/judge-report.json` |
| Ruff check / format | 本轮 9 个 Python 文件通过 | 下述文件列表 |
| CI 配置及 action pin 校验 | passed | `ci-contract.json` |

证据目录：`artifacts/evaluation-e2-e5-20261023/`。首次失败保留在 `pytest-initial.log`、`postgres.log`。最初采购断言错误已按实际业务字段契约纠正：`estimated_total_amount=6000`；只有显式总金额和计算总金额同时存在时，业务才保存 `calculated_total_amount`，本用例预期它缺失。没有修改生产业务算法来迎合新测试。

## 命令

使用 Python 3.12.10 的 `.venv/Scripts/python.exe`。合并回归在独立 PostgreSQL 17 容器上设置进程级 `AGENT_POSTGRES_TEST_DSN`，数据库名称以 `_test` 结尾，没有使用现有项目数据库。

```powershell
.venv/Scripts/python.exe -m pytest tests/evaluation/test_evaluation_upgrade.py tests/integration/test_workflow_evaluation_postgres.py tests/unit/test_optional_tracing.py tests/unit/test_agent_submission.py tests/unit/test_agent_conversation.py tests/unit/test_agent_resilience.py tests/unit/test_sqlite_persistence.py tests/unit/test_policy_access_control.py tests/unit/test_prompt_injection.py tests/deployment/test_ci_contract.py -q -rx --basetemp=.pytest_tmp_e2_combined --junitxml=artifacts/evaluation-e2-e5-20261023/combined.xml
.venv/Scripts/python.exe -m pytest tests/evaluation/test_evaluation_upgrade.py tests/evaluation/test_evaluation_runner.py tests/evaluation/test_retrieval_runner.py tests/deployment/test_ci_contract.py -q --basetemp=.pytest_tmp_e2_last --junitxml=artifacts/evaluation-e2-e5-20261023/contracts.xml
.venv/Scripts/python.exe -m scripts.run_workflow_evaluation --baseline artifacts/evaluation-e2-e5-20261023/workflow-report.json --output-dir artifacts/evaluation-e2-e5-20261023
.venv/Scripts/python.exe -m scripts.run_golden_evaluation --mode offline --output-dir artifacts/evaluation-e2-e5-20261023/golden
.venv/Scripts/python.exe -m scripts.run_retrieval_evaluation --mode offline --output-dir artifacts/evaluation-e2-e5-20261023/retrieval
.venv/Scripts/python.exe -m scripts.verify_ci_configuration
```

Semantic / Pairwise 完整运行命令见 [运行说明](e2_e5_delivery.md)。人工标签数仍为 0；示例数据与所有 Mock 数值不能用于描述真实模型能力。首次未配置 DSN 的运行曾跳过集成测试，后续已使用独立容器实际运行并保留独立结果。

Ruff 文件范围：`app/evaluation/{workflow_evaluation,judge,pairwise,failure_pipeline,experiment_tracking}.py`，`scripts/{run_workflow_evaluation,run_semantic_evaluation}.py`，`tests/evaluation/test_evaluation_upgrade.py`，`tests/integration/test_workflow_evaluation_postgres.py`。运行 `python -m ruff check <这些文件>`、`python -m ruff format --check <这些文件>` 均通过。

## 必须保留的限制

- 全 PostgreSQL 投影：确认草稿触发 `DraftRevisionConflictError`，原因见运行说明。测试仅在准确命中这一已知异常时 XFAIL；不计通过。PostgreSQL checkpoint + SQLite 投影的同一组 7 用例通过。
- 新数据集的权限违规率和恢复成功率没有分母，报告保留 null；权限/恢复能力通过既有安全与故障恢复测试验证，不能混作新数据集成绩。
- 工具参数评测目前覆盖提交参数约束；完整工具参数、业务规则和独立挑战集覆盖仍需扩充。
- 全仓历史 target drift、Ruff/格式问题见 E0/E1 验收记录。本轮没有宣称完整 pytest 或远端 GitHub Actions 全绿。
- 没有付费模型调用、外部 Langfuse 发布或人工作为事实来源的认证。生成的校准/失败卡全部保持待审核。

下一步应先在 Phase 38 专项修复投影确认兼容问题并移除已知阻塞，再扩大业务场景与人工校准样本；真实模型评测须明确模型、预算及数据范围后启动。
