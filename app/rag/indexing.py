from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentLoaderRegistry,
)
from app.rag.embeddings import EmbeddingProvider
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.vector_index import VectorIndex, VectorIndexEntry, VectorRecord
from app.schemas.chunk import PolicyChunk

DEFAULT_INDEX_PIPELINE_VERSION = "policy-index-v1"
INDEX_OWNER = "enterprise-policy-document-indexer"
INDEX_FINGERPRINT_METADATA_KEY = "index_fingerprint"
INDEX_OWNER_METADATA_KEY = "index_owner"
INDEX_PIPELINE_VERSION_METADATA_KEY = "index_pipeline_version"
EMBEDDING_IDENTITY_METADATA_KEY = "embedding_identity"


class DocumentIndexingStatus(StrEnum):
    """Document-level outcome produced by one synchronization run."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class DocumentIndexingResult:
    document_id: str
    status: DocumentIndexingStatus
    fingerprint: str
    chunk_count: int
    upserted_chunk_count: int
    deleted_chunk_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "status": self.status.value,
            "fingerprint": self.fingerprint,
            "chunk_count": self.chunk_count,
            "upserted_chunk_count": self.upserted_chunk_count,
            "deleted_chunk_count": self.deleted_chunk_count,
        }


@dataclass(frozen=True, slots=True)
class DocumentIndexingReport:
    pipeline_version: str
    embedding_identity: str
    snapshot_sha256: str
    documents: tuple[DocumentIndexingResult, ...]
    total_chunk_count: int
    upserted_chunk_count: int
    deleted_chunk_count: int
    unchanged_chunk_count: int

    @property
    def changed(self) -> bool:
        return bool(self.upserted_chunk_count or self.deleted_chunk_count)

    def to_dict(self) -> dict[str, object]:
        status_counts = Counter(result.status.value for result in self.documents)
        return {
            "pipeline_version": self.pipeline_version,
            "embedding_identity": self.embedding_identity,
            "snapshot_sha256": self.snapshot_sha256,
            "changed": self.changed,
            "document_count": len(self.documents),
            "document_status_counts": {
                status.value: status_counts.get(status.value, 0)
                for status in DocumentIndexingStatus
            },
            "total_chunk_count": self.total_chunk_count,
            "upserted_chunk_count": self.upserted_chunk_count,
            "deleted_chunk_count": self.deleted_chunk_count,
            "unchanged_chunk_count": self.unchanged_chunk_count,
            "documents": [result.to_dict() for result in self.documents],
        }


@dataclass(frozen=True, slots=True)
class PolicyIndexingRun:
    chunks: tuple[PolicyChunk, ...]
    report: DocumentIndexingReport


@dataclass(frozen=True, slots=True)
class IndexSnapshotValidationReport:
    expected_chunk_count: int
    stored_record_count: int
    missing_record_ids: tuple[str, ...]
    unexpected_record_ids: tuple[str, ...]
    fingerprint_mismatch_ids: tuple[str, ...]
    expected_snapshot_sha256: str
    stored_snapshot_sha256: str

    @property
    def valid(self) -> bool:
        return not (
            self.missing_record_ids
            or self.unexpected_record_ids
            or self.fingerprint_mismatch_ids
            or self.expected_snapshot_sha256 != self.stored_snapshot_sha256
        )

    def require_valid(self) -> None:
        if self.valid:
            return
        raise RuntimeError(
            "published vector snapshot does not match current corpus: "
            f"missing={len(self.missing_record_ids)}, "
            f"unexpected={len(self.unexpected_record_ids)}, "
            f"fingerprint_mismatch={len(self.fingerprint_mismatch_ids)}"
        )


def build_record_metadata(chunk: PolicyChunk) -> dict[str, str]:
    """Build the citation, authorization and provenance metadata stored with a vector."""

    metadata = {
        "document_id": chunk.document_id,
        "document_type": chunk.document_type,
        "document_title": chunk.document_title,
        "document_version": chunk.document_version,
        "document_status": chunk.document_status.value,
        "issuing_department": chunk.issuing_department,
        "chapter_title": chunk.chapter_title,
        "article_label": chunk.article_label,
        "article_title": chunk.article_title,
        "source_path": str(chunk.source_path),
        "source_media_type": chunk.source_media_type,
        "source_line_start": str(chunk.source_line_start),
        "source_line_end": str(chunk.source_line_end),
        "security_level": chunk.security_level.value,
        "effective_date": chunk.effective_date.isoformat(),
        "allowed_departments": json.dumps(
            sorted(chunk.allowed_departments),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "allowed_roles": json.dumps(
            sorted(chunk.allowed_roles),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "content_hash": chunk.content_hash,
    }
    if chunk.expiry_date is not None:
        metadata["expiry_date"] = chunk.expiry_date.isoformat()
    if chunk.region is not None:
        metadata["region"] = chunk.region
    if chunk.metadata_source_path is not None:
        metadata["metadata_source_path"] = str(chunk.metadata_source_path)
    if chunk.source_page_start is not None and chunk.source_page_end is not None:
        metadata["source_page_start"] = str(chunk.source_page_start)
        metadata["source_page_end"] = str(chunk.source_page_end)
    if chunk.source_block_start is not None and chunk.source_block_end is not None:
        metadata["source_block_start"] = str(chunk.source_block_start)
        metadata["source_block_end"] = str(chunk.source_block_end)
    if chunk.source_ocr_applied:
        metadata["source_ocr_engine"] = chunk.source_ocr_engine or ""
        metadata["source_ocr_unit_kind"] = chunk.source_ocr_unit_kind or ""
        metadata["source_ocr_unit_numbers"] = ",".join(
            str(number) for number in chunk.source_ocr_unit_numbers
        )
        metadata["source_ocr_confidence_min"] = str(chunk.source_ocr_confidence_min)
    return metadata


def _stable_digest(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _chunk_fingerprint(
    chunk: PolicyChunk,
    *,
    metadata: dict[str, str],
    pipeline_version: str,
    embedding_identity: str,
) -> str:
    return _stable_digest(
        {
            "pipeline_version": pipeline_version,
            "embedding_identity": embedding_identity,
            "chunk_id": chunk.chunk_id,
            "retrieval_text": chunk.retrieval_text,
            "metadata": metadata,
        }
    )


def _document_fingerprint(
    document_id: str,
    entries: Sequence[tuple[str, str]],
) -> str:
    return _stable_digest(
        {
            "document_id": document_id,
            "chunks": sorted(entries),
        }
    )


def index_snapshot_sha256_from_entries(entries: Sequence[tuple[str, str]]) -> str:
    """Hash the complete sorted record/fingerprint manifest for release validation."""

    return _stable_digest({"records": sorted(entries)})


def policy_index_snapshot_sha256(
    chunks: Sequence[PolicyChunk],
    *,
    embedding_identity: str,
    pipeline_version: str = DEFAULT_INDEX_PIPELINE_VERSION,
) -> str:
    chunk_tuple = tuple(chunks)
    if not chunk_tuple:
        raise ValueError("chunks must not be empty")
    return index_snapshot_sha256_from_entries(
        [
            (
                chunk.chunk_id,
                _chunk_fingerprint(
                    chunk,
                    metadata=build_record_metadata(chunk),
                    pipeline_version=pipeline_version,
                    embedding_identity=embedding_identity,
                ),
            )
            for chunk in chunk_tuple
        ]
    )


def validate_policy_index_snapshot(
    chunks: Sequence[PolicyChunk],
    *,
    vector_index: VectorIndex,
    embedding_identity: str,
    pipeline_version: str = DEFAULT_INDEX_PIPELINE_VERSION,
) -> IndexSnapshotValidationReport:
    """Validate a published snapshot without embedding or mutating any records."""

    chunk_tuple = tuple(chunks)
    if not chunk_tuple:
        raise ValueError("chunks must not be empty")
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunk_tuple}
    if len(chunks_by_id) != len(chunk_tuple):
        raise ValueError("chunk_id values must be unique")
    normalized_identity = embedding_identity.strip()
    normalized_version = pipeline_version.strip()
    if not normalized_identity or not normalized_version:
        raise ValueError("embedding identity and pipeline version must not be blank")

    entries = vector_index.list_entries()
    entries_by_id = {entry.record_id: entry for entry in entries}
    if len(entries_by_id) != len(entries):
        raise RuntimeError("vector index returned duplicate record IDs")
    desired_fingerprints = {
        chunk.chunk_id: _chunk_fingerprint(
            chunk,
            metadata=build_record_metadata(chunk),
            pipeline_version=normalized_version,
            embedding_identity=normalized_identity,
        )
        for chunk in chunk_tuple
    }
    desired_ids = set(chunks_by_id)
    stored_ids = set(entries_by_id)
    common_ids = desired_ids.intersection(stored_ids)
    return IndexSnapshotValidationReport(
        expected_chunk_count=len(chunk_tuple),
        stored_record_count=len(entries),
        missing_record_ids=tuple(sorted(desired_ids.difference(stored_ids))),
        unexpected_record_ids=tuple(sorted(stored_ids.difference(desired_ids))),
        fingerprint_mismatch_ids=tuple(
            sorted(
                record_id
                for record_id in common_ids
                if entries_by_id[record_id].metadata.get(INDEX_FINGERPRINT_METADATA_KEY)
                != desired_fingerprints[record_id]
            )
        ),
        expected_snapshot_sha256=index_snapshot_sha256_from_entries(
            list(desired_fingerprints.items())
        ),
        stored_snapshot_sha256=index_snapshot_sha256_from_entries(
            [
                (
                    entry.record_id,
                    entry.metadata.get(INDEX_FINGERPRINT_METADATA_KEY, ""),
                )
                for entry in entries
            ]
        ),
    )


class PolicyDocumentIndexer:
    """Synchronize parsed policy chunks into the configured VectorIndex."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_index: VectorIndex,
        embedding_identity: str,
        pipeline_version: str = DEFAULT_INDEX_PIPELINE_VERSION,
    ) -> None:
        normalized_identity = embedding_identity.strip()
        normalized_version = pipeline_version.strip()
        if not normalized_identity:
            raise ValueError("embedding_identity must not be blank")
        if not normalized_version:
            raise ValueError("pipeline_version must not be blank")
        if embedding_provider.dimension != vector_index.dimension:
            raise ValueError(
                "vector index dimension does not match embedding provider: "
                f"{vector_index.dimension} != {embedding_provider.dimension}"
            )

        self._embedding_provider = embedding_provider
        self._vector_index = vector_index
        self._embedding_identity = normalized_identity
        self._pipeline_version = normalized_version

    def synchronize_directory(
        self,
        policy_directory: Path,
        *,
        loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
    ) -> PolicyIndexingRun:
        chunks = chunk_policy_directory(
            policy_directory,
            loader_registry=loader_registry,
        )
        return PolicyIndexingRun(
            chunks=tuple(chunks),
            report=self.synchronize(chunks),
        )

    def synchronize(self, chunks: Sequence[PolicyChunk]) -> DocumentIndexingReport:
        """Embed only changed chunks and atomically remove stale vector records."""

        chunk_list = list(chunks)
        if not chunk_list:
            raise ValueError("chunks must not be empty; refusing to clear the collection")
        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunk_list}
        if len(chunks_by_id) != len(chunk_list):
            raise ValueError("chunk_id values must be unique")

        current_entries = self._vector_index.list_entries()
        current_by_id = {entry.record_id: entry for entry in current_entries}
        if len(current_by_id) != len(current_entries):
            raise RuntimeError("vector index returned duplicate record IDs")

        base_metadata: dict[str, dict[str, str]] = {}
        desired_fingerprints: dict[str, str] = {}
        desired_by_document: dict[str, list[str]] = defaultdict(list)
        for chunk in chunk_list:
            metadata = build_record_metadata(chunk)
            base_metadata[chunk.chunk_id] = metadata
            desired_fingerprints[chunk.chunk_id] = _chunk_fingerprint(
                chunk,
                metadata=metadata,
                pipeline_version=self._pipeline_version,
                embedding_identity=self._embedding_identity,
            )
            desired_by_document[chunk.document_id].append(chunk.chunk_id)

        current_by_document: dict[str, list[VectorIndexEntry]] = defaultdict(list)
        for entry in current_entries:
            document_id = entry.metadata.get("document_id")
            if not document_id:
                document_id = f"__orphan__:{entry.record_id}"
            current_by_document[document_id].append(entry)

        changed_chunks = [
            chunk
            for chunk in chunk_list
            if current_by_id.get(chunk.chunk_id) is None
            or current_by_id[chunk.chunk_id].metadata.get(INDEX_FINGERPRINT_METADATA_KEY)
            != desired_fingerprints[chunk.chunk_id]
        ]
        stale_ids = tuple(sorted(set(current_by_id).difference(chunks_by_id)))

        vectors = (
            self._embedding_provider.embed_documents(
                [chunk.retrieval_text for chunk in changed_chunks]
            )
            if changed_chunks
            else []
        )
        if len(vectors) != len(changed_chunks):
            raise RuntimeError(
                "Embedding count does not match changed chunk count: "
                f"{len(vectors)} != {len(changed_chunks)}"
            )

        records: list[VectorRecord] = []
        for chunk, vector in zip(changed_chunks, vectors, strict=True):
            metadata = dict(base_metadata[chunk.chunk_id])
            metadata.update(
                {
                    INDEX_OWNER_METADATA_KEY: INDEX_OWNER,
                    INDEX_PIPELINE_VERSION_METADATA_KEY: self._pipeline_version,
                    EMBEDDING_IDENTITY_METADATA_KEY: self._embedding_identity,
                    INDEX_FINGERPRINT_METADATA_KEY: desired_fingerprints[chunk.chunk_id],
                }
            )
            records.append(
                VectorRecord(
                    record_id=chunk.chunk_id,
                    text=chunk.content,
                    vector=vector,
                    metadata=metadata,
                )
            )

        self._vector_index.apply_changes(
            records,
            delete_record_ids=stale_ids,
        )

        changed_ids = {chunk.chunk_id for chunk in changed_chunks}
        stale_id_set = set(stale_ids)
        document_results: list[DocumentIndexingResult] = []
        for document_id in sorted(desired_by_document):
            desired_ids = sorted(desired_by_document[document_id])
            desired_entries = [
                (chunk_id, desired_fingerprints[chunk_id]) for chunk_id in desired_ids
            ]
            desired_document_fingerprint = _document_fingerprint(
                document_id,
                desired_entries,
            )
            current_document_entries = current_by_document.get(document_id, [])
            current_ids = {entry.record_id for entry in current_document_entries}
            current_document_fingerprint = _document_fingerprint(
                document_id,
                [
                    (
                        entry.record_id,
                        entry.metadata.get(INDEX_FINGERPRINT_METADATA_KEY, ""),
                    )
                    for entry in current_document_entries
                ],
            )
            if not current_document_entries:
                status = DocumentIndexingStatus.ADDED
            elif (
                current_ids == set(desired_ids)
                and current_document_fingerprint == desired_document_fingerprint
            ):
                status = DocumentIndexingStatus.UNCHANGED
            else:
                status = DocumentIndexingStatus.UPDATED

            document_results.append(
                DocumentIndexingResult(
                    document_id=document_id,
                    status=status,
                    fingerprint=desired_document_fingerprint,
                    chunk_count=len(desired_ids),
                    upserted_chunk_count=len(changed_ids.intersection(desired_ids)),
                    deleted_chunk_count=len(stale_id_set.intersection(current_ids)),
                )
            )

        for document_id in sorted(set(current_by_document).difference(desired_by_document)):
            entries = current_by_document[document_id]
            document_results.append(
                DocumentIndexingResult(
                    document_id=document_id,
                    status=DocumentIndexingStatus.DELETED,
                    fingerprint=_document_fingerprint(
                        document_id,
                        [
                            (
                                entry.record_id,
                                entry.metadata.get(INDEX_FINGERPRINT_METADATA_KEY, ""),
                            )
                            for entry in entries
                        ],
                    ),
                    chunk_count=0,
                    upserted_chunk_count=0,
                    deleted_chunk_count=len(entries),
                )
            )

        return DocumentIndexingReport(
            pipeline_version=self._pipeline_version,
            embedding_identity=self._embedding_identity,
            snapshot_sha256=index_snapshot_sha256_from_entries(list(desired_fingerprints.items())),
            documents=tuple(document_results),
            total_chunk_count=len(chunk_list),
            upserted_chunk_count=len(records),
            deleted_chunk_count=len(stale_ids),
            unchanged_chunk_count=len(chunk_list) - len(records),
        )


__all__ = [
    "DEFAULT_INDEX_PIPELINE_VERSION",
    "DocumentIndexingReport",
    "DocumentIndexingResult",
    "DocumentIndexingStatus",
    "IndexSnapshotValidationReport",
    "PolicyDocumentIndexer",
    "PolicyIndexingRun",
    "build_record_metadata",
    "index_snapshot_sha256_from_entries",
    "policy_index_snapshot_sha256",
    "validate_policy_index_snapshot",
]
