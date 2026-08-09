from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.persistence.sqlite_schema import (
    connect_database,
    initialize_database,
)


def _version_key(version: str | float) -> str:
    return json.dumps(
        [type(version).__name__, version],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class SQLiteCheckpointSaver(BaseCheckpointSaver[str]):
    """Small async-compatible SQLite saver for this single-process demo.

    Every operation uses a short-lived connection and async calls are moved to
    a worker thread, so FastAPI's event loop is not blocked by sqlite3 calls.
    """

    backend_name = "sqlite"
    survives_process_restart = True

    def __init__(
        self,
        database_path: str | Path,
        *,
        serde: SerializerProtocol | None = None,
    ) -> None:
        super().__init__(
            serde=(
                serde
                if serde is not None
                else JsonPlusSerializer(
                    allowed_msgpack_modules=()
                )
            )
        )
        self.database_path = initialize_database(database_path)

    def _load_blobs(
        self,
        connection,
        *,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = connection.execute(
                """
                SELECT value_type, value
                FROM langgraph_blobs
                WHERE thread_id = ?
                  AND checkpoint_ns = ?
                  AND channel = ?
                  AND version = ?
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    channel,
                    _version_key(version),
                ),
            ).fetchone()
            if row is None or row["value_type"] == "empty":
                continue
            values[channel] = self.serde.loads_typed(
                (row["value_type"], bytes(row["value"]))
            )
        return values

    def _checkpoint_tuple(self, connection, row) -> CheckpointTuple:
        thread_id = row["thread_id"]
        checkpoint_ns = row["checkpoint_ns"]
        checkpoint_id = row["checkpoint_id"]
        checkpoint: Checkpoint = self.serde.loads_typed(
            (row["checkpoint_type"], bytes(row["checkpoint"]))
        )
        checkpoint = {
            **checkpoint,
            "channel_values": self._load_blobs(
                connection,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                versions=checkpoint["channel_versions"],
            ),
        }
        metadata = self.serde.loads_typed(
            (row["metadata_type"], bytes(row["metadata"]))
        )
        write_rows = connection.execute(
            """
            SELECT task_id, channel, value_type, value
            FROM langgraph_writes
            WHERE thread_id = ?
              AND checkpoint_ns = ?
              AND checkpoint_id = ?
            ORDER BY task_id, write_index
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        parent_id = row["parent_checkpoint_id"]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=[
                (
                    write["task_id"],
                    write["channel"],
                    self.serde.loads_typed(
                        (
                            write["value_type"],
                            bytes(write["value"]),
                        )
                    ),
                )
                for write in write_rows
            ],
        )

    def get_tuple(
        self,
        config: RunnableConfig,
    ) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        connection = connect_database(self.database_path)
        try:
            if checkpoint_id is None:
                row = connection.execute(
                    """
                    SELECT *
                    FROM langgraph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ?
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_ns),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                    FROM langgraph_checkpoints
                    WHERE thread_id = ?
                      AND checkpoint_ns = ?
                      AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
            if row is None:
                return None
            return self._checkpoint_tuple(connection, row)
        finally:
            connection.close()

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if limit is not None and limit <= 0:
            return

        where: list[str] = []
        parameters: list[object] = []
        if config is not None:
            configurable = config["configurable"]
            where.append("thread_id = ?")
            parameters.append(str(configurable["thread_id"]))
            if "checkpoint_ns" in configurable:
                where.append("checkpoint_ns = ?")
                parameters.append(
                    str(configurable.get("checkpoint_ns", ""))
                )
            if checkpoint_id := get_checkpoint_id(config):
                where.append("checkpoint_id = ?")
                parameters.append(checkpoint_id)
        if before is not None and (
            before_id := get_checkpoint_id(before)
        ):
            where.append("checkpoint_id < ?")
            parameters.append(before_id)

        query = "SELECT * FROM langgraph_checkpoints"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY checkpoint_id DESC"

        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(query, parameters).fetchall()
            yielded = 0
            for row in rows:
                item = self._checkpoint_tuple(connection, row)
                if filter and not all(
                    item.metadata.get(key) == value
                    for key, value in filter.items()
                ):
                    continue
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    break
        finally:
            connection.close()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        parent_checkpoint_id = configurable.get("checkpoint_id")
        checkpoint_copy = checkpoint.copy()
        values: dict[str, Any] = checkpoint_copy.pop(
            "channel_values"
        )  # type: ignore[misc]
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(
            checkpoint_copy
        )
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )

        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for channel, version in new_versions.items():
                value_type, value_blob = (
                    self.serde.dumps_typed(values[channel])
                    if channel in values
                    else ("empty", b"")
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO langgraph_blobs (
                        thread_id,
                        checkpoint_ns,
                        channel,
                        version,
                        value_type,
                        value
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        channel,
                        _version_key(version),
                        value_type,
                        value_blob,
                    ),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_checkpoints (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    parent_checkpoint_id,
                    checkpoint_type,
                    checkpoint,
                    metadata_type,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    parent_checkpoint_id,
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_type,
                    metadata_blob,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                value_type, value_blob = self.serde.dumps_typed(value)
                verb = (
                    "INSERT OR REPLACE"
                    if write_index < 0
                    else "INSERT OR IGNORE"
                )
                connection.execute(
                    f"""
                    {verb} INTO langgraph_writes (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_index,
                        channel,
                        value_type,
                        value,
                        task_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_index,
                        channel,
                        value_type,
                        value_blob,
                        task_path,
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_thread(self, thread_id: str) -> None:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "langgraph_writes",
                "langgraph_blobs",
                "langgraph_checkpoints",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE thread_id = ?",
                    (thread_id,),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def aget_tuple(
        self,
        config: RunnableConfig,
    ) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(
                self.list(
                    config,
                    filter=filter,
                    before=before,
                    limit=limit,
                )
            )
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    def get_next_version(
        self,
        current: str | int | None,
        channel: None,
    ) -> str:
        if current is None:
            current_value = 0
        elif isinstance(current, int):
            current_value = current
        else:
            current_value = int(current.split(".")[0])
        return (
            f"{current_value + 1:032}."
            f"{random.random():016}"
        )
