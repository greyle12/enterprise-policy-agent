from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class PostgresStateCursor(Protocol):
    """Minimum async Psycopg cursor surface used by Agent state repositories."""

    async def fetchone(self) -> Sequence[Any] | None:
        """Return one result row."""

    async def fetchall(self) -> Sequence[Sequence[Any]]:
        """Return all result rows."""


class PostgresStateConnection(Protocol):
    """Async transactional connection borrowed from a Psycopg pool."""

    async def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> PostgresStateCursor:
        """Execute one parameterized PostgreSQL statement."""


class PostgresStateConnectionPool(Protocol):
    """Injectable async pool boundary shared by all Agent state repositories."""

    def connection(
        self,
        timeout: float | None = None,
    ) -> AbstractAsyncContextManager[PostgresStateConnection]:
        """Borrow one connection whose context commits or rolls back the transaction."""


__all__ = [
    "PostgresStateConnection",
    "PostgresStateConnectionPool",
    "PostgresStateCursor",
]
