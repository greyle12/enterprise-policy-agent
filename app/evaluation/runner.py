from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Protocol

from pydantic import JsonValue

from app.agent.router import AgentRouter
from app.agent.workflow_models import AgentRouteResult, AgentWorkflowNode
from app.evaluation.models import (
    CaseDimensionResult,
    EvaluationAssertion,
    EvaluationCaseResult,
    EvaluationMetric,
    EvaluationMode,
    EvaluationReport,
    EvaluationThresholds,
    EvaluationTool,
    ExpectedCitation,
    GoldenApprovalCase,
    GoldenCase,
    GoldenMaterialCase,
    GoldenRoutingCase,
    MetricSummary,
)
from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import ApprovalCheckAnswer
from app.tools.material_models import MaterialCheckAnswer

_METRIC_ORDER = (
    EvaluationMetric.INTENT_ACCURACY,
    EvaluationMetric.TOOL_SELECTION_ACCURACY,
    EvaluationMetric.MATERIAL_CHECK_ACCURACY,
    EvaluationMetric.APPROVAL_ROUTE_ACCURACY,
    EvaluationMetric.CITATION_ACCURACY,
)
_ACTION_TOOL_BY_NODE = {
    AgentWorkflowNode.ANSWER_POLICY: EvaluationTool.SEARCH_POLICY,
    AgentWorkflowNode.CHECK_MATERIALS: EvaluationTool.CHECK_REQUIRED_MATERIALS,
    AgentWorkflowNode.CHECK_APPROVAL: EvaluationTool.CHECK_APPROVAL_ROUTE,
    AgentWorkflowNode.GENERATE_DRAFT: EvaluationTool.CREATE_APPLICATION_DRAFT,
    AgentWorkflowNode.REQUEST_CLARIFICATION: EvaluationTool.NONE,
}


class MaterialEvaluationTool(Protocol):
    async def check(self, user_input: str) -> MaterialCheckAnswer:
        """执行一次材料检查。"""

        ...


class ApprovalEvaluationTool(Protocol):
    async def check(self, user_input: str) -> ApprovalCheckAnswer:
        """执行一次审批路线判断。"""

        ...


def _assertion(
    name: str,
    *,
    expected: JsonValue,
    actual: JsonValue,
) -> EvaluationAssertion:
    return EvaluationAssertion(
        name=name,
        passed=expected == actual,
        expected=expected,
        actual=actual,
    )


def _dimension(
    metric: EvaluationMetric,
    assertions: Sequence[EvaluationAssertion],
) -> CaseDimensionResult:
    assertion_tuple = tuple(assertions)
    return CaseDimensionResult(
        metric=metric,
        passed=all(item.passed for item in assertion_tuple),
        assertions=assertion_tuple,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _expected_citation_pairs(
    citations: Sequence[ExpectedCitation],
) -> list[dict[str, str]]:
    return sorted(
        (
            {
                "document_title": item.document_title,
                "article_label": item.article_label,
            }
            for item in citations
        ),
        key=lambda item: (
            item["document_title"],
            item["article_label"],
        ),
    )


def _actual_citation_pairs(
    citations: Sequence[PolicyCitation],
) -> list[dict[str, str]]:
    return sorted(
        (
            {
                "document_title": item.document_title,
                "article_label": item.article_label,
            }
            for item in citations
        ),
        key=lambda item: (
            item["document_title"],
            item["article_label"],
        ),
    )


def _citation_dimension(
    actual: Sequence[PolicyCitation],
    expected: Sequence[ExpectedCitation],
) -> CaseDimensionResult:
    actual_source_ids = [item.source_id for item in actual]
    expected_source_ids = [
        f"S{index}" for index in range(1, len(actual) + 1)
    ]
    actual_chunk_ids = [item.chunk_id for item in actual]
    assertions = (
        _assertion(
            "citation_references",
            expected=_expected_citation_pairs(expected),
            actual=_actual_citation_pairs(actual),
        ),
        _assertion(
            "citation_source_ids",
            expected=expected_source_ids,
            actual=actual_source_ids,
        ),
        _assertion(
            "citation_chunk_ids_unique",
            expected=True,
            actual=(len(actual_chunk_ids) == len(set(actual_chunk_ids))),
        ),
    )
    return _dimension(EvaluationMetric.CITATION_ACCURACY, assertions)


def _selected_tool(result: AgentRouteResult) -> EvaluationTool:
    if result.workflow is None:
        raise RuntimeError("route result does not contain workflow trace")

    for step in result.workflow.steps:
        selected = _ACTION_TOOL_BY_NODE.get(step.node)
        if selected is not None:
            return selected

    raise RuntimeError("workflow trace does not contain a scored action node")


def _runtime_error_dimensions(
    metrics: Sequence[EvaluationMetric],
    exc: Exception,
) -> tuple[CaseDimensionResult, ...]:
    actual = {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    return tuple(
        _dimension(
            metric,
            (
                _assertion(
                    "runtime_execution",
                    expected="success",
                    actual=actual,
                ),
            ),
        )
        for metric in metrics
    )


class GoldenEvaluationRunner:
    """执行黄金用例、汇总五项准确率并应用质量门禁。"""

    def __init__(
        self,
        *,
        router: AgentRouter,
        material_checker: MaterialEvaluationTool,
        approval_checker: ApprovalEvaluationTool,
        evaluation_mode: EvaluationMode,
        intent_provider: str,
        dataset_sha256: str,
        thresholds: EvaluationThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] | None = None,
    ) -> None:
        self._router = router
        self._material_checker = material_checker
        self._approval_checker = approval_checker
        self._evaluation_mode = evaluation_mode
        self._intent_provider = intent_provider
        self._dataset_sha256 = dataset_sha256
        self._thresholds = thresholds or EvaluationThresholds()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timer = timer or perf_counter

    async def _evaluate_routing(
        self,
        case: GoldenRoutingCase,
    ) -> tuple[CaseDimensionResult, ...]:
        try:
            result = await self._router.route(
                case.query,
                session_id=f"golden-{case.case_id.lower()}",
            )
            intent = _dimension(
                EvaluationMetric.INTENT_ACCURACY,
                (
                    _assertion(
                        "intent",
                        expected=case.expected_intent.value,
                        actual=result.classification.intent.value,
                    ),
                ),
            )
            tool = _dimension(
                EvaluationMetric.TOOL_SELECTION_ACCURACY,
                (
                    _assertion(
                        "selected_tool",
                        expected=case.expected_tool.value,
                        actual=_selected_tool(result).value,
                    ),
                ),
            )
            return intent, tool
        except Exception as exc:  # noqa: BLE001 - 每条评测必须独立记录失败
            return _runtime_error_dimensions(
                (
                    EvaluationMetric.INTENT_ACCURACY,
                    EvaluationMetric.TOOL_SELECTION_ACCURACY,
                ),
                exc,
            )

    async def _evaluate_material(
        self,
        case: GoldenMaterialCase,
    ) -> tuple[CaseDimensionResult, ...]:
        try:
            answer = await self._material_checker.check(case.query)
            result = answer.result
            required = {
                item.material_type: item.required_count
                for item in result.required_materials
            }
            missing = {
                item.material_type: item.missing_count
                for item in result.missing_materials
            }
            sensitive = sorted(
                {
                    item.material_type
                    for item in result.required_materials
                    if item.sensitive
                }
                | {
                    item.material_type
                    for item in result.missing_materials
                    if item.sensitive
                }
            )
            assertions = (
                _assertion(
                    "application_type",
                    expected=case.expected_application_type.value,
                    actual=(
                        result.application_type.value
                        if result.application_type is not None
                        else None
                    ),
                ),
                _assertion(
                    "mode",
                    expected=case.expected_mode.value,
                    actual=result.mode.value,
                ),
                _assertion(
                    "required_materials",
                    expected=dict(case.expected_required_materials),
                    actual=required,
                ),
                _assertion(
                    "missing_materials",
                    expected=dict(case.expected_missing_materials),
                    actual=missing,
                ),
                _assertion(
                    "materials_complete",
                    expected=case.expected_materials_complete,
                    actual=result.materials_complete,
                ),
                _assertion(
                    "clarification_required",
                    expected=case.expected_clarification,
                    actual=(result.clarification_question is not None),
                ),
                _assertion(
                    "sensitive_materials",
                    expected=sorted(case.expected_sensitive_materials),
                    actual=sensitive,
                ),
            )
            return (
                _dimension(
                    EvaluationMetric.MATERIAL_CHECK_ACCURACY,
                    assertions,
                ),
                _citation_dimension(
                    result.citations,
                    case.expected_citations,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 每条评测必须独立记录失败
            return _runtime_error_dimensions(
                (
                    EvaluationMetric.MATERIAL_CHECK_ACCURACY,
                    EvaluationMetric.CITATION_ACCURACY,
                ),
                exc,
            )

    async def _evaluate_approval(
        self,
        case: GoldenApprovalCase,
    ) -> tuple[CaseDimensionResult, ...]:
        try:
            answer = await self._approval_checker.check(case.query)
            result = answer.result
            assertions = (
                _assertion(
                    "application_type",
                    expected=case.expected_application_type.value,
                    actual=(
                        result.application_type.value
                        if result.application_type is not None
                        else None
                    ),
                ),
                _assertion(
                    "approval_level",
                    expected=case.expected_approval_level.value,
                    actual=(
                        result.approval_level.value
                        if result.approval_level is not None
                        else None
                    ),
                ),
                _assertion(
                    "amount",
                    expected=_decimal_text(case.expected_amount),
                    actual=_decimal_text(result.amount),
                ),
                _assertion(
                    "leave_days",
                    expected=_decimal_text(case.expected_leave_days),
                    actual=_decimal_text(result.leave_days),
                ),
                _assertion(
                    "approvers",
                    expected=[item.value for item in case.expected_approvers],
                    actual=[item.approver.value for item in result.steps],
                ),
                _assertion(
                    "special_conditions",
                    expected=list(case.expected_special_conditions),
                    actual=list(result.special_conditions),
                ),
                _assertion(
                    "clarification_required",
                    expected=case.expected_clarification,
                    actual=(result.clarification_question is not None),
                ),
            )
            return (
                _dimension(
                    EvaluationMetric.APPROVAL_ROUTE_ACCURACY,
                    assertions,
                ),
                _citation_dimension(
                    result.citations,
                    case.expected_citations,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 每条评测必须独立记录失败
            return _runtime_error_dimensions(
                (
                    EvaluationMetric.APPROVAL_ROUTE_ACCURACY,
                    EvaluationMetric.CITATION_ACCURACY,
                ),
                exc,
            )

    async def _evaluate_case(
        self,
        case: GoldenCase,
    ) -> EvaluationCaseResult:
        started = self._timer()
        if isinstance(case, GoldenRoutingCase):
            dimensions = await self._evaluate_routing(case)
        elif isinstance(case, GoldenMaterialCase):
            dimensions = await self._evaluate_material(case)
        elif isinstance(case, GoldenApprovalCase):
            dimensions = await self._evaluate_approval(case)
        else:
            raise TypeError(f"unsupported golden case type: {type(case)!r}")

        duration_ms = max((self._timer() - started) * 1000, 0.0)
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            title=case.title,
            query=case.query,
            passed=all(item.passed for item in dimensions),
            duration_ms=round(duration_ms, 3),
            dimensions=dimensions,
        )

    def _metric_summaries(
        self,
        results: Sequence[EvaluationCaseResult],
    ) -> tuple[MetricSummary, ...]:
        summaries: list[MetricSummary] = []
        for metric in _METRIC_ORDER:
            dimensions = [
                dimension
                for case_result in results
                for dimension in case_result.dimensions
                if dimension.metric is metric
            ]
            total = len(dimensions)
            passed = sum(item.passed for item in dimensions)
            accuracy = passed / total if total else 0.0
            threshold = self._thresholds.for_metric(metric)
            summaries.append(
                MetricSummary(
                    metric=metric,
                    passed_cases=passed,
                    total_cases=total,
                    accuracy=accuracy,
                    threshold=threshold,
                    meets_threshold=(total > 0 and accuracy >= threshold),
                )
            )
        return tuple(summaries)

    async def run(
        self,
        cases: Sequence[GoldenCase],
    ) -> EvaluationReport:
        """按文件顺序执行全部用例，单条失败不会中止剩余评测。"""

        started = self._timer()
        results = tuple(
            [await self._evaluate_case(case) for case in cases]
        )
        metrics = self._metric_summaries(results)
        passed_cases = sum(item.passed for item in results)
        failed_case_ids = tuple(
            item.case_id for item in results if not item.passed
        )
        duration_ms = max((self._timer() - started) * 1000, 0.0)

        return EvaluationReport(
            evaluation_mode=self._evaluation_mode,
            intent_provider=self._intent_provider,
            live_intent_llm_calls=(
                self._evaluation_mode is EvaluationMode.LIVE
            ),
            generated_at=self._clock(),
            dataset_sha256=self._dataset_sha256,
            duration_ms=round(duration_ms, 3),
            total_cases=len(results),
            passed_cases=passed_cases,
            failed_cases=len(results) - passed_cases,
            quality_gate_passed=all(item.meets_threshold for item in metrics),
            metrics=metrics,
            failed_case_ids=failed_case_ids,
            case_results=results,
        )
