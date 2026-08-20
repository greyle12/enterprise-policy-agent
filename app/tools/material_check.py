from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_context import PolicyCitation
from app.schemas.chunk import PolicyChunk
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckAnswer,
    MaterialCheckMode,
    MaterialCheckResult,
    MaterialRequirement,
    MissingMaterial,
    ProvidedMaterial,
)


@dataclass(frozen=True, slots=True)
class _PolicyArticleRef:
    document_id: str
    article_label: str


@dataclass(frozen=True, slots=True)
class _RuleMaterial:
    material_type: str
    display_name: str
    aliases: tuple[str, ...]
    reason: str
    source: _PolicyArticleRef
    required_count: int = 1
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class _ResolvedRequest:
    application_type: ApplicationType | None
    mode: MaterialCheckMode
    provided_counts: Counter[str]
    purchase_amount: Decimal | None
    is_it_purchase: bool
    is_goods_purchase: bool
    is_service_purchase: bool
    is_emergency_purchase: bool
    expense_category: str | None
    leave_type: str | None
    leave_days: Decimal | None
    explicitly_no_materials: bool
    has_material_reference: bool


_TRAVEL_SOURCE = _PolicyArticleRef(
    "TRAVEL_POLICY_001",
    "第十六条",
)
_PURCHASE_ATTACHMENT_SOURCE = _PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十条",
)
_PURCHASE_INQUIRY_SOURCE = _PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十七条",
)
_PURCHASE_COMPARISON_SOURCE = _PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十八条",
)
_PURCHASE_TENDER_SOURCE = _PolicyArticleRef(
    "PROCUREMENT_POLICY_001",
    "第十九条",
)
_EXPENSE_GENERAL_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十三条",
)
_EXPENSE_MEETING_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十四条",
)
_EXPENSE_TRAINING_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十五条",
)
_EXPENSE_ENTERTAINMENT_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十六条",
)
_LEAVE_SICK_SOURCE = _PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第十二条",
)
_LEAVE_MARRIAGE_SOURCE = _PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第十六条",
)
_LEAVE_PARENTAL_SOURCE = _PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第十八条",
)
_LEAVE_APPLICATION_SOURCE = _PolicyArticleRef(
    "LEAVE_POLICY_001",
    "第二十四条",
)


def _rule_material(
    material_type: str,
    display_name: str,
    aliases: Sequence[str],
    reason: str,
    source: _PolicyArticleRef,
    *,
    required_count: int = 1,
    sensitive: bool = False,
) -> _RuleMaterial:
    return _RuleMaterial(
        material_type=material_type,
        display_name=display_name,
        aliases=tuple(aliases),
        reason=reason,
        source=source,
        required_count=required_count,
        sensitive=sensitive,
    )


_TRAVEL_MATERIALS = (
    _rule_material(
        "approved_travel_application",
        "已审批的出差申请单",
        ("已审批的出差申请单", "出差审批单", "出差申请单"),
        "差旅报销必须关联已经审批的出差申请。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "travel_itinerary",
        "差旅行程单",
        ("差旅行程单", "行程单", "差旅行程"),
        "差旅报销必须提供能够核对出差路线和时间的行程材料。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "transportation_receipts",
        "交通票据或电子客票凭证",
        ("交通票据", "电子客票", "车票", "机票", "交通凭证"),
        "交通费用需要有效票据或电子客票凭证。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "accommodation_invoice",
        "住宿发票",
        ("住宿发票", "酒店发票"),
        "住宿费用需要合规住宿发票。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "hotel_detail",
        "酒店住宿明细",
        ("酒店住宿明细", "住宿明细", "酒店明细"),
        "住宿费用需要酒店住宿明细用于核对入住信息。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "payment_records",
        "支付记录",
        ("支付记录", "付款记录", "支付截图", "付款截图"),
        "差旅费用需要支付记录用于核对实际支出。",
        _TRAVEL_SOURCE,
    ),
    _rule_material(
        "business_trip_result",
        "出差成果或工作说明",
        ("出差成果", "工作说明", "出差总结", "会议纪要"),
        "差旅报销需要说明出差形成的工作成果。",
        _TRAVEL_SOURCE,
    ),
)

_PURCHASE_MATERIALS = (
    _rule_material(
        "technical_requirement",
        "技术需求说明",
        ("技术需求说明", "技术需求"),
        "信息技术类采购应提供可供技术评审的需求说明。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "product_specification",
        "产品规格说明",
        ("产品规格说明", "规格说明", "产品规格"),
        "货物采购需要明确产品规格、型号或技术参数。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "service_scope",
        "服务范围说明",
        ("服务范围说明", "服务范围", "服务需求说明"),
        "服务采购需要明确服务范围和交付内容。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "budget_proof",
        "项目预算证明",
        ("项目预算证明", "预算证明", "预算材料"),
        "采购附件可以包含能够证明预算来源的材料。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "market_inquiry",
        "市场询价材料",
        ("市场询价材料", "询价材料", "询价记录"),
        "采购附件可以包含市场询价过程材料。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "historical_price",
        "历史采购价格",
        ("历史采购价格", "历史价格"),
        "采购附件可以使用历史采购价格作为价格依据。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "quotation",
        "供应商报价单",
        ("供应商报价单", "供应商报价", "报价单", "报价"),
        "采购金额达到询价或比价门槛时，需要足够数量的有效供应商报价。",
        _PURCHASE_INQUIRY_SOURCE,
    ),
    _rule_material(
        "comparison_record",
        "书面比价记录",
        ("书面比价记录", "比价记录", "比价表"),
        "超过五万元、不超过二十万元的采购应形成书面比价记录。",
        _PURCHASE_COMPARISON_SOURCE,
    ),
    _rule_material(
        "emergency_purchase_explanation",
        "紧急采购说明",
        ("紧急采购说明", "紧急说明"),
        "紧急采购需要说明无法按普通采购周期办理的原因。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "it_review_opinion",
        "信息技术评审意见",
        ("信息技术评审意见", "IT评审意见", "技术评审意见"),
        "信息技术类采购需要保留信息技术评审意见。",
        _PURCHASE_ATTACHMENT_SOURCE,
    ),
    _rule_material(
        "tender_document",
        "招标或竞争性采购材料",
        ("招标文件", "投标文件", "竞争性采购材料"),
        "超过二十万元的采购原则上应采用招标或经批准的竞争性采购方式。",
        _PURCHASE_TENDER_SOURCE,
    ),
)

_EXPENSE_GENERAL_MATERIALS = (
    _rule_material(
        "expense_reimbursement_form",
        "费用报销单",
        ("费用报销单", "报销单"),
        "普通费用报销必须提交费用报销单。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "valid_invoice",
        "合规发票或有效凭证",
        ("合规发票", "有效凭证", "发票", "报销凭证"),
        "费用报销必须有合规发票或其他有效凭证。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "payment_records",
        "支付记录",
        ("支付记录", "付款记录", "支付截图", "付款截图"),
        "费用报销需要支付记录用于核对实际付款。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "expense_details",
        "费用明细",
        ("费用明细", "消费明细", "费用清单"),
        "费用报销必须说明费用构成。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "business_purpose_statement",
        "业务事由说明",
        ("业务事由说明", "业务事由", "用途说明"),
        "费用报销必须说明真实、具体的业务事由。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "prior_approval_record",
        "事前审批记录",
        ("事前审批记录", "审批记录", "事前审批"),
        "需要事前审批的费用应提供审批记录。",
        _EXPENSE_GENERAL_SOURCE,
    ),
    _rule_material(
        "budget_reference",
        "预算编号或成本中心",
        ("预算编号", "成本中心", "预算信息"),
        "费用报销必须关联预算编号或成本中心。",
        _EXPENSE_GENERAL_SOURCE,
    ),
)

_EXPENSE_CATEGORY_MATERIALS = {
    "meeting": (
        _rule_material(
            "meeting_notice",
            "会议通知或议程",
            ("会议通知", "会议议程", "议程"),
            "会议费用报销需要提供会议通知或议程。",
            _EXPENSE_MEETING_SOURCE,
        ),
        _rule_material(
            "attendee_list",
            "参会人员名单",
            ("参会人员名单", "参会名单", "人员名单"),
            "会议费用报销需要提供参会人员名单。",
            _EXPENSE_MEETING_SOURCE,
        ),
        _rule_material(
            "venue_or_service_order",
            "会议场地或服务订单",
            ("会议场地订单", "会议服务订单", "场地订单", "服务订单"),
            "会议费用报销需要提供场地或服务订单。",
            _EXPENSE_MEETING_SOURCE,
        ),
    ),
    "training": (
        _rule_material(
            "training_notice",
            "培训通知或课程介绍",
            ("培训通知", "课程介绍", "培训介绍"),
            "培训费用报销需要提供培训通知或课程介绍。",
            _EXPENSE_TRAINING_SOURCE,
        ),
        _rule_material(
            "registration_record",
            "报名记录",
            ("报名记录", "报名凭证"),
            "培训费用报销需要提供报名记录。",
            _EXPENSE_TRAINING_SOURCE,
        ),
        _rule_material(
            "completion_proof",
            "完课证明或考试结果",
            ("完课证明", "结业证明", "考试结果", "培训证书"),
            "培训费用报销需要提供完课证明或考试结果。",
            _EXPENSE_TRAINING_SOURCE,
        ),
    ),
    "business_entertainment": (
        _rule_material(
            "entertainment_parties",
            "招待对象及参与人员",
            ("招待对象", "参与人员", "客户名单"),
            "业务招待报销需要说明招待对象和参与人员。",
            _EXPENSE_ENTERTAINMENT_SOURCE,
        ),
        _rule_material(
            "dining_invoice",
            "餐饮发票",
            ("餐饮发票", "餐费发票"),
            "业务招待报销需要提供餐饮发票。",
            _EXPENSE_ENTERTAINMENT_SOURCE,
        ),
    ),
}

_LEAVE_MATERIALS = (
    _rule_material(
        "medical_proof",
        "医疗证明（诊断证明、病假建议书、门诊或住院记录等任一项）",
        (
            "医疗证明",
            "诊断证明",
            "病假建议书",
            "门诊记录",
            "住院记录",
            "医院证明",
        ),
        "连续病假超过一个工作日，原则上应提供一种有效医疗证明。",
        _LEAVE_SICK_SOURCE,
        sensitive=True,
    ),
    _rule_material(
        "marriage_registration_certificate",
        "结婚登记证明",
        ("结婚登记证明", "结婚证", "婚姻证明"),
        "申请婚假时应提供结婚登记证明。",
        _LEAVE_MARRIAGE_SOURCE,
        sensitive=True,
    ),
)

_ALL_RULE_MATERIALS = (
    *_TRAVEL_MATERIALS,
    *_PURCHASE_MATERIALS,
    *_EXPENSE_GENERAL_MATERIALS,
    *(material for materials in _EXPENSE_CATEGORY_MATERIALS.values() for material in materials),
    *_LEAVE_MATERIALS,
)

_MATERIALS_BY_TYPE = {material.material_type: material for material in _ALL_RULE_MATERIALS}

_COMPARISON_CUES = (
    "齐全",
    "还缺",
    "缺什么",
    "缺哪些",
    "帮我检查",
    "检查一下",
    "我有",
    "已有",
    "已经有",
    "准备了",
    "已准备",
    "提供了",
    "已提供",
    "提交了",
    "手上有",
    "还没有",
    "目前没有",
    "尚未提供",
    "未提供",
)
_NO_MATERIALS_CUES = (
    "没有任何材料",
    "什么材料都没有",
    "还没准备材料",
    "尚未准备材料",
)
_NEGATIVE_CUES = (
    "没有",
    "还没有",
    "没准备",
    "未准备",
    "未提供",
    "没提供",
    "缺少",
)


class PolicyArticleCatalog:
    """把确定性业务规则绑定到真实制度条款。"""

    def __init__(self, chunks: Iterable[PolicyChunk]) -> None:
        self._chunks = {(chunk.document_id, chunk.article_label): chunk for chunk in chunks}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
    ) -> PolicyArticleCatalog:
        return cls(chunk_policy_directory(directory))

    def citation(
        self,
        reference: _PolicyArticleRef,
        *,
        source_id: str,
    ) -> PolicyCitation:
        key = (reference.document_id, reference.article_label)
        chunk = self._chunks.get(key)

        if chunk is None:
            raise RuntimeError(
                "material rule references missing policy article: "
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


def _detect_application_type(text: str) -> ApplicationType | None:
    if any(word in text for word in ("差旅", "出差")) and (
        "报销" in text
        or any(
            material_word in text
            for material_word in (
                "申请单",
                "行程单",
                "交通票据",
                "住宿发票",
                "住宿明细",
                "出差成果",
            )
        )
    ):
        return ApplicationType.TRAVEL_REIMBURSEMENT

    if "费用报销" in text or (
        "报销" in text
        and any(
            word in text
            for word in (
                "会议费",
                "培训费",
                "招待费",
                "办公费",
                "通信费",
                "市内交通费",
                "餐费",
            )
        )
    ):
        return ApplicationType.EXPENSE_REIMBURSEMENT

    if any(
        word in text
        for word in (
            "采购",
            "购买",
            "买电脑",
            "买显示器",
            "买设备",
        )
    ):
        return ApplicationType.PURCHASE

    if any(
        word in text
        for word in (
            "请假",
            "年假",
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
        return ApplicationType.LEAVE

    return None


def _detect_expense_category(text: str) -> str | None:
    if "会议" in text:
        return "meeting"
    if "培训" in text or "课程" in text:
        return "training"
    if "招待" in text or "客户餐费" in text:
        return "business_entertainment"
    return None


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


def _extract_decimal(
    raw_value: str,
    unit: str | None = None,
) -> Decimal | None:
    try:
        value = Decimal(raw_value)
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


def _detect_purchase_amount(text: str) -> Decimal | None:
    quantity_price_match = re.search(
        r"(?P<quantity>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"
        r"\s*(?:台|个|套|件|份)"
        r".{0,20}?每(?:台|个|套|件|份)?\s*"
        r"(?P<unit_price>\d+(?:\.\d+)?)\s*元",
        text,
    )

    if quantity_price_match is not None:
        quantity = _parse_small_number(quantity_price_match.group("quantity"))
        unit_price = _extract_decimal(quantity_price_match.group("unit_price"))
        if quantity is not None and unit_price is not None:
            return quantity * unit_price

    amount_match = re.search(
        r"(?:总金额|总价|预算|金额|预计)[为是约大概:\s]*"
        r"(?P<amount>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>万元|万|元|块)",
        text,
    )
    if amount_match is None:
        amount_match = re.search(
            r"(?P<amount>\d+(?:\.\d+)?)\s*"
            r"(?P<unit>万元|万|元|块)(?:的)?(?:采购|电脑|设备|服务)",
            text,
        )

    if amount_match is None:
        return None

    return _extract_decimal(
        amount_match.group("amount"),
        amount_match.group("unit"),
    )


def _detect_leave_days(text: str) -> Decimal | None:
    match = re.search(
        r"(?P<days>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"
        r"\s*(?:个)?(?:工作)?天",
        text,
    )
    if match is None:
        return None
    return _parse_small_number(match.group("days"))


def _is_negated(text: str, alias_start: int) -> bool:
    prefix = text[max(0, alias_start - 8) : alias_start]
    return any(cue in prefix for cue in _NEGATIVE_CUES)


def _quotation_count(text: str) -> int | None:
    patterns = (
        r"(?P<count>\d+)\s*家(?:供应商)?(?:的)?(?:有效)?报价",
        r"(?P<count>\d+)\s*份(?:供应商)?报价",
        r"(?:报价|报价单)(?P<count>\d+)\s*份",
    )

    for pattern in patterns:
        match = re.search(pattern, text)
        if match is not None:
            return int(match.group("count"))

    chinese_counts = {
        "一家": 1,
        "一份": 1,
        "两家": 2,
        "二家": 2,
        "两份": 2,
        "二份": 2,
        "三家": 3,
        "三份": 3,
    }
    for token, count in chinese_counts.items():
        if token in text and "报价" in text:
            return count

    return None


def _extract_provided_counts(
    text: str,
    *,
    mode: MaterialCheckMode,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if mode is not MaterialCheckMode.COMPARISON:
        return counts

    for material in _ALL_RULE_MATERIALS:
        for alias in sorted(
            material.aliases,
            key=len,
            reverse=True,
        ):
            match = re.search(re.escape(alias), text)
            if match is None or _is_negated(text, match.start()):
                continue
            counts[material.material_type] = 1
            break

    if counts.get("quotation"):
        counts["quotation"] = _quotation_count(text) or 1

    return counts


def _has_material_reference(text: str) -> bool:
    return any(alias in text for material in _ALL_RULE_MATERIALS for alias in material.aliases)


def _resolve_request(text: str) -> _ResolvedRequest:
    mode = (
        MaterialCheckMode.COMPARISON
        if any(cue in text for cue in _COMPARISON_CUES)
        else MaterialCheckMode.REQUIREMENTS
    )

    explicitly_no_materials = any(cue in text for cue in _NO_MATERIALS_CUES)

    return _ResolvedRequest(
        application_type=_detect_application_type(text),
        mode=mode,
        provided_counts=_extract_provided_counts(
            text,
            mode=mode,
        ),
        purchase_amount=_detect_purchase_amount(text),
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
        is_goods_purchase=any(
            word in text
            for word in (
                "电脑",
                "显示器",
                "服务器",
                "设备",
                "用品",
                "商品",
                "硬件",
            )
        ),
        is_service_purchase=any(
            word in text
            for word in (
                "服务",
                "咨询",
                "外包",
                "订阅",
                "培训",
                "维修",
            )
        ),
        is_emergency_purchase="紧急" in text,
        expense_category=_detect_expense_category(text),
        leave_type=_detect_leave_type(text),
        leave_days=_detect_leave_days(text),
        explicitly_no_materials=explicitly_no_materials,
        has_material_reference=_has_material_reference(text),
    )


def _material_by_type(material_type: str) -> _RuleMaterial:
    return _MATERIALS_BY_TYPE[material_type]


def _purchase_requirements(
    request: _ResolvedRequest,
) -> tuple[list[_RuleMaterial], list[str], str | None]:
    requirements: list[_RuleMaterial] = []
    notes = ["采购申请附件应根据采购事项选择，制度并未要求所有附件一律提交。"]

    if request.is_it_purchase:
        requirements.extend(
            (
                _material_by_type("technical_requirement"),
                _material_by_type("it_review_opinion"),
            )
        )

    if request.is_goods_purchase:
        requirements.append(_material_by_type("product_specification"))
    elif request.is_service_purchase:
        requirements.append(_material_by_type("service_scope"))

    if request.is_emergency_purchase:
        requirements.append(_material_by_type("emergency_purchase_explanation"))

    amount = request.purchase_amount
    if amount is None:
        notes.append(
            "报价数量取决于预计采购总金额：超过5,000元至50,000元原则上至少2家，"
            "超过50,000元至200,000元原则上至少3家并形成比价记录，"
            "超过200,000元原则上采用招标或经批准的竞争性采购。"
        )
        return (
            requirements,
            notes,
            "请补充预计采购总金额，以便确定报价数量和采购材料。",
        )

    if amount > Decimal(200000):
        requirements.append(_material_by_type("tender_document"))
    elif amount > Decimal(50000):
        quotation = _material_by_type("quotation")
        requirements.append(
            _RuleMaterial(
                material_type=quotation.material_type,
                display_name=quotation.display_name,
                aliases=quotation.aliases,
                reason=(
                    "超过五万元、不超过二十万元的采购，原则上需要不少于三家合格供应商有效报价。"
                ),
                source=_PURCHASE_COMPARISON_SOURCE,
                required_count=3,
            )
        )
        requirements.append(_material_by_type("comparison_record"))
    elif amount > Decimal(5000):
        quotation = _material_by_type("quotation")
        requirements.append(
            _RuleMaterial(
                material_type=quotation.material_type,
                display_name=quotation.display_name,
                aliases=quotation.aliases,
                reason=("超过五千元、不超过五万元的采购，原则上需要不少于两家合格供应商有效报价。"),
                source=_PURCHASE_INQUIRY_SOURCE,
                required_count=2,
            )
        )
    else:
        notes.append("预计总金额不超过5,000元时，制度未规定必须取得多家供应商报价。")

    return requirements, notes, None


def _leave_requirements(
    request: _ResolvedRequest,
) -> tuple[list[_RuleMaterial], list[str], str | None]:
    leave_type = request.leave_type
    if leave_type is None:
        return (
            [],
            ["不同假期类型的证明材料要求不同。"],
            "请说明具体假期类型，例如年假、病假、婚假或调休。",
        )

    if leave_type == "sick":
        if request.leave_days is None:
            return (
                [],
                [
                    (
                        "病假不超过一个工作日原则上可以不提交医疗证明；"
                        "连续超过一个工作日原则上应提供有效医疗证明。"
                    )
                ],
                "请补充连续请病假的工作日数。",
            )
        if request.leave_days > Decimal(1):
            return (
                [_material_by_type("medical_proof")],
                ["医疗材料属于个人敏感信息，不应在普通日志中记录原文。"],
                None,
            )
        return (
            [],
            [
                (
                    "病假不超过一个工作日，原则上可以不提交医疗证明；"
                    "直属经理或人力资源部仍可根据实际情况要求补充说明。"
                )
            ],
            None,
        )

    if leave_type == "marriage":
        return (
            [_material_by_type("marriage_registration_certificate")],
            [],
            None,
        )

    if leave_type == "parental":
        return (
            [],
            [
                (
                    "产假、陪产假和育儿假需按员工所在地规定及人力资源部要求"
                    "提交相应证明材料，现有制度未列出统一清单。"
                )
            ],
            None,
        )

    if leave_type == "work_injury":
        return (
            [_material_by_type("medical_proof")],
            ["工伤认定完成前，可根据医疗材料先记录为病假或待确认假期。"],
            None,
        )

    return (
        [],
        [("现有制度未要求该假期类型提交固定证明附件，但请假申请字段和工作交接信息仍需完整填写。")],
        None,
    )


def _requirements_for(
    request: _ResolvedRequest,
) -> tuple[list[_RuleMaterial], list[str], str | None]:
    application_type = request.application_type

    if application_type is ApplicationType.TRAVEL_REIMBURSEMENT:
        return list(_TRAVEL_MATERIALS), [], None

    if application_type is ApplicationType.PURCHASE:
        return _purchase_requirements(request)

    if application_type is ApplicationType.EXPENSE_REIMBURSEMENT:
        requirements = list(_EXPENSE_GENERAL_MATERIALS)
        if request.expense_category is not None:
            requirements.extend(_EXPENSE_CATEGORY_MATERIALS[request.expense_category])
        return requirements, [], None

    if application_type is ApplicationType.LEAVE:
        return _leave_requirements(request)

    return (
        [],
        [],
        "请说明要办理的事项：采购、差旅报销、普通费用报销还是请假。",
    )


def _deduplicate_requirements(
    requirements: Iterable[_RuleMaterial],
) -> list[_RuleMaterial]:
    result: list[_RuleMaterial] = []
    positions: dict[str, int] = {}

    for requirement in requirements:
        position = positions.get(requirement.material_type)
        if position is None:
            positions[requirement.material_type] = len(result)
            result.append(requirement)
            continue

        existing = result[position]
        if requirement.required_count > existing.required_count:
            result[position] = requirement

    return result


def _build_citations(
    requirements: Sequence[_RuleMaterial],
    *,
    catalog: PolicyArticleCatalog,
    extra_sources: Sequence[_PolicyArticleRef] = (),
) -> tuple[PolicyCitation, ...]:
    references: list[_PolicyArticleRef] = []

    for reference in (
        *(material.source for material in requirements),
        *extra_sources,
    ):
        if reference not in references:
            references.append(reference)

    return tuple(
        catalog.citation(
            reference,
            source_id=f"S{index}",
        )
        for index, reference in enumerate(references, start=1)
    )


def _extra_sources_for(
    request: _ResolvedRequest,
) -> tuple[_PolicyArticleRef, ...]:
    if request.application_type is ApplicationType.PURCHASE:
        sources = [_PURCHASE_ATTACHMENT_SOURCE]
        if request.purchase_amount is None:
            sources.extend(
                (
                    _PURCHASE_INQUIRY_SOURCE,
                    _PURCHASE_COMPARISON_SOURCE,
                    _PURCHASE_TENDER_SOURCE,
                )
            )
        return tuple(sources)

    if request.application_type is ApplicationType.LEAVE:
        if request.leave_type == "sick":
            return (_LEAVE_SICK_SOURCE,)
        if request.leave_type == "parental":
            return (_LEAVE_PARENTAL_SOURCE,)
        return (_LEAVE_APPLICATION_SOURCE,)

    if request.application_type is ApplicationType.EXPENSE_REIMBURSEMENT:
        return (_EXPENSE_GENERAL_SOURCE,)

    if request.application_type is ApplicationType.TRAVEL_REIMBURSEMENT:
        return (_TRAVEL_SOURCE,)

    return ()


def _missing_materials(
    requirements: Sequence[_RuleMaterial],
    provided_counts: Counter[str],
) -> tuple[MissingMaterial, ...]:
    missing: list[MissingMaterial] = []

    for requirement in requirements:
        provided_count = provided_counts.get(
            requirement.material_type,
            0,
        )
        missing_count = max(
            requirement.required_count - provided_count,
            0,
        )
        if missing_count == 0:
            continue
        missing.append(
            MissingMaterial(
                material_type=requirement.material_type,
                display_name=requirement.display_name,
                missing_count=missing_count,
                reason=requirement.reason,
                sensitive=requirement.sensitive,
            )
        )

    return tuple(missing)


def _provided_materials(
    provided_counts: Counter[str],
) -> tuple[ProvidedMaterial, ...]:
    return tuple(
        ProvidedMaterial(
            material_type=material_type,
            display_name=_MATERIALS_BY_TYPE[material_type].display_name,
            provided_count=count,
        )
        for material_type, count in provided_counts.items()
        if material_type in _MATERIALS_BY_TYPE and count > 0
    )


def _source_suffix(citations: Sequence[PolicyCitation]) -> str:
    if not citations:
        return ""
    return " " + " ".join(f"[{citation.source_id}]" for citation in citations)


def _format_reply(result: MaterialCheckResult) -> str:
    if result.application_type is None:
        return result.clarification_question or "请补充办理事项。"

    citation_suffix = _source_suffix(result.citations)
    lines: list[str] = []

    if result.mode is MaterialCheckMode.REQUIREMENTS:
        if result.required_materials:
            lines.append("需要准备的材料如下：")
            for index, item in enumerate(
                result.required_materials,
                start=1,
            ):
                count_text = f"（至少{item.required_count}份）" if item.required_count > 1 else ""
                sensitive_text = "（敏感材料）" if item.sensitive else ""
                lines.append(f"{index}. {item.display_name}{count_text}{sensitive_text}")
        else:
            lines.append("现有制度没有列出固定的必交证明附件。")
    elif result.materials_complete is None:
        lines.append("还需要补充信息后才能完成精确材料检查。")
    elif result.materials_complete:
        lines.append("按当前提供的信息，必需材料已齐全。")
    else:
        lines.append("按当前提供的信息，材料尚未齐全，还缺：")
        for index, item in enumerate(
            result.missing_materials,
            start=1,
        ):
            count_text = f"（还缺{item.missing_count}份）" if item.missing_count > 1 else ""
            sensitive_text = "（敏感材料）" if item.sensitive else ""
            lines.append(f"{index}. {item.display_name}{count_text}{sensitive_text}")

    lines.extend(f"提示：{note}" for note in result.notes)

    if result.clarification_question:
        lines.append(result.clarification_question)

    if citation_suffix:
        lines.append(f"制度依据：{citation_suffix.strip()}")

    return "\n".join(lines)


class RequiredMaterialsChecker:
    """基于确定性规则检查材料，并返回可追溯制度引用。"""

    def __init__(self, *, catalog: PolicyArticleCatalog) -> None:
        self._catalog = catalog

    @classmethod
    def from_policy_directory(
        cls,
        directory: str | Path,
    ) -> RequiredMaterialsChecker:
        return cls(catalog=PolicyArticleCatalog.from_directory(directory))

    async def check(self, user_input: str) -> MaterialCheckAnswer:
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")

        request = _resolve_request(normalized_input)
        raw_requirements, notes, clarification = _requirements_for(request)
        requirements = _deduplicate_requirements(raw_requirements)
        citations = _build_citations(
            requirements,
            catalog=self._catalog,
            extra_sources=_extra_sources_for(request),
        )

        provided = _provided_materials(request.provided_counts)
        missing: tuple[MissingMaterial, ...] = ()
        materials_complete: bool | None = None

        if request.mode is MaterialCheckMode.COMPARISON and request.application_type is not None:
            if (
                not request.provided_counts
                and not request.explicitly_no_materials
                and not request.has_material_reference
                and clarification is None
            ):
                clarification = "请列出你已经准备的材料，我才能逐项检查缺失内容。"
            elif clarification is None:
                missing = _missing_materials(
                    requirements,
                    request.provided_counts,
                )
                materials_complete = not missing

        result = MaterialCheckResult(
            application_type=request.application_type,
            mode=request.mode,
            required_materials=tuple(
                MaterialRequirement(
                    material_type=item.material_type,
                    display_name=item.display_name,
                    reason=item.reason,
                    required_count=item.required_count,
                    sensitive=item.sensitive,
                )
                for item in requirements
            ),
            provided_materials=provided,
            missing_materials=missing,
            materials_complete=materials_complete,
            clarification_question=clarification,
            notes=tuple(notes),
            citations=citations,
        )

        return MaterialCheckAnswer(
            request=normalized_input,
            result=result,
            reply=_format_reply(result),
        )
