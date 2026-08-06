from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.tools.approval_check import ApprovalRuleChecker
from app.tools.approval_models import (
    ApprovalApplicationType,
    ApprovalLevel,
    ApproverCode,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_CASES = (
    {
        "input": "采购三台显示器，每台2000元，需要走什么审批？",
        "application_type": ApprovalApplicationType.PURCHASE,
        "approval_level": ApprovalLevel.GENERAL_PURCHASE,
        "approvers": (
            ApproverCode.DIRECT_MANAGER,
            ApproverCode.DEPARTMENT_HEAD,
            ApproverCode.IT_DEPARTMENT,
            ApproverCode.PROCUREMENT_DEPARTMENT,
        ),
        "needs_clarification": False,
    },
    {
        "input": "紧急采购服务器，预计总金额60000元，需要谁批准？",
        "application_type": ApprovalApplicationType.PURCHASE,
        "approval_level": ApprovalLevel.EMERGENCY_PURCHASE,
        "approvers": (
            ApproverCode.DEPARTMENT_HEAD,
            ApproverCode.IT_DEPARTMENT,
            ApproverCode.PROCUREMENT_DEPARTMENT,
            ApproverCode.FINANCE_DEPARTMENT,
            ApproverCode.BUSINESS_VICE_PRESIDENT,
        ),
        "needs_clarification": False,
    },
    {
        "input": "出差预计总费用40000元，需要走什么审批？",
        "application_type": ApprovalApplicationType.TRAVEL,
        "approval_level": ApprovalLevel.LARGE_TRAVEL,
        "approvers": (
            ApproverCode.DIRECT_MANAGER,
            ApproverCode.DEPARTMENT_HEAD,
            ApproverCode.FINANCE_DEPARTMENT,
            ApproverCode.BUSINESS_VICE_PRESIDENT,
        ),
        "needs_clarification": False,
    },
    {
        "input": "800元业务招待费报销怎么审批？",
        "application_type": ApprovalApplicationType.EXPENSE_REIMBURSEMENT,
        "approval_level": ApprovalLevel.SMALL_EXPENSE,
        "approvers": (
            ApproverCode.DIRECT_MANAGER,
            ApproverCode.FINANCE_DEPARTMENT,
        ),
        "needs_clarification": False,
    },
    {
        "input": "请四天病假需要谁审批？",
        "application_type": ApprovalApplicationType.LEAVE,
        "approval_level": ApprovalLevel.EXTENDED_LEAVE,
        "approvers": (
            ApproverCode.DIRECT_MANAGER,
            ApproverCode.DEPARTMENT_HEAD,
            ApproverCode.HUMAN_RESOURCES,
        ),
        "needs_clarification": False,
    },
    {
        "input": "采购一台办公电脑需要走什么审批？",
        "application_type": ApprovalApplicationType.PURCHASE,
        "approval_level": None,
        "approvers": (),
        "needs_clarification": True,
    },
)


async def _main() -> None:
    checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    failures: list[str] = []

    for case in _CASES:
        answer = await checker.check(case["input"])
        result = answer.result
        approvers = tuple(step.approver for step in result.steps)
        needs_clarification = result.clarification_question is not None
        passed = (
            result.application_type is case["application_type"]
            and result.approval_level is case["approval_level"]
            and approvers == case["approvers"]
            and needs_clarification is case["needs_clarification"]
            and bool(result.citations)
        )

        print(
            json.dumps(
                {
                    "input": case["input"],
                    "application_type": result.application_type,
                    "approval_level": result.approval_level,
                    "amount": (str(result.amount) if result.amount is not None else None),
                    "leave_days": (
                        str(result.leave_days) if result.leave_days is not None else None
                    ),
                    "approvers": approvers,
                    "needs_clarification": needs_clarification,
                    "citations": [citation.source_id for citation in result.citations],
                    "passed": passed,
                },
                ensure_ascii=False,
            )
        )

        if not passed:
            failures.append(case["input"])

    if failures:
        raise RuntimeError("Approval check verification failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    asyncio.run(_main())
