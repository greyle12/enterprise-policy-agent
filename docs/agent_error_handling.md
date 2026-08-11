# Agent 工具重试、超时与安全降级

Day 20 为统一 Agent 工作流增加工具执行容错层。目标不是吞掉异常，而是让调用方明确知道工具是否重试、是否恢复、是否降级，以及下一步应该做什么。

## 1. 处理边界

容错层保护以下工具：

| 工具 | 操作类型 | 默认最大尝试次数 | 自动重试 |
|---|---|---:|---|
| `intent_classifier` | 只读 | 3 | 仅瞬时错误 |
| `policy_answer` | 只读 | 3 | 仅瞬时错误 |
| `material_check` | 只读 | 3 | 仅瞬时错误 |
| `approval_check` | 只读 | 3 | 仅瞬时错误 |
| `draft_generation` | 纯计算 | 3 | 仅瞬时错误 |
| `draft_revision` | 纯计算 | 3 | 仅瞬时错误 |
| `approval_submission` | 有副作用写操作 | 1 | 否 |

SQLite checkpoint、对话记忆和状态投影仍由各自的事务与健康检查负责。本阶段没有引入分布式事务、消息队列或跨服务补偿。

## 2. 错误分类

| 分类 | 示例 | 自动重试 |
|---|---|---|
| `timeout` | 工具超过本次调用时限 | 只读/纯计算工具可以 |
| `rate_limited` | 上游返回 HTTP 429 | 只读/纯计算工具可以 |
| `upstream_unavailable` | 连接失败、HTTP 5xx | 只读/纯计算工具可以 |
| `invalid_response` | JSON、Pydantic 或引用校验失败 | 否 |
| `internal_error` | 未知内部异常 | 否 |

错误分类只依赖异常类型和状态码。底层异常正文可能包含 URL、请求片段或凭据，因此不会写入 Agent 回复和 `resilience` 响应。

## 3. 重试策略

只读和纯计算工具使用 Tenacity 指数退避：

```text
调用工具
→ 独立超时控制
→ 判断错误是否属于 timeout / rate_limited / upstream_unavailable
→ 属于且操作可重试：等待后重试
→ 成功：outcome = recovered
→ 达到上限：outcome = failed，status = unavailable
```

以下错误不会自动重试：

- 输入或业务前置条件不满足；
- 工具返回无法验证的结构；
- LLM 回答引用不存在的来源；
- 未知内部编程错误；
- 任何审批提交写操作。

## 4. 为什么提交不自动重试

审批提交可能已经在服务端完成，但客户端恰好在收到响应前断开连接。此时自动重试可能产生重复副作用。

Day 20 的处理方式是：

1. 一次逻辑提交最多调用提交工具一次；
2. 超时或连接失败时返回 `unavailable`；
3. 保留提交前的已确认草稿；
4. 提示用户在同一个 `session_id` 下再次回复“提交审批”；
5. 第二次请求继续使用相同的确定性幂等键；
6. 提交服务按幂等规则返回首次结果或创建一次新申请。

这避免了工作流在不知道首次调用结果的情况下盲目重复写入。

## 5. 草稿状态保护

草稿修改工具失败时：

- 不覆盖上一个有效草稿；
- 不增加草稿 revision；
- 不把草稿标记为已确认或已提交；
- 返回 `unavailable` 和恢复动作。

提交工具失败时：

- 草稿保持 `confirmed`；
- 不生成伪造的审批单号；
- 不返回成功的提交对象；
- 允许调用方用同一会话安全重试。

## 6. API 响应

成功且无需重试：

```json
{
  "resilience": {
    "degraded": false,
    "recovered": false,
    "tool_calls": [
      {
        "tool": "intent_classifier",
        "operation": "read_only",
        "outcome": "success",
        "attempts": 1,
        "max_attempts": 3,
        "timeout_seconds": 65.0,
        "retry_safe": true
      }
    ]
  }
}
```

重试耗尽后的安全降级：

```json
{
  "status": "unavailable",
  "resilience": {
    "degraded": true,
    "recovered": false,
    "tool_calls": [
      {
        "tool": "policy_answer",
        "operation": "read_only",
        "outcome": "failed",
        "attempts": 3,
        "max_attempts": 3,
        "timeout_seconds": 65.0,
        "retry_safe": true,
        "error": {
          "error_id": "ERR-...",
          "code": "tool_upstream_unavailable",
          "category": "upstream_unavailable",
          "retryable": true,
          "recovery_action": "retry_later",
          "message": "制度问答服务暂时不可用，本轮已安全停止。请稍后重试。"
        }
      }
    ]
  }
}
```

HTTP 请求仍然成功到达 Agent 并得到结构化业务结果，因此统一入口返回 HTTP 200，并用业务字段 `status=unavailable` 表示本轮工具不可用。输入校验错误仍返回 HTTP 422。

## 7. 配置

```dotenv
AGENT_SAFE_TOOL_TIMEOUT_SECONDS=65
AGENT_MUTATION_TOOL_TIMEOUT_SECONDS=10
AGENT_TOOL_MAX_ATTEMPTS=3
AGENT_RETRY_MIN_WAIT_SECONDS=0.1
AGENT_RETRY_MAX_WAIT_SECONDS=1.0
```

说明：

- `AGENT_TOOL_MAX_ATTEMPTS` 包含第一次调用；默认值 3 表示最多 1 次初始调用和 2 次重试。
- 提交工具忽略该尝试次数并固定为 1。
- 最大等待必须大于或等于最小等待。
- Agent 容错层和 OpenAI SDK 的 `LLM_MAX_RETRIES` 是两层不同边界；真实部署时应结合整体延迟预算调整，避免重试层数相乘导致长尾延迟过高。

## 8. 本地验收

```powershell
Set-Location D:\Ai_agent_program\demo1

& .\.venv\Scripts\python.exe -X utf8 `
  -m scripts.verify_agent_resilience

& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
```

离线验收不调用真实 LLM、DeepSeek、网络服务或正式审批系统。它验证：

- 只读工具在前两次瞬时失败后第三次恢复；
- 重试耗尽后返回 `unavailable`；
- 原始敏感异常正文不会进入响应；
- 提交类操作只调用一次；
- 提交失败建议使用相同会话再次提交。

## 9. 当前限制

- 没有集中式结构化日志和指标；
- 没有分布式 trace；
- 没有熔断器和跨实例共享故障状态；
- 没有按工具独立配置重试预算；
- 没有队列、死信和异步补偿；
- 没有真实第三方审批系统的幂等契约验证。

因此 Day 20 证明的是单机作品集版本具备明确、可测试的工具错误边界，不代表已经达到多实例生产容错能力。
