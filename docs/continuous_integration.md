# Day 18 持续集成与质量门禁

Day 18 使用 GitHub Actions 将项目已有的测试、代码规范、黄金评测、Python
构建和 Docker 构建检查连接成自动化持续集成流程。Day 22 在同一只读质量 Job 中
增加完全离线的性能预算检查和结构化性能证据；Day 23 增加 Redis LLM 缓存契约验收，
Day 24 增加异步 single-flight 并发契约，Day 25 增加三种请求分布的离线并发负载报告，
Day 26 再增加 Embedding/Reranker 逐条与批量处理的等价性和调用次数报告；Day 27 增加
LLM Provider 有界并发、FIFO 排队、超时和取消清理的完全离线背压契约；Day 28 增加请求
关联、脱敏日志、低基数 HTTP 指标、安全 500 和 Prometheus 格式的进程内可观测性契约；
Day 29 增加检索前权限过滤、提示注入拒绝、污染证据隔离和零 Provider 调用断言；Day 30
增加六场景作品集演示、发布契约和可下载的 JSON / Markdown 演示证据；Advanced RAG
Phase 22 增加统一 Document Loader、原有 Parser/Chunker 兼容性和 5 文档/199 Chunk 契约。
Advanced RAG Phase 23 增加真实 PDF 动态生成、原生文本、sidecar、页码和 OCR handoff 契约。
Advanced RAG Phase 24 增加真实 DOCX 动态生成、段落/表格顺序、sidecar、块定位和 OCR handoff
契约。
Advanced RAG Phase 25 增加扫描 PDF、图片型 DOCX、OCR provenance、低置信度拒绝和安全边界契约。

CI 只验证代码，不部署服务、不发布镜像、不调用真实 LLM，也不读取项目密钥。

## 1. 文件与入口

| 文件 | 职责 |
|---|---|
| `.github/workflows/ci.yml` | 定义触发条件、三个 CI Job、质量命令和构建证据 |
| `.github/dependabot.yml` | 每周检查 Python 和 GitHub Actions 依赖更新 |
| `scripts/verify_ci_configuration.py` | 在本地和 CI 中验证工作流安全与质量契约 |
| `tests/deployment/test_ci_contract.py` | 验证仓库中实际 CI 配置满足部署契约 |
| `tests/unit/test_verify_ci_configuration.py` | 验证危险或不完整配置会被拒绝 |

本地验证 CI 配置：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_ci_configuration
```

成功时返回：

```json
{
  "passed": true,
  "jobs": [
    "container-build",
    "dependency-review",
    "quality"
  ],
  "dependency_ecosystems": [
    "github-actions",
    "pip"
  ]
}
```

报告还会包含两个配置文件的 SHA-256 和四个外部 Action 的完整提交 SHA。

## 2. 触发条件

工作流在以下情况自动运行：

| 事件 | 运行内容 |
|---|---|
| 向 `main` 或 `master` 推送 | Python 质量门禁，成功后构建 Docker 镜像 |
| 向 `main` 或 `master` 提交 Pull Request | Python 质量门禁和新增依赖风险审查 |
| Actions 页面手动运行 | Python 质量门禁，成功后构建 Docker 镜像 |

同时配置了并发取消：同一分支出现较新的提交时，旧的未完成运行会被取消，避免浪费
Runner 时间，也避免旧结果晚于新结果返回。

## 3. Python quality gates

`quality` Job 固定使用：

```text
ubuntu-24.04
Python 3.12.10
timeout 30 分钟
```

按顺序执行：

```text
安装项目和开发依赖
→ pip 依赖一致性检查
→ CI 配置契约检查
→ Ruff 静态检查
→ Ruff 格式检查
→ 全量 pytest
→ 30 条离线黄金评测
→ 五场景离线性能预算
→ Redis LLM 缓存离线契约
→ 异步 LLM single-flight 离线并发契约
→ LLM Provider 并发与背压离线契约
→ 请求关联与运行时可观测性离线契约
→ RAG 权限与提示注入防护离线契约
→ Phase 22 Document Loader 离线契约
→ Phase 23 PDF 原生文本与页码离线契约
→ Phase 24 DOCX 段落/表格与块定位离线契约
→ Phase 25 PDF/DOCX OCR fallback 与质量门禁离线契约
→ 六场景离线作品集演示与 Day 30 发布契约
→ 三种 load shape 的离线并发吞吐报告
→ Embedding/Reranker 离线批处理对照报告
→ 构建 Python Wheel
```

任意一步返回非零退出码，Job 即失败。

离线黄金评测、性能基准、缓存契约、single-flight、Provider 背压、运行时可观测性、RAG 安全、
Document Loader、PDF/DOCX/OCR、作品集演示、并发负载和批处理对照都不使用 `.env` 中的模型配置，
也不会发送外部模型请求。缓存与负载专项使用内存协议替身，不连接真实 Redis。Loader 专项读取
仓库中的 Markdown；PDF/DOCX/OCR 专项在临时目录动态生成真实文档和 sidecar，不保存用户文档。
OCR CI 使用确定性进程内 Provider，不安装或调用系统 Tesseract。可观测性
专项使用进程内 TestClient；安全专项使用固定身份、制度和攻击夹具，不调用 Provider。因此来自
Fork 的 Pull Request 可以在没有密钥、不启动端口且不部署 Prometheus 的情况下执行相同质量门禁。

### 3.1 构建证据

CI 保存两组 14 天构建证据：

| Artifact | 内容 | 失败时行为 |
|---|---|---|
| `quality-evidence-<run_id>` | pytest JUnit XML、黄金评测、串行性能、并发负载、批处理和作品集 JSON / Markdown | 尽可能保存已生成文件 |
| `python-wheel-<run_id>` | 可安装 `.whl` | 仅全部质量门禁通过后保存 |

JUnit XML 适合测试平台或后续脚本读取；黄金评测报告记录指标和失败用例；性能报告
记录 p50、p95、错误率和预算结果；并发报告另外记录吞吐、调用放大率和 Provider 峰值；
批处理报告记录调用减少、内部批次、结果等价性和吞吐；作品集报告记录六个集成场景的可展示
证据；Wheel 证明 Python 包能够从干净环境构建。

CI 不上传 `.cprofile`、py-spy SVG 或 Scalene 原始结果，避免把 Runner 绝对路径和大量
采样细节当作长期构建证据。

## 4. Dependency risk review

`dependency-review` 仅在 Pull Request 中运行，比较目标分支和 PR 引入的依赖变化。

当前门槛：

```text
新引入 high 或 critical 漏洞 → PR 检查失败
```

本阶段没有定义许可证白名单，因此关闭许可证阻断，只执行漏洞门禁。该功能可用于公开
GitHub 仓库；私有仓库需要相应的 GitHub Advanced Security 权限。

## 5. Container build verification

`container-build` 只在 Push 或手动运行中执行，并依赖 `quality` 成功：

```text
Python 质量门禁通过
→ 使用 .env.example 生成非密钥 Compose 配置
→ 若 Dockerfile 使用本地 CPU PyTorch wheel，则下载并校验固定 SHA-256
→ docker compose config --quiet
→ 构建完整 runtime 镜像
→ 检查镜像以 agent 非 root 用户运行
→ 检查镜像内可以导入 app 包
```

本地 wheel 文件被 `.gitignore` 排除，不提交 192 MB 二进制；container Job 只在 Dockerfile
确实引用该路径时下载官方文件，并在构建前校验固定 SHA-256。CI 不启动 FastAPI，因此不会
在 Runner 中下载 BGE 模型。Day 17 的本地部署验收脚本
仍负责 readiness 和 SQLite 跨容器重建验证。

容器 Job 不推送镜像到任何 Registry，也没有 Docker Registry 凭据。

## 6. 安全边界

工作流强制执行：

- 顶层 `GITHUB_TOKEN` 权限只有 `contents: read`；
- `checkout` 不保留 Git 凭据；
- 不引用 `${{ secrets.* }}`；
- 使用 `pull_request`，禁止 `pull_request_target`；
- 所有外部 Action 固定到完整 40 位提交 SHA；
- 每个 Job 都有明确超时；
- PR 不运行 Docker 构建和任何有副作用的部署操作。

`verify_ci_configuration.py` 会同时验证这些约束和必须执行的质量命令。有人修改
CI 文件时，如果意外放宽权限或删除门禁，普通 pytest 就会先失败。

## 7. Dependabot

Dependabot 每周一按 `Asia/Shanghai` 时区检查：

- `pip`：`pyproject.toml` 中的 Python 依赖；
- `github-actions`：工作流中的 Action 提交版本。

同一生态的更新会分组，减少零散 PR。更新 PR 仍须通过相同 CI，Dependabot 不能绕过
质量门禁。

## 8. 第一次 GitHub 验收

提交并推送 Day 18 后：

1. 打开仓库的 `Actions` 页面；
2. 选择 `Continuous Integration`；
3. 确认 `Python quality gates` 成功；
4. 如果是 Push，确认 `Container build verification` 成功；
5. 在运行详情的 Artifacts 中下载测试证据和 Wheel；
6. 创建测试 Pull Request，确认 `Dependency risk review` 出现。

推荐在仓库 Ruleset 或 Branch protection 中，把以下检查设为合并必需：

```text
Python quality gates
Dependency risk review
```

Docker 构建只在 Push 或手动运行中执行，因此不应设为 PR 必需检查。

## 9. 本地等价质量门禁

```powershell
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ci_configuration
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m ruff format --check .
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_golden_evaluation `
  --mode offline
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_performance_benchmark `
  --warmups 1 `
  --iterations 5
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_llm_cache
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_async_singleflight
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_provider_backpressure
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_runtime_observability
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_rag_security
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_document_loader
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_pdf_document_parsing
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_docx_document_parsing
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_ocr_fallback
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_portfolio_demo `
  --output-dir artifacts/portfolio
& .\.venv\Scripts\python.exe -X utf8 -m scripts.verify_portfolio_release
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_concurrency_load_test `
  --requests 24 `
  --concurrency 12 `
  --provider-latency-ms 15
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_batch_optimization `
  --items 32 `
  --batch-size 8 `
  --call-overhead-ms 1.5 `
  --batch-latency-ms 0.25
& .\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir dist
```

Docker Desktop 已启动时还可以运行 Day 17 的完整容器验收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_docker_deployment
```

## 10. 排障

| 现象 | 检查方向 |
|---|---|
| `quality` 安装耗时长 | 首次需要下载 PyTorch 等依赖；后续会复用 pip 缓存 |
| Ruff 失败 | 本地运行 `ruff format` 和 `ruff check --fix`，再人工检查差异 |
| 黄金评测失败 | 下载 `quality-evidence`，查看 Markdown 错例和 JSON 原子断言 |
| 性能预算失败 | 在同环境重复基准，再用 cProfile 定位最慢场景；不要直接抬高预算 |
| 缓存契约失败 | 单独运行 `scripts.verify_llm_cache`，检查键、TTL、绕过和降级断言 |
| single-flight 契约失败 | 单独运行 `scripts.verify_async_singleflight`，检查去重、取消隔离和并发断言 |
| Provider 背压契约失败 | 单独运行 `scripts.verify_provider_backpressure`，检查执行峰值、FIFO、溢出、超时和取消清理 |
| 运行时可观测性契约失败 | 单独运行 `scripts.verify_runtime_observability`，检查请求 ID、路由模板、500 脱敏、指标和 Prometheus 格式 |
| RAG 安全契约失败 | 单独运行 `scripts.verify_rag_security`，检查可信身份、7 类权限边界、攻击/正常用例、证据隔离和 Provider 调用数 |
| Document Loader 契约失败 | 单独运行 `scripts.verify_document_loader`，检查扩展名注册、5 份制度、199 个 Chunk 和稳定来源路径 |
| PDF 解析契约失败 | 单独运行 `scripts.verify_pdf_document_parsing`，检查 PyMuPDF、sidecar、页码、OCR handoff 和加密/损坏拒绝 |
| DOCX 解析契约失败 | 单独运行 `scripts.verify_docx_document_parsing`，检查 python-docx、sidecar、段落/表格顺序、块定位和 OCR handoff |
| OCR fallback 契约失败 | 单独运行 `scripts.verify_ocr_fallback`，检查 PDF 页渲染、DOCX 图片、置信度门禁、provenance 和安全顺序 |
| 并发负载契约失败 | 单独运行 `scripts.verify_concurrency_load`，检查三个 load shape 的调用数与错误率 |
| 批处理契约失败 | 单独运行 `scripts.verify_embedding_reranker_batching`，检查调用数、内部批次、摘要和顺序 |
| 作品集发布契约失败 | 单独运行 `scripts.run_portfolio_demo`，再检查三份 Day 30 文档和 CI 证据路径 |
| Dependency Review 不可用 | 检查仓库是否公开，或私有仓库是否具备所需安全功能 |
| Container build 超时 | 检查 Docker Hub 可达性和 Python 依赖下载日志 |
| 修改 Action 后契约失败 | 使用该 Action 官方仓库发布版本对应的完整 40 位提交 SHA |

## 11. 当前限制

Day 18 实现的是持续集成，不是持续部署：

- 不部署到云服务器；
- 不推送容器镜像；
- 不运行真实 LLM 评测；
- 不把离线性能预算解释为真实模型或公网 SLA；
- 不运行生产级持续压测、py-spy 或 Scalene；Day 25 只执行短时、确定性的离线 load shape；
- 不运行真实 BGE Reranker；Day 26 只执行固定离线模型替身；
- 不调用真实 LLM 验证容量；Day 27 只执行固定离线 Provider 替身；
- 不启动 Prometheus、Grafana 或日志采集器；Day 28 只验证进程内指标、格式和脱敏契约；
- 不连接真实身份系统或调用模型执行红队；Day 29 只使用固定离线身份、制度和攻击夹具；
- 不用作品集 6/6 代替真实模型评测；Day 30 使用确定性词法向量、LLM 和 Web 夹具；
- 不验证 BGE 首次模型下载；
- 不替代 Day 17 的本机 SQLite 持久卷重建验收；
- 不自动配置 GitHub Ruleset 或 Branch protection。

这些边界保证来自外部贡献者的普通 PR 可以在无密钥、只读权限下安全运行。
