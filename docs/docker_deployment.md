# Day 17 Docker 部署与验收

本文档说明如何在 Windows Docker Desktop 上构建、启动、检查和停止企业制度 Agent。

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

查看状态：

```powershell
docker compose ps
docker compose logs --tail 100 agent
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
| readiness 返回 503 | SQLite 卷权限、schema 版本、应用生命周期初始化 |
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
- Redis；
- HTTPS 终止和域名；
- 云平台密钥管理；
- 镜像漏洞扫描；
- 指标、链路追踪和集中日志；
- 数据库备份恢复；
- 正式 CI/CD 发布流水线。
