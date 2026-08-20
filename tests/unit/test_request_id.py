from __future__ import annotations

import pytest

from app.observability import select_request_id


def test_accepts_bounded_safe_request_id() -> None:
    assert select_request_id("client-request:123", factory=lambda: "unused") == (
        "client-request:123"
    )


@pytest.mark.parametrize(
    "candidate",
    [None, "", "contains spaces", "line\nbreak", "x" * 65, "中文请求"],
)
def test_replaces_missing_or_unsafe_request_id(candidate: str | None) -> None:
    assert select_request_id(candidate, factory=lambda: "req_generated123") == ("req_generated123")


def test_rejects_unsafe_request_id_factory() -> None:
    with pytest.raises(ValueError, match="factory"):
        select_request_id(None, factory=lambda: "unsafe generated value")
