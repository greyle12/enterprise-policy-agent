# E6 基线与 E7 真实评测准备

后续真实运行结果见 [E7 首批 Judge 试运行](e7_live_judge_smoke.md)。以下保留准备阶段状态。

本次完成基线核验与运行准备。E7 实际模型运行、人工校准及独立测试集冻结均待完成。

## 交付与复现

```powershell
.venv/Scripts/python.exe -m scripts.prepare_evaluation_pilot --output-dir artifacts/evaluation-pilot-new
```

每次指定全新的输出目录，拒绝覆盖历史目录。脚本没有网络调用，先核对 E2–E5 交付清单中的 21 个文件；文件缺失或指纹变化则终止。正常输出：

- `baseline.json`：HEAD、分支、Python/关键依赖版本、已修改 tracked 路径及源目录内 untracked 路径；原验收 JUnit 的哈希与计数。HEAD 需与工作区指纹联合使用。
- `source-hashes.json`：当前 app/scripts/tests Python、制度 Markdown、pyproject 和原交付文件的逐文件 SHA-256。它用于检查差异；复现时还需要同一份工作区文件和所列依赖，单凭哈希不能重建代码。
- `workflow-pilot-plan.json`：现有 19 条用例逐项登记原数据路径、数据哈希、场景及轮次，全部 `pending_live_agent_capture`。
- `live-config.template.json`：模型、接口、数据范围、预算、调用与输出上限均待明确配置；凭据仅通过进程环境传入。
- `judge-blind-review.jsonl`：3 条现有合成 Judge 样例的答案、证据、reference 与 rubric。隐藏 Judge 判定和模型名，人工字段均为空；答案来源标记为人工编写的合成 fixture，不是实际 Agent 输出。

本轮正式材料目录为 `artifacts/evaluation-e6-e7-ready-final/`；初次准备快照 `artifacts/evaluation-e6-e7-preparation/` 保留。

## 基线与已知问题

原验收记录为 223 项通过，无失败、错误、跳过；本轮读取 JUnit 并核对代码指纹，没有再次运行这些测试。原离线工作流 19/19、黄金集 30/30、检索门禁通过记录继续见 [最终交付](e2_e5_final_delivery.md)。

工作区存在大量历史文件和未提交修改。基线清单只记录路径和源文件哈希，不清理或提交它们。未追踪文件清单限定 app/scripts/tests/docs/.github；不盘点凭据与运行时目录。

历史已知问题见 [E0/E1 验收](e0_e1_acceptance.md)：Step268 的 default target configuration drift、全仓 Ruff 四项报错及 315 个格式差异。此处为历史状态，未在本轮重跑，不能据此断言当前全仓检查通过或失败。远端 Actions 状态未验证。

## 首批实际运行顺序

1. **Judge 接口小批量验证**：使用已备好的 3 条 synthetic smoke，显式配置 Judge 模型、API 地址、最高输入/输出单价、Token/费用上限及允许发送的数据范围。复用 `scripts.run_semantic_evaluation`。这一步验证结构化返回、usage、错误和预算处理。
2. **采集实际 Agent 输出**：按 19 条 checklist，在隔离存储和模拟审批下验证模型接入。应保存问题、实际回复、授权证据与版本、模型/Prompt、执行 ID、持久化状态和工具记录。优先覆盖正常回答、澄清、拒答、确认、重复提交及超时恢复。现有 workflow runner 固定离线组件，需要先补齐实际模型采集与用量控制，不能加一个 live 标签就算完成。
3. **人工盲审**：实际 Agent 输出准备完毕后再生成对应盲审材料，Reviewer 先独立标注，再对照 Judge。未审保留 null；UNKNOWN 不进入二分类准确率分母。当前 3 条模板仅供 Judge fixture 审核，不能充当 Agent 校准数据。
4. **校准与扩大样本**：记录 agreement、false pass、false fail、有效分母和分歧原因，再决定是否扩大样本或修改 rubric。当前 19 条属于开发/安全回归集，不能转称独立 holdout。

人工完成 smoke 模板后，可将 case_id、repetition、human、reviewer、review_status 转为既有 CLI 的 calibration labels 格式。保留原始审核文件；不要把 pending 模板直接当成已审核标签。Judge 当前 CLI 会重新调用模型再做标签对照，若要校准某份历史报告，应先补齐独立离线 join，避免误将新判定作为旧判定。

## 本地验证

```powershell
.venv/Scripts/python.exe -m pytest tests/evaluation/test_evaluation_pilot_preparation.py -q
.venv/Scripts/python.exe -m scripts.run_semantic_evaluation --mode offline --input tests/evaluation/semantic_smoke.jsonl --output-dir artifacts/evaluation-e6-e7-ready-final/offline-smoke --token-budget 20000 --cost-budget 1 --max-output-tokens 500 --max-usd-per-million 1 --timeout 10 --max-calls 3 --repetitions 1
```

以上数值只用于离线 Mock 检查，不是已批准的真实调用预算。应得到三条 status=ok、verdict=UNKNOWN，人工样本数 0。实际模型运行配置尚未确定，`live_ready=false`。

下一阶段需要确定：被测模型、Judge 模型、接口、费用/Token/调用上限、允许发送的数据范围。模型价格不在本地模板中预设，配置实际运行前按对应供应商资料核实。

