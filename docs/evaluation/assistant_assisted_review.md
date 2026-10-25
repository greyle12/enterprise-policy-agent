# 助手辅助人工审核

2026-10-24（Asia/Shanghai）。按用户要求，后续采用助手先审阅、给出证据与建议，人工按需确认的模式。助手辅助报告本身是可交付的工程诊断结果，不再把逐条人工填标签作为日常工程推进的通用前置条件。

## 流程与来源

| 阶段 | 保存的信息 | 可以用于 |
|---|---|---|
| 待辅助审核 | 原问题、实际回答、授权正文/元数据、执行 ID、来源指纹 | 助手审阅准备 |
| 助手辅助完成 | 建议 PASS/FAIL/UNKNOWN、证据 ID、理由、关注点及建议动作 | 错误定位、后续测试准备和工程改进 |
| 人工按需确认 | 明确的审核人、判定、理由及是否已看过 Judge 结果 | 符合条件时计入人工校准；保留与助手意见的来源区别 |

助手结果写为 review_mode=assistant_assisted_human_review、review_status=assistant_assistance_complete_pending_human_confirmation。人工字段 human/reviewer/reviewer_decision 保留 null。助手建议不会自动成为人工标签、Ground Truth、冻结 holdout 或回归批准决定。

本轮对后续待审模板、失败卡、保存报告校准入口统一增加 review_mode 标识；保留原 review_status，兼容既有消费者。历史数据和已产生的报告不批量改名或回填。

## 已完成的实际回答审核

来源：`artifacts/evaluation-agent-live-20261024/capture/capture-report.json`，共 19 条工作流/39 轮；其中可供忠实性审核的实际制度回答有 3 条。

| 用例 | 助手建议 | 理由及关注点 |
|---|---|---|
| WF-013 | PASS | 提交申请得到 S1 支持，差旅细则明确保留不确定性；没有把通用规则扩展为专门标准 |
| WF-017 | PASS | 限定在当前提供的证据范围，未宣称整个制度库都没有差旅制度 |
| WF-018 | PASS | 正文支持申请要求，article 元数据支持“制定目的”；只看正文会漏掉这一依据 |

这些建议仅覆盖当前证据下的 faithfulness。合成证据不包含完整差旅制度，不能据此给出差旅业务完整性认证。其余 36 轮的原业务/安全/状态断言已经由程序验证，不因此计为 36 条语义审核。

助手辅助审核 3 条，人工确认 0 条，新增外部模型/语义 Judge 调用均为 0。三个 PASS 是助手建议，不是人工准确率或生产效果。

## 可复现的审核包

```powershell
.venv/Scripts/python.exe -m scripts.prepare_assisted_review --capture artifacts/evaluation-agent-live-20261024/capture/capture-report.json --recommendations artifacts/evaluation-assisted-review-20261024/assistant-recommendations.json --output-dir artifacts/assisted-review-new
```

命令只读取本地记录，不请求模型，也不自动推断语义标签。它校验由助手提供的建议包绑定原始 capture SHA-256、覆盖全部可审核回答、case 唯一、引用 ID 有效；从原始执行记录重建问题/回复和完整证据，避免手工复制时改变内容。建议包含未定义字段、假冒人工字段、错误指纹、缺失条目或覆盖已有目录时拒绝写入。

本轮产物位于 `artifacts/evaluation-assisted-review-20261024/`：

- `assistant-recommendations.json`：本次助手建议和原采集报告指纹。
- `review/assisted-review.md`：供阅读的实际答案、建议、依据及关注点。
- `review/assisted-review.jsonl`：可追溯的结构化审核包。
- `review/summary.json`：助手建议与人工确认分别统计。
- `tests.xml`、`acceptance.json`、`evidence-manifest.json`：验证与当前证据。

人工若随后明确确认，应另存有来源的决定，不覆盖助手原始意见；只有提供审核人、实际判定及理由的记录才进入原有人工校准。无需为获得工程交付而虚构这些字段。

## 当前验证范围

专项测试覆盖源指纹、推荐覆盖/重复、引用 ID、字段严格校验、禁止伪造人工字段、人工校准分母隔离及禁止覆盖。并与原保存结果校准、Judge/工作流升级、失败卡、基线准备、实际模型 smoke 适配及全量工作流管线合并验证，结果见本目录 acceptance.json。

本轮新增 10 项专项测试，合并验证 **71 项全部通过，无失败或跳过**；七份新增/修改 Python 文件 Ruff check / format check 通过。

本轮修改的是审核模式和本地产物生成，没有再次执行真实模型采集或追加付费 Judge。原 21 次真实采集的响应、用量、标签和历史证据保留。
