"""Contract-only data structures for semantic parsing experiments.

This module deliberately contains no model invocation, network access, parser
implementation, or production runtime wiring. It defines the boundary that a
future semantic parser must satisfy and the offline validation rules for
serialized records.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

CONTRACT_SCHEMA_VERSION = "1.0"


class ParseStatus(StrEnum):
    """Completion state of one semantic parse record."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class NodeType(StrEnum):
    """The semantic role of a graph node."""

    USER_REQUEST = "USER_REQUEST"
    REPORTED_ASSERTION = "REPORTED_ASSERTION"
    ENTITY = "ENTITY"
    CONDITION = "CONDITION"


class SemanticAct(StrEnum):
    """Acts that are intentionally exposed by the first contract version."""

    VERIFY_ASSERTION = "VERIFY_ASSERTION"
    DENY_REQUEST = "DENY_REQUEST"
    REPORT_ASSERTION = "REPORT_ASSERTION"


class RelationType(StrEnum):
    """Allowed directed relations between semantic nodes."""

    TARGETS = "TARGETS"
    REFERS_TO = "REFERS_TO"
    SUPPORTS = "SUPPORTS"
    DENIES = "DENIES"
    SCOPED_BY = "SCOPED_BY"
    QUOTES = "QUOTES"


class ScopeType(StrEnum):
    """The scope represented by a scope object."""

    QUERY = "QUERY"
    USER_REQUEST = "USER_REQUEST"
    REPORTED_ASSERTION = "REPORTED_ASSERTION"
    NEGATION = "NEGATION"
    CONDITION = "CONDITION"
    UNKNOWN = "UNKNOWN"


class ContractDecodeError(ValueError):
    """Raised when a serialized payload cannot be decoded structurally."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One deterministic contract validation finding."""

    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


class ContractValidationError(ValueError):
    """Raised by assert_valid_record when a record is invalid."""

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        detail = "; ".join(
            f"{issue.code} at {issue.path}: {issue.message}" for issue in self.issues
        )
        super().__init__(detail or "semantic parse contract validation failed")


EnumT = TypeVar("EnumT", bound=StrEnum)


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractDecodeError(f"{path} must be an object")
    return value


def _required(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        raise ContractDecodeError(f"{path}.{key} is required")
    return mapping[key]


def _parse_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ContractDecodeError(f"{path} must be a string")
    return value


def _parse_enum(enum_type: type[EnumT], value: object, path: str) -> EnumT:
    if not isinstance(value, str):
        raise ContractDecodeError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ContractDecodeError(
            f"{path} has unsupported value {value!r}; expected one of {allowed}"
        ) from exc


def _parse_optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _parse_string(value, path)


def _parse_spans(value: object, path: str) -> tuple[Span, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractDecodeError(f"{path} must be an array")
    return tuple(Span.from_dict(item, path=f"{path}[{index}]") for index, item in enumerate(value))


def _parse_string_array(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractDecodeError(f"{path} must be an array")
    return tuple(_parse_string(item, path=f"{path}[{index}]") for index, item in enumerate(value))


def _enum_value(value: StrEnum | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, StrEnum):
        return value.value
    return value


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open source span whose text must match the original query."""

    start: int
    end: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_dict(cls, value: object, *, path: str = "span") -> Span:
        mapping = _require_mapping(value, path)
        start = _required(mapping, "start", path)
        end = _required(mapping, "end", path)
        text = _required(mapping, "text", path)
        if not isinstance(start, int) or isinstance(start, bool):
            raise ContractDecodeError(f"{path}.start must be an integer")
        if not isinstance(end, int) or isinstance(end, bool):
            raise ContractDecodeError(f"{path}.end must be an integer")
        return cls(start=start, end=end, text=_parse_string(text, f"{path}.text"))


@dataclass(frozen=True, slots=True)
class SemanticNode:
    """A span-backed semantic node.

    USER_REQUEST and REPORTED_ASSERTION are deliberately different node
    types. This prevents a reported or quoted statement from silently being
    treated as an instruction issued by the current user.
    """

    node_id: str
    node_type: NodeType
    text: str
    source_spans: tuple[Span, ...]
    act: SemanticAct | None = None
    scope_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "node_type": _enum_value(self.node_type),
            "text": self.text,
            "source_spans": [span.as_dict() for span in self.source_spans],
            "act": _enum_value(self.act),
            "scope_id": self.scope_id,
        }

    @classmethod
    def from_dict(cls, value: object, *, path: str = "node") -> SemanticNode:
        mapping = _require_mapping(value, path)
        raw_act = _required(mapping, "act", path)
        raw_scope_id = _required(mapping, "scope_id", path)
        return cls(
            node_id=_parse_string(_required(mapping, "node_id", path), f"{path}.node_id"),
            node_type=_parse_enum(
                NodeType,
                _required(mapping, "node_type", path),
                f"{path}.node_type",
            ),
            text=_parse_string(_required(mapping, "text", path), f"{path}.text"),
            source_spans=_parse_spans(
                _required(mapping, "source_spans", path),
                f"{path}.source_spans",
            ),
            act=(None if raw_act is None else _parse_enum(SemanticAct, raw_act, f"{path}.act")),
            scope_id=_parse_optional_string(raw_scope_id, f"{path}.scope_id"),
        )


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    """A directed, span-backed relation between two semantic nodes."""

    edge_id: str
    source: str
    target: str
    relation: RelationType
    source_spans: tuple[Span, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "relation": _enum_value(self.relation),
            "source_spans": [span.as_dict() for span in self.source_spans],
        }

    @classmethod
    def from_dict(cls, value: object, *, path: str = "edge") -> SemanticEdge:
        mapping = _require_mapping(value, path)
        return cls(
            edge_id=_parse_string(_required(mapping, "edge_id", path), f"{path}.edge_id"),
            source=_parse_string(_required(mapping, "source", path), f"{path}.source"),
            target=_parse_string(_required(mapping, "target", path), f"{path}.target"),
            relation=_parse_enum(
                RelationType,
                _required(mapping, "relation", path),
                f"{path}.relation",
            ),
            source_spans=_parse_spans(
                _required(mapping, "source_spans", path),
                f"{path}.source_spans",
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticScope:
    """A span-backed scope containing nodes and optionally a parent scope."""

    scope_id: str
    scope_type: ScopeType
    source_spans: tuple[Span, ...]
    node_ids: tuple[str, ...]
    parent_scope_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "scope_id": self.scope_id,
            "scope_type": _enum_value(self.scope_type),
            "source_spans": [span.as_dict() for span in self.source_spans],
            "node_ids": list(self.node_ids),
            "parent_scope_id": self.parent_scope_id,
        }

    @classmethod
    def from_dict(cls, value: object, *, path: str = "scope") -> SemanticScope:
        mapping = _require_mapping(value, path)
        raw_parent_scope_id = _required(mapping, "parent_scope_id", path)
        return cls(
            scope_id=_parse_string(_required(mapping, "scope_id", path), f"{path}.scope_id"),
            scope_type=_parse_enum(
                ScopeType,
                _required(mapping, "scope_type", path),
                f"{path}.scope_type",
            ),
            source_spans=_parse_spans(
                _required(mapping, "source_spans", path),
                f"{path}.source_spans",
            ),
            node_ids=_parse_string_array(
                _required(mapping, "node_ids", path),
                f"{path}.node_ids",
            ),
            parent_scope_id=_parse_optional_string(raw_parent_scope_id, f"{path}.parent_scope_id"),
        )


@dataclass(frozen=True, slots=True)
class MissingSemanticOutput:
    """A required semantic output that was unavailable or ambiguous.

    Missing outputs are data, not an invitation for the consumer to infer a
    value. They remain serialized in PARTIAL and FAILED records.
    """

    output_type: str
    reason: str
    source_spans: tuple[Span, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "output_type": self.output_type,
            "reason": self.reason,
            "source_spans": [span.as_dict() for span in self.source_spans],
        }

    @classmethod
    def from_dict(cls, value: object, *, path: str = "missing_output") -> MissingSemanticOutput:
        mapping = _require_mapping(value, path)
        return cls(
            output_type=_parse_string(
                _required(mapping, "output_type", path),
                f"{path}.output_type",
            ),
            reason=_parse_string(
                _required(mapping, "reason", path),
                f"{path}.reason",
            ),
            source_spans=_parse_spans(
                _required(mapping, "source_spans", path),
                f"{path}.source_spans",
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticParseRecord:
    """The versioned semantic parser boundary for one user query."""

    query: str
    status: ParseStatus
    nodes: tuple[SemanticNode, ...] = ()
    edges: tuple[SemanticEdge, ...] = ()
    scopes: tuple[SemanticScope, ...] = ()
    missing_outputs: tuple[MissingSemanticOutput, ...] = ()
    schema_version: str = CONTRACT_SCHEMA_VERSION
    parser_id: str | None = None
    parser_revision: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "query": self.query,
            "status": _enum_value(self.status),
            "parser_id": self.parser_id,
            "parser_revision": self.parser_revision,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "scopes": [scope.as_dict() for scope in self.scopes],
            "missing_outputs": [output.as_dict() for output in self.missing_outputs],
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, value: object, *, path: str = "record") -> SemanticParseRecord:
        mapping = _require_mapping(value, path)
        raw_nodes = _required(mapping, "nodes", path)
        raw_edges = _required(mapping, "edges", path)
        raw_scopes = _required(mapping, "scopes", path)
        raw_missing_outputs = _required(mapping, "missing_outputs", path)
        return cls(
            query=_parse_string(_required(mapping, "query", path), f"{path}.query"),
            status=_parse_enum(
                ParseStatus,
                _required(mapping, "status", path),
                f"{path}.status",
            ),
            nodes=tuple(
                SemanticNode.from_dict(item, path=f"{path}.nodes[{index}]")
                for index, item in enumerate(_parse_array(raw_nodes, f"{path}.nodes"))
            ),
            edges=tuple(
                SemanticEdge.from_dict(item, path=f"{path}.edges[{index}]")
                for index, item in enumerate(_parse_array(raw_edges, f"{path}.edges"))
            ),
            scopes=tuple(
                SemanticScope.from_dict(item, path=f"{path}.scopes[{index}]")
                for index, item in enumerate(_parse_array(raw_scopes, f"{path}.scopes"))
            ),
            missing_outputs=tuple(
                MissingSemanticOutput.from_dict(item, path=f"{path}.missing_outputs[{index}]")
                for index, item in enumerate(
                    _parse_array(raw_missing_outputs, f"{path}.missing_outputs")
                )
            ),
            schema_version=_parse_string(
                _required(mapping, "schema_version", path),
                f"{path}.schema_version",
            ),
            parser_id=_parse_optional_string(
                _required(mapping, "parser_id", path), f"{path}.parser_id"
            ),
            parser_revision=_parse_optional_string(
                _required(mapping, "parser_revision", path),
                f"{path}.parser_revision",
            ),
        )


def _parse_array(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractDecodeError(f"{path} must be an array")
    return value


def _as_sequence(value: object, path: str, issues: list[ValidationIssue]) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        issues.append(
            ValidationIssue(
                code="COLLECTION_TYPE",
                path=path,
                message="value must be an array-like sequence",
            )
        )
        return ()
    return tuple(value)


def _validate_span(
    span: object,
    query: str,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(span, Span):
        issues.append(
            ValidationIssue(
                code="SPAN_TYPE",
                path=path,
                message="value must be a Span",
            )
        )
        return
    if not isinstance(span.start, int) or isinstance(span.start, bool):
        issues.append(
            ValidationIssue(
                code="SPAN_START_TYPE",
                path=f"{path}.start",
                message="start must be an integer",
            )
        )
        return
    if not isinstance(span.end, int) or isinstance(span.end, bool):
        issues.append(
            ValidationIssue(
                code="SPAN_END_TYPE",
                path=f"{path}.end",
                message="end must be an integer",
            )
        )
        return
    if not isinstance(span.text, str):
        issues.append(
            ValidationIssue(
                code="SPAN_TEXT_TYPE",
                path=f"{path}.text",
                message="text must be a string",
            )
        )
        return
    if span.start < 0 or span.end > len(query):
        issues.append(
            ValidationIssue(
                code="SPAN_OUT_OF_BOUNDS",
                path=path,
                message=f"span must be within query bounds [0, {len(query)})",
            )
        )
    if span.start >= span.end:
        issues.append(
            ValidationIssue(
                code="SPAN_EMPTY_OR_REVERSED",
                path=path,
                message="span must use a non-empty half-open interval",
            )
        )
    if 0 <= span.start < span.end <= len(query):
        if query[span.start : span.end] != span.text:
            issues.append(
                ValidationIssue(
                    code="SPAN_TEXT_MISMATCH",
                    path=f"{path}.text",
                    message="span text does not match the original query",
                )
            )


def _find_cycle(adjacency: Mapping[str, Sequence[str]]) -> tuple[str, ...] | None:
    """Return one deterministic directed cycle, if one exists."""

    colors: dict[str, int] = {}
    stack: list[str] = []
    positions: dict[str, int] = {}

    def visit(node_id: str) -> tuple[str, ...] | None:
        colors[node_id] = 1
        positions[node_id] = len(stack)
        stack.append(node_id)
        for target_id in adjacency.get(node_id, ()):
            if target_id not in adjacency:
                continue
            target_color = colors.get(target_id, 0)
            if target_color == 0:
                cycle = visit(target_id)
                if cycle is not None:
                    return cycle
            elif target_color == 1:
                return tuple(stack[positions[target_id] :] + [target_id])
        stack.pop()
        positions.pop(node_id, None)
        colors[node_id] = 2
        return None

    for node_id in adjacency:
        if colors.get(node_id, 0) == 0:
            cycle = visit(node_id)
            if cycle is not None:
                return cycle
    return None


def validate_record(record: SemanticParseRecord) -> tuple[ValidationIssue, ...]:
    """Validate one decoded record without invoking a parser or model."""

    if not isinstance(record, SemanticParseRecord):
        raise TypeError("record must be a SemanticParseRecord")

    issues: list[ValidationIssue] = []
    query = record.query if isinstance(record.query, str) else ""

    if not isinstance(record.schema_version, str):
        issues.append(
            ValidationIssue(
                code="SCHEMA_VERSION_TYPE",
                path="schema_version",
                message="schema_version must be a string",
            )
        )
    elif record.schema_version != CONTRACT_SCHEMA_VERSION:
        issues.append(
            ValidationIssue(
                code="UNSUPPORTED_SCHEMA_VERSION",
                path="schema_version",
                message=(f"expected {CONTRACT_SCHEMA_VERSION}, got {record.schema_version}"),
            )
        )

    if not isinstance(record.query, str):
        issues.append(
            ValidationIssue(
                code="QUERY_TYPE",
                path="query",
                message="query must be a string",
            )
        )
    elif not record.query:
        issues.append(
            ValidationIssue(
                code="EMPTY_QUERY",
                path="query",
                message="query must not be empty",
            )
        )

    if not isinstance(record.status, ParseStatus):
        issues.append(
            ValidationIssue(
                code="STATUS_TYPE",
                path="status",
                message="status must be a ParseStatus value",
            )
        )

    for field_name in ("parser_id", "parser_revision"):
        value = getattr(record, field_name)
        if value is not None and not isinstance(value, str):
            issues.append(
                ValidationIssue(
                    code="PARSER_METADATA_TYPE",
                    path=field_name,
                    message=f"{field_name} must be a string or null",
                )
            )

    nodes = _as_sequence(record.nodes, "nodes", issues)
    edges = _as_sequence(record.edges, "edges", issues)
    scopes = _as_sequence(record.scopes, "scopes", issues)
    missing_outputs = _as_sequence(record.missing_outputs, "missing_outputs", issues)

    node_ids: set[str] = {
        node.node_id
        for node in nodes
        if isinstance(node, SemanticNode) and isinstance(node.node_id, str) and node.node_id
    }
    scope_ids: set[str] = {
        scope.scope_id
        for scope in scopes
        if isinstance(scope, SemanticScope) and isinstance(scope.scope_id, str) and scope.scope_id
    }
    edge_ids: set[str] = set()
    seen_node_ids: set[str] = set()
    seen_scope_ids: set[str] = set()

    for index, scope in enumerate(scopes):
        path = f"scopes[{index}]"
        if not isinstance(scope, SemanticScope):
            issues.append(
                ValidationIssue(
                    code="SCOPE_TYPE",
                    path=path,
                    message="value must be a SemanticScope",
                )
            )
            continue
        if not scope.scope_id:
            issues.append(
                ValidationIssue(
                    code="EMPTY_SCOPE_ID",
                    path=f"{path}.scope_id",
                    message="scope_id must not be empty",
                )
            )
        elif scope.scope_id in seen_scope_ids:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_SCOPE_ID",
                    path=f"{path}.scope_id",
                    message=f"scope_id {scope.scope_id!r} is duplicated",
                )
            )
        else:
            seen_scope_ids.add(scope.scope_id)
        if not isinstance(scope.scope_type, ScopeType):
            issues.append(
                ValidationIssue(
                    code="INVALID_SCOPE_TYPE",
                    path=f"{path}.scope_type",
                    message="scope_type must be a ScopeType value",
                )
            )
        if not scope.source_spans:
            issues.append(
                ValidationIssue(
                    code="SCOPE_SOURCE_SPAN_REQUIRED",
                    path=f"{path}.source_spans",
                    message="every scope must retain at least one source span",
                )
            )
        for span_index, span in enumerate(scope.source_spans):
            _validate_span(
                span,
                query,
                f"{path}.source_spans[{span_index}]",
                issues,
            )
        seen_scope_nodes: set[str] = set()
        for node_index, node_id in enumerate(scope.node_ids):
            node_path = f"{path}.node_ids[{node_index}]"
            if not node_id:
                issues.append(
                    ValidationIssue(
                        code="EMPTY_SCOPE_NODE_ID",
                        path=node_path,
                        message="scope node ids must not be empty",
                    )
                )
            elif node_id in seen_scope_nodes:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_SCOPE_NODE_ID",
                        path=node_path,
                        message=f"node id {node_id!r} is duplicated in the scope",
                    )
                )
            else:
                seen_scope_nodes.add(node_id)
            if node_id not in node_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_SCOPE_NODE",
                        path=node_path,
                        message=f"scope references unknown node {node_id!r}",
                    )
                )
        if scope.parent_scope_id == scope.scope_id:
            issues.append(
                ValidationIssue(
                    code="SCOPE_SELF_PARENT",
                    path=f"{path}.parent_scope_id",
                    message="a scope cannot be its own parent",
                )
            )
        elif scope.parent_scope_id is not None and scope.parent_scope_id not in scope_ids:
            issues.append(
                ValidationIssue(
                    code="DANGLING_SCOPE_PARENT",
                    path=f"{path}.parent_scope_id",
                    message=(f"scope references unknown parent {scope.parent_scope_id!r}"),
                )
            )

    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"
        if not isinstance(node, SemanticNode):
            issues.append(
                ValidationIssue(
                    code="NODE_TYPE",
                    path=path,
                    message="value must be a SemanticNode",
                )
            )
            continue
        if not node.node_id:
            issues.append(
                ValidationIssue(
                    code="EMPTY_NODE_ID",
                    path=f"{path}.node_id",
                    message="node_id must not be empty",
                )
            )
        elif node.node_id in seen_node_ids:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_NODE_ID",
                    path=f"{path}.node_id",
                    message=f"node_id {node.node_id!r} is duplicated",
                )
            )
        else:
            seen_node_ids.add(node.node_id)
        if not isinstance(node.node_type, NodeType):
            issues.append(
                ValidationIssue(
                    code="INVALID_NODE_TYPE",
                    path=f"{path}.node_type",
                    message="node_type must be a NodeType value",
                )
            )
        if not isinstance(node.text, str) or not node.text:
            issues.append(
                ValidationIssue(
                    code="NODE_TEXT_REQUIRED",
                    path=f"{path}.text",
                    message="node text must be a non-empty string",
                )
            )
        if not node.source_spans:
            issues.append(
                ValidationIssue(
                    code="NODE_SOURCE_SPAN_REQUIRED",
                    path=f"{path}.source_spans",
                    message="every node must retain at least one source span",
                )
            )
        for span_index, span in enumerate(node.source_spans):
            _validate_span(
                span,
                query,
                f"{path}.source_spans[{span_index}]",
                issues,
            )
        if node.act is not None and not isinstance(node.act, SemanticAct):
            issues.append(
                ValidationIssue(
                    code="INVALID_SEMANTIC_ACT",
                    path=f"{path}.act",
                    message="act must be a SemanticAct value or null",
                )
            )
        elif isinstance(node.node_type, NodeType):
            if (
                node.node_type
                in (
                    NodeType.USER_REQUEST,
                    NodeType.REPORTED_ASSERTION,
                )
                and node.act is None
            ):
                issues.append(
                    ValidationIssue(
                        code="SEMANTIC_ACT_REQUIRED",
                        path=f"{path}.act",
                        message=("USER_REQUEST and REPORTED_ASSERTION nodes must declare an act"),
                    )
                )
            elif (
                node.act
                in (
                    SemanticAct.VERIFY_ASSERTION,
                    SemanticAct.DENY_REQUEST,
                )
                and node.node_type != NodeType.USER_REQUEST
            ):
                issues.append(
                    ValidationIssue(
                        code="ACT_NODE_TYPE_MISMATCH",
                        path=f"{path}.act",
                        message=(f"{node.act.value} must target a USER_REQUEST node"),
                    )
                )
            elif (
                node.act == SemanticAct.REPORT_ASSERTION
                and node.node_type != NodeType.REPORTED_ASSERTION
            ):
                issues.append(
                    ValidationIssue(
                        code="ACT_NODE_TYPE_MISMATCH",
                        path=f"{path}.act",
                        message=("REPORT_ASSERTION must target a REPORTED_ASSERTION node"),
                    )
                )
        if node.scope_id is not None and not node.scope_id:
            issues.append(
                ValidationIssue(
                    code="EMPTY_NODE_SCOPE_ID",
                    path=f"{path}.scope_id",
                    message="scope_id must be null or a non-empty string",
                )
            )
        elif node.scope_id is not None and node.scope_id not in scope_ids:
            issues.append(
                ValidationIssue(
                    code="DANGLING_NODE_SCOPE",
                    path=f"{path}.scope_id",
                    message=f"node references unknown scope {node.scope_id!r}",
                )
            )

    for index, edge in enumerate(edges):
        path = f"edges[{index}]"
        if not isinstance(edge, SemanticEdge):
            issues.append(
                ValidationIssue(
                    code="EDGE_TYPE",
                    path=path,
                    message="value must be a SemanticEdge",
                )
            )
            continue
        if not edge.edge_id:
            issues.append(
                ValidationIssue(
                    code="EMPTY_EDGE_ID",
                    path=f"{path}.edge_id",
                    message="edge_id must not be empty",
                )
            )
        elif edge.edge_id in edge_ids:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_EDGE_ID",
                    path=f"{path}.edge_id",
                    message=f"edge_id {edge.edge_id!r} is duplicated",
                )
            )
        else:
            edge_ids.add(edge.edge_id)
        if edge.source not in node_ids:
            issues.append(
                ValidationIssue(
                    code="DANGLING_EDGE_SOURCE",
                    path=f"{path}.source",
                    message=f"edge references unknown source {edge.source!r}",
                )
            )
        if edge.target not in node_ids:
            issues.append(
                ValidationIssue(
                    code="DANGLING_EDGE_TARGET",
                    path=f"{path}.target",
                    message=f"edge references unknown target {edge.target!r}",
                )
            )
        if not isinstance(edge.relation, RelationType):
            issues.append(
                ValidationIssue(
                    code="INVALID_RELATION_TYPE",
                    path=f"{path}.relation",
                    message="relation must be a RelationType value",
                )
            )
        for span_index, span in enumerate(edge.source_spans):
            _validate_span(
                span,
                query,
                f"{path}.source_spans[{span_index}]",
                issues,
            )

    for index, missing_output in enumerate(missing_outputs):
        path = f"missing_outputs[{index}]"
        if not isinstance(missing_output, MissingSemanticOutput):
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUT_TYPE",
                    path=path,
                    message="value must be a MissingSemanticOutput",
                )
            )
            continue
        if not missing_output.output_type:
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUT_KIND_REQUIRED",
                    path=f"{path}.output_type",
                    message="output_type must not be empty",
                )
            )
        if not missing_output.reason:
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUT_REASON_REQUIRED",
                    path=f"{path}.reason",
                    message="reason must not be empty",
                )
            )
        for span_index, span in enumerate(missing_output.source_spans):
            _validate_span(
                span,
                query,
                f"{path}.source_spans[{span_index}]",
                issues,
            )

    if isinstance(record.status, ParseStatus):
        if record.status == ParseStatus.COMPLETE and missing_outputs:
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUT_STATUS_MISMATCH",
                    path="missing_outputs",
                    message="COMPLETE records cannot contain missing outputs",
                )
            )
        if record.status != ParseStatus.COMPLETE and not missing_outputs:
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUTS_REQUIRED",
                    path="missing_outputs",
                    message=(
                        "PARTIAL and FAILED records must preserve at least one missing output"
                    ),
                )
            )

    relation_graph: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if (
            isinstance(edge, SemanticEdge)
            and edge.source in node_ids
            and edge.target in node_ids
            and isinstance(edge.relation, RelationType)
        ):
            relation_graph[edge.source].append(edge.target)
    relation_cycle = _find_cycle(relation_graph)
    if relation_cycle is not None:
        issues.append(
            ValidationIssue(
                code="RELATION_CYCLE",
                path="edges",
                message=(f"directed relation cycle detected: {' -> '.join(relation_cycle)}"),
            )
        )

    scope_graph: dict[str, list[str]] = {scope_id: [] for scope_id in scope_ids}
    for scope in scopes:
        if (
            isinstance(scope, SemanticScope)
            and scope.scope_id in scope_ids
            and scope.parent_scope_id in scope_ids
        ):
            scope_graph[scope.scope_id].append(scope.parent_scope_id)
    scope_cycle = _find_cycle(scope_graph)
    if scope_cycle is not None:
        issues.append(
            ValidationIssue(
                code="SCOPE_CYCLE",
                path="scopes",
                message=f"scope parent cycle detected: {' -> '.join(scope_cycle)}",
            )
        )

    return tuple(issues)


def assert_valid_record(record: SemanticParseRecord) -> None:
    """Raise ContractValidationError unless the record is valid."""

    issues = validate_record(record)
    if issues:
        raise ContractValidationError(issues)


# Short aliases keep the contract vocabulary convenient for callers while
# preserving the explicit serialized class name.
SourceSpan = Span
MissingOutput = MissingSemanticOutput


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ContractDecodeError",
    "ContractValidationError",
    "MissingOutput",
    "MissingSemanticOutput",
    "NodeType",
    "ParseStatus",
    "RelationType",
    "SemanticAct",
    "SemanticEdge",
    "SemanticNode",
    "SemanticParseRecord",
    "SemanticScope",
    "SourceSpan",
    "ScopeType",
    "Span",
    "ValidationIssue",
    "assert_valid_record",
    "validate_record",
]
