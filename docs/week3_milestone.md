# 第 3 周里程碑总结（Day 15–Day 21）

## 1. 本周结果

第 3 周把项目从“能够调用几个独立工具”推进到一个具备状态、评测、部署、记忆、
容错和受控外部研究能力的单机 Agent 后端。

```text
确定性业务工具
→ LangGraph 多轮办理与人工确认
→ SQLite 重启恢复
→ 黄金集自动评测
→ Docker Compose 部署
→ GitHub Actions 持续集成
→ 受限对话记忆
→ 工具有界重试与安全降级
→ 内部 RAG + 显式 Web Search 研究助手
```

## 2. 每日能力累计

| Day | 核心能力 | 可验证结果 |
|---:|---|---|
| 15 | 多轮草稿、人工确认、幂等模拟提交和 SQLite 状态恢复 | 会话中断后可继续，重复提交不重复创建申请 |
| 16 | 30 条离线黄金评测与结构化报告 | 意图、工具、材料、审批和引用五项门禁 |
| 17 | 非 root 多阶段镜像与 Docker Compose 持久卷 | 容器重建后 SQLite 数据仍存在 |
| 18 | GitHub Actions、依赖审查和 Wheel 构建 | Push/PR 自动执行无密钥质量门禁 |
| 19 | SQLite 对话记忆、会话隔离和省略追问消解 | 重启后仍可理解“那需要哪些材料？” |
| 20 | Tenacity 重试、超时、错误分类和副作用保护 | 只读工具可恢复，审批提交绝不盲目重试 |
| 21 | 受控制度研究助手 | 内部 `S` 来源与外部 `W` 来源分区，Web 双重显式授权 |

## 3. 当前可演示的完整链路

### 3.1 制度问答

```text
用户问题
→ 意图识别
→ BGE 向量检索
→ 制度证据上下文
→ 带 S 引用回答
```

### 3.2 流程办理

```text
生成申请草稿
→ 补齐字段与材料
→ 人工确认
→ 单次幂等提交
→ 冻结审批路线和审计记录
```

### 3.3 多轮恢复

```text
SQLite checkpoint 保存业务状态
+ SQLite conversation memory 保存自然语言上下文
→ 服务重启后继续同一 session
```

### 3.4 制度研究

```text
内部制度 RAG（权威依据）
+ 可选 Web Search（公开参考）
→ 分栏回答和来源用途边界
```

## 4. 本周关键工程原则

1. 大模型负责理解和表达，金额、材料、审批、状态与幂等由确定性代码负责。
2. checkpoint 与 conversation memory 分离，避免两套状态含义混在一起。
3. 只读工具可以有界重试，有副作用提交只能单次执行并依靠幂等恢复。
4. 外部网页不是企业制度，不能覆盖内部依据或驱动办理流程。
5. 错误响应返回稳定分类和恢复动作，不泄露异常正文或凭据。
6. 每项能力都必须有离线专项验收和全仓回归证据。

## 5. 一键验收顺序

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.run_golden_evaluation --mode offline

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_conversation_memory

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_agent_resilience

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_policy_research

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_ci_configuration
```

## 6. 面试可讲的技术决策

- 为什么没有让 LLM 直接计算审批路线；
- 为什么提交操作不能使用普通自动重试；
- 为什么 checkpoint 不等于对话记忆；
- 为什么外部 Web Search 必须显式授权并与内部制度分栏；
- 如何用离线黄金集防止意图和工具选择回归；
- 如何证明一个增量补丁不依赖本地缓存或隐藏文件。

## 7. 当前边界

本周完成的是可测试、可容器化的单机作品集版本，不代表多实例生产系统。仍未完成的
重点包括权限过滤、提示注入专项评测、BM25/Hybrid Search/Rerank、集中式日志与指标、
PostgreSQL/pgvector、Redis 和真实企业数据验证。

后续 Day 29 已补充检索前权限过滤和提示注入专项评测；其余边界仍然成立，详见
`docs/rag_security_guardrails.md`。
