"""Conservative, structured intent decisions for coverage-rule experiments.

This module is deliberately independent from the frozen v1/v2 gates.  It
parses only the two existing coverage relations and returns an auditable
four-valued decision.  It does not read labels, retrieval output, case IDs, or
production configuration, and it is not wired into the runtime selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Iterable


CITY_RULE_ID = "city_category_for_lodging_table"
FINANCE_RULE_ID = "finance_review_for_overdue_expense"

TOPIC_CITY_CATEGORY = "city_category"
TOPIC_LODGING_AMOUNT = "lodging_amount"
TOPIC_ROOM_SHARING = "room_sharing"
TOPIC_EXPENSE_DEADLINE = "expense_deadline"
TOPIC_REASON_STATEMENT = "reason_statement"
TOPIC_APPROVAL = "approval"
TOPIC_FINANCE_REVIEW = "finance_review"


class RequestAct(StrEnum):
    ASK_REQUIREMENT = "ASK_REQUIREMENT"
    ASK_EXEMPTION = "ASK_EXEMPTION"
    EXCLUDE_TOPIC = "EXCLUDE_TOPIC"


class ConditionStatus(StrEnum):
    GIVEN = "GIVEN"
    QUESTIONED = "QUESTIONED"
    UNKNOWN = "UNKNOWN"
    CONFLICTED = "CONFLICTED"


class GateDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class Span:
    """A source-preserving half-open character span."""

    start: int
    end: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True, slots=True)
class QueryClause:
    text: str
    start: int
    end: int

    @property
    def span(self) -> Span:
        return Span(self.start, self.end, self.text)

    def as_dict(self) -> dict[str, object]:
        return {"text": self.text, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class QueryRequest:
    topic: str
    act: RequestAct
    clause_index: int
    span: Span
    evidence: str
    referent: str | None = None

    @property
    def source_spans(self) -> tuple[Span, ...]:
        return (self.span,)

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "act": self.act.value,
            "clause_index": self.clause_index,
            "span": self.span.as_dict(),
            "source_spans": [span.as_dict() for span in self.source_spans],
            "referent": self.referent,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class QueryCondition:
    key: str
    value: str | None
    status: ConditionStatus
    clause_index: int
    span: Span
    evidence: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "value": self.value,
            "status": self.status.value,
            "clause_index": self.clause_index,
            "span": self.span.as_dict(),
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class QueryAmbiguity:
    kind: str
    clause_index: int
    span: Span
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "clause_index": self.clause_index,
            "span": self.span.as_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class QueryIntent:
    query: str
    clauses: tuple[QueryClause, ...]
    requests: tuple[QueryRequest, ...]
    conditions: tuple[QueryCondition, ...]
    ambiguities: tuple[QueryAmbiguity, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "clauses": [clause.as_dict() for clause in self.clauses],
            "requests": [request.as_dict() for request in self.requests],
            "conditions": [condition.as_dict() for condition in self.conditions],
            "ambiguities": [ambiguity.as_dict() for ambiguity in self.ambiguities],
        }


@dataclass(frozen=True, slots=True)
class RuleRelation:
    rule_id: str
    source_topic: str
    target_topic: str


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: GateDecision
    reason_code: str
    evidence_spans: tuple[Span, ...] = ()
    unresolved_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "evidence_spans": [span.as_dict() for span in self.evidence_spans],
            "unresolved_reasons": list(self.unresolved_reasons),
        }


RELATIONS = {
    CITY_RULE_ID: RuleRelation(
        rule_id=CITY_RULE_ID,
        source_topic=TOPIC_LODGING_AMOUNT,
        target_topic=TOPIC_CITY_CATEGORY,
    ),
    FINANCE_RULE_ID: RuleRelation(
        rule_id=FINANCE_RULE_ID,
        source_topic=TOPIC_EXPENSE_DEADLINE,
        target_topic=TOPIC_FINANCE_REVIEW,
    ),
}


_TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    TOPIC_CITY_CATEGORY: (
        r"城市类别",
        r"城市分类",
        r"[一二三]类城市",
        r"算不算",
        r"哪类",
        r"哪些(?:地方|城市)",
    ),
    TOPIC_LODGING_AMOUNT: (
        r"住宿",
        r"酒店",
        r"每晚",
        r"限额",
        r"上限",
        r"多少钱",
        r"报多少",
        r"最高多少",
    ),
    TOPIC_ROOM_SHARING: (r"合住", r"同住", r"标准间"),
    TOPIC_EXPENSE_DEADLINE: (
        r"超时",
        r"超过[一二三四五六七八九十百0-9]+天",
        r"拖了[一二三四五六七八九十百0-9]+天",
        r"交了[一二三四五六七八九十百0-9]+天",
        r"拒收",
        r"不予受理",
        r"拒绝受理",
    ),
    TOPIC_REASON_STATEMENT: (r"原因说明", r"说明原因", r"迟交原因", r"写明.*原因", r"写好原因"),
    TOPIC_APPROVAL: (r"审批", r"审批人", r"审批流程", r"经过哪些人", r"继续.*办"),
    TOPIC_FINANCE_REVIEW: (r"财务复核", r"财务管理部复核", r"复核"),
}


def relation_for(rule_id: str) -> RuleRelation:
    """Return one of the frozen relations; reject invented rule IDs."""

    try:
        return RELATIONS[rule_id]
    except KeyError as exc:
        raise ValueError(f"unknown coverage relation: {rule_id}") from exc


def _span_for_match(clause: QueryClause, match: re.Match[str]) -> Span:
    start = clause.start + match.start()
    end = clause.start + match.end()
    return Span(start, end, clause.text[match.start() : match.end()])


def _split_clauses(query: str) -> tuple[QueryClause, ...]:
    separators = re.compile(r"[，,。！？!?；;：:]|但是|不过|但")
    clauses: list[QueryClause] = []
    raw_start = 0
    for match in separators.finditer(query):
        raw = query[raw_start : match.start()]
        left_trim = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if text:
            start = raw_start + left_trim
            clauses.append(QueryClause(text=text, start=start, end=start + len(text)))
        raw_start = match.end()
    raw = query[raw_start:]
    text = raw.strip()
    if text:
        start = raw_start + len(raw) - len(raw.lstrip())
        clauses.append(QueryClause(text=text, start=start, end=start + len(text)))
    if not clauses:
        raise ValueError("query must contain non-whitespace text")
    return tuple(clauses)


def _topic_match(clause: QueryClause, topic: str) -> re.Match[str] | None:
    for pattern in _TOPIC_PATTERNS[topic]:
        match = re.search(pattern, clause.text)
        if match:
            return match
    return None


def _has_question_or_request_language(text: str) -> bool:
    return bool(
        re.search(
            r"[？?吗]|是否|是不是|能否|可不可以|可以|需要|应该|多少|哪些|什么|怎么|只想|只问|只给|只告诉|要求|上限|限额|标准|经过",
            text,
        )
    )


def _is_positive_exemption(text: str) -> bool:
    return bool(re.search(r"不是不需要|并非不需要|不是要免掉|不想略过|不是要略过|不是不用", text))


def _is_ask_exemption(text: str) -> bool:
    return bool(
        re.search(
            r"(是否|是不是|能否|可不可以|吗|意味着).{0,16}(不用|无需|不需要|不必|免掉|免于|略过|直接报)|"
            r"(不用|无需|不需要|不必).{0,12}(审批|复核|流程|交财务)",
            text,
        )
    ) or bool(re.search(r"直接报", text))


def _scope_excludes_finance(text: str) -> bool:
    if _is_positive_exemption(text):
        return False
    return bool(
        re.search(
            r"不要展开.{0,8}(审批|复核|流程)|"
            r"不问.{0,8}(审批|复核|流程)|"
            r"不是问.{0,8}(审批|复核)|"
            r"不需要.{0,8}(审批|复核|流程)|"
            r"不用.{0,8}(审批|复核|流程)|"
            r"其他环节.{0,8}不讲|"
            r"只问.{0,8}(原因说明|迟交原因)",
            text,
        )
    )


def _request_act(text: str, topic: str) -> RequestAct | None:
    if _is_positive_exemption(text):
        return RequestAct.ASK_REQUIREMENT
    if _is_ask_exemption(text) and topic in {
        TOPIC_APPROVAL,
        TOPIC_FINANCE_REVIEW,
        TOPIC_REASON_STATEMENT,
        TOPIC_EXPENSE_DEADLINE,
    }:
        return RequestAct.ASK_EXEMPTION
    exclusion = bool(
        re.search(
            r"不问|不要展开|不需要|无需|不用|不必|不想略过|不想讲|不是问|只回答是否",
            text,
        )
    )
    if exclusion:
        return RequestAct.EXCLUDE_TOPIC
    if _has_question_or_request_language(text):
        return RequestAct.ASK_REQUIREMENT
    return None


def _append_request(
    requests: list[QueryRequest], clause: QueryClause, index: int, topic: str, match: re.Match[str]
) -> None:
    act = _request_act(clause.text, topic)
    if act is None:
        return
    span = _span_for_match(clause, match)
    candidate = QueryRequest(
        topic=topic,
        act=act,
        clause_index=index,
        span=span,
        evidence=clause.text,
        referent={
            TOPIC_CITY_CATEGORY: "destination_city_category",
            TOPIC_LODGING_AMOUNT: "lodging_expense",
            TOPIC_ROOM_SHARING: "room_sharing_arrangement",
            TOPIC_EXPENSE_DEADLINE: "expense_submission",
            TOPIC_REASON_STATEMENT: "overdue_reason",
            TOPIC_APPROVAL: "approval_requirement",
            TOPIC_FINANCE_REVIEW: "finance_review_requirement",
        }[topic],
    )
    if candidate not in requests:
        requests.append(candidate)


def _condition_for_city(clause: QueryClause, index: int) -> QueryCondition | None:
    given = re.search(
        r"(?:已确认|已确定|已经确认|已知|属于|是|核实|确认)[^。；，,]{0,12}?([一二三])类城市?",
        clause.text,
    )
    unknown = re.search(
        r"(?:类别|城市).{0,8}(?:还没查|未查|没查|未知|不清楚|待确认|未确定)", clause.text
    )
    questioned = re.search(r"算不算|属于哪类|哪一类|哪类|城市分类|哪些地方", clause.text)
    match = given or unknown or questioned
    if match is None:
        return None
    if given:
        value = given.group(1) + "类城市"
        status = ConditionStatus.GIVEN
    elif unknown:
        value = None
        status = ConditionStatus.UNKNOWN
    else:
        value = None
        status = ConditionStatus.QUESTIONED
    return QueryCondition(
        key="destination_city_category",
        value=value,
        status=status,
        clause_index=index,
        span=_span_for_match(clause, match),
        evidence=clause.text,
    )


def _condition_for_destination(clause: QueryClause, index: int) -> QueryCondition | None:
    match = re.search(r"北京|上海|广州|深圳|苏州|杭州|南京|宁波|厦门|青岛", clause.text)
    if not match:
        return None
    return QueryCondition(
        key="destination_city",
        value=match.group(0),
        status=ConditionStatus.GIVEN,
        clause_index=index,
        span=_span_for_match(clause, match),
        evidence=clause.text,
    )


def _collect_ambiguities(
    clauses: tuple[QueryClause, ...], requests: Iterable[QueryRequest]
) -> tuple[QueryAmbiguity, ...]:
    topic_by_clause: dict[int, set[str]] = {}
    for request in requests:
        topic_by_clause.setdefault(request.clause_index, set()).add(request.topic)
    ambiguities: list[QueryAmbiguity] = []
    for index, clause in enumerate(clauses):
        match = re.search(r"这种|这个|这(?:笔|种|个|里)?|该|其", clause.text)
        if not match:
            continue
        has_same_clause_topic = bool(topic_by_clause.get(index))
        has_prior_topic = any(topic_by_clause.get(i) for i in range(index))
        if not has_same_clause_topic and not has_prior_topic:
            ambiguities.append(
                QueryAmbiguity(
                    kind="unbound_reference",
                    clause_index=index,
                    span=_span_for_match(clause, match),
                    reason="指代没有可唯一绑定的前文主题",
                )
            )
    return tuple(ambiguities)


def parse_query(query: str) -> QueryIntent:
    """Parse a query without inventing facts or silently resolving ambiguity."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    clauses = _split_clauses(query)
    requests: list[QueryRequest] = []
    conditions: list[QueryCondition] = []
    for index, clause in enumerate(clauses):
        for topic in _TOPIC_PATTERNS:
            match = _topic_match(clause, topic)
            if match:
                _append_request(requests, clause, index, topic, match)
        if _scope_excludes_finance(clause.text):
            match = re.search(r"审批|复核|流程|原因说明|迟交原因|财务", clause.text)
            if match:
                span = _span_for_match(clause, match)
                request = QueryRequest(
                    topic=TOPIC_FINANCE_REVIEW,
                    act=RequestAct.EXCLUDE_TOPIC,
                    clause_index=index,
                    span=span,
                    evidence=clause.text,
                    referent="finance_review_requirement",
                )
                if request not in requests:
                    requests.append(request)
        city_condition = _condition_for_city(clause, index)
        if city_condition:
            conditions.append(city_condition)
        destination = _condition_for_destination(clause, index)
        if destination:
            conditions.append(destination)

    # A common Chinese form places the category value after a comma, e.g.
    # "类别已经核实，是二类".  Preserve both clauses in one condition rather
    # than treating the first fragment as missing data.
    for index, (current, following) in enumerate(zip(clauses, clauses[1:], strict=False)):
        if not re.search(r"(?:类别|城市).*(?:核实|确认|确定)$", current.text):
            continue
        value_match = re.fullmatch(r"是?([一二三])类(?:城市)?", following.text)
        if not value_match:
            continue
        conditions = [
            condition
            for condition in conditions
            if not (
                condition.key == "destination_city_category"
                and condition.clause_index in {index, index + 1}
            )
        ]
        conditions.append(
            QueryCondition(
                key="destination_city_category",
                value=value_match.group(1) + "类城市",
                status=ConditionStatus.GIVEN,
                clause_index=index,
                span=Span(current.start, following.end, query[current.start : following.end]),
                evidence=current.text + "，" + following.text,
            )
        )

    # "其他环节先不讲" is a query-wide scope declaration.  It does not
    # identify a new business fact, but it does exclude the finance-review
    # supplement when the query separately asks only about a reason/deadline.
    if re.search(r"其他环节.{0,8}不讲", query) and not any(
        request.topic == TOPIC_FINANCE_REVIEW and request.act is RequestAct.ASK_EXEMPTION
        for request in requests
    ):
        match = re.search(r"其他环节.{0,8}不讲", query)
        if match is None:
            raise ValueError("exemption wording disappeared while parsing")
        clause_index = next(
            index
            for index, clause in enumerate(clauses)
            if clause.start <= match.start() < clause.end
        )
        clause = clauses[clause_index]
        local = re.search(r"其他环节.{0,8}不讲", clause.text)
        if local is None:
            raise ValueError("scope wording disappeared while parsing")
        requests.append(
            QueryRequest(
                topic=TOPIC_FINANCE_REVIEW,
                act=RequestAct.EXCLUDE_TOPIC,
                clause_index=clause_index,
                span=_span_for_match(clause, local),
                evidence=clause.text,
                referent="finance_review_requirement",
            )
        )

    # A negative question about exemption is a requirement question, not a
    # topic exclusion.  Add an explicit finance request when the wording only
    # contains "directly submit" and has no finance-topic hit.
    if _is_ask_exemption(query) and not any(
        request.topic in {TOPIC_FINANCE_REVIEW, TOPIC_APPROVAL}
        and request.act is RequestAct.ASK_EXEMPTION
        for request in requests
    ):
        match = re.search(r"直接报|不用|无需|免掉|略过", query)
        if match:
            # Find the containing clause so offsets remain source-preserving.
            clause_index = next(
                index
                for index, clause in enumerate(clauses)
                if clause.start <= match.start() < clause.end
            )
            clause = clauses[clause_index]
            exemption_match = re.search(r"直接报|不用|无需|免掉|略过", clause.text)
            if exemption_match is None:
                raise ValueError("exemption wording disappeared while parsing")
            span = _span_for_match(clause, exemption_match)
            requests.append(
                QueryRequest(
                    topic=TOPIC_FINANCE_REVIEW,
                    act=RequestAct.ASK_EXEMPTION,
                    clause_index=clause_index,
                    span=span,
                    evidence=clause.text,
                    referent="finance_review_requirement",
                )
            )

    # Mark contradictory category assertions explicitly instead of selecting
    # the first one by order.
    category_conditions = [
        condition for condition in conditions if condition.key == "destination_city_category"
    ]
    if any(condition.status is ConditionStatus.GIVEN for condition in category_conditions) and any(
        condition.status is ConditionStatus.UNKNOWN for condition in category_conditions
    ):
        first = category_conditions[0]
        conditions = [
            condition for condition in conditions if condition.key != "destination_city_category"
        ]
        conditions.append(
            QueryCondition(
                key="destination_city_category",
                value=None,
                status=ConditionStatus.CONFLICTED,
                clause_index=first.clause_index,
                span=first.span,
                evidence="查询同时给出已知和未知城市类别",
            )
        )
    ambiguities = _collect_ambiguities(tuple(clauses), requests)
    return QueryIntent(
        query=query,
        clauses=tuple(clauses),
        requests=tuple(requests),
        conditions=tuple(conditions),
        ambiguities=ambiguities,
    )


def _spans(requests: Iterable[QueryRequest]) -> tuple[Span, ...]:
    return tuple(request.span for request in requests)


def _result(
    decision: GateDecision,
    reason_code: str,
    requests: Iterable[QueryRequest] = (),
    unresolved_reasons: Iterable[str] = (),
) -> DecisionResult:
    return DecisionResult(
        decision=decision,
        reason_code=reason_code,
        evidence_spans=_spans(requests),
        unresolved_reasons=tuple(unresolved_reasons),
    )


def _is_direct_city_target(intent: QueryIntent) -> bool:
    city_requests = [request for request in intent.requests if request.topic == TOPIC_CITY_CATEGORY]
    lodging_requests = [
        request for request in intent.requests if request.topic == TOPIC_LODGING_AMOUNT
    ]
    return (
        bool(city_requests)
        and not lodging_requests
        and bool(re.search(r"只列|名单|包括哪些|有哪些地方", intent.query))
    )


def _is_direct_finance_target(intent: QueryIntent) -> bool:
    finance_requests = [
        request
        for request in intent.requests
        if request.topic == TOPIC_FINANCE_REVIEW and request.act is RequestAct.ASK_REQUIREMENT
    ]
    return bool(finance_requests) and bool(re.search(r"哪些费用|什么费用|哪些.*复核", intent.query))


def decide_intent(intent: QueryIntent, relation: RuleRelation) -> DecisionResult:
    """Apply the confirmed four-valued contract to a parsed query."""

    relation_for(relation.rule_id)
    if relation.target_topic not in {
        TOPIC_CITY_CATEGORY,
        TOPIC_FINANCE_REVIEW,
    }:
        raise ValueError(f"unsupported relation target topic: {relation.target_topic}")

    relevant_conditions = [
        condition for condition in intent.conditions if condition.key == "destination_city_category"
    ]
    if any(condition.status is ConditionStatus.CONFLICTED for condition in relevant_conditions):
        return _result(
            GateDecision.UNRESOLVED,
            "CONFLICTING_CONDITION",
            unresolved_reasons=("城市类别同时被陈述为已知和未知",),
        )

    if relation.target_topic == TOPIC_CITY_CATEGORY:
        if _is_direct_city_target(intent):
            return _result(GateDecision.NOT_APPLICABLE, "TARGET_IS_PRIMARY_QUERY_EVIDENCE")
        city_requests = [
            request for request in intent.requests if request.topic == TOPIC_CITY_CATEGORY
        ]
        lodging_requests = [
            request for request in intent.requests if request.topic == TOPIC_LODGING_AMOUNT
        ]
        category_question = any(
            request.act is RequestAct.ASK_REQUIREMENT for request in city_requests
        )
        known = any(condition.status is ConditionStatus.GIVEN for condition in relevant_conditions)
        if intent.ambiguities and (city_requests or lodging_requests):
            return _result(
                GateDecision.UNRESOLVED,
                "UNBOUND_REFERENCE",
                unresolved_reasons=tuple(ambiguity.reason for ambiguity in intent.ambiguities),
            )
        if lodging_requests and not known:
            return _result(
                GateDecision.ALLOW, "LODGING_AMOUNT_NEEDS_CITY_CATEGORY", lodging_requests
            )
        if category_question:
            return _result(GateDecision.ALLOW, "CITY_CATEGORY_IS_QUESTIONED", city_requests)
        if lodging_requests and known:
            return _result(GateDecision.DENY, "CITY_CATEGORY_ALREADY_GIVEN", relevant_conditions)
        if any(
            request.topic == TOPIC_ROOM_SHARING and request.act is not RequestAct.EXCLUDE_TOPIC
            for request in intent.requests
        ):
            return _result(GateDecision.DENY, "ONLY_ROOM_SHARING_REQUESTED")
        if any(
            request.topic == TOPIC_CITY_CATEGORY and request.act is RequestAct.EXCLUDE_TOPIC
            for request in city_requests
        ):
            return _result(GateDecision.DENY, "CITY_CATEGORY_EXPLICITLY_EXCLUDED", city_requests)
        return _result(
            GateDecision.UNRESOLVED,
            "INSUFFICIENT_CITY_INTENT",
            unresolved_reasons=("没有可确认的城市分类或住宿金额需求",),
        )

    finance_requests = [
        request for request in intent.requests if request.topic == TOPIC_FINANCE_REVIEW
    ]
    approval_requests = [request for request in intent.requests if request.topic == TOPIC_APPROVAL]
    deadline_requests = [
        request for request in intent.requests if request.topic == TOPIC_EXPENSE_DEADLINE
    ]
    reason_requests = [
        request for request in intent.requests if request.topic == TOPIC_REASON_STATEMENT
    ]
    if _is_direct_finance_target(intent):
        return _result(
            GateDecision.NOT_APPLICABLE, "TARGET_IS_PRIMARY_QUERY_EVIDENCE", finance_requests
        )
    if intent.ambiguities and (finance_requests or approval_requests):
        return _result(
            GateDecision.UNRESOLVED,
            "UNBOUND_REFERENCE",
            unresolved_reasons=tuple(ambiguity.reason for ambiguity in intent.ambiguities),
        )
    positive_finance = [
        request
        for request in finance_requests + approval_requests
        if request.act in {RequestAct.ASK_REQUIREMENT, RequestAct.ASK_EXEMPTION}
    ]
    if positive_finance:
        return _result(GateDecision.ALLOW, "APPROVAL_OR_EXEMPTION_REQUIREMENT", positive_finance)
    excluded = [
        request
        for request in finance_requests + approval_requests
        if request.act is RequestAct.EXCLUDE_TOPIC
    ]
    if excluded:
        return _result(GateDecision.DENY, "FINANCE_REVIEW_EXPLICITLY_EXCLUDED", excluded)
    if deadline_requests or reason_requests:
        return _result(
            GateDecision.DENY,
            "ONLY_DEADLINE_OR_REASON_REQUESTED",
            deadline_requests + reason_requests,
        )
    return _result(
        GateDecision.UNRESOLVED,
        "INSUFFICIENT_FINANCE_INTENT",
        unresolved_reasons=("没有可确认的审批或财务复核需求",),
    )


def evaluate(query: str, rule_id: str) -> tuple[QueryIntent, DecisionResult]:
    """Convenience API used by small offline diagnostics and tests."""

    intent = parse_query(query)
    return intent, decide_intent(intent, relation_for(rule_id))


def main() -> int:
    """Print one auditable decision for manual PowerShell checks."""

    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule-id", choices=tuple(RELATIONS), required=True)
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    intent, result = evaluate(args.query, args.rule_id)
    print(
        json.dumps(
            {"intent": intent.as_dict(), "decision": result.as_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


__all__ = [
    "CITY_RULE_ID",
    "FINANCE_RULE_ID",
    "ConditionStatus",
    "DecisionResult",
    "GateDecision",
    "QueryAmbiguity",
    "QueryClause",
    "QueryCondition",
    "QueryIntent",
    "QueryRequest",
    "RequestAct",
    "RuleRelation",
    "Span",
    "TOPIC_APPROVAL",
    "TOPIC_CITY_CATEGORY",
    "TOPIC_EXPENSE_DEADLINE",
    "TOPIC_FINANCE_REVIEW",
    "TOPIC_LODGING_AMOUNT",
    "TOPIC_REASON_STATEMENT",
    "TOPIC_ROOM_SHARING",
    "decide_intent",
    "evaluate",
    "parse_query",
    "relation_for",
]


if __name__ == "__main__":
    raise SystemExit(main())
