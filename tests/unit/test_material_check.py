from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.tools.material_check import (
    PolicyArticleCatalog,
    RequiredMaterialsChecker,
)
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


@pytest.fixture
def checker() -> RequiredMaterialsChecker:
    return RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)


def _check(
    checker: RequiredMaterialsChecker,
    user_input: str,
):
    return asyncio.run(checker.check(user_input))


def _requirements_by_type(answer) -> dict[str, object]:
    return {item.material_type: item for item in answer.result.required_materials}


def _missing_types(answer) -> set[str]:
    return {item.material_type for item in answer.result.missing_materials}


def test_lists_travel_reimbursement_requirements(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "出差报销需要准备哪些材料？",
    )

    assert answer.result.application_type is ApplicationType.TRAVEL_REIMBURSEMENT
    assert answer.result.mode is MaterialCheckMode.REQUIREMENTS
    assert answer.result.materials_complete is None
    assert len(answer.result.required_materials) == 7
    assert {item.material_type for item in answer.result.required_materials} == {
        "approved_travel_application",
        "travel_itinerary",
        "transportation_receipts",
        "accommodation_invoice",
        "hotel_detail",
        "payment_records",
        "business_trip_result",
    }
    assert [citation.article_label for citation in answer.result.citations] == ["第十六条"]
    assert "[S1]" in answer.reply


def test_compares_travel_materials_and_lists_missing_items(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "我准备了出差申请单、行程单和交通票据，帮我检查还缺什么。",
    )

    assert answer.result.mode is MaterialCheckMode.COMPARISON
    assert answer.result.materials_complete is False
    assert _missing_types(answer) == {
        "accommodation_invoice",
        "hotel_detail",
        "payment_records",
        "business_trip_result",
    }
    assert "住宿发票" in answer.reply


def test_complete_travel_materials_pass_comparison(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "出差报销材料我已有出差申请单、行程单、交通票据、"
        "住宿发票、住宿明细、支付记录和出差总结，齐全吗？",
    )

    assert answer.result.materials_complete is True
    assert answer.result.missing_materials == ()
    assert "已齐全" in answer.reply


def test_ambiguous_reimbursement_requests_application_type(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(checker, "报销需要哪些材料？")

    assert answer.result.application_type is None
    assert answer.result.clarification_question is not None
    assert "差旅报销" in answer.result.clarification_question
    assert answer.result.citations == ()


def test_purchase_recalculates_total_and_requires_two_quotes(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "采购三台显示器，每台2000元，需要哪些材料？",
    )
    requirements = _requirements_by_type(answer)

    assert answer.result.application_type is ApplicationType.PURCHASE
    assert answer.result.clarification_question is None
    assert requirements["quotation"].required_count == 2
    assert "technical_requirement" in requirements
    assert "it_review_opinion" in requirements
    assert "product_specification" in requirements


def test_purchase_over_fifty_thousand_requires_three_quotes_and_comparison(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额60000元的设备采购需要哪些材料？",
    )
    requirements = _requirements_by_type(answer)

    assert requirements["quotation"].required_count == 3
    assert "comparison_record" in requirements


def test_purchase_over_two_hundred_thousand_requires_tender_materials(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额250000元的服务采购需要哪些材料？",
    )
    requirements = _requirements_by_type(answer)

    assert "tender_document" in requirements
    assert "service_scope" in requirements
    assert "quotation" not in requirements


def test_purchase_without_amount_requests_amount(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "采购办公电脑需要准备哪些材料？",
    )

    assert answer.result.clarification_question is not None
    assert "预计采购总金额" in answer.result.clarification_question
    assert answer.result.materials_complete is None
    assert len(answer.result.citations) == 4


def test_purchase_comparison_counts_multiple_supplier_quotes(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "6000元电脑采购，我有两家供应商报价、技术需求说明、"
        "产品规格说明和IT评审意见，帮我检查是否齐全。",
    )

    assert answer.result.materials_complete is True
    quotation = next(
        item for item in answer.result.provided_materials if item.material_type == "quotation"
    )
    assert quotation.provided_count == 2


def test_purchase_comparison_reports_missing_quote_count(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "6000元电脑采购，我有一家报价、技术需求说明、产品规格说明和IT评审意见，还缺什么？",
    )
    quotation_gap = next(
        item for item in answer.result.missing_materials if item.material_type == "quotation"
    )

    assert answer.result.materials_complete is False
    assert quotation_gap.missing_count == 1


def test_training_expense_adds_category_specific_materials(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "培训费用报销需要哪些材料？",
    )
    requirements = _requirements_by_type(answer)

    assert answer.result.application_type is ApplicationType.EXPENSE_REIMBURSEMENT
    assert "expense_reimbursement_form" in requirements
    assert "training_notice" in requirements
    assert "registration_record" in requirements
    assert "completion_proof" in requirements
    assert {citation.article_label for citation in answer.result.citations} == {
        "第十三条",
        "第十五条",
    }


def test_one_day_sick_leave_does_not_require_medical_proof(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "请一天病假需要医院证明吗？",
    )

    assert answer.result.required_materials == ()
    assert answer.result.clarification_question is None
    assert "可以不提交医疗证明" in answer.reply


def test_four_day_sick_leave_marks_missing_medical_proof_sensitive(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "我需要请四天病假，目前还没有医院证明。",
    )

    assert answer.result.mode is MaterialCheckMode.COMPARISON
    assert answer.result.materials_complete is False
    assert len(answer.result.missing_materials) == 1
    missing = answer.result.missing_materials[0]
    assert missing.material_type == "medical_proof"
    assert missing.sensitive is True
    assert "诊断内容" not in answer.reply


def test_four_day_sick_leave_accepts_one_allowed_medical_document(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "请四天病假，我已经有门诊记录，材料齐全吗？",
    )

    assert answer.result.materials_complete is True
    assert answer.result.missing_materials == ()


def test_marriage_leave_requires_registration_certificate(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(checker, "申请婚假需要什么材料？")

    assert [item.material_type for item in answer.result.required_materials] == [
        "marriage_registration_certificate"
    ]
    assert answer.result.required_materials[0].sensitive is True


def test_generic_leave_request_asks_for_leave_type(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(checker, "请假需要准备哪些材料？")

    assert answer.result.clarification_question is not None
    assert "假期类型" in answer.result.clarification_question


def test_requirement_query_does_not_treat_mentioned_invoice_as_provided(
    checker: RequiredMaterialsChecker,
) -> None:
    answer = _check(
        checker,
        "费用报销需要发票吗，还需要哪些材料？",
    )

    assert answer.result.mode is MaterialCheckMode.REQUIREMENTS
    assert answer.result.provided_materials == ()
    assert answer.result.materials_complete is None


def test_fails_closed_when_rule_references_missing_policy_article() -> None:
    checker = RequiredMaterialsChecker(catalog=PolicyArticleCatalog([]))

    with pytest.raises(
        RuntimeError,
        match="material rule references missing policy article",
    ):
        _check(checker, "出差报销需要哪些材料？")


@pytest.mark.parametrize("user_input", ["", "   ", "\n"])
def test_rejects_blank_input(
    checker: RequiredMaterialsChecker,
    user_input: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        _check(checker, user_input)
