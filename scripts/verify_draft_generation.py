from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.material_models import ApplicationType

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_PURCHASE_COMPLETE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)

_CASES = (
    {
        "name": "complete_purchase",
        "input": _PURCHASE_COMPLETE,
        "application_type": ApplicationType.PURCHASE,
        "draft_status": DraftStatus.WAITING_FOR_CONFIRMATION,
        "ready_for_confirmation": True,
    },
    {
        "name": "incomplete_purchase",
        "input": "帮我生成采购申请草稿。",
        "application_type": ApplicationType.PURCHASE,
        "draft_status": DraftStatus.WAITING_FOR_INFORMATION,
        "ready_for_confirmation": False,
    },
    {
        "name": "complete_travel_reimbursement",
        "input": (
            "帮我生成差旅报销草稿，从苏州到上海出差，出差申请编号TRAVEL-001，"
            "开始日期2026-07-20，结束日期2026-07-22，出差事由为客户需求评审，"
            "项目名称为客户平台升级，报销总金额1662元，"
            "费用明细为交通费256元、住宿费920元和餐补486元，"
            "已准备出差申请单、行程单、交通票据、住宿发票、酒店住宿明细、"
            "支付记录和出差成果。"
        ),
        "application_type": ApplicationType.TRAVEL_REIMBURSEMENT,
        "draft_status": DraftStatus.WAITING_FOR_CONFIRMATION,
        "ready_for_confirmation": True,
    },
    {
        "name": "complete_leave",
        "input": (
            "帮我生成请假申请草稿，我请3天年假，开始日期2026-08-10，"
            "结束日期2026-08-12，全天，请假原因为家庭事务，交接人为李明，"
            "紧急联系人为王芳，普通请假。"
        ),
        "application_type": ApplicationType.LEAVE,
        "draft_status": DraftStatus.WAITING_FOR_CONFIRMATION,
        "ready_for_confirmation": True,
    },
    {
        "name": "expense_waiting_for_materials",
        "input": (
            "帮我生成800元业务招待费报销草稿，业务目的为客户项目沟通，"
            "发生日期2026-08-01，成本中心CC-SALES，收款对象为示例餐厅，"
            "不涉及合同，不涉及采购。"
        ),
        "application_type": ApplicationType.EXPENSE_REIMBURSEMENT,
        "draft_status": DraftStatus.WAITING_FOR_MATERIALS,
        "ready_for_confirmation": False,
    },
    {
        "name": "amount_mismatch",
        "input": _PURCHASE_COMPLETE.replace(
            "预算编号RD-2026",
            "预计总金额7000元，预算编号RD-2026",
        ),
        "application_type": ApplicationType.PURCHASE,
        "draft_status": DraftStatus.WAITING_FOR_INFORMATION,
        "ready_for_confirmation": False,
    },
)


async def _main() -> None:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=DraftUserContext(
            employee_id="DEMO-EMP-001",
            employee_name="演示用户",
            department="演示部门",
            roles=("EMPLOYEE",),
            region="中国大陆",
            identity_source="trusted_demo_context",
        ),
    )
    failures: list[str] = []

    for case in _CASES:
        answer = await generator.generate(case["input"])
        draft = answer.result.draft
        passed = (
            answer.result.application_type is case["application_type"]
            and draft is not None
            and draft.status is case["draft_status"]
            and draft.ready_for_confirmation is case["ready_for_confirmation"]
            and draft.user_confirmed is False
            and draft.submitted is False
            and draft.audit_metadata.persisted is False
            and bool(answer.result.citations)
        )

        print(
            json.dumps(
                {
                    "name": case["name"],
                    "application_type": answer.result.application_type,
                    "draft_id": draft.draft_id if draft is not None else None,
                    "draft_status": draft.status if draft is not None else None,
                    "missing_fields": (
                        [item.field_name for item in draft.missing_fields]
                        if draft is not None
                        else []
                    ),
                    "ready_for_confirmation": (
                        draft.ready_for_confirmation if draft is not None else False
                    ),
                    "user_confirmed": (draft.user_confirmed if draft is not None else None),
                    "submitted": draft.submitted if draft is not None else None,
                    "citations": [citation.source_id for citation in answer.result.citations],
                    "passed": passed,
                },
                ensure_ascii=False,
            )
        )

        if not passed:
            failures.append(case["name"])

    first = await generator.generate(_PURCHASE_COMPLETE)
    second = await generator.generate(_PURCHASE_COMPLETE)
    stable_id = (
        first.result.draft is not None
        and second.result.draft is not None
        and first.result.draft.draft_id == second.result.draft.draft_id
    )
    print(
        json.dumps(
            {
                "name": "idempotent_draft_id",
                "draft_id": (
                    first.result.draft.draft_id if first.result.draft is not None else None
                ),
                "passed": stable_id,
            },
            ensure_ascii=False,
        )
    )
    if not stable_id:
        failures.append("idempotent_draft_id")

    if failures:
        raise RuntimeError("Draft generation verification failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    asyncio.run(_main())
