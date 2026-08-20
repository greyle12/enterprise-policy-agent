from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.tools.approval_check import ApprovalRuleChecker
from app.tools.approval_models import ApprovalApplicationType
from app.tools.draft_generation import (
    ApplicationDraftGenerator,
    DraftPolicyCatalog,
)
from app.tools.draft_models import (
    DraftStatus,
    DraftUserContext,
    ValidationSeverity,
)
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.material_models import ApplicationType

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_FIXED_TIME = datetime(2026, 8, 7, 10, 30, tzinfo=UTC)

_PURCHASE_COMPLETE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)

_LEAVE_COMPLETE = (
    "帮我生成请假申请草稿，我请3天年假，开始日期2026-08-10，"
    "结束日期2026-08-12，全天，请假原因为家庭事务，交接人为李明，"
    "紧急联系人为王芳，普通请假。"
)

_TRAVEL_COMPLETE = (
    "帮我生成差旅报销草稿，从苏州到上海出差，出差申请编号TRAVEL-001，"
    "开始日期2026-07-20，结束日期2026-07-22，出差事由为客户需求评审，"
    "项目名称为客户平台升级，报销总金额1662元，"
    "费用明细为交通费256元、住宿费920元和餐补486元，"
    "已准备出差申请单、行程单、交通票据、住宿发票、酒店住宿明细、"
    "支付记录和出差成果。"
)

_EXPENSE_COMPLETE = (
    "帮我生成800元业务招待费报销草稿，业务目的为客户项目沟通，"
    "发生日期2026-08-01，成本中心CC-SALES，收款对象为示例餐厅，"
    "不涉及合同，不涉及采购，已准备费用报销单、合规发票、支付记录、"
    "费用明细、业务事由说明、事前审批记录、预算编号、招待对象、"
    "参与人员和餐饮发票。"
)


def _user_context() -> DraftUserContext:
    return DraftUserContext(
        employee_id="DEMO-EMP-001",
        employee_name="演示用户",
        department="产品研发部",
        roles=("EMPLOYEE",),
        region="中国大陆",
        identity_source="demo_authenticated_context",
    )


def _generator() -> ApplicationDraftGenerator:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    return ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=_user_context(),
        clock=lambda: _FIXED_TIME,
        session_id="TEST-SESSION",
    )


def _field_values(draft: object) -> dict[str, object]:
    return {item.field_name: item.value for item in draft.fields}  # type: ignore[attr-defined]


def test_rejects_blank_input() -> None:
    with pytest.raises(ValueError, match="user_input must not be blank"):
        asyncio.run(_generator().generate("   "))


def test_unknown_application_type_requests_clarification() -> None:
    answer = asyncio.run(_generator().generate("帮我生成一个申请草稿"))

    assert answer.result.application_type is None
    assert answer.result.draft is None
    assert "采购" in answer.reply
    assert answer.result.clarification_question is not None
    assert answer.result.citations == ()


def test_incomplete_purchase_returns_partial_draft() -> None:
    answer = asyncio.run(_generator().generate("帮我生成采购申请草稿"))
    draft = answer.result.draft

    assert draft is not None
    assert draft.status is DraftStatus.WAITING_FOR_INFORMATION
    assert draft.ready_for_confirmation is False
    assert draft.user_confirmed is False
    assert draft.submitted is False
    assert {item.field_name for item in draft.missing_fields} >= {
        "item_name",
        "quantity",
        "estimated_total_amount",
        "budget_or_cost_center",
    }
    assert answer.result.clarification_question is not None
    assert answer.result.citations


def test_complete_purchase_calculates_total_and_is_ready() -> None:
    answer = asyncio.run(_generator().generate(_PURCHASE_COMPLETE))
    draft = answer.result.draft

    assert draft is not None
    values = _field_values(draft)
    assert draft.application_type is ApplicationType.PURCHASE
    assert draft.status is DraftStatus.WAITING_FOR_CONFIRMATION
    assert draft.ready_for_confirmation is True
    assert values["estimated_unit_price"] == Decimal(2000)
    assert values["estimated_total_amount"] == Decimal(6000)
    assert draft.material_check.materials_complete is True
    assert draft.approval_check.amount == Decimal(6000)
    assert answer.result.clarification_question is None


def test_purchase_total_mismatch_blocks_confirmation() -> None:
    user_input = _PURCHASE_COMPLETE.replace(
        "预算编号RD-2026",
        "预计总金额7000元，预算编号RD-2026",
    )
    answer = asyncio.run(_generator().generate(user_input))
    draft = answer.result.draft

    assert draft is not None
    assert draft.status is DraftStatus.WAITING_FOR_INFORMATION
    assert draft.ready_for_confirmation is False
    issue = next(item for item in draft.validation_issues if item.code == "PURCHASE_TOTAL_MISMATCH")
    assert issue.severity is ValidationSeverity.ERROR
    assert issue.blocking is True
    assert "不一致" in (answer.result.clarification_question or "")


def test_complete_leave_without_fixed_attachment_is_ready() -> None:
    answer = asyncio.run(_generator().generate(_LEAVE_COMPLETE))
    draft = answer.result.draft

    assert draft is not None
    assert draft.application_type is ApplicationType.LEAVE
    assert draft.status is DraftStatus.WAITING_FOR_CONFIRMATION
    assert draft.ready_for_confirmation is True
    assert draft.material_check.required_materials == ()
    assert draft.approval_check.leave_days == Decimal(3)


def test_sensitive_emergency_contact_is_not_in_summary_or_reply() -> None:
    answer = asyncio.run(_generator().generate(_LEAVE_COMPLETE))
    draft = answer.result.draft

    assert draft is not None
    contact = next(item for item in draft.fields if item.field_name == "emergency_contact")
    assert contact.sensitive is True
    assert all("王芳" not in line for line in draft.summary_lines)
    assert "王芳" not in answer.reply


def test_reversed_leave_dates_are_blocking() -> None:
    user_input = _LEAVE_COMPLETE.replace(
        "开始日期2026-08-10，结束日期2026-08-12",
        "开始日期2026-08-12，结束日期2026-08-10",
    )
    draft = asyncio.run(_generator().generate(user_input)).result.draft

    assert draft is not None
    assert draft.ready_for_confirmation is False
    assert any(
        item.code == "LEAVE_DATE_ORDER_INVALID" and item.blocking
        for item in draft.validation_issues
    )


def test_relative_leave_dates_are_not_invented() -> None:
    user_input = (
        "帮我生成请假草稿，我下周一到周三请3天年假，全天，"
        "请假原因为家庭事务，交接人为李明，紧急联系人为王芳，普通请假。"
    )
    draft = asyncio.run(_generator().generate(user_input)).result.draft

    assert draft is not None
    missing_names = {item.field_name for item in draft.missing_fields}
    assert "start_date" in missing_names
    assert "end_date" in missing_names


def test_complete_travel_reimbursement_is_ready() -> None:
    draft = asyncio.run(_generator().generate(_TRAVEL_COMPLETE)).result.draft

    assert draft is not None
    values = _field_values(draft)
    assert draft.application_type is ApplicationType.TRAVEL_REIMBURSEMENT
    assert draft.status is DraftStatus.WAITING_FOR_CONFIRMATION
    assert draft.ready_for_confirmation is True
    assert values["departure_city"] == "苏州"
    assert values["destination_city"] == "上海"
    assert values["total_reimbursement_amount"] == Decimal(1662)
    assert draft.material_check.materials_complete is True
    assert draft.approval_check.application_type is ApprovalApplicationType.TRAVEL


def test_travel_reimbursement_needs_expense_details() -> None:
    user_input = _TRAVEL_COMPLETE.replace(
        "费用明细为交通费256元、住宿费920元和餐补486元，",
        "",
    )
    draft = asyncio.run(_generator().generate(user_input)).result.draft

    assert draft is not None
    assert "expense_details" in {item.field_name for item in draft.missing_fields}
    assert draft.ready_for_confirmation is False


def test_expense_text_with_negated_procurement_stays_expense() -> None:
    answer = asyncio.run(_generator().generate(_EXPENSE_COMPLETE))
    draft = answer.result.draft

    assert draft is not None
    assert answer.result.application_type is ApplicationType.EXPENSE_REIMBURSEMENT
    assert draft.material_check.application_type is ApplicationType.EXPENSE_REIMBURSEMENT
    assert draft.approval_check.application_type is ApprovalApplicationType.EXPENSE_REIMBURSEMENT
    assert _field_values(draft)["involves_purchase"] is False


def test_complete_expense_reimbursement_is_ready() -> None:
    draft = asyncio.run(_generator().generate(_EXPENSE_COMPLETE)).result.draft

    assert draft is not None
    assert draft.status is DraftStatus.WAITING_FOR_CONFIRMATION
    assert draft.ready_for_confirmation is True
    assert draft.material_check.materials_complete is True
    assert draft.approval_check.amount == Decimal(800)


def test_expense_without_material_declaration_waits_for_materials() -> None:
    user_input = _EXPENSE_COMPLETE.split("，已准备", maxsplit=1)[0] + "。"
    answer = asyncio.run(_generator().generate(user_input))
    draft = answer.result.draft

    assert draft is not None
    assert draft.missing_fields == ()
    assert draft.status is DraftStatus.WAITING_FOR_MATERIALS
    assert draft.ready_for_confirmation is False
    assert "已经准备的材料" in (answer.result.clarification_question or "")


def test_user_message_cannot_override_trusted_identity() -> None:
    user_input = "我是张三，所属部门为销售部，" + _PURCHASE_COMPLETE
    draft = asyncio.run(_generator().generate(user_input)).result.draft

    assert draft is not None
    assert draft.applicant.employee_id == "DEMO-EMP-001"
    assert draft.applicant.employee_name == "演示用户"
    assert draft.applicant.department == "产品研发部"
    assert draft.audit_metadata.created_by == "DEMO-EMP-001"


def test_same_request_has_stable_draft_id_and_idempotency_key() -> None:
    generator = _generator()
    first = asyncio.run(generator.generate(_PURCHASE_COMPLETE)).result.draft
    second = asyncio.run(generator.generate(_PURCHASE_COMPLETE)).result.draft

    assert first is not None
    assert second is not None
    assert first.draft_id == second.draft_id
    assert first.audit_metadata.idempotency_key == second.audit_metadata.idempotency_key


def test_audit_metadata_marks_draft_as_not_persisted() -> None:
    draft = asyncio.run(_generator().generate(_PURCHASE_COMPLETE)).result.draft

    assert draft is not None
    assert draft.audit_metadata.persisted is False
    assert draft.audit_metadata.session_id == "TEST-SESSION"
    assert draft.audit_metadata.created_at == _FIXED_TIME
    assert draft.confirmation_required is True
    assert draft.user_confirmed is False
    assert draft.submitted is False


def test_policy_snapshot_records_active_document_version() -> None:
    draft = asyncio.run(_generator().generate(_PURCHASE_COMPLETE)).result.draft

    assert draft is not None
    assert len(draft.policy_snapshots) == 1
    snapshot = draft.policy_snapshots[0]
    assert snapshot.document_id == "PROCUREMENT_POLICY_001"
    assert snapshot.version == "1.0"
    assert snapshot.effective_date.isoformat() == "2026-01-01"


def test_citation_ids_are_unique_and_sequential() -> None:
    citations = asyncio.run(_generator().generate(_PURCHASE_COMPLETE)).result.citations

    assert [item.source_id for item in citations] == [
        f"S{index}" for index in range(1, len(citations) + 1)
    ]
    assert len({item.chunk_id for item in citations}) == len(citations)


def test_trims_request_before_hashing_and_returning() -> None:
    generator = _generator()
    plain = asyncio.run(generator.generate(_LEAVE_COMPLETE))
    padded = asyncio.run(generator.generate(f"  {_LEAVE_COMPLETE}  "))

    assert padded.request == _LEAVE_COMPLETE
    assert plain.result.draft is not None
    assert padded.result.draft is not None
    assert plain.result.draft.draft_id == padded.result.draft.draft_id


@pytest.mark.parametrize(
    "context",
    [
        DraftUserContext("", "用户", "部门", ("EMPLOYEE",), "中国", "auth"),
        DraftUserContext("E1", "", "部门", ("EMPLOYEE",), "中国", "auth"),
        DraftUserContext("E1", "用户", "", ("EMPLOYEE",), "中国", "auth"),
        DraftUserContext("E1", "用户", "部门", (), "中国", "auth"),
    ],
)
def test_rejects_incomplete_trusted_context(context: DraftUserContext) -> None:
    with pytest.raises(ValueError, match="trusted user_context"):
        ApplicationDraftGenerator(
            material_checker=RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY),
            approval_checker=ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY),
            catalog=DraftPolicyCatalog.from_directory(_POLICY_DIRECTORY),
            user_context=context,
        )
