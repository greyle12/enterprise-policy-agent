from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.agent.intent import IntentType
from app.agent.router import AgentResponseStatus

_MessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1000,
    ),
]


class AgentMessageRequest(BaseModel):
    """统一 Agent 入口的用户消息。"""

    message: _MessageText


class IntentClassificationResponse(BaseModel):
    """通过 API 返回的意图分类信息。"""

    intent: IntentType
    confidence: float
    reason: str


class AgentMessageResponse(BaseModel):
    """统一 Agent 入口的路由结果。"""

    request: str
    classification: IntentClassificationResponse
    status: AgentResponseStatus
    reply: str
    citations: list[str]
