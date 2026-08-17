# Docker 部署与验收（Day 17，Day 27 更新）

本文档说明如何在 Windows Docker Desktop 上构建、启动、检查和停止企业制度 Agent。
Day 23 的 Compose 同时启动临时 Redis，用于可丢失、短 TTL 的 LLM 响应缓存；Day 24
在 Agent 进程内合并相同缓存键的并发未命中请求。Day 27 将 Compose 镜像标签更新为
`enterprise-policy-agent:day27`，并增加默认关闭的单进程 LLM Provider 背压；专项验收仍在
宿主机完全离线执行，不会从容器向真实 Provider 发送压测请求。

## 1. 前置条件

- Docker Desktop 已安装并启动；
- Docker Desktop 使用 Linux containers；
- 项目根目录为 `D:\Ai_agent_program\demo1`；
- 已准备可用的 OpenAI-compatible LLM API 配置；
- 首次构建和首次启动能够访问 Python 包源及 BGE 模型源。

验证 Docker：

```powershell
docker version
docker compose version
```

## 2. 创建运行配置

在项目根目录复制环境变量模板：

```powershell
Set-Location D:\Ai_agent_program\demo1
Copy-Item .env.example .env
notepad .env
```

至少替换：

```env
LLM_API_KEY=你的真实密钥
LLM_BASE_URL=你的OpenAI-compatible接口地址
LLM_MODEL=你的模型名称
```

`.env` 已被 Git 和 Docker build context 排除，不会写入镜像。不要把真实密钥写入 `Dockerfile`、`compose.yaml` 或 `.env.example`。

## 3. 校验并一键启动

先校验 Compose 配置：

```powershell
docker compose config --quiet
```

构建并后台启动：

```powershell
docker compose up --build --detach --wait
```

第一次构建需要安装 `sentence-transformers` 和 PyTorch，第一次启动还需要下载 `BAAI/bge-small-zh-v1.5`。因此所需时间和镜像体积都会明显大于普通 FastAPI 项目。模型下载完成后会保存在 `model_cache` 具名卷中，后续重建容器不必重复下载。

如果 Docker Desktop 内下载大型 PyTorch wheel 不稳定，可以把已校验的 CPU wheel 保留在
`vendor/wheels/`。该二进制已被 `.gitignore` 排除，不应提交；CI 在 Dockerfile 引用它时会
自行下载官方文件并校验固定 SHA-256。

查看状态：

```powershell
docker compose ps
docker compose logs --tail 100 agent
docker compose logs --tail 100 redis
```

## 4. 健康检查

存活检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
```

预期：

```json
{
  "status": "ok",
  "service": "Enterprise Policy Agent",
  "version": "0.1.0"
}
```

就绪检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

预期：

```json
{
  "status": "ready",
  "checks": {
    "application": "ok",
    "database": "ok"
  }
}
```

`/health/live` 只说明 API 进程能够响应；`/health/ready` 还会检查应用级组件和 SQLite 连接及 schema 版本。Docker 使用就绪检查判断容器是否健康。

Redis 缓存状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cache/status |
  ConvertTo-Json -Depth 5
```

Redis 是可选性能组件：状态为 `degraded` 时，Agent 会直连 LLM，不会因此让 readiness
失败。Compose 内 Agent 使用 `redis://redis:6379/0`；宿主机只通过 `127.0.0.1:6379`
访问 Redis。

Day 24 状态还会返回 `singleflight_enabled`、`singleflight_max_keys`、
`singleflight_in_flight`、`metrics.coalesced` 和 `metrics.singleflight_overflows`。这些值只属于
当前 Agent 进程，不是 Redis 中的共享状态。

Provider 容量状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/provider/status |
  ConvertTo-Json -Depth 5
```

Day 27 默认返回 `state=disabled`。只有在 `.env` 显式设置
`LLM_PROVIDER_LIMIT_ENABLED=true` 后才执行进程内限流；`in_flight`、`queued` 和所有 metrics
只属于当前 Agent 进程，不是多个容器或 worker 的聚合值。

## 5. 自动验收

以下脚本会依次执行：

```text
校验 Compose 配置
→ 构建并启动服务
→ 等待 readiness 通过
→ 向 SQLite 写入隔离探针会话
→ 强制重建容器
→ 从同一数据库读取探针会话
→ 删除探针会话
```

运行：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_docker_deployment
```

预期最终输出包含：

```json
{
  "passed": true,
  "compose_config_valid": true,
  "container_ready": true,
  "sqlite_volume_survived_recreation": true
}
```

脚本不会删除业务具名卷，也不会关闭验收后的服务。

如果宿主机端口不是 `8000`，先修改 `.env` 中的 `APP_PORT`，再把实际地址传给脚本：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_docker_deployment `
  --health-url http://127.0.0.1:9000/health/ready
```

## 6. SQLite 持久卷

Compose 将两个具名卷挂载到容器：

| 具名卷 | 容器路径 | 内容 |
|---|---|---|
| `agent_runtime` | `/app/data/runtime` | SQLite 主文件、WAL 和共享内存文件 |
| `model_cache` | `/app/data/model-cache` | BGE、Hugging Face 和 Torch 缓存 |

执行以下操作不会删除具名卷：

```powershell
docker compose restart agent
docker compose down
docker compose up --detach --wait
```

因此草稿、会话、审批单和审计记录可以跨进程重启和容器重建恢复。

Redis 使用 128 MiB 临时 `/data`、`allkeys-lru`，并关闭 RDB 与 AOF。它不是具名卷，
重建或停止 Redis 后缓存会丢失；业务状态和正确性不依赖这些缓存数据。

以下命令会删除具名卷及其中数据，只有明确希望清空本地演示数据时才能执行：

```powershell
docker compose down --volumes
```

## 7. 停止与排障

停止并保留数据：

```powershell
docker compose down
```

实时查看日志：

```powershell
docker compose logs --follow agent
```

重新构建：

```powershell
docker compose up --build --detach --wait
```

常见问题：

| 现象 | 优先检查 |
|---|---|
| 容器不断重启 | `.env` 中的 LLM 配置、`docker compose logs agent` |
| 长时间处于 `starting` | 首次 BGE 模型下载、网络连接和磁盘空间 |
| 宿主机端口被占用 | 修改 `.env` 的 `APP_PORT` |
| Redis 端口被占用 | 修改 `.env` 的 `REDIS_PORT`，Agent 容器内部地址不变 |
| readiness 返回 503 | SQLite 卷权限、schema 版本、应用生命周期初始化 |
| cache 状态为 `degraded` | `docker compose ps redis`、Redis 日志、容器内部 DNS |
| Docker build 很慢 | PyTorch 依赖体积、镜像源和网络速度 |

## 8. 当前边界

Day 17 的 Docker 方案适合：

- 本地演示；
- 单机个人作品集；
- 面试现场一键启动；
- 单个 FastAPI 实例；
- SQLite 持久化；
- 自动健康检查。

当前仍不等于正式生产部署，尚未包含：

- 多实例共享数据库；
- PostgreSQL / pgvector；
- 多实例 Redis 高可用、ACL、TLS 和托管服务认证；
- HTTPS 终止和域名；
- 云平台密钥管理；
- 镜像漏洞扫描；
- 指标、链路追踪和集中日志；
- 数据库备份恢复；
- 正式 CI/CD 发布流水线。
