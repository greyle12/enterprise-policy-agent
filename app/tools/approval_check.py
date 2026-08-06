from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_context import PolicyCitation
from app.schemas.chunk import PolicyChunk
from app.tools.approval_models import (
    ApprovalAction,
    ApprovalApplicationType,
    ApprovalCheckAnswer,
    ApprovalCheckResult,
    ApprovalLevel,
    ApprovalStep,
    ApproverCode,
)


@dataclass(frozen=True, slots=True)
class PolicyArticleRef:
    """确定性审批规则所引用的一条制度条款。"""

    document_id: str
    article_label: str


@dataclass(frozen=True, slots=True)
class _StepSpec:
    approver: ApproverCode
    action: ApprovalAction
    reason: str


@dataclass(frozen=True, slots=True)
class _ResolvedApprovalRequest:
    application_type: ApprovalApplicationType | None
    amount: Decimal | None
    leave_days: Decimal | None
    travel_days: Decimal | None
    is_it_purchase: bool
    is_emergency_purchase: bool
    is_major_project: bool
    is_travel_reimbursement: bool
    travel_over_standard: bool
    travel_overseas: bool
    travel_advance_requested: bool
    expense_special_conditions: tuple[str, ...]
    expense_preapproval_required: bool
    leave_type: str | None
    applicant_is_department_head: bool
    leave_balance_disputed: bool
    leave_materials_conflict: bool


@dataclass(frozen=True, slots=True)
class _RuleDecision:
    level: ApprovalLevel | None
    steps: tuple[_StepSpec, ...]
    special_conditions: tuple[str, ...]
    clarification_question: str | None
    notes: tuple[str, ...]
    sources: tuple[PolicyArticleRef, ...]


_PROCUREMENT_IT_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第七条",
)
_PROCUREMENT_SMALL_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十一条",
)
_PROCUREMENT_GENERAL_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十二条",
)
_PROCUREMENT_IMPORTANT_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十三条",
)
_PROCUREMENT_MAJOR_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十四条",
)
_PROCUREMENT_EMERGENCY_SOURCE = PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第三十一条",
)

_TRAVEL_APPLICATION_SOURCE = PolicyArticleRef(
    "TRAVEL_POLICY_001",
    "第五条",
)
_TRAVEL_SPECIAL_SOURCE = PolicyArticleRef(
    "TRAVEL_POLICY_001",
    "第六条",
)
_TRAVEL_REIMBURSEMENT_SOURCE = PolicyArticleRef(
    "TRAVEL_POLICY_001",
    "第二十条",
)

_EXPENSE_PREAPPROVAL_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十一条",
)
_EXPENSE_SMALL_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第二十一条",
)
_EXPENSE_GENERAL_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第二十二条",
)
_EXPENSE_LARGE_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第二十三条",
)
_EXPENSE_MAJOR_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第二十四条",
)
_EXPENSE_SPECIAL_SOURCE = PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第二十五条",
)

_LEAVE_GENERAL_SOURCE = PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第二十条",
)
_LEAVE_LONG_TERM_SOURCE = PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第二十一条",
)
_LEAVE_DEPARTMENT_HEAD_SOURCE = PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第二十二条",
)
_LEAVE_HR_REVIEW_SOURCE = PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第二十三条",
)

_APPROVER_NAMES = {
    ApproverCode.DIRECT_MANAGER: "直属经理",
    ApproverCode.DEPARTMENT_HEAD: "部门负责人",
    ApproverCode.IT_DEPARTMENT: "信息技术部",
    ApproverCode.PROCUREMENT_DEPARTMENT: "采购管理部",
    ApproverCode.FINANCE_DEPARTMENT: "财务管理部",
    ApproverCode.HUMAN_RESOURCES: "人力资源部",
    ApproverCode.BUSINESS_VICE_PRESIDENT: "分管副总经理",
    ApproverCode.GENERAL_MANAGER: "总经理",
    ApproverCode.MANAGEMENT_COMMITTEE: "公司管理委员会",
}

_ACTION_NAMES = {
    ApprovalAction.APPROVE: "审批",
    ApprovalAction.REVIEW: "复核",
    ApprovalAction.TECHNICAL_REVIEW: "技术评审",
    ApprovalAction.CONFIRM: "确认",
    ApprovalAction.DELIBERATE: "审议",
}

_LEVEL_NAMES = {
    ApprovalLevel.SMALL_PURCHASE: "小额采购",
    ApprovalLevel.GENERAL_PURCHASE: "一般采购",
    ApprovalLevel.IMPORTANT_PURCHASE: "重要采购",
    ApprovalLevel.MAJOR_PURCHASE: "重大采购",
    ApprovalLevel.EMERGENCY_PURCHASE: "紧急采购",
    ApprovalLevel.SMALL_TRAVEL: "普通出差（5,000元及以下）",
    ApprovalLevel.GENERAL_TRAVEL: "普通出差（5,000元至20,000元）",
    ApprovalLevel.LARGE_TRAVEL: "较大金额出差",
    ApprovalLevel.TRAVEL_REIMBURSEMENT: "差旅报销审核",
    ApprovalLevel.SMALL_EXPENSE: "小额费用报销",
    ApprovalLevel.GENERAL_EXPENSE: "一般费用报销",
    ApprovalLevel.LARGE_EXPENSE: "较大费用报销",
    ApprovalLevel.MAJOR_EXPENSE: "大额费用报销",
    ApprovalLevel.SHORT_LEAVE: "短期请假",
    ApprovalLevel.MEDIUM_LEAVE: "一般请假",
    ApprovalLevel.EXTENDED_LEAVE: "较长请假",
    ApprovalLevel.LONG_TERM_LEAVE: "长期请假",
    ApprovalLevel.DEPARTMENT_HEAD_LEAVE: "部门负责人请假",
}

_EXPENSE_SPECIAL_CUES = (
    (("无发票", "没有发票", "无法取得发票"), "无发票费用"),
    (("业务招待", "招待费", "客户招待"), "业务招待费"),
    (("超时报销", "超过报销时限", "逾期报销"), "超过报销时限"),
    (("费用类型难以判断", "费用类型不明"), "费用类型难以判断"),
    (("付款人和报销人不一致", "他人代付", "代付"), "付款人与报销人不一致"),
    (("个人敏感信息", "敏感信息"), "涉及个人敏感信息"),
    (("高风险费用", "高风险"), "财务系统识别为高风险"),
)

_EXPENSE_PREAPPROVAL_CUES = (
    "业务招待",
    "招待费",
    "外部培训",
    "会议场地",
    "大额办公支出",
    "软件订阅",
    "市场活动",
    "咨询服务",
    "专业服务",
)


class ApprovalPolicyCatalog:
    """把审批结论绑定到真实制度条款。"""

    def __init__(self, chunks: Iterable[PolicyChunk]) -> None:
        self._chunks = {(chunk.document_id, chunk.article_label): chunk for chunk in chunks}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
    ) -> ApprovalPolicyCatalog:
        return cls(chunk_policy_directory(directory))

    def citation(
        self,
        reference: PolicyArticleRef,
        *,
        source_id: str,
    ) -> PolicyCitation:
        chunk = self._chunks.get((reference.document_id, reference.article_label))
        if chunk is None:
            raise RuntimeError(
                "approval rule references missing policy article: "
                f"{reference.document_id}/{reference.article_label}"
            )

        return PolicyCitation(
            source_id=source_id,
            chunk_id=chunk.chunk_id,
            document_title=chunk.document_title,
            chapter_title=chunk.chapter_title,
            article_label=chunk.article_label,
            article_title=chunk.article_title,
            score=1.0,
        )


def _detect_application_type(
    text: str,
) -> ApprovalApplicationType | None:
    if any(word in text for word in ("差旅", "出差")):
        return ApprovalApplicationType.TRAVEL

    if any(
        word in text
        for word in (
            "采购",
            "购买",
            "购置",
            "买电脑",
            "买显示器",
            "买设备",
        )
    ):
        return ApprovalApplicationType.PURCHASE

    if any(
        word in text
        for word in (
            "请假",
            "年假",
            "年休假",
            "病假",
            "婚假",
            "丧假",
            "事假",
            "调休",
            "产假",
            "陪产假",
            "育儿假",
            "工伤假",
        )
    ):
        return ApprovalApplicationType.LEAVE

    if "费用报销" in text or any(
        word in text
        for word in (
            "办公费报销",
            "会议费报销",
            "培训费报销",
            "招待费报销",
            "通信费报销",
            "市内交通费报销",
        )
    ):
        return ApprovalApplicationType.EXPENSE_REIMBURSEMENT

    return None


def _extract_decimal(
    raw_value: str,
    unit: str | None = None,
) -> Decimal | None:
    try:
        value = Decimal(raw_value.replace(",", ""))
    except InvalidOperation:
        return None

    if unit in {"万", "万元"}:
        value *= Decimal(10000)
    return value


def _parse_small_number(raw_value: str) -> Decimal | None:
    numeric = _extract_decimal(raw_value)
    if numeric is not None:
        return numeric

    chinese_digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if raw_value in chinese_digits:
        return Decimal(chinese_digits[raw_value])

    if raw_value.startswith("十") and len(raw_value) == 2:
        suffix = chinese_digits.get(raw_value[1])
        if suffix is not None:
            return Decimal(10 + suffix)

    if raw_value.endswith("十") and len(raw_value) == 2:
        prefix = chinese_digits.get(raw_value[0])
        if prefix is not None:
            return Decimal(prefix * 10)

    if "十" in raw_value and len(raw_value) == 3:
        prefix = chinese_digits.get(raw_value[0])
        suffix = chinese_digits.get(raw_value[2])
        if prefix is not None and suffix is not None:
            return Decimal(prefix * 10 + suffix)

    return None


def _detect_amount(text: str) -> Decimal | None:
    quantity_price_match = re.search(
        r"(?P<quantity>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"
        r"\s*(?:台|个|套|件|份)"
        r".{0,20}?每(?:台|个|套|件|份)?\s*"
        r"(?P<unit_price>[\d,]+(?:\.\d+)?)\s*元",
        text,
    )
    if quantity_price_match is not None:
        quantity = _parse_small_number(quantity_price_match.group("quantity"))
        unit_price = _extract_decimal(quantity_price_match.group("unit_price"))
        if quantity is not None and unit_price is not None:
            return quantity * unit_price

    amount_match = re.search(
        r"(?:预计总金额|预计总费用|报销金额|费用金额|总金额|总费用|"
        r"总价|预算|金额|预计)[为是约大概:\s]*"
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s*"
        r"(?P<unit>万元|万|元|块)",
        text,
    )
    if amount_match is None:
        amount_match = re.search(
            r"(?P<amount>[\d,]+(?:\.\d+)?)\s*"
            r"(?P<unit>万元|万|元|块)(?:的)?"
            r"(?:采购|电脑|设备|服务|出差|差旅|报销|费用|"
            r"业务招待|招待费|培训费)",
            text,
        )

    if amount_match is None:
        return None

    return _extract_decimal(
        amount_match.group("amount"),
        amount_match.group("unit"),
    )


def _detect_days(text: str) -> Decimal | None:
    if "半天" in text or "半个工作日" in text:
        return Decimal("0.5")

    match = re.search(
        r"(?P<days>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"
        r"\s*(?:个)?(?:工作|自然)?(?:日|天)",
        text,
    )
    if match is None:
        return None
    return _parse_small_number(match.group("days"))


def _detect_leave_type(text: str) -> str | None:
    mappings = (
        (("病假",), "sick"),
        (("婚假",), "marriage"),
        (("丧假",), "bereavement"),
        (("产假", "陪产假", "育儿假"), "parental"),
        (("工伤假", "工伤休假"), "work_injury"),
        (("年假", "年休假"), "annual"),
        (("调休",), "compensatory"),
        (("事假",), "personal"),
    )
    for aliases, leave_type in mappings:
        if any(alias in text for alias in aliases):
            return leave_type
    return None


def _detect_expense_special_conditions(text: str) -> tuple[str, ...]:
    return tuple(
        description
        for cues, description in _EXPENSE_SPECIAL_CUES
        if any(cue in text for cue in cues)
    )


def _resolve_request(text: str) -> _ResolvedApprovalRequest:
    application_type = _detect_application_type(text)
    detected_days = _detect_days(text)

    return _ResolvedApprovalRequest(
        application_type=application_type,
        amount=_detect_amount(text),
        leave_days=(detected_days if application_type is ApprovalApplicationType.LEAVE else None),
        travel_days=(detected_days if application_type is ApprovalApplicationType.TRAVEL else None),
        is_it_purchase=any(
            word in text.lower()
            for word in (
                "电脑",
                "显示器",
                "服务器",
                "软件",
                "系统",
                "云服务",
                "数据服务",
                "网络设备",
                "计算机",
                "it设备",
                "it采购",
            )
        ),
        is_emergency_purchase=(
            application_type is ApprovalApplicationType.PURCHASE and "紧急" in text
        ),
        is_major_project="重大项目" in text,
        is_travel_reimbursement=(
            application_type is ApprovalApplicationType.TRAVEL and "报销" in text
        ),
        travel_over_standard=any(
            cue in text
            for cue in (
                "超出标准",
                "超过标准",
                "超标",
            )
        ),
        travel_overseas=any(
            cue in text
            for cue in (
                "境外",
                "国外",
                "海外",
                "港澳台",
                "香港",
                "澳门",
                "台湾",
            )
        ),
        travel_advance_requested=any(
            cue in text
            for cue in (
                "预借差旅备用金",
                "差旅备用金",
                "预借备用金",
            )
        ),
        expense_special_conditions=(_detect_expense_special_conditions(text)),
        expense_preapproval_required=any(cue in text for cue in _EXPENSE_PREAPPROVAL_CUES),
        leave_type=_detect_leave_type(text),
        applicant_is_department_head=any(
            cue in text
            for cue in (
                "我是部门负责人",
                "部门负责人请假",
                "部门负责人申请",
            )
        ),
        leave_balance_disputed=(
            "余额" in text and any(cue in text for cue in ("争议", "有误", "不一致"))
        ),
        leave_materials_conflict=(
            "材料" in text and any(cue in text for cue in ("矛盾", "冲突", "不一致"))
        ),
    )


def _spec(
    approver: ApproverCode,
    action: ApprovalAction,
    reason: str,
) -> _StepSpec:
    return _StepSpec(
        approver=approver,
        action=action,
        reason=reason,
    )


def _deduplicate_specs(
    specs: Iterable[_StepSpec],
) -> tuple[_StepSpec, ...]:
    result: list[_StepSpec] = []
    seen: set[ApproverCode] = set()
    for item in specs:
        if item.approver in seen:
            continue
        seen.add(item.approver)
        result.append(item)
    return tuple(result)


def _purchase_decision(
    request: _ResolvedApprovalRequest,
) -> _RuleDecision:
    amount = request.amount
    if amount is None:
        sources = [
            _PROCUREMENT_SMALL_SOURCE,
            _PROCUREMENT_GENERAL_SOURCE,
            _PROCUREMENT_IMPORTANT_SOURCE,
            _PROCUREMENT_MAJOR_SOURCE,
        ]
        if request.is_it_purchase:
            sources.insert(0, _PROCUREMENT_IT_SOURCE)
        return _RuleDecision(
            level=None,
            steps=(),
            special_conditions=(("涉及信息技术类采购",) if request.is_it_purchase else ()),
            clarification_question=(
                "请补充预计采购总金额（应包含税费、运输、安装、培训和维护等必要费用）。"
            ),
            notes=("采购审批层级由预计总金额决定。",),
            sources=tuple(sources),
        )

    conditions: list[str] = []
    notes: list[str] = []
    sources: list[PolicyArticleRef] = []

    if request.is_emergency_purchase:
        level = ApprovalLevel.EMERGENCY_PURCHASE
        sources.append(_PROCUREMENT_EMERGENCY_SOURCE)
        conditions.append("紧急采购")
        specs: list[_StepSpec] = [
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "紧急采购至少需要部门负责人批准。",
            )
        ]
        if request.is_it_purchase:
            specs.append(
                _spec(
                    ApproverCode.IT_DEPARTMENT,
                    ApprovalAction.TECHNICAL_REVIEW,
                    "信息技术类采购需要信息技术部进行技术评审。",
                )
            )
            sources.append(_PROCUREMENT_IT_SOURCE)
            conditions.append("涉及信息技术类采购")
        specs.extend(
            (
                _spec(
                    ApproverCode.PROCUREMENT_DEPARTMENT,
                    ApprovalAction.CONFIRM,
                    "紧急采购需要采购管理部确认。",
                ),
                _spec(
                    ApproverCode.FINANCE_DEPARTMENT,
                    ApprovalAction.CONFIRM,
                    "紧急采购需要财务管理部确认资金来源。",
                ),
            )
        )
        if amount > Decimal(50000):
            specs.append(
                _spec(
                    ApproverCode.BUSINESS_VICE_PRESIDENT,
                    ApprovalAction.APPROVE,
                    "紧急采购金额超过50,000元。",
                )
            )
        if amount > Decimal(200000):
            specs.append(
                _spec(
                    ApproverCode.GENERAL_MANAGER,
                    ApprovalAction.APPROVE,
                    "紧急采购金额超过200,000元。",
                )
            )
        if amount > Decimal(1000000) or request.is_major_project:
            specs.append(
                _spec(
                    ApproverCode.MANAGEMENT_COMMITTEE,
                    ApprovalAction.DELIBERATE,
                    "金额超过1,000,000元或属于公司重大项目。",
                )
            )
            sources.append(_PROCUREMENT_MAJOR_SOURCE)
        notes.append("口头批准的，应在采购实施后两个工作日内补齐书面审批。")
        return _RuleDecision(
            level=level,
            steps=_deduplicate_specs(specs),
            special_conditions=tuple(conditions),
            clarification_question=None,
            notes=tuple(notes),
            sources=tuple(sources),
        )

    specs = [
        _spec(
            ApproverCode.DIRECT_MANAGER,
            ApprovalAction.APPROVE,
            "采购申请首先由申请人的直属经理审批。",
        )
    ]

    if amount <= Decimal(5000):
        level = ApprovalLevel.SMALL_PURCHASE
        sources.append(_PROCUREMENT_SMALL_SOURCE)
    elif amount <= Decimal(50000):
        level = ApprovalLevel.GENERAL_PURCHASE
        sources.append(_PROCUREMENT_GENERAL_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "预计采购总金额超过5,000元。",
            )
        )
    elif amount <= Decimal(200000):
        level = ApprovalLevel.IMPORTANT_PURCHASE
        sources.append(_PROCUREMENT_IMPORTANT_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "预计采购总金额超过50,000元。",
            )
        )
    else:
        level = ApprovalLevel.MAJOR_PURCHASE
        sources.append(_PROCUREMENT_MAJOR_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "预计采购总金额超过200,000元。",
            )
        )

    if request.is_it_purchase:
        specs.append(
            _spec(
                ApproverCode.IT_DEPARTMENT,
                ApprovalAction.TECHNICAL_REVIEW,
                "信息技术类采购需要信息技术部技术评审。",
            )
        )
        sources.append(_PROCUREMENT_IT_SOURCE)
        conditions.append("涉及信息技术类采购")

    if amount > Decimal(5000):
        specs.append(
            _spec(
                ApproverCode.PROCUREMENT_DEPARTMENT,
                ApprovalAction.APPROVE,
                "采购金额超过5,000元，需采购管理部审批。",
            )
        )
    if amount > Decimal(50000):
        specs.extend(
            (
                _spec(
                    ApproverCode.FINANCE_DEPARTMENT,
                    ApprovalAction.APPROVE,
                    "采购金额超过50,000元，需财务管理部审批。",
                ),
                _spec(
                    ApproverCode.BUSINESS_VICE_PRESIDENT,
                    ApprovalAction.APPROVE,
                    "采购金额超过50,000元，需分管副总经理审批。",
                ),
            )
        )
    if amount > Decimal(200000):
        specs.append(
            _spec(
                ApproverCode.GENERAL_MANAGER,
                ApprovalAction.APPROVE,
                "采购金额超过200,000元。",
            )
        )
    if amount > Decimal(1000000) or request.is_major_project:
        specs.append(
            _spec(
                ApproverCode.MANAGEMENT_COMMITTEE,
                ApprovalAction.DELIBERATE,
                "金额超过1,000,000元或属于公司重大项目。",
            )
        )
        conditions.append("需提交公司管理委员会审议")

    if amount <= Decimal(1000):
        notes.append("日常办公消耗品且单笔不超过1,000元时，可按部门小额采购流程办理。")

    return _RuleDecision(
        level=level,
        steps=_deduplicate_specs(specs),
        special_conditions=tuple(conditions),
        clarification_question=None,
        notes=tuple(notes),
        sources=tuple(sources),
    )


def _travel_decision(
    request: _ResolvedApprovalRequest,
) -> _RuleDecision:
    if request.is_travel_reimbursement:
        return _RuleDecision(
            level=ApprovalLevel.TRAVEL_REIMBURSEMENT,
            steps=(
                _spec(
                    ApproverCode.DIRECT_MANAGER,
                    ApprovalAction.APPROVE,
                    "直属经理确认出差真实性。",
                ),
                _spec(
                    ApproverCode.DEPARTMENT_HEAD,
                    ApprovalAction.APPROVE,
                    "部门负责人确认业务合理性。",
                ),
                _spec(
                    ApproverCode.FINANCE_DEPARTMENT,
                    ApprovalAction.REVIEW,
                    "财务管理部审核票据和费用标准。",
                ),
            ),
            special_conditions=("差旅报销审核",),
            clarification_question=None,
            notes=("超权限事项还需提交相应管理人员审批。",),
            sources=(_TRAVEL_REIMBURSEMENT_SOURCE,),
        )

    amount = request.amount
    if amount is None:
        return _RuleDecision(
            level=None,
            steps=(),
            special_conditions=(),
            clarification_question="请补充本次出差的预计总费用。",
            notes=("出差申请审批层级由预计差旅总费用决定。",),
            sources=(
                _TRAVEL_APPLICATION_SOURCE,
                _TRAVEL_SPECIAL_SOURCE,
            ),
        )

    if amount <= Decimal(5000):
        level = ApprovalLevel.SMALL_TRAVEL
        specs = [
            _spec(
                ApproverCode.DIRECT_MANAGER,
                ApprovalAction.APPROVE,
                "预计差旅总费用不超过5,000元。",
            )
        ]
    elif amount <= Decimal(20000):
        level = ApprovalLevel.GENERAL_TRAVEL
        specs = [
            _spec(
                ApproverCode.DIRECT_MANAGER,
                ApprovalAction.APPROVE,
                "出差申请首先由直属经理审批。",
            ),
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "预计差旅总费用超过5,000元。",
            ),
        ]
    else:
        level = ApprovalLevel.LARGE_TRAVEL
        specs = [
            _spec(
                ApproverCode.DIRECT_MANAGER,
                ApprovalAction.APPROVE,
                "出差申请首先由直属经理审批。",
            ),
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "预计差旅总费用超过20,000元。",
            ),
            _spec(
                ApproverCode.BUSINESS_VICE_PRESIDENT,
                ApprovalAction.APPROVE,
                "预计差旅总费用超过20,000元。",
            ),
        ]

    conditions: list[str] = []
    finance_required = False
    if amount > Decimal(30000):
        finance_required = True
        conditions.append("预计总费用超过30,000元")
    if request.travel_days is not None and request.travel_days > Decimal(15):
        finance_required = True
        conditions.append("出差时间超过15个自然日")
    if request.travel_over_standard:
        finance_required = True
        conditions.append("交通工具或住宿超出制度标准")
    if request.travel_overseas:
        finance_required = True
        conditions.append("前往境外或港澳台地区")
    if request.travel_advance_requested:
        finance_required = True
        conditions.append("需要预借差旅备用金")

    sources = [_TRAVEL_APPLICATION_SOURCE]
    if finance_required:
        finance_spec = _spec(
            ApproverCode.FINANCE_DEPARTMENT,
            ApprovalAction.REVIEW,
            "本次出差触发特殊出差财务复核条件。",
        )
        vice_position = next(
            (
                index
                for index, item in enumerate(specs)
                if item.approver is ApproverCode.BUSINESS_VICE_PRESIDENT
            ),
            len(specs),
        )
        specs.insert(vice_position, finance_spec)
        sources.append(_TRAVEL_SPECIAL_SOURCE)

    notes = (("如存在超标、超过15天、境外出差或预借备用金等情况，还需财务管理部复核。"),)
    return _RuleDecision(
        level=level,
        steps=_deduplicate_specs(specs),
        special_conditions=tuple(conditions),
        clarification_question=None,
        notes=notes,
        sources=tuple(sources),
    )


def _expense_decision(
    request: _ResolvedApprovalRequest,
) -> _RuleDecision:
    amount = request.amount
    if amount is None:
        return _RuleDecision(
            level=None,
            steps=(),
            special_conditions=request.expense_special_conditions,
            clarification_question="请补充本张费用报销单的总金额。",
            notes=("费用审批层级按单张报销单总金额计算。",),
            sources=(
                _EXPENSE_SMALL_SOURCE,
                _EXPENSE_GENERAL_SOURCE,
                _EXPENSE_LARGE_SOURCE,
                _EXPENSE_MAJOR_SOURCE,
                _EXPENSE_SPECIAL_SOURCE,
            ),
        )

    specs = [
        _spec(
            ApproverCode.DIRECT_MANAGER,
            ApprovalAction.APPROVE,
            "费用报销首先由直属经理审批。",
        )
    ]
    sources: list[PolicyArticleRef] = []

    if amount <= Decimal(1000):
        level = ApprovalLevel.SMALL_EXPENSE
        sources.append(_EXPENSE_SMALL_SOURCE)
    elif amount <= Decimal(5000):
        level = ApprovalLevel.GENERAL_EXPENSE
        sources.append(_EXPENSE_GENERAL_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "单张报销单总金额超过1,000元。",
            )
        )
    elif amount <= Decimal(20000):
        level = ApprovalLevel.LARGE_EXPENSE
        sources.append(_EXPENSE_LARGE_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "单张报销单总金额超过5,000元。",
            )
        )
    else:
        level = ApprovalLevel.MAJOR_EXPENSE
        sources.append(_EXPENSE_MAJOR_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "单张报销单总金额超过20,000元。",
            )
        )

    finance_action = ApprovalAction.REVIEW if amount <= Decimal(5000) else ApprovalAction.APPROVE
    specs.append(
        _spec(
            ApproverCode.FINANCE_DEPARTMENT,
            finance_action,
            "财务管理部审核凭证、费用性质和适用审批要求。",
        )
    )
    if amount > Decimal(20000):
        specs.append(
            _spec(
                ApproverCode.BUSINESS_VICE_PRESIDENT,
                ApprovalAction.APPROVE,
                "单张报销单总金额超过20,000元。",
            )
        )
    if amount > Decimal(100000):
        specs.append(
            _spec(
                ApproverCode.GENERAL_MANAGER,
                ApprovalAction.APPROVE,
                "单张报销单总金额超过100,000元。",
            )
        )

    conditions = list(request.expense_special_conditions)
    notes: list[str] = []
    if request.expense_special_conditions:
        sources.append(_EXPENSE_SPECIAL_SOURCE)
        notes.append("该费用无论金额大小均需财务管理部复核。")
    if request.expense_preapproval_required:
        conditions.append("该费用原则上需要事前审批")
        sources.append(_EXPENSE_PREAPPROVAL_SOURCE)
        notes.append("事前审批与本次报销审核是两个不同控制环节。")

    return _RuleDecision(
        level=level,
        steps=_deduplicate_specs(specs),
        special_conditions=tuple(dict.fromkeys(conditions)),
        clarification_question=None,
        notes=tuple(notes),
        sources=tuple(sources),
    )


def _leave_requires_hr_review(
    request: _ResolvedApprovalRequest,
) -> tuple[bool, tuple[str, ...]]:
    conditions: list[str] = []
    days = request.leave_days
    if days is not None and days > Decimal(3):
        conditions.append("单次请假超过3个工作日")
    if request.leave_type == "sick" and days is not None and days > Decimal(1):
        conditions.append("连续病假超过1个工作日")
    if request.leave_type in {
        "marriage",
        "bereavement",
        "parental",
        "work_injury",
    }:
        conditions.append("申请特殊假期")
    if request.leave_balance_disputed:
        conditions.append("假期余额存在争议")
    if request.leave_materials_conflict:
        conditions.append("请假材料存在明显矛盾")
    return bool(conditions), tuple(conditions)


def _leave_decision(
    request: _ResolvedApprovalRequest,
) -> _RuleDecision:
    days = request.leave_days
    if days is None:
        return _RuleDecision(
            level=None,
            steps=(),
            special_conditions=(),
            clarification_question="请补充本次申请的请假工作日数。",
            notes=("请假审批层级按单次请假工作日数判断。",),
            sources=(
                _LEAVE_GENERAL_SOURCE,
                _LEAVE_LONG_TERM_SOURCE,
                _LEAVE_DEPARTMENT_HEAD_SOURCE,
                _LEAVE_HR_REVIEW_SOURCE,
            ),
        )

    hr_required, conditions = _leave_requires_hr_review(request)
    sources: list[PolicyArticleRef] = []

    if request.applicant_is_department_head:
        level = ApprovalLevel.DEPARTMENT_HEAD_LEAVE
        sources.append(_LEAVE_DEPARTMENT_HEAD_SOURCE)
        specs: list[_StepSpec] = []
        if hr_required:
            specs.append(
                _spec(
                    ApproverCode.HUMAN_RESOURCES,
                    ApprovalAction.REVIEW,
                    "部门负责人请假触发人力资源部复核条件。",
                )
            )
            sources.append(_LEAVE_HR_REVIEW_SOURCE)
        specs.append(
            _spec(
                ApproverCode.BUSINESS_VICE_PRESIDENT,
                ApprovalAction.APPROVE,
                "部门负责人请假由分管副总经理审批。",
            )
        )
        if days > Decimal(15):
            specs.append(
                _spec(
                    ApproverCode.GENERAL_MANAGER,
                    ApprovalAction.APPROVE,
                    "部门负责人单次请假超过15个工作日。",
                )
            )
        return _RuleDecision(
            level=level,
            steps=_deduplicate_specs(specs),
            special_conditions=conditions,
            clarification_question=None,
            notes=(),
            sources=tuple(sources),
        )

    specs = [
        _spec(
            ApproverCode.DIRECT_MANAGER,
            ApprovalAction.APPROVE,
            "普通员工请假首先由直属经理审批。",
        )
    ]
    if days <= Decimal(1):
        level = ApprovalLevel.SHORT_LEAVE
        sources.append(_LEAVE_GENERAL_SOURCE)
    elif days <= Decimal(3):
        level = ApprovalLevel.MEDIUM_LEAVE
        sources.append(_LEAVE_GENERAL_SOURCE)
        specs.append(
            _spec(
                ApproverCode.DEPARTMENT_HEAD,
                ApprovalAction.APPROVE,
                "单次请假超过1个工作日。",
            )
        )
    elif days <= Decimal(5):
        level = ApprovalLevel.EXTENDED_LEAVE
        sources.append(_LEAVE_GENERAL_SOURCE)
        specs.extend(
            (
                _spec(
                    ApproverCode.DEPARTMENT_HEAD,
                    ApprovalAction.APPROVE,
                    "单次请假超过3个工作日。",
                ),
                _spec(
                    ApproverCode.HUMAN_RESOURCES,
                    ApprovalAction.REVIEW,
                    "单次请假超过3个工作日。",
                ),
            )
        )
    else:
        level = ApprovalLevel.LONG_TERM_LEAVE
        sources.append(_LEAVE_LONG_TERM_SOURCE)
        specs.extend(
            (
                _spec(
                    ApproverCode.DEPARTMENT_HEAD,
                    ApprovalAction.APPROVE,
                    "长期请假需要部门负责人审批。",
                ),
                _spec(
                    ApproverCode.HUMAN_RESOURCES,
                    ApprovalAction.REVIEW,
                    "长期请假需要人力资源部审核。",
                ),
                _spec(
                    ApproverCode.BUSINESS_VICE_PRESIDENT,
                    ApprovalAction.APPROVE,
                    "单次请假超过5个工作日。",
                ),
            )
        )
        if days > Decimal(15):
            specs.append(
                _spec(
                    ApproverCode.GENERAL_MANAGER,
                    ApprovalAction.APPROVE,
                    "单次请假超过15个工作日。",
                )
            )

    if hr_required and not any(item.approver is ApproverCode.HUMAN_RESOURCES for item in specs):
        specs.append(
            _spec(
                ApproverCode.HUMAN_RESOURCES,
                ApprovalAction.REVIEW,
                "本次申请触发人力资源部复核条件。",
            )
        )
    if hr_required:
        sources.append(_LEAVE_HR_REVIEW_SOURCE)

    notes = ("当前按普通员工审批规则判断；部门负责人请假适用单独规则。",)
    if request.leave_type is None:
        notes += ("如属于连续病假或特殊假期，可能额外触发人力资源部复核。",)

    return _RuleDecision(
        level=level,
        steps=_deduplicate_specs(specs),
        special_conditions=conditions,
        clarification_question=None,
        notes=notes,
        sources=tuple(sources),
    )


def _decision_for(
    request: _ResolvedApprovalRequest,
) -> _RuleDecision:
    if request.application_type is ApprovalApplicationType.PURCHASE:
        return _purchase_decision(request)
    if request.application_type is ApprovalApplicationType.TRAVEL:
        return _travel_decision(request)
    if request.application_type is ApprovalApplicationType.EXPENSE_REIMBURSEMENT:
        return _expense_decision(request)
    if request.application_type is ApprovalApplicationType.LEAVE:
        return _leave_decision(request)

    return _RuleDecision(
        level=None,
        steps=(),
        special_conditions=(),
        clarification_question=(
            "请说明要判断哪类审批：采购、出差申请、差旅报销、普通费用报销还是请假。"
        ),
        notes=(),
        sources=(),
    )


def _build_steps(specs: Sequence[_StepSpec]) -> tuple[ApprovalStep, ...]:
    return tuple(
        ApprovalStep(
            sequence=index,
            approver=item.approver,
            display_name=_APPROVER_NAMES[item.approver],
            action=item.action,
            reason=item.reason,
        )
        for index, item in enumerate(specs, start=1)
    )


def _build_citations(
    sources: Sequence[PolicyArticleRef],
    *,
    catalog: ApprovalPolicyCatalog,
) -> tuple[PolicyCitation, ...]:
    unique_sources = tuple(dict.fromkeys(sources))
    return tuple(
        catalog.citation(source, source_id=f"S{index}")
        for index, source in enumerate(unique_sources, start=1)
    )


def _format_decimal(value: Decimal) -> str:
    formatted = f"{value:,.2f}"
    return formatted.rstrip("0").rstrip(".")


def _source_suffix(citations: Sequence[PolicyCitation]) -> str:
    return " ".join(f"[{item.source_id}]" for item in citations)


def _format_reply(result: ApprovalCheckResult) -> str:
    lines: list[str] = []

    if result.clarification_question is not None:
        lines.append("还需要补充信息后才能准确判断审批路线。")
        lines.append(result.clarification_question)
    else:
        if result.approval_level is not None:
            lines.append(f"审批层级：{_LEVEL_NAMES[result.approval_level]}。")
        if result.amount is not None:
            lines.append(f"规则计算金额：{_format_decimal(result.amount)}元。")
        if result.leave_days is not None:
            lines.append(f"规则计算请假天数：{_format_decimal(result.leave_days)}个工作日。")
        lines.append("审批路线：")
        for step in result.steps:
            lines.append(f"{step.sequence}. {step.display_name}（{_ACTION_NAMES[step.action]}）")

    if result.special_conditions:
        lines.append("触发条件：" + "；".join(result.special_conditions) + "。")
    lines.extend(f"提示：{note}" for note in result.notes)
    if result.citations:
        lines.append(f"制度依据：{_source_suffix(result.citations)}")
    return "\n".join(lines)


class ApprovalRuleChecker:
    """基于确定性规则计算审批层级、节点和制度引用。"""

    def __init__(self, *, catalog: ApprovalPolicyCatalog) -> None:
        self._catalog = catalog

    @classmethod
    def from_policy_directory(
        cls,
        directory: str | Path,
    ) -> ApprovalRuleChecker:
        return cls(catalog=ApprovalPolicyCatalog.from_directory(directory))

    async def check(self, user_input: str) -> ApprovalCheckAnswer:
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")

        request = _resolve_request(normalized_input)
        decision = _decision_for(request)
        citations = _build_citations(
            decision.sources,
            catalog=self._catalog,
        )
        result = ApprovalCheckResult(
            application_type=request.application_type,
            approval_level=decision.level,
            amount=request.amount,
            leave_days=request.leave_days,
            steps=_build_steps(decision.steps),
            special_conditions=decision.special_conditions,
            clarification_question=decision.clarification_question,
            notes=decision.notes,
            citations=citations,
        )
        return ApprovalCheckAnswer(
            request=normalized_input,
            result=result,
            reply=_format_reply(result),
        )
