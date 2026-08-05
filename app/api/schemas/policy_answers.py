from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

_QuestionText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1000,
    ),
]


class PolicyAnswerRequest(BaseModel):
    """制度问答请求。"""

    question: _QuestionText


class PolicyAnswerResponse(BaseModel):
    """制度问答响应。"""

    question: str
    answer: str
    citations: list[str]