# PostgreSQL 阻塞修复与后续推进验收

## 修复结果

已修复确认草稿时的 `DraftRevisionConflictError`，并移除全 PostgreSQL 工作流测试中的 XFAIL。未启用生产 PostgreSQL 运行时，也未修改 Redis 协调激活限制。

根因：工作流确认/提交草稿时会替换生命周期提示，PostgreSQL 将整个 warnings 数组放入不可变业务内容比较，因此把合法状态变化拒绝为业务内容篡改。

改动限制在 `app/persistence/postgres_runtime.py`：只过滤四条代码内固定的生命周期提示后比较业务内容。没有整体忽略 warnings；金额、标题、材料、审批路线和其他业务风险提示仍不可在同一 revision 内改变。行锁、事务回滚、session state_version CAS、draft status CAS、合法状态迁移表和幂等键校验均未修改。

新增回归先复现 **1 failed / 1 passed**；修复后 PostgreSQL 状态/提交仓储单元测试 **24 passed**。验证包括合法确认/提交提示变化可通过、其他业务提示变化被拒绝，以及已有业务内容篡改、CAS 和回滚测试。

## 阻塞修复后推进

新增版本化开发挑战集 `tests/evaluation/workflow_challenge_cases.jsonl`（`workflow-challenge-v1`）：

1. 采购缺字段后补齐，确认、提交、重放；读回数量与金额。
2. 请假草稿确认、提交、重复提交，核对日期与天数。
3. 差旅草稿确认、提交、重复提交，核对报销总额。
4. 采购单价×数量与用户填写总额矛盾，阻止提交并保持零提交记录。

挑战集 SQLite 运行 **4/4**；原工作流集仍 **7/7**。真实 PostgreSQL checkpoint + SQLite 投影、全 PostgreSQL checkpoint/投影/提交两种模式均执行这 11 条用例，无已知例外。CI 已加入挑战集命令和报告上传目录；Runner 记录实际数据集版本。

这些数据是开发回归集，不是独立 holdout；没有更改原冻结标签、人工审核状态或独立性声明。权限与恢复指标的既有 null 分母不因此变为已测成绩。

## 最终验收

- 合并回归：**82 passed，无 skipped，无 XFAIL**，`artifacts/evaluation-unblock-20261023/acceptance.xml` / `acceptance.log`。
- 单独真实 PostgreSQL 原集成与工作流测试：**9 passed**（挑战集加入前的中间结果），`postgres.xml` / `postgres.log`；不与合并回归累加。
- 原工作流报告：`golden-workflow/workflow-report.json`；新挑战集：`challenge/workflow-report.json` / `.md`，均位于上述证据目录。
- 本轮 6 个 Python 文件 Ruff check / format check 通过；现有 CI 配置校验通过，见 `ci-contract.json`。
- 使用一次性 PostgreSQL 17 容器和独立 `_test` 数据库，未连接现有项目数据库。

完整合并回归命令（在进程内配置独立 `AGENT_POSTGRES_TEST_DSN` 后）：

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_postgres_agent_state_store.py tests/unit/test_postgres_submission_repository.py tests/integration/test_workflow_evaluation_postgres.py tests/integration/test_postgres_repositories.py tests/integration/test_postgres_checkpointer.py tests/evaluation/test_evaluation_upgrade.py tests/unit/test_agent_submission.py tests/unit/test_agent_resilience.py tests/unit/test_optional_tracing.py tests/deployment/test_ci_contract.py -q --basetemp=.pytest_tmp_eval_unblock_full --junitxml=artifacts/evaluation-unblock-20261023/acceptance.xml
```

历史失败证据仍在 `artifacts/evaluation-e2-e5-20261023/`；本次测试集范围与前次不同，不对测试数量作提升比较。全仓历史 target drift/Ruff 问题不在本次修复范围，不宣称全仓或远端 Actions 全绿。真实模型、人工校准及外部 Langfuse 接收尚未执行。
