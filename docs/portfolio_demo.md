# Day 30：一键作品集演示

Day 30 把分散在制度问答、业务工具、LangGraph、安全和研究助手中的能力组合成一条可重复、
完全离线的演示链路。它适合提交前验收、录制项目演示和技术面试现场讲解。

## 1. 一键运行

在项目根目录执行：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_portfolio_demo `
  --output-dir artifacts/portfolio
```

发布契约复验：

```powershell
& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_portfolio_release
```

成功时终端显示 `quality_gate_passed: true` 和 `6/6`。报告位于：

```text
artifacts/portfolio/portfolio-demo-report.json
artifacts/portfolio/portfolio-demo-report.md
```

JSON 用于 CI 和机器校验，Markdown 用于演示、评审和归档。

## 2. 六个演示场景

| 顺序 | 场景 | 证明的能力 | 关键结果 |
|---:|---|---|---|
| 1 | `rag_citation` | 授权检索、上下文构造、引用校验 | 命中差旅制度并返回 `S1` |
| 2 | `material_rules` | 意图路由和确定性材料检查 | 返回 7 项差旅材料及第十六条 |
| 3 | `approval_route` | 金额解析和确定性审批链 | 6,000 元 IT 采购生成 4 步路线 |
| 4 | `human_in_loop` | 草稿、确认、副作用和幂等 | 未确认不提交，重复提交复用结果 |
| 5 | `research_boundary` | 内部优先和内外来源分区 | 内部 `S1`、外部 `W1`，外部仅参考 |
| 6 | `security_boundary` | 执行前提示注入防护 | 阻断攻击，Provider 调用增量为 0 |

## 3. 为什么演示默认完全离线

面试演示最怕网络抖动、API 额度、模型版本和随机输出破坏复现。Day 30 因此复用真实制度解析、
检索器、LangGraph、规则、人工确认和安全代码，同时替换三个不稳定边界：

- BGE：使用确定性中文 n-gram Hash 词法向量；
- LLM：使用固定且带合法 `S1` 的返回；
- Web Search：使用固定 `W1` 公开资料夹具。

这能证明系统编排和工程契约，但不能把离线结果描述为真实 BGE 召回率、LLM 回答质量、
Web Search 可用性或生产 SLA。真实 Provider 验证应作为单独、显式授权的演示步骤。

## 4. 推荐的 5 分钟演示顺序

1. 用 30 秒说明场景：员工既能问制度，也能办理申请；
2. 展示 `docs/system_architecture.md`，说明 LLM 与确定性代码的职责分界；
3. 运行一键命令，展示 6/6 结果；
4. 打开 Markdown 报告，讲 `rag_citation` 与 `human_in_loop`；
5. 重点展示 `security_boundary` 的 Provider 调用增量为 0；
6. 用最后 30 秒主动说明单机、离线夹具和未接真实认证等边界。

## 5. 可选的真实 API 演示

只有本地 `.env` 已正确配置且允许调用真实 Provider 时，才运行：

```powershell
docker compose up --detach --wait
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/security/status
```

不要在录屏、日志或截图中展示 `.env`、API Key、Redis URL 或真实员工信息。

## 6. 失败排查

- `ModuleNotFoundError`：确认当前目录是项目根目录，并执行 `pip install -e ".[dev]"`；
- 报告不是 6/6：先运行 `python -m pytest -q` 定位对应组件；
- 中文乱码：保留 `-X utf8`，PowerShell 可先执行 `$env:PYTHONUTF8="1"`；
- Docker 演示失败：先单独运行 `docker info` 和 `docker compose config --quiet`；
- 安全场景失败：运行 `python -X utf8 -m scripts.verify_rag_security` 获取专项结果。
