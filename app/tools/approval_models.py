from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.rag.policy_context import PolicyCitation


class ApprovalApplicationType(StrEnum):
    """审批规则工具当前支持的业务类型。"""

    PURCHASE = "purchase"
    TRAVEL = "travel"
    EXPENSE_REIMBURSEMENT = "expense_reimbursement"
    LEAVE = "leave"


class ApprovalLevel(StrEnum):
    """由制度门槛计算得到的审批层级。"""

    SMALL_PURCHASE = "small_purchase"
    GENERAL_PURCHASE = "general_purchase"
    IMPORTANT_PURCHASE = "important_purchase"
    MAJOR_PURCHASE = "major_purchase"
    EMERGENCY_PURCHASE = "emergency_purchase"
    SMALL_TRAVEL = "small_travel"
    GENERAL_TRAVEL = "general_travel"
    LARGE_TRAVEL = "large_travel"
    TRAVEL_REIMBURSEMENT = "travel_reimbursement"
    SMALL_EXPENSE = "small_expense"
    GENERAL_EXPENSE = "general_expense"
    LARGE_EXPENSE = "large_expense"
    MAJOR_EXPENSE = "major_expense"
    SHORT_LEAVE = "short_leave"
    MEDIUM_LEAVE = "medium_leave"
    EXTENDED_LEAVE = "extended_leave"
    LONG_TERM_LEAVE = "long_term_leave"
    DEPARTMENT_HEAD_LEAVE = "department_head_leave"


class ApproverCode(StrEnum):
    """审批节点的稳定机器编码。"""

    DIRECT_MANAGER = "DIRECT_MANAGER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    IT_DEPARTMENT = "IT_DEPARTMENT"
    PROCUREMENT_DEPARTMENT = "PROCUREMENT_DEPARTMENT"
    FINANCE_DEPARTMENT = "FINANCE_DEPARTMENT"
    HUMAN_RESOURCES = "HUMAN_RESOURCES"
    BUSINESS_VICE_PRESIDENT = "BUSINESS_VICE_PRESIDENT"
    GENERAL_MANAGER = "GENERAL_MANAGER"
    MANAGEMENT_COMMITTEE = "MANAGEMENT_COMMITTEE"


class ApprovalAction(StrEnum):
    """审批节点要执行的动作。"""

    APPROVE = "approve"
    REVIEW = "review"
    TECHNICAL_REVIEW = "technical_review"
    CONFIRM = "confirm"
    DELIBERATE = "deliberate"


@dataclass(frozen=True, slots=True)
class ApprovalStep:
    """审批路线中的一个有序节点。"""

    sequence: int
    approver: ApproverCode
    display_name: str
    action: ApprovalAction
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalCheckResult:
    """一次确定性审批规则判断结果。"""

    application_type: ApprovalApplicationType | None
    approval_level: ApprovalLevel | None
    amount: Decimal | None
    leave_days: Decimal | None
    steps: tuple[ApprovalStep, ...]
    special_conditions: tuple[str, ...]
    clarification_question: str | None
    notes: tuple[str, ...]
    citations: tuple[PolicyCitation, ...]


@dataclass(frozen=True, slots=True)
class ApprovalCheckAnswer:
    """供 AgentRouter 使用的审批判断回答。"""

    request: str
    result: ApprovalCheckResult
    reply: str
