from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_agent_router
from app.main import create_app
from app.memory import (
    ConversationMemorySnapshot,
    ConversationMessage,
    ConversationRole,
)

app = create_app(enable_lifespan=False)
_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class FakeSessionRouter:
    def __init__(self) -> None:
        self.history_calls: list[tuple[str, int]] = []
        self.clear_calls: list[str] = []

    async def get_conversation_history(
        self,
        session_id: str,
        *,
        limit: int,
    ) -> ConversationMemorySnapshot:
        self.history_calls.append((session_id, limit))
        messages = (
            ConversationMessage(
                message_id="memory-user",
                session_id=session_id,
                turn_number=1,
                role=ConversationRole.USER,
                content="差旅住宿标准是多少？",
                created_at=_NOW,
            ),
            ConversationMessage(
                message_id="memory-assistant",
                session_id=session_id,
                turn_number=1,
                role=ConversationRole.ASSISTANT,
                content="请按照目的地标准执行。",
                created_at=_NOW,
                redacted=True,
            ),
        )
        return ConversationMemorySnapshot(
            session_id=session_id,
            messages=messages[-limit:],
            total_message_count=2,
            backend="sqlite",
            survives_process_restart=True,
        )

    async def clear_session(self, session_id: str) -> None:
        self.clear_calls.append(session_id)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _use_router(router: FakeSessionRouter) -> None:
    app.dependency_overrides[get_agent_router] = lambda: router


def test_returns_sanitized_session_messages() -> None:
    router = FakeSessionRouter()
    _use_router(router)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/sessions/history-demo/messages",
            params={"limit": 10},
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "history-demo",
        "messages": [
            {
                "turn_number": 1,
                "role": "user",
                "content": "差旅住宿标准是多少？",
                "created_at": "2026-08-11T12:00:00Z",
                "redacted": False,
                "truncated": False,
            },
            {
                "turn_number": 1,
                "role": "assistant",
                "content": "请按照目的地标准执行。",
                "created_at": "2026-08-11T12:00:00Z",
                "redacted": True,
                "truncated": False,
            },
        ],
        "total_message_count": 2,
        "returned_message_count": 2,
        "backend": "sqlite",
        "survives_process_restart": True,
    }
    assert router.history_calls == [("history-demo", 10)]


def test_clears_complete_agent_session() -> None:
    router = FakeSessionRouter()
    _use_router(router)

    with TestClient(app) as client:
        response = client.delete("/api/v1/agent/sessions/history-demo")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "history-demo",
        "cleared": True,
    }
    assert router.clear_calls == ["history-demo"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agent/sessions/invalid%20session/messages",
        "/api/v1/agent/sessions/valid-session/messages?limit=0",
        "/api/v1/agent/sessions/valid-session/messages?limit=101",
    ],
)
def test_rejects_invalid_history_parameters(path: str) -> None:
    router = FakeSessionRouter()
    _use_router(router)

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 422
    assert router.history_calls == []
