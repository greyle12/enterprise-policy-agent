# E10 观测接收验收工程交付

本阶段完成本地验收入口、服务端回读契约、脱敏与失败路径回归，并接入现有 CI。初次交付时未配置真实服务，以下保留该阶段证据；后续已完成 [Langfuse 4.69.0 本地真实接收验收](local_langfuse_acceptance.md)，8 个新合成 observation 回读匹配且资源已清理，没有新增模型或语义 Judge 调用。

## 验收行为

`scripts.verify_optional_tracing` 默认使用真实 Langfuse SDK 和私有 OTel provider，执行一条固定的合成材料查询，经过真实 LangGraph 与离线分类器。默认导出器仅在内存中记录，不连接配置中的服务地址，也不读取应用 LLM 凭据或历史真实采集内容。该查询不提交审批。

探针保存生成的 trace/span ID、父子关系、固定名称、时间及状态和版本摘要。结果明确区分：

| 状态 | 含义 |
|---|---|
| offline_verified | 本地 SDK 和应用链路通过，server_receipt_verified=false |
| configuration_required | 显式导出所需配置缺失或地址不合要求，尚未初始化 SDK |
| optional_sdk_required | 当前环境没有安装可选依赖 |
| probe_failed | 初始化、运行、导出或本地检查未完成，异常正文不写入报告 |
| receipt_unverified | 未能在预算内完整回读，或身份/父子关系/元数据/隐私检查不通过 |
| receipt_verified | 本次合成探针的所有 observation 已从指定服务回读并匹配 |

只有 `--export` 才使用明确配置的地址和项目凭据发送本次合成元数据。地址允许 HTTPS，或指向 localhost/127.0.0.1/::1 的本地 HTTP；禁止 URL 内嵌凭据、查询参数和 fragment。此开关不是本次对外发送的执行记录。

读回使用已安装 SDK 4.18.0 的 `api.observations.get_many`（Observations v2），按本次 trace ID 和采集时间窗口过滤，明确请求 core/basic/io/metadata 字段。旧 Trace get 接口已有弃用提示，本入口不依赖它。服务必须支持 Observations v2；不支持或未索引完成时保留未验证状态。

读回最多 10 次，单次超时 2 秒，间隔 3 秒，SDK 额外重试为 0。不把 flush 成功视为接收证明。完整核对 observation ID 集合、trace ID、名称、已记录的父子关系、结束状态和元数据；多出记录、重复记录、分页未核验、缺记录或错配均不通过。SDK 为新 trace 生成的未记录 remote parent 可保持原值或被服务规范化为 null，实际已记录子节点的 parent 必须精确匹配。

读回的 observation 必须为非公开，input、output、user_id、session_id、status_message 为空，元数据不得新增字段。报告不保存原始服务响应、错误正文或凭据。`server_receipt_verified` 仅覆盖这一次探针的 observation，不能作为历史 19 条真实 Agent 采集、实验发布、生产环境或模型质量的接收证明。

## 操作

从仓库根目录运行，输出目录必须是新目录，以免覆盖既有证据：

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev,observability]'
.venv/Scripts/python.exe -m scripts.verify_optional_tracing --output-dir artifacts/observability-local-check
```

在明确配置目标服务、项目凭据并授权发送后，可在独立 CLI 进程执行：

```powershell
# LANGFUSE_BASE_URL、LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY 由进程环境提供。
# 不把凭据写入命令记录、报告或聊天。
.venv/Scripts/python.exe -m scripts.verify_optional_tracing --export --output-dir artifacts/observability-server-check
```

输出 `receipt-report.json` 与绑定其 SHA-256 的 `manifest.json`。本地成功或真实读回匹配时退出 0，其余状态退出 1。该探针会临时切换进程内 tracing backend，并在 finally 中恢复；作为独立验收命令运行，不在并发业务服务内调用。

## 本地证据与后续

本轮产物在 `artifacts/evaluation-observability-20261024/`：`offline-probe-final/` 是实际运行的内存探针；`acceptance-final.xml` 与 `acceptance.json` 记录本轮回归及文件指纹。最终合并回归 129/129 通过，无失败、无跳过，覆盖 tracing/receipt、E2–E5 评测、辅助审核、对比契约、ACL、注入、恢复与 CI 配置；本轮 5 个 Python 文件 Ruff check/format check 通过。未重跑 PostgreSQL 集成或全仓历史测试。HTTP 读回测试通过 SDK 的 MockTransport 执行，OTLP 测试拦截真实序列化请求的本地 transport；二者属于接口契约测试，不是真实 Langfuse 服务回执。

现有 quality job 安装 observability extra，快速回归覆盖 tracing 与 receipt，新增默认离线探针并沿用 workflow-evaluation 产物上传。没有新增 CI secrets，没有触发远端 Actions。全仓历史质量门禁与生产网络仍不在本轮通过结论内。

E2–E5 工程已交付；E7 的真实采集和 E8 的 3 条助手建议保留原证据；E9 对比工程已有，实际双候选比较待候选结果；E10 本地真实服务接收已在后续验收完成，云端/生产环境仍未验证。助手建议继续与人工确认分层，人工决定不由观测探针填写。

后续本地整链路扩展见[完整工作流的本地观测与失败定位](workflow_tracing_delivery.md)：将 19 条工作流、39 轮离线执行与 SDK trace 和本地待审卡关联。它同样不构成外部服务接收证明。
