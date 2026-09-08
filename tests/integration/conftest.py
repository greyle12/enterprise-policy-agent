from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Run Psycopg integration tests on a Windows-compatible selector loop."""

    del config, item
    return {"psycopg-selector": asyncio.SelectorEventLoop}
