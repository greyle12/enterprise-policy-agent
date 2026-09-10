"""Offline adapter for joining semantic records with confirmed request facets.

The adapter is deliberately independent from model inference, retrieval, Gate
selection, and the application runtime. It validates the versioned semantic
JSONL, the confirmed Facet sidecar, the user confirmation record, and the
provenance manifest before returning a joined in-memory view.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.semantic_parse_contract import (
    ContractDecodeError,
    SemanticParseRecord,
    Span,
    validate_record,
)

ADAPTER_SCHEMA_VERSION = "1.0"
CONFIRMED_STATUS = "user_confirmed"
FACET_EXPRESSION_STATUS = "EXPRESSED"
FACET_NAMES = frozenset(
    {
        "PROCEDURE",
        "MATERIAL",
        "DEADLINE",
        "RESPONSIBLE_ROLE",
        "PROCESS_CHOICE",
    }
)
BINDING_STATUSES = frozenset({"BOUND", "UNBOUND", "MISSING"})
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")


class FacetAdapterError(ValueError):
    """One deterministic adapter validation finding."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class ConfirmedRequestFacet:
    """One confirmed request information facet attached to a source query."""

    facet: str
    status: str
    binding: str
    source_spans: tuple[Span, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class ConfirmedRequestFacetCase:
    """One source record joined with its confirmed sidecar annotations."""

    case_id: str
    record_line: int
    query: str
    record: SemanticParseRecord
    accepted_facets: tuple[ConfirmedRequestFacet, ...]
    preserved_unresolved_output_types: tuple[str, ...]
    note: str

    @property
    def derived_binding_status(self) -> str:
        """Summarize the case without turning an empty Facet list into certainty."""

        if not self.accepted_facets:
            return "MISSING"
        if any(facet.binding == "UNBOUND" for facet in self.accepted_facets):
            return "UNBOUND"
        return "BOUND"


@dataclass(frozen=True, slots=True)
class SemanticRequestFacetBundle:
    """Validated, read-only joined view of the semantic and Facet datasets."""

    dataset_version: str
    source_dataset_version: str
    source_records_sha256: str
    source_proposal_sha256: str
    manifest_hashes_verified: bool
    records: tuple[SemanticParseRecord, ...]
    cases: tuple[ConfirmedRequestFacetCase, ...]
    allowed_facets: tuple[str, ...]
    binding_statuses: tuple[str, ...]

    @property
    def accepted_facet_instance_count(self) -> int:
        return sum(len(case.accepted_facets) for case in self.cases)

    def summary(self) -> dict[str, object]:
        return {
            "status": "passed",
            "schema_version": ADAPTER_SCHEMA_VERSION,
            "dataset_version": self.dataset_version,
            "source_dataset_version": self.source_dataset_version,
            "record_count": len(self.records),
            "facet_case_count": len(self.cases),
            "accepted_facet_instance_count": self.accepted_facet_instance_count,
            "partial_record_count": sum(
                record.status.value == "PARTIAL" for record in self.records
            ),
            "records_with_missing_outputs": sum(
                bool(record.missing_outputs) for record in self.records
            ),
            "allowed_facets": list(self.allowed_facets),
            "binding_statuses": list(self.binding_statuses),
            "source_records_sha256": self.source_records_sha256,
            "source_proposal_sha256": self.source_proposal_sha256,
            "manifest_hashes_verified": self.manifest_hashes_verified,
            "model_inference": False,
            "retrieval_executed": False,
            "parser_runtime_integration": False,
            "errors": [],
        }


@dataclass(frozen=True, slots=True)
class _RawFacet:
    facet: str
    status: str
    binding: str
    source_spans: tuple[Span, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class _RawCase:
    case_id: str
    record_line: int
    query: str
    accepted_facets: tuple[_RawFacet, ...]
    preserved_unresolved_output_types: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class _AcceptedPayload:
    schema_version: str
    dataset_version: str
    source_proposal_version: str
    source_dataset_version: str
    sample_count: int
    accepted_case_count: int
    accepted_facet_instance_count: int
    allowed_facets: tuple[str, ...]
    binding_statuses: tuple[str, ...]
    cases: tuple[_RawCase, ...]


@dataclass(frozen=True, slots=True)
class _ConfirmationMapping:
    case_id: str
    accepted_facet_labels: tuple[str, ...]
    binding_by_facet: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ConfirmationPayload:
    schema_version: str
    dataset_version: str
    source_proposal_version: str
    source_proposal_sha256: str
    source_dataset_version: str
    source_records_sha256: str
    accepted_case_count: int
    rejected_case_count: int
    pending_case_count: int
    confirmed_definition_count: int
    accepted_facet_instance_count: int
    case_ids_in_dataset_order: tuple[str, ...]
    accepted_mappings: tuple[_ConfirmationMapping, ...]


def _strict_object(value: object, path: str, allowed_fields: Sequence[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FacetAdapterError("OBJECT_TYPE", path, "value must be an object")
    allowed = set(allowed_fields)
    keys = set(value)
    unknown = keys - allowed
    if unknown:
        names = ", ".join(sorted(repr(name) for name in unknown))
        raise FacetAdapterError("UNKNOWN_FIELD", path, f"unknown fields: {names}")
    missing = allowed - keys
    if missing:
        names = ", ".join(sorted(repr(name) for name in missing))
        raise FacetAdapterError("MISSING_FIELD", path, f"required fields missing: {names}")
    return dict(value)


def _parse_string(value: object, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise FacetAdapterError("STRING_TYPE", path, "value must be a string")
    if non_empty and not value:
        raise FacetAdapterError("EMPTY_STRING", path, "value must not be empty")
    return value


def _parse_int(value: object, path: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FacetAdapterError("INTEGER_TYPE", path, "value must be an integer")
    if positive and value <= 0:
        raise FacetAdapterError("INTEGER_RANGE", path, "value must be positive")
    return value


def _parse_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise FacetAdapterError("BOOLEAN_TYPE", path, "value must be a boolean")
    return value


def _parse_array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise FacetAdapterError("ARRAY_TYPE", path, "value must be an array")
    return value


def _parse_string_array(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _parse_string(item, f"{path}[{index}]")
        for index, item in enumerate(_parse_array(value, path))
    )


def _parse_sha256(value: object, path: str) -> str:
    digest = _parse_string(value, path).lower()
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise FacetAdapterError(
            "SHA256_FORMAT", path, "value must be a 64-character SHA-256 digest"
        )
    return digest


def _read_json(path: Path, path_label: str) -> object:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", path_label, "file does not exist") from exc
    except UnicodeDecodeError as exc:
        raise FacetAdapterError("INPUT_ENCODING_ERROR", path_label, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise FacetAdapterError(
            "INVALID_JSON", f"{path_label}:{exc.lineno}:{exc.colno}", exc.msg
        ) from exc
    except OSError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", path_label, str(exc)) from exc


def _sha256_file(path: Path, path_label: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", path_label, "file does not exist") from exc
    except OSError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", path_label, str(exc)) from exc
    return digest.hexdigest()


def _validate_source_span(span: Span, query: str, path: str) -> None:
    if span.start < 0 or span.end > len(query):
        raise FacetAdapterError(
            "SPAN_OUT_OF_BOUNDS",
            path,
            f"span must be within query bounds [0, {len(query)})",
        )
    if span.start >= span.end:
        raise FacetAdapterError(
            "SPAN_EMPTY_OR_REVERSED",
            path,
            "span must use a non-empty half-open interval",
        )
    if query[span.start : span.end] != span.text:
        raise FacetAdapterError(
            "SPAN_TEXT_MISMATCH",
            f"{path}.text",
            "span text does not match the case query",
        )


def _parse_spans(value: object, query: str, path: str) -> tuple[Span, ...]:
    raw_spans = _parse_array(value, path)
    spans: list[Span] = []
    for index, raw_span in enumerate(raw_spans):
        span_path = f"{path}[{index}]"
        try:
            span = Span.from_dict(raw_span, path=span_path)
        except ContractDecodeError as exc:
            raise FacetAdapterError("SPAN_DECODE_ERROR", span_path, str(exc)) from exc
        _validate_source_span(span, query, span_path)
        spans.append(span)
    if not spans:
        raise FacetAdapterError(
            "EMPTY_SPANS", path, "a confirmed facet requires at least one source span"
        )
    return tuple(spans)


def _parse_allowed_facets(value: object, path: str) -> tuple[str, ...]:
    names: list[str] = []
    for index, raw_facet in enumerate(_parse_array(value, path)):
        item_path = f"{path}[{index}]"
        item = _strict_object(
            item := raw_facet, item_path, ("facet", "definition", "positive_cues", "exclusions")
        )
        name = _parse_string(item["facet"], f"{item_path}.facet", non_empty=True)
        if name not in FACET_NAMES:
            raise FacetAdapterError(
                "UNKNOWN_FACET", f"{item_path}.facet", f"unsupported facet {name!r}"
            )
        if name in names:
            raise FacetAdapterError(
                "DUPLICATE_FACET", f"{item_path}.facet", f"facet {name!r} is duplicated"
            )
        _parse_string(item["definition"], f"{item_path}.definition", non_empty=True)
        _parse_string_array(item["positive_cues"], f"{item_path}.positive_cues")
        _parse_string_array(item["exclusions"], f"{item_path}.exclusions")
        names.append(name)
    if set(names) != FACET_NAMES:
        raise FacetAdapterError(
            "FACET_VOCABULARY_MISMATCH",
            path,
            f"expected exactly {sorted(FACET_NAMES)}, got {sorted(names)}",
        )
    return tuple(names)


def _parse_binding_statuses(value: object, path: str) -> tuple[str, ...]:
    statuses: list[str] = []
    for index, raw_status in enumerate(_parse_array(value, path)):
        item_path = f"{path}[{index}]"
        item = _strict_object(raw_status, item_path, ("status", "meaning"))
        status = _parse_string(item["status"], f"{item_path}.status", non_empty=True)
        if status not in BINDING_STATUSES:
            raise FacetAdapterError(
                "UNKNOWN_BINDING", f"{item_path}.status", f"unsupported binding {status!r}"
            )
        if status in statuses:
            raise FacetAdapterError(
                "DUPLICATE_BINDING", f"{item_path}.status", f"binding {status!r} is duplicated"
            )
        _parse_string(item["meaning"], f"{item_path}.meaning", non_empty=True)
        statuses.append(status)
    if set(statuses) != BINDING_STATUSES:
        raise FacetAdapterError(
            "BINDING_VOCABULARY_MISMATCH",
            path,
            f"expected exactly {sorted(BINDING_STATUSES)}, got {sorted(statuses)}",
        )
    return tuple(statuses)


def _parse_accepted_facet(value: object, query: str, path: str) -> _RawFacet:
    item = _strict_object(
        value,
        path,
        ("facet", "status", "binding", "source_spans", "rationale"),
    )
    facet = _parse_string(item["facet"], f"{path}.facet", non_empty=True)
    if facet not in FACET_NAMES:
        raise FacetAdapterError("UNKNOWN_FACET", f"{path}.facet", f"unsupported facet {facet!r}")
    status = _parse_string(item["status"], f"{path}.status")
    if status != FACET_EXPRESSION_STATUS:
        raise FacetAdapterError(
            "UNKNOWN_FACET_STATUS", f"{path}.status", f"expected {FACET_EXPRESSION_STATUS!r}"
        )
    binding = _parse_string(item["binding"], f"{path}.binding")
    if binding not in {"BOUND", "UNBOUND"}:
        raise FacetAdapterError(
            "INVALID_FACET_BINDING",
            f"{path}.binding",
            f"expected BOUND or UNBOUND, got {binding!r}",
        )
    spans = _parse_spans(item["source_spans"], query, f"{path}.source_spans")
    rationale = _parse_string(item["rationale"], f"{path}.rationale", non_empty=True)
    return _RawFacet(facet, status, binding, spans, rationale)


def _parse_accepted(path: Path) -> _AcceptedPayload:
    payload = _strict_object(
        _read_json(path, "accepted"),
        "accepted",
        (
            "schema_version",
            "dataset_version",
            "status",
            "source_proposal_version",
            "source_dataset_version",
            "sample_count",
            "accepted_case_count",
            "accepted_facet_instance_count",
            "independent_test_set",
            "numeric_resume_ready",
            "model_inference",
            "retrieval_executed",
            "parser_runtime_integration",
            "allowed_facets",
            "binding_statuses",
            "cases",
        ),
    )
    schema_version = _parse_string(payload["schema_version"], "accepted.schema_version")
    if schema_version != ADAPTER_SCHEMA_VERSION:
        raise FacetAdapterError(
            "SCHEMA_VERSION",
            "accepted.schema_version",
            f"expected {ADAPTER_SCHEMA_VERSION}, got {schema_version}",
        )
    status = _parse_string(payload["status"], "accepted.status")
    if status != CONFIRMED_STATUS:
        raise FacetAdapterError(
            "STATUS_MISMATCH", "accepted.status", f"expected {CONFIRMED_STATUS!r}, got {status!r}"
        )
    source_proposal_version = _parse_string(
        payload["source_proposal_version"], "accepted.source_proposal_version", non_empty=True
    )
    source_dataset_version = _parse_string(
        payload["source_dataset_version"], "accepted.source_dataset_version", non_empty=True
    )
    allowed_facets = _parse_allowed_facets(payload["allowed_facets"], "accepted.allowed_facets")
    binding_statuses = _parse_binding_statuses(
        payload["binding_statuses"], "accepted.binding_statuses"
    )

    cases: list[_RawCase] = []
    seen_case_ids: set[str] = set()
    seen_record_lines: set[int] = set()
    for index, raw_case in enumerate(_parse_array(payload["cases"], "accepted.cases")):
        case_path = f"accepted.cases[{index}]"
        item = _strict_object(
            raw_case,
            case_path,
            (
                "case_id",
                "record_line",
                "query",
                "accepted_facets",
                "preserved_unresolved_output_types",
                "note",
            ),
        )
        case_id = _parse_string(item["case_id"], f"{case_path}.case_id", non_empty=True)
        if case_id in seen_case_ids:
            raise FacetAdapterError(
                "DUPLICATE_CASE_ID", f"{case_path}.case_id", f"case ID {case_id!r} is duplicated"
            )
        seen_case_ids.add(case_id)
        record_line = _parse_int(item["record_line"], f"{case_path}.record_line", positive=True)
        if record_line in seen_record_lines:
            raise FacetAdapterError(
                "DUPLICATE_RECORD_LINE",
                f"{case_path}.record_line",
                f"record line {record_line} is duplicated",
            )
        seen_record_lines.add(record_line)
        query = _parse_string(item["query"], f"{case_path}.query", non_empty=True)
        raw_facets: list[_RawFacet] = []
        seen_facets: set[str] = set()
        for facet_index, raw_facet in enumerate(
            _parse_array(item["accepted_facets"], f"{case_path}.accepted_facets")
        ):
            facet = _parse_accepted_facet(
                raw_facet, query, f"{case_path}.accepted_facets[{facet_index}]"
            )
            if facet.facet in seen_facets:
                raise FacetAdapterError(
                    "DUPLICATE_CASE_FACET",
                    f"{case_path}.accepted_facets[{facet_index}].facet",
                    f"facet {facet.facet!r} is duplicated",
                )
            seen_facets.add(facet.facet)
            raw_facets.append(facet)
        unresolved = _parse_string_array(
            item["preserved_unresolved_output_types"],
            f"{case_path}.preserved_unresolved_output_types",
        )
        note = _parse_string(item["note"], f"{case_path}.note", non_empty=True)
        cases.append(_RawCase(case_id, record_line, query, tuple(raw_facets), unresolved, note))

    sample_count = _parse_int(payload["sample_count"], "accepted.sample_count")
    accepted_case_count = _parse_int(payload["accepted_case_count"], "accepted.accepted_case_count")
    accepted_facet_instance_count = _parse_int(
        payload["accepted_facet_instance_count"],
        "accepted.accepted_facet_instance_count",
    )
    if sample_count != len(cases):
        raise FacetAdapterError(
            "COUNT_MISMATCH", "accepted.sample_count", f"expected {len(cases)}, got {sample_count}"
        )
    if accepted_case_count != len(cases):
        raise FacetAdapterError(
            "COUNT_MISMATCH",
            "accepted.accepted_case_count",
            f"expected {len(cases)}, got {accepted_case_count}",
        )
    if sum(len(case.accepted_facets) for case in cases) != accepted_facet_instance_count:
        raise FacetAdapterError(
            "COUNT_MISMATCH",
            "accepted.accepted_facet_instance_count",
            "count does not match accepted case facets",
        )
    for field in (
        "independent_test_set",
        "numeric_resume_ready",
        "model_inference",
        "retrieval_executed",
        "parser_runtime_integration",
    ):
        if _parse_bool(payload[field], f"accepted.{field}"):
            raise FacetAdapterError(
                "UNSAFE_METADATA",
                f"accepted.{field}",
                "confirmed development sidecar must remain offline",
            )
    return _AcceptedPayload(
        schema_version,
        _parse_string(payload["dataset_version"], "accepted.dataset_version", non_empty=True),
        source_proposal_version,
        source_dataset_version,
        sample_count,
        accepted_case_count,
        accepted_facet_instance_count,
        allowed_facets,
        binding_statuses,
        tuple(cases),
    )


def _parse_confirmation(path: Path) -> _ConfirmationPayload:
    payload = _strict_object(
        _read_json(path, "confirmation"),
        "confirmation",
        (
            "schema_version",
            "confirmation_version",
            "dataset_version",
            "source_proposal_version",
            "source_proposal_sha256",
            "source_dataset_version",
            "source_records_sha256",
            "status",
            "recorded_at",
            "confirmation_source",
            "confirmation_scope",
            "case_ids_in_dataset_order",
            "accepted_case_count",
            "rejected_case_count",
            "pending_case_count",
            "confirmed_definition_count",
            "accepted_facet_instance_count",
            "records_changed",
            "draft_preserved",
            "partial_records_remain_partial",
            "unresolved_outputs_preserved",
            "historical_labels_changed",
            "model_inference",
            "semantic_accuracy",
            "independent_double_annotation",
            "independent_test_set",
            "numeric_resume_ready",
            "retrieval_executed",
            "parser_runtime_integration",
            "accepted_mappings",
            "scope_boundary",
        ),
    )
    schema_version = _parse_string(payload["schema_version"], "confirmation.schema_version")
    if schema_version != ADAPTER_SCHEMA_VERSION:
        raise FacetAdapterError(
            "SCHEMA_VERSION",
            "confirmation.schema_version",
            f"expected {ADAPTER_SCHEMA_VERSION}, got {schema_version}",
        )
    status = _parse_string(payload["status"], "confirmation.status")
    if status != CONFIRMED_STATUS:
        raise FacetAdapterError(
            "STATUS_MISMATCH",
            "confirmation.status",
            f"expected {CONFIRMED_STATUS!r}, got {status!r}",
        )
    for field in ("confirmation_version", "recorded_at", "confirmation_source", "scope_boundary"):
        _parse_string(payload[field], f"confirmation.{field}", non_empty=True)
    for field in (
        "records_changed",
        "draft_preserved",
        "partial_records_remain_partial",
        "unresolved_outputs_preserved",
        "historical_labels_changed",
        "model_inference",
        "independent_double_annotation",
        "independent_test_set",
        "numeric_resume_ready",
        "retrieval_executed",
        "parser_runtime_integration",
    ):
        expected = field == "draft_preserved" or field in {
            "partial_records_remain_partial",
            "unresolved_outputs_preserved",
        }
        if _parse_bool(payload[field], f"confirmation.{field}") is not expected:
            raise FacetAdapterError(
                "UNSAFE_METADATA", f"confirmation.{field}", f"expected {expected}"
            )
    if payload["semantic_accuracy"] is not None:
        raise FacetAdapterError(
            "UNSAFE_METADATA",
            "confirmation.semantic_accuracy",
            "confirmed sidecar has no semantic accuracy result",
        )
    mappings: list[_ConfirmationMapping] = []
    seen_case_ids: set[str] = set()
    for index, raw_mapping in enumerate(
        _parse_array(payload["accepted_mappings"], "confirmation.accepted_mappings")
    ):
        mapping_path = f"confirmation.accepted_mappings[{index}]"
        item = _strict_object(
            raw_mapping, mapping_path, ("case_id", "accepted_facet_labels", "binding_by_facet")
        )
        case_id = _parse_string(item["case_id"], f"{mapping_path}.case_id", non_empty=True)
        if case_id in seen_case_ids:
            raise FacetAdapterError(
                "DUPLICATE_CASE_ID", f"{mapping_path}.case_id", f"case ID {case_id!r} is duplicated"
            )
        seen_case_ids.add(case_id)
        labels = _parse_string_array(
            item["accepted_facet_labels"], f"{mapping_path}.accepted_facet_labels"
        )
        if len(labels) != len(set(labels)):
            raise FacetAdapterError(
                "DUPLICATE_CASE_FACET",
                f"{mapping_path}.accepted_facet_labels",
                "facet labels must be unique",
            )
        for label_index, label in enumerate(labels):
            if label not in FACET_NAMES:
                raise FacetAdapterError(
                    "UNKNOWN_FACET",
                    f"{mapping_path}.accepted_facet_labels[{label_index}]",
                    f"unsupported facet {label!r}",
                )
        raw_bindings = item["binding_by_facet"]
        if not isinstance(raw_bindings, Mapping):
            raise FacetAdapterError(
                "OBJECT_TYPE", f"{mapping_path}.binding_by_facet", "value must be an object"
            )
        bindings: dict[str, str] = {}
        for label, binding in raw_bindings.items():
            if not isinstance(label, str):
                raise FacetAdapterError(
                    "STRING_TYPE", f"{mapping_path}.binding_by_facet", "facet keys must be strings"
                )
            if label not in labels:
                raise FacetAdapterError(
                    "MAPPING_MISMATCH",
                    f"{mapping_path}.binding_by_facet",
                    f"unexpected facet key {label!r}",
                )
            parsed_binding = _parse_string(binding, f"{mapping_path}.binding_by_facet.{label}")
            if parsed_binding not in {"BOUND", "UNBOUND"}:
                raise FacetAdapterError(
                    "INVALID_FACET_BINDING",
                    f"{mapping_path}.binding_by_facet.{label}",
                    f"expected BOUND or UNBOUND, got {parsed_binding!r}",
                )
            bindings[label] = parsed_binding
        if set(bindings) != set(labels):
            raise FacetAdapterError(
                "MAPPING_MISMATCH",
                f"{mapping_path}.binding_by_facet",
                "binding keys must equal accepted facet labels",
            )
        mappings.append(_ConfirmationMapping(case_id, labels, bindings))
    return _ConfirmationPayload(
        schema_version,
        _parse_string(payload["dataset_version"], "confirmation.dataset_version", non_empty=True),
        _parse_string(
            payload["source_proposal_version"],
            "confirmation.source_proposal_version",
            non_empty=True,
        ),
        _parse_sha256(payload["source_proposal_sha256"], "confirmation.source_proposal_sha256"),
        _parse_string(
            payload["source_dataset_version"], "confirmation.source_dataset_version", non_empty=True
        ),
        _parse_sha256(payload["source_records_sha256"], "confirmation.source_records_sha256"),
        _parse_int(payload["accepted_case_count"], "confirmation.accepted_case_count"),
        _parse_int(payload["rejected_case_count"], "confirmation.rejected_case_count"),
        _parse_int(payload["pending_case_count"], "confirmation.pending_case_count"),
        _parse_int(
            payload["confirmed_definition_count"], "confirmation.confirmed_definition_count"
        ),
        _parse_int(
            payload["accepted_facet_instance_count"], "confirmation.accepted_facet_instance_count"
        ),
        _parse_string_array(
            payload["case_ids_in_dataset_order"], "confirmation.case_ids_in_dataset_order"
        ),
        tuple(mappings),
    )


def _read_records(path: Path) -> dict[int, SemanticParseRecord]:
    records: dict[int, SemanticParseRecord] = {}
    try:
        handle = path.open("r", encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", "records", "file does not exist") from exc
    except UnicodeDecodeError as exc:
        raise FacetAdapterError("INPUT_ENCODING_ERROR", "records", str(exc)) from exc
    except OSError as exc:
        raise FacetAdapterError("INPUT_READ_ERROR", "records", str(exc)) from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise FacetAdapterError(
                    "INVALID_JSON", f"records.line[{line_number}]", exc.msg
                ) from exc
            try:
                record = SemanticParseRecord.from_dict(payload, path=f"records.line[{line_number}]")
            except ContractDecodeError as exc:
                raise FacetAdapterError(
                    "RECORD_CONTRACT_DECODE_ERROR", f"records.line[{line_number}]", str(exc)
                ) from exc
            issues = validate_record(record)
            if issues:
                issue = issues[0]
                raise FacetAdapterError(
                    f"RECORD_{issue.code}",
                    f"records.line[{line_number}].{issue.path}",
                    issue.message,
                )
            records[line_number] = record
    if not records:
        raise FacetAdapterError(
            "NO_RECORDS", "records", "input did not contain any non-blank records"
        )
    return records


def _safe_manifest_path(root: Path, raw_path: str, path: str) -> tuple[str, Path]:
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise FacetAdapterError(
            "UNSAFE_MANIFEST_PATH",
            path,
            "manifest paths must be relative and stay within project root",
        )
    resolved_root = root.resolve()
    resolved_path = (resolved_root / candidate).resolve()
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise FacetAdapterError(
            "UNSAFE_MANIFEST_PATH", path, "manifest path escapes project root"
        ) from exc
    return relative.as_posix(), resolved_path


def _verify_manifest(
    manifest_path: Path,
    project_root: Path,
    records_path: Path,
    accepted_path: Path,
    confirmation_path: Path,
    accepted: _AcceptedPayload,
    confirmation: _ConfirmationPayload,
) -> None:
    payload = _read_json(manifest_path, "manifest")
    if not isinstance(payload, Mapping):
        raise FacetAdapterError("OBJECT_TYPE", "manifest", "value must be an object")
    for field in (
        "dataset_version",
        "source_dataset_version",
        "status",
        "sample_count",
        "source_files",
        "deliverables",
    ):
        if field not in payload:
            raise FacetAdapterError(
                "MISSING_FIELD", "manifest", f"required field {field!r} is missing"
            )
    if payload["dataset_version"] != accepted.dataset_version:
        raise FacetAdapterError(
            "VERSION_MISMATCH",
            "manifest.dataset_version",
            "manifest does not describe accepted sidecar",
        )
    if payload["source_dataset_version"] != accepted.source_dataset_version:
        raise FacetAdapterError(
            "VERSION_MISMATCH",
            "manifest.source_dataset_version",
            "manifest does not describe source records",
        )
    if payload["status"] != CONFIRMED_STATUS or payload["sample_count"] != accepted.sample_count:
        raise FacetAdapterError(
            "MANIFEST_METADATA_MISMATCH",
            "manifest",
            "manifest status or sample count is inconsistent",
        )
    entries: dict[str, str] = {}
    for section in ("source_files", "deliverables"):
        section_path = f"manifest.{section}"
        for index, raw_entry in enumerate(_parse_array(payload[section], section_path)):
            entry_path = f"{section_path}[{index}]"
            item = _strict_object(raw_entry, entry_path, ("path", "sha256"))
            relative, resolved_path = _safe_manifest_path(
                project_root,
                _parse_string(item["path"], f"{entry_path}.path", non_empty=True),
                f"{entry_path}.path",
            )
            digest = _parse_sha256(item["sha256"], f"{entry_path}.sha256")
            previous = entries.get(relative)
            if previous is not None and previous != digest:
                raise FacetAdapterError(
                    "MANIFEST_DUPLICATE_PATH",
                    entry_path,
                    f"path {relative!r} has conflicting hashes",
                )
            entries[relative] = digest
            actual = _sha256_file(resolved_path, relative)
            if actual != digest:
                raise FacetAdapterError(
                    "MANIFEST_HASH_MISMATCH", relative, f"expected {digest}, got {actual}"
                )
    root = project_root.resolve()
    input_paths = {
        "records": records_path.resolve().relative_to(root).as_posix(),
        "accepted": accepted_path.resolve().relative_to(root).as_posix(),
        "confirmation": confirmation_path.resolve().relative_to(root).as_posix(),
    }
    for label, relative in input_paths.items():
        if relative not in entries:
            raise FacetAdapterError(
                "MANIFEST_INPUT_MISSING",
                f"manifest.{label}",
                f"{relative!r} is not listed in manifest",
            )
    proposal_paths = [
        path for path in entries if path.endswith("/semantic-request-facets-v1/facets.json")
    ]
    if len(proposal_paths) != 1:
        raise FacetAdapterError(
            "MANIFEST_PROPOSAL_MISSING",
            "manifest.source_files",
            "expected one semantic Facet proposal entry",
        )
    if entries[proposal_paths[0]] != confirmation.source_proposal_sha256:
        raise FacetAdapterError(
            "PROPOSAL_HASH_MISMATCH", proposal_paths[0], "proposal hash does not match confirmation"
        )


def load_semantic_request_facet_bundle(
    records_path: Path,
    accepted_path: Path,
    confirmation_path: Path,
    *,
    manifest_path: Path | None = None,
    project_root: Path | None = None,
) -> SemanticRequestFacetBundle:
    """Load and validate the confirmed semantic records plus Facet sidecar."""

    records_by_line = _read_records(records_path)
    accepted = _parse_accepted(accepted_path)
    confirmation = _parse_confirmation(confirmation_path)
    actual_records_hash = _sha256_file(records_path, "records")
    if actual_records_hash != confirmation.source_records_sha256:
        raise FacetAdapterError(
            "SOURCE_RECORDS_HASH_MISMATCH",
            "confirmation.source_records_sha256",
            f"expected {confirmation.source_records_sha256}, got {actual_records_hash}",
        )
    if accepted.dataset_version != confirmation.dataset_version:
        raise FacetAdapterError(
            "VERSION_MISMATCH", "dataset_version", "accepted and confirmation versions differ"
        )
    if accepted.source_proposal_version != confirmation.source_proposal_version:
        raise FacetAdapterError(
            "VERSION_MISMATCH",
            "source_proposal_version",
            "accepted and confirmation versions differ",
        )
    if accepted.source_dataset_version != confirmation.source_dataset_version:
        raise FacetAdapterError(
            "VERSION_MISMATCH",
            "source_dataset_version",
            "accepted and confirmation versions differ",
        )
    if accepted.sample_count != len(records_by_line):
        raise FacetAdapterError(
            "COUNT_MISMATCH", "accepted.sample_count", "accepted sample count differs from records"
        )
    if set(case.record_line for case in accepted.cases) != set(records_by_line):
        raise FacetAdapterError(
            "RECORD_LINE_COVERAGE_MISMATCH",
            "accepted.cases",
            "case record lines must cover every non-blank source record line exactly once",
        )
    if (
        confirmation.accepted_case_count != len(accepted.cases)
        or confirmation.rejected_case_count != 0
        or confirmation.pending_case_count != 0
    ):
        raise FacetAdapterError(
            "CONFIRMATION_COUNT_MISMATCH",
            "confirmation",
            "confirmation counts do not match the accepted case set",
        )
    if confirmation.confirmed_definition_count != len(accepted.allowed_facets):
        raise FacetAdapterError(
            "CONFIRMATION_COUNT_MISMATCH",
            "confirmation.confirmed_definition_count",
            "definition count does not match Facet vocabulary",
        )
    if confirmation.accepted_facet_instance_count != accepted.accepted_facet_instance_count:
        raise FacetAdapterError(
            "CONFIRMATION_COUNT_MISMATCH",
            "confirmation.accepted_facet_instance_count",
            "Facet instance count differs from accepted sidecar",
        )
    case_ids = tuple(case.case_id for case in accepted.cases)
    if confirmation.case_ids_in_dataset_order != case_ids:
        raise FacetAdapterError(
            "CONFIRMATION_CASE_ORDER_MISMATCH",
            "confirmation.case_ids_in_dataset_order",
            "case order does not match accepted sidecar",
        )
    if tuple(mapping.case_id for mapping in confirmation.accepted_mappings) != case_ids:
        raise FacetAdapterError(
            "CONFIRMATION_CASE_ORDER_MISMATCH",
            "confirmation.accepted_mappings",
            "mapping order does not match accepted sidecar",
        )

    joined_cases: list[ConfirmedRequestFacetCase] = []
    for index, raw_case in enumerate(accepted.cases):
        case_path = f"accepted.cases[{index}]"
        record = records_by_line[raw_case.record_line]
        if raw_case.query != record.query:
            raise FacetAdapterError(
                "QUERY_MISMATCH",
                f"{case_path}.query",
                "sidecar query does not equal source record query",
            )
        if record.status.value != "PARTIAL":
            raise FacetAdapterError(
                "RECORD_STATUS_MISMATCH",
                f"records.line[{raw_case.record_line}].status",
                "confirmed source records must remain PARTIAL",
            )
        missing_types = tuple(output.output_type for output in record.missing_outputs)
        if raw_case.preserved_unresolved_output_types != missing_types:
            raise FacetAdapterError(
                "MISSING_OUTPUTS_MISMATCH",
                f"{case_path}.preserved_unresolved_output_types",
                "sidecar unresolved output types do not equal source record missing_outputs",
            )
        mapping = confirmation.accepted_mappings[index]
        labels = tuple(facet.facet for facet in raw_case.accepted_facets)
        bindings = {facet.facet: facet.binding for facet in raw_case.accepted_facets}
        if (
            mapping.case_id != raw_case.case_id
            or mapping.accepted_facet_labels != labels
            or mapping.binding_by_facet != bindings
        ):
            raise FacetAdapterError(
                "CONFIRMATION_MAPPING_MISMATCH",
                f"confirmation.accepted_mappings[{index}]",
                "confirmation does not equal accepted case mapping",
            )
        joined_cases.append(
            ConfirmedRequestFacetCase(
                raw_case.case_id,
                raw_case.record_line,
                raw_case.query,
                record,
                tuple(
                    ConfirmedRequestFacet(
                        facet.facet,
                        facet.status,
                        facet.binding,
                        facet.source_spans,
                        facet.rationale,
                    )
                    for facet in raw_case.accepted_facets
                ),
                raw_case.preserved_unresolved_output_types,
                raw_case.note,
            )
        )

    if manifest_path is not None:
        root = project_root if project_root is not None else Path.cwd()
        _verify_manifest(
            manifest_path,
            root,
            records_path,
            accepted_path,
            confirmation_path,
            accepted,
            confirmation,
        )
    return SemanticRequestFacetBundle(
        accepted.dataset_version,
        accepted.source_dataset_version,
        confirmation.source_records_sha256,
        confirmation.source_proposal_sha256,
        manifest_path is not None,
        tuple(records_by_line.values()),
        tuple(joined_cases),
        accepted.allowed_facets,
        accepted.binding_statuses,
    )
