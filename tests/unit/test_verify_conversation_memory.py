from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_conversation_memory import run_verification


@pytest.mark.asyncio
async def test_offline_memory_verification_passes(tmp_path: Path) -> None:
    report = await run_verification(tmp_path / "verify-memory.db")

    assert report == {
        "passed": True,
        "memory_backend": "sqlite",
        "survives_process_restart": True,
        "follow_up_context_applied": True,
        "resolved_application_type": "travel_reimbursement",
        "messages_before_clear": 4,
        "messages_after_clear": 0,
    }
