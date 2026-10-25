# 本地 Langfuse 服务接收验收

本轮已完成隔离的本地 Langfuse 4.69.0 真实服务验收：8 个 observation 从 Observations v2 回读匹配，临时容器、卷、网络及凭据已清理。每次尝试只发送一条新生成的合成材料查询的元数据，不发送历史真实 Agent 采集，不调用模型或语义 Judge，也不使用应用 LLM 凭据。

部署参考 [Langfuse 官方 Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose) 与[无界面初始化](https://langfuse.com/self-hosting/administration/headless-initialization)。原始上游 Compose、提交指纹和 release 记录保存在本轮证据目录。web/worker 版本为 4.69.0；运行时对全部六个镜像解析并固定 RepoDigest，保存实际镜像清单。

## 运行与资源边界

```powershell
.venv/Scripts/python.exe -m scripts.run_local_langfuse_acceptance --output-dir artifacts/langfuse-local-acceptance
```

需要 Docker Desktop、Compose 以及项目 observability extra。初次运行会下载依赖镜像。输出目录必须不存在。此命令会创建六个容器、五个数据卷、一条内部数据网络和一条 web 前端网络，生成独立随机 Compose 项目名；不连接或迁移应用的 PostgreSQL/Redis。

仅 web API 绑定动态的 127.0.0.1 端口；其余服务不发布主机端口，数据网络标记 internal。web 同时连接前端网络，保证当前 Docker 版本真正发布回环端口；数据服务仅接入内部网络。禁用产品遥测和内置 AI 功能，不传入任何模型 API key。数据库、Redis、对象存储、加密和项目凭据均独立生成，只在临时目录保存环境文件，不写入仓库或报告。

脚本等待真实健康接口就绪，随后调用既有 `verify_optional_tracing --export`，目的地址只能是本轮创建的回环地址。仅对子进程设置 NO_PROXY/no_proxy 的回环列表，避免 SDK 被当前进程或系统代理转发；不修改全局代理。继续使用 Observations v2 的有界读回、span 身份与父子关系核对以及隐私字段检查；不能把 exporter flush 或健康接口通过替代为接收证明。

私有 OTel provider 只保留固定 service.name。SDK 在内部仍用 scope 公钥检查项目归属；自定义 exporter 在该检查后复制 span 并清空 scope attributes，再交给 OTLP 序列化，避免把项目公钥保存为服务端元数据，不修改原 span。回读仅接受固定的 scope.name、scope.version、resourceAttributes.service.name 与本次业务安全元数据的精确集合和值；非空 user/session/status message 仍拒绝。

无论成功或失败，finally 都尝试清理本轮项目的容器、卷和网络。删除容器前检查专用归属标签；归属不符时停止清理。最终独立检查三类资源均为空，并删除临时凭据文件。依赖镜像保留为 Docker 本地缓存。日志写入前脱敏随机凭据和 Basic Auth；没有必要对外转发完整服务日志。

只有真实读回匹配且清理验证通过时，CLI 才返回 0。否则报告保留 failed/unverified 和 cleanup_verified=false 等状态，不伪造通过。

## 证据与限制

本轮目录：`artifacts/evaluation-langfuse-local-20261024/`。

- `upstream-provenance.json` / `upstream-compose.yml`：官方来源快照。
- 实际运行子目录中的 `compose.yml` / `image-inventory.json`：固定镜像摘要的部署条件；无真实凭据。
- `health.json`：服务健康及其返回的版本信息。
- `probe/receipt-report.json`：探针执行与服务接收验证结果；以该文件实际状态为准。
- `summary.json`：真实接收、模型调用数和清理状态。
- 顶层 `acceptance-final.xml` / `acceptance.json` / `delivery-manifest.json`：最终回归与文件指纹；较早的 XML 保留为阶段记录。

CI 只运行使用模拟 Docker 的安全契约测试，不自动启动本地栈，也不需要新增 secrets。本轮测试覆盖回环绑定、内部网络、命名卷、模型凭据隔离、日志脱敏、已有目录保护、启动失败及清理归属检查。

本机原生 Docker 下载大镜像时出现长时间无进展和仓库超时，worker 的 Docker Hub 查询还出现匿名限流。本轮改用宿主机分段续传与官方 GHCR worker 镜像；导入前按原 manifest 对全部压缩 blob 校验 SHA-256，相关 transfer JSON 保留来源摘要。没有修改 Docker/VPN 的全局代理配置。首次服务尝试也暴露出内部网络端口未发布的问题，已通过 web 单独接入前端网络修复；数据网络保留内部隔离。

## 最终结果（2026-10-24）

- `server-attempt-3/health.json` 返回 OK、4.69.0；`probe/receipt-report.json` 为 receipt_verified、matched、8 个 observation，第 2 次读回匹配；`summary.json` 同时确认 server_receipt_verified=true 与 cleanup_verified=true。
- `server-attempt-1/`、`server-attempt-2/` 的未验证结果保留，两次 cleanup_verified 均为 true。首轮安全字段诊断发现 SDK 默认写入 scope 公钥及进程资源属性，已修正导出；第二轮记录 SDK HTTP 502，回环代理绕过后第三轮通过。没有将失败结果改成通过。
- 最终合并回归 192/192 通过，无失败、错误或跳过，覆盖 E2–E5、采集/比较/辅助审核、tracing/receipt、ACL/注入/恢复、CI 和本地部署契约。本轮 8 个 Python 源码/测试文件 Ruff check、format check 及本地 CI 配置检查通过；未触发远端 CI，未重跑全仓历史测试或 PostgreSQL 集成。
- `workflow-post-fix/` 复验 19 条离线工作流、39 轮、449 项业务断言，333 个内存 span 全部完成关联。该批次没有外发，报告中的 server_receipt_verified=false 仍然正确；真实服务接收仅由上面的新合成探针证明。
- 没有新增模型或 Judge 调用、人工决定、标签冻结或质量认证；原真实采集 SHA-256 保持 75bd77832bd8e87639e27342d5bc86f30059339b57057c6cfedcab28b8cd5455。

这项验收最多证明指定本地版本在当前机器上接收了该次合成 trace。它不证明云端部署、生产网络、高可用、历史实验/19 条真实采集的外部接收，也不产生模型质量提升、人工标签或独立评测认证。E9 实际双候选比较仍需要匹配条件的候选结果。
