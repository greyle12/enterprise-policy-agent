from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.tools.approval_check import (
    ApprovalPolicyCatalog,
    ApprovalRuleChecker,
)
from app.tools.approval_models import (
    ApprovalApplicationType,
    ApprovalLevel,
    ApproverCode,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


@pytest.fixture
def checker() -> ApprovalRuleChecker:
    return ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)


def _check(
    checker: ApprovalRuleChecker,
    user_input: str,
):
    return asyncio.run(checker.check(user_input))


def _approvers(answer) -> list[ApproverCode]:
    return [item.approver for item in answer.result.steps]


def test_small_purchase_boundary_requires_direct_manager_only(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额5000元的办公用品采购需要谁审批？",
    )

    assert answer.result.application_type is ApprovalApplicationType.PURCHASE
    assert answer.result.approval_level is ApprovalLevel.SMALL_PURCHASE
    assert answer.result.amount == Decimal(5000)
    assert _approvers(answer) == [ApproverCode.DIRECT_MANAGER]
    assert [item.article_label for item in answer.result.citations] == ["第十一条"]


def test_purchase_recalculates_total_and_adds_it_review(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "采购三台显示器，每台2000元，需要走什么审批？",
    )

    assert answer.result.amount == Decimal(6000)
    assert answer.result.approval_level is ApprovalLevel.GENERAL_PURCHASE
    assert _approvers(answer) == [
        ApproverCode.DIRECT_MANAGER,
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.IT_DEPARTMENT,
        ApproverCode.PROCUREMENT_DEPARTMENT,
    ]
    assert "涉及信息技术类采购" in answer.result.special_conditions


def test_important_purchase_includes_finance_and_vice_president(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额60000元的设备采购要经过哪些审批？",
    )

    assert answer.result.approval_level is ApprovalLevel.IMPORTANT_PURCHASE
    assert _approvers(answer) == [
        ApproverCode.DIRECT_MANAGER,
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.PROCUREMENT_DEPARTMENT,
        ApproverCode.FINANCE_DEPARTMENT,
        ApproverCode.BUSINESS_VICE_PRESIDENT,
    ]


def test_major_purchase_includes_general_manager(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额250000元的服务采购由谁审批？",
    )

    assert answer.result.approval_level is ApprovalLevel.MAJOR_PURCHASE
    assert _approvers(answer)[-1] is ApproverCode.GENERAL_MANAGER


def test_purchase_over_one_million_adds_management_committee(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "预计总金额120万元的采购需要走什么审批？",
    )

    assert answer.result.amount == Decimal(1200000)
    assert _approvers(answer)[-1] is ApproverCode.MANAGEMENT_COMMITTEE
    assert "需提交公司管理委员会审议" in answer.result.special_conditions


def test_major_project_adds_management_committee_regardless_of_million_threshold(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "公司重大项目采购，预计总金额300000元，需要谁审批？",
    )

    assert _approvers(answer)[-1] is ApproverCode.MANAGEMENT_COMMITTEE


def test_emergency_it_purchase_uses_emergency_route(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "紧急采购服务器，预计总金额60000元，需要谁批准？",
    )

    assert answer.result.approval_level is ApprovalLevel.EMERGENCY_PURCHASE
    assert _approvers(answer) == [
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.IT_DEPARTMENT,
        ApproverCode.PROCUREMENT_DEPARTMENT,
        ApproverCode.FINANCE_DEPARTMENT,
        ApproverCode.BUSINESS_VICE_PRESIDENT,
    ]
    assert any("两个工作日" in note for note in answer.result.notes)


def test_purchase_without_amount_requests_total_amount(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "采购一台办公电脑需要走什么审批？",
    )

    assert answer.result.approval_level is None
    assert answer.result.steps == ()
    assert answer.result.clarification_question is not None
    assert "预计采购总金额" in answer.result.clarification_question
    assert len(answer.result.citations) == 5


@pytest.mark.parametrize(
    ("amount", "level", "expected_approvers"),
    [
        (
            "5000",
            ApprovalLevel.SMALL_TRAVEL,
            [ApproverCode.DIRECT_MANAGER],
        ),
        (
            "6000",
            ApprovalLevel.GENERAL_TRAVEL,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
            ],
        ),
        (
            "20001",
            ApprovalLevel.LARGE_TRAVEL,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.BUSINESS_VICE_PRESIDENT,
            ],
        ),
    ],
)
def test_travel_amount_boundaries(
    checker: ApprovalRuleChecker,
    amount: str,
    level: ApprovalLevel,
    expected_approvers: list[ApproverCode],
) -> None:
    answer = _check(
        checker,
        f"出差预计总费用{amount}元，需要谁审批？",
    )

    assert answer.result.approval_level is level
    assert _approvers(answer) == expected_approvers


def test_travel_over_thirty_thousand_adds_finance_review_before_vice_president(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "出差预计总费用40000元，需要走什么审批？",
    )

    assert _approvers(answer) == [
        ApproverCode.DIRECT_MANAGER,
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.FINANCE_DEPARTMENT,
        ApproverCode.BUSINESS_VICE_PRESIDENT,
    ]
    assert "预计总费用超过30,000元" in answer.result.special_conditions


def test_overseas_travel_adds_finance_review(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "去香港出差，预计总费用6000元，需要谁审批？",
    )

    assert _approvers(answer)[-1] is ApproverCode.FINANCE_DEPARTMENT
    assert "前往境外或港澳台地区" in answer.result.special_conditions


def test_long_travel_adds_finance_review(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "出差20天，预计总费用6000元，需要谁审批？",
    )

    assert _approvers(answer)[-1] is ApproverCode.FINANCE_DEPARTMENT
    assert "出差时间超过15个自然日" in answer.result.special_conditions


def test_travel_reimbursement_uses_fixed_review_flow_without_amount(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(checker, "出差报销要走什么审核流程？")

    assert answer.result.approval_level is ApprovalLevel.TRAVEL_REIMBURSEMENT
    assert answer.result.clarification_question is None
    assert _approvers(answer) == [
        ApproverCode.DIRECT_MANAGER,
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.FINANCE_DEPARTMENT,
    ]
    assert [item.article_label for item in answer.result.citations] == ["第二十条"]


@pytest.mark.parametrize(
    ("amount", "level", "expected_approvers"),
    [
        (
            "1000",
            ApprovalLevel.SMALL_EXPENSE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.FINANCE_DEPARTMENT,
            ],
        ),
        (
            "3000",
            ApprovalLevel.GENERAL_EXPENSE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.FINANCE_DEPARTMENT,
            ],
        ),
        (
            "6000",
            ApprovalLevel.LARGE_EXPENSE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.FINANCE_DEPARTMENT,
            ],
        ),
        (
            "120000",
            ApprovalLevel.MAJOR_EXPENSE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.FINANCE_DEPARTMENT,
                ApproverCode.BUSINESS_VICE_PRESIDENT,
                ApproverCode.GENERAL_MANAGER,
            ],
        ),
    ],
)
def test_expense_amount_boundaries(
    checker: ApprovalRuleChecker,
    amount: str,
    level: ApprovalLevel,
    expected_approvers: list[ApproverCode],
) -> None:
    answer = _check(
        checker,
        f"费用报销总金额{amount}元，需要谁审批？",
    )

    assert answer.result.approval_level is level
    assert _approvers(answer) == expected_approvers


def test_special_expense_requires_finance_review_without_duplicate_step(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "800元业务招待费报销怎么审批？",
    )

    assert _approvers(answer).count(ApproverCode.FINANCE_DEPARTMENT) == 1
    assert "业务招待费" in answer.result.special_conditions
    assert "该费用原则上需要事前审批" in answer.result.special_conditions
    assert {item.article_label for item in answer.result.citations} == {
        "第十一条",
        "第二十一条",
        "第二十五条",
    }


@pytest.mark.parametrize(
    ("days", "level", "expected_approvers"),
    [
        (
            "0.5",
            ApprovalLevel.SHORT_LEAVE,
            [ApproverCode.DIRECT_MANAGER],
        ),
        (
            "3",
            ApprovalLevel.MEDIUM_LEAVE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
            ],
        ),
        (
            "4",
            ApprovalLevel.EXTENDED_LEAVE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.HUMAN_RESOURCES,
            ],
        ),
        (
            "6",
            ApprovalLevel.LONG_TERM_LEAVE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.HUMAN_RESOURCES,
                ApproverCode.BUSINESS_VICE_PRESIDENT,
            ],
        ),
        (
            "16",
            ApprovalLevel.LONG_TERM_LEAVE,
            [
                ApproverCode.DIRECT_MANAGER,
                ApproverCode.DEPARTMENT_HEAD,
                ApproverCode.HUMAN_RESOURCES,
                ApproverCode.BUSINESS_VICE_PRESIDENT,
                ApproverCode.GENERAL_MANAGER,
            ],
        ),
    ],
)
def test_leave_day_boundaries(
    checker: ApprovalRuleChecker,
    days: str,
    level: ApprovalLevel,
    expected_approvers: list[ApproverCode],
) -> None:
    answer = _check(
        checker,
        f"请假{days}天需要谁审批？",
    )

    assert answer.result.approval_level is level
    assert _approvers(answer) == expected_approvers


def test_two_day_sick_leave_adds_hr_review(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(checker, "请两天病假需要谁审批？")

    assert _approvers(answer) == [
        ApproverCode.DIRECT_MANAGER,
        ApproverCode.DEPARTMENT_HEAD,
        ApproverCode.HUMAN_RESOURCES,
    ]
    assert "连续病假超过1个工作日" in answer.result.special_conditions


def test_department_head_leave_uses_separate_route(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(
        checker,
        "我是部门负责人，申请请假4天需要谁审批？",
    )

    assert answer.result.approval_level is ApprovalLevel.DEPARTMENT_HEAD_LEAVE
    assert _approvers(answer) == [
        ApproverCode.HUMAN_RESOURCES,
        ApproverCode.BUSINESS_VICE_PRESIDENT,
    ]


def test_leave_without_days_requests_working_days(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(checker, "申请年假需要谁审批？")

    assert answer.result.clarification_question is not None
    assert "请假工作日数" in answer.result.clarification_question
    assert answer.result.steps == ()


def test_ambiguous_reimbursement_requests_business_type(
    checker: ApprovalRuleChecker,
) -> None:
    answer = _check(checker, "这张报销单5000元需要谁审批？")

    assert answer.result.application_type is None
    assert answer.result.clarification_question is not None
    assert "差旅报销" in answer.result.clarification_question
    assert answer.result.citations == ()


def test_fails_closed_when_rule_references_missing_policy_article() -> None:
    checker = ApprovalRuleChecker(catalog=ApprovalPolicyCatalog([]))

    with pytest.raises(
        RuntimeError,
        match="approval rule references missing policy article",
    ):
        _check(checker, "预计总金额6000元的采购需要谁审批？")


@pytest.mark.parametrize("user_input", ["", "   ", "\n"])
def test_rejects_blank_input(
    checker: ApprovalRuleChecker,
    user_input: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        _check(checker, user_input)
