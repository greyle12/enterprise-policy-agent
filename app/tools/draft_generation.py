from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_context import PolicyCitation
from app.schemas.chunk import PolicyChunk
from app.tools.approval_models import ApprovalCheckAnswer, ApprovalCheckResult
from app.tools.draft_models import (
    ApplicationDraft,
    DraftAuditMetadata,
    DraftField,
    DraftFieldSource,
    DraftGenerationAnswer,
    DraftGenerationResult,
    DraftPolicySnapshot,
    DraftStatus,
    DraftUserContext,
    DraftValidationIssue,
    MissingDraftField,
    ValidationSeverity,
)
from app.tools.material_models import ApplicationType, MaterialCheckAnswer, MaterialCheckResult


class _MaterialChecker(Protocol):
    async def check(self, user_input: str) -> MaterialCheckAnswer:
        """检查当前消息中声明的申请材料。"""

        ...


class _ApprovalChecker(Protocol):
    async def check(self, user_input: str) -> ApprovalCheckAnswer:
        """根据当前消息计算审批路线。"""

        ...


@dataclass(frozen=True, slots=True)
class _PolicyArticleRef:
    document_id: str
    article_label: str


@dataclass(frozen=True, slots=True)
class _ExtractionResult:
    fields: tuple[DraftField, ...]
    missing_fields: tuple[MissingDraftField, ...]
    validation_issues: tuple[DraftValidationIssue, ...]


_PURCHASE_REQUIRED_SOURCE = _PolicyArticleRef("PROCUREMENT_POLICY_001", "第九条")
_TRAVEL_APPLICATION_SOURCE = _PolicyArticleRef("TRAVEL_POLICY_001", "第四条")
_TRAVEL_MATERIAL_SOURCE = _PolicyArticleRef("TRAVEL_POLICY_001", "第十六条")
_LEAVE_REQUIRED_SOURCE = _PolicyArticleRef("LEAVE_POLICY_001", "第二十四条")
_LEAVE_HANDOVER_SOURCE = _PolicyArticleRef("LEAVE_POLICY_001", "第二十五条")
_LEAVE_STATUS_SOURCE = _PolicyArticleRef("LEAVE_POLICY_001", "第二十六条")
_EXPENSE_APPLICATION_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十二条",
)
_EXPENSE_MATERIAL_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第十三条",
)
_EXPENSE_DRAFT_SOURCE = _PolicyArticleRef(
    "EXPENSE_REIMBURSEMENT_GUIDE_001",
    "第三十七条",
)

_POLICY_REFS = {
    ApplicationType.PURCHASE: (_PURCHASE_REQUIRED_SOURCE,),
    ApplicationType.TRAVEL_REIMBURSEMENT: (
        _TRAVEL_APPLICATION_SOURCE,
        _TRAVEL_MATERIAL_SOURCE,
    ),
    ApplicationType.LEAVE: (
        _LEAVE_REQUIRED_SOURCE,
        _LEAVE_HANDOVER_SOURCE,
        _LEAVE_STATUS_SOURCE,
    ),
    ApplicationType.EXPENSE_REIMBURSEMENT: (
        _EXPENSE_APPLICATION_SOURCE,
        _EXPENSE_MATERIAL_SOURCE,
        _EXPENSE_DRAFT_SOURCE,
    ),
}

_DRAFT_TITLES = {
    ApplicationType.PURCHASE: "采购申请草稿",
    ApplicationType.TRAVEL_REIMBURSEMENT: "差旅报销草稿",
    ApplicationType.LEAVE: "请假申请草稿",
    ApplicationType.EXPENSE_REIMBURSEMENT: "费用报销草稿",
}

_DRAFT_PREFIXES = {
    ApplicationType.PURCHASE: "PURCHASE",
    ApplicationType.TRAVEL_REIMBURSEMENT: "TRAVEL-RMB",
    ApplicationType.LEAVE: "LEAVE",
    ApplicationType.EXPENSE_REIMBURSEMENT: "EXPENSE",
}

_IT_PURCHASE_CUES = (
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

_SERVICE_PURCHASE_CUES = (
    "服务",
    "咨询",
    "外包",
    "订阅",
    "培训",
    "维修",
)

_LEAVE_TYPES = (
    (("病假",), "SICK_LEAVE"),
    (("婚假",), "MARRIAGE_LEAVE"),
    (("丧假",), "BEREAVEMENT_LEAVE"),
    (("陪产假",), "PATERNITY_LEAVE"),
    (("产假",), "MATERNITY_LEAVE"),
    (("育儿假",), "PARENTAL_LEAVE"),
    (("工伤假", "工伤休假"), "WORK_INJURY_LEAVE"),
    (("年假", "年休假"), "ANNUAL_LEAVE"),
    (("调休",), "COMPENSATORY_LEAVE"),
    (("事假",), "PERSONAL_LEAVE"),
)

_EXPENSE_CATEGORIES = (
    (("业务招待", "招待费", "客户餐费"), "BUSINESS_ENTERTAINMENT"),
    (("会议费", "会议费用"), "MEETING"),
    (("培训费", "培训费用", "课程费"), "TRAINING"),
    (("办公费", "办公费用"), "OFFICE"),
    (("通信费", "网络费"), "COMMUNICATION"),
    (("市内交通费", "打车费", "出租车费"), "LOCAL_TRANSPORTATION"),
)

_VALUE_BOUNDARY = r"[^，,；;。\n]+"
_DATE_PATTERN = re.compile(
    r"(?P<iso>20\d{2}[-/.](?:1[0-2]|0?[1-9])[-/.](?:3[01]|[12]\d|0?[1-9]))"
    r"|(?P<zh>20\d{2}年(?:1[0-2]|0?[1-9])月(?:3[01]|[12]\d|0?[1-9])日)"
)

_REVISION_WORD_PATTERN = re.compile(
    r"(?:修改|更改|调整|变更|改)(?:成|为|到)"
)

_APPLICATION_TYPE_CUES = {
    ApplicationType.PURCHASE: "采购",
    ApplicationType.TRAVEL_REIMBURSEMENT: "差旅报销",
    ApplicationType.LEAVE: "请假",
    ApplicationType.EXPENSE_REIMBURSEMENT: "费用报销",
}

_PURCHASE_CATEGORY_TEXT = {
    "IT_EQUIPMENT": "IT设备",
    "SERVICE": "服务",
    "GOODS": "货物",
}

_LEAVE_TYPE_TEXT = {
    "SICK_LEAVE": "病假",
    "MARRIAGE_LEAVE": "婚假",
    "BEREAVEMENT_LEAVE": "丧假",
    "PATERNITY_LEAVE": "陪产假",
    "MATERNITY_LEAVE": "产假",
    "PARENTAL_LEAVE": "育儿假",
    "WORK_INJURY_LEAVE": "工伤假",
    "ANNUAL_LEAVE": "年假",
    "COMPENSATORY_LEAVE": "调休",
    "PERSONAL_LEAVE": "事假",
}

_EXPENSE_CATEGORY_TEXT = {
    "BUSINESS_ENTERTAINMENT": "业务招待费",
    "MEETING": "会议费",
    "TRAINING": "培训费",
    "OFFICE": "办公费",
    "COMMUNICATION": "通信费",
    "LOCAL_TRANSPORTATION": "市内交通费",
}


class DraftPolicyCatalog:
    """按制度编号和条款编号提供草稿引用及版本快照。"""

    def __init__(self, chunks: Iterable[PolicyChunk]) -> None:
        self._articles: dict[tuple[str, str], PolicyChunk] = {}
        self._documents: dict[str, PolicyChunk] = {}

        for chunk in chunks:
            key = (chunk.document_id, chunk.article_label)
            if key in self._articles:
                raise ValueError(f"duplicate policy article for draft catalog: {key}")
            self._articles[key] = chunk
            self._documents.setdefault(chunk.document_id, chunk)

    @classmethod
    def from_directory(cls, directory: str | Path) -> DraftPolicyCatalog:
        return cls(chunk_policy_directory(directory))

    def citation(self, ref: _PolicyArticleRef) -> PolicyCitation:
        key = (ref.document_id, ref.article_label)
        try:
            chunk = self._articles[key]
        except KeyError as exc:
            raise ValueError(f"required draft policy article not found: {key}") from exc

        return PolicyCitation(
            source_id="UNASSIGNED",
            chunk_id=chunk.chunk_id,
            document_title=chunk.document_title,
            chapter_title=chunk.chapter_title,
            article_label=chunk.article_label,
            article_title=chunk.article_title,
            score=1.0,
        )

    def snapshot(self, document_id: str) -> DraftPolicySnapshot:
        try:
            chunk = self._documents[document_id]
        except KeyError as exc:
            raise ValueError(f"required draft policy document not found: {document_id}") from exc

        return DraftPolicySnapshot(
            document_id=chunk.document_id,
            document_title=chunk.document_title,
            version=chunk.document_version,
            effective_date=chunk.effective_date,
        )


def _detect_application_type(text: str) -> ApplicationType | None:
    if any(word in text for word in ("差旅报销", "出差报销")):
        return ApplicationType.TRAVEL_REIMBURSEMENT

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
        return ApplicationType.EXPENSE_REIMBURSEMENT

    if any(word in text for word in ("采购", "购买", "购置", "买电脑", "买显示器", "买设备")):
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


def _normalize_text_value(value: str) -> str | None:
    normalized = value.strip().strip("：:，,；;。.!！?？\"'“”‘’")
    return normalized or None


def _extract_labeled_value(text: str, labels: Sequence[str]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})(?:\s*(?:是|为|：|:))?\s*(?P<value>{_VALUE_BOUNDARY})",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _normalize_text_value(match.group("value"))


def _extract_code(text: str, labels: Sequence[str]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})(?:\s*(?:是|为|：|:))?\s*"
        r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.\-/]*)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group("value")


def _parse_decimal(raw_value: str, unit: str | None = None) -> Decimal | None:
    try:
        value = Decimal(raw_value.replace(",", ""))
    except InvalidOperation:
        return None
    if unit in {"万", "万元"}:
        value *= Decimal(10000)
    return value


def _parse_small_number(raw_value: str) -> Decimal | None:
    numeric = _parse_decimal(raw_value)
    if numeric is not None:
        return numeric

    digits = {
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
    if raw_value in digits:
        return Decimal(digits[raw_value])
    if raw_value.startswith("十") and len(raw_value) == 2 and raw_value[1] in digits:
        return Decimal(10 + digits[raw_value[1]])
    if raw_value.endswith("十") and len(raw_value) == 2 and raw_value[0] in digits:
        return Decimal(digits[raw_value[0]] * 10)
    if len(raw_value) == 3 and raw_value[1] == "十":
        prefix = digits.get(raw_value[0])
        suffix = digits.get(raw_value[2])
        if prefix is not None and suffix is not None:
            return Decimal(prefix * 10 + suffix)
    return None


def _parse_date_token(raw_value: str) -> date | None:
    normalized = raw_value.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _extract_dates(text: str) -> tuple[date, ...]:
    dates: list[date] = []
    for match in _DATE_PATTERN.finditer(text):
        parsed = _parse_date_token(match.group(0))
        if parsed is not None and parsed not in dates:
            dates.append(parsed)
    return tuple(dates)


def _extract_labeled_date(text: str, labels: Sequence[str]) -> date | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})(?:\s*(?:是|为|：|:))?\s*(?P<value>{_DATE_PATTERN.pattern})",
        text,
    )
    if match is None:
        return None
    return _parse_date_token(match.group("value"))


def _extract_amount_for_labels(text: str, labels: Sequence[str]) -> Decimal | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})(?:\s*(?:是|为|：|:|约))?\s*"
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<unit>万元|万|元|块)",
        text,
    )
    if match is None:
        return None
    return _parse_decimal(match.group("amount"), match.group("unit"))


def _add_field(
    fields: list[DraftField],
    *,
    field_name: str,
    display_name: str,
    value: str | int | Decimal | bool | None,
    source: DraftFieldSource = DraftFieldSource.USER_INPUT,
    sensitive: bool = False,
) -> bool:
    if value is None or value == "":
        return False
    fields.append(
        DraftField(
            field_name=field_name,
            display_name=display_name,
            value=value,
            source=source,
            sensitive=sensitive,
        )
    )
    return True


def _missing(field_name: str, display_name: str, question: str) -> MissingDraftField:
    return MissingDraftField(
        field_name=field_name,
        display_name=display_name,
        question=question,
    )


def _purchase_item_and_quantity(text: str) -> tuple[str | None, int | None, str | None]:
    patterns = (
        re.compile(
            r"(?:采购|购买|购置|买)\s*"
            r"(?P<count>\d+|[一二两三四五六七八九十]+)\s*"
            r"(?P<unit>台|个|套|件|份)\s*"
            r"(?P<item>[^，,；;。\n]{1,40})"
        ),
        re.compile(
            r"(?:采购|购买|购置|买)\s*"
            r"(?P<item>[^，,；;。\n]{1,30}?)\s*"
            r"(?P<count>\d+|[一二两三四五六七八九十]+)\s*"
            r"(?P<unit>台|个|套|件|份)"
        ),
    )

    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        parsed_count = _parse_small_number(match.group("count"))
        item = _normalize_text_value(match.group("item"))
        if parsed_count is None or parsed_count != parsed_count.to_integral_value():
            return item, None, match.group("unit")
        return item, int(parsed_count), match.group("unit")
    return None, None, None


def _purchase_unit_price(text: str) -> Decimal | None:
    match = re.search(
        r"每(?:台|个|套|件|份)?(?:单价)?\s*(?:是|为|：|:)?\s*"
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<unit>万元|万|元|块)",
        text,
    )
    if match is None:
        match = re.search(
            r"(?:预计单价|单价)(?:\s*(?:是|为|：|:))?\s*"
            r"(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<unit>万元|万|元|块)",
            text,
        )
    if match is None:
        return None
    return _parse_decimal(match.group("amount"), match.group("unit"))


def _purchase_category(text: str) -> str | None:
    lower_text = text.lower()
    if any(cue in lower_text for cue in _IT_PURCHASE_CUES):
        return "IT_EQUIPMENT"
    if any(cue in text for cue in _SERVICE_PURCHASE_CUES):
        return "SERVICE"
    if any(cue in text for cue in ("设备", "用品", "商品", "硬件", "耗材")):
        return "GOODS"
    return _extract_labeled_value(text, ("采购类别", "类别"))


def _purchase_it_flag(text: str) -> bool | None:
    lower_text = text.lower()
    if any(cue in lower_text for cue in _IT_PURCHASE_CUES):
        return True
    if any(cue in text for cue in ("不涉及信息系统", "不涉及数据处理", "非IT采购")):
        return False
    if any(cue in text for cue in ("涉及信息系统", "涉及数据处理", "涉及公司数据")):
        return True
    return None


def _purchase_emergency_flag(text: str) -> bool | None:
    if any(cue in text for cue in ("非紧急采购", "不属于紧急采购", "普通采购")):
        return False
    if "紧急采购" in text:
        return True
    return None


def _extract_purchase(text: str, approval: ApprovalCheckResult) -> _ExtractionResult:
    fields: list[DraftField] = []
    missing_fields: list[MissingDraftField] = []
    issues: list[DraftValidationIssue] = []

    item_name, quantity, unit = _purchase_item_and_quantity(text)
    if not _add_field(fields, field_name="item_name", display_name="采购事项", value=item_name):
        missing_fields.append(_missing("item_name", "采购事项", "要采购什么商品或服务？"))
    if not _add_field(fields, field_name="quantity", display_name="采购数量", value=quantity):
        missing_fields.append(_missing("quantity", "采购数量", "采购数量是多少？"))
    _add_field(fields, field_name="unit", display_name="计量单位", value=unit)

    purpose = _extract_labeled_value(text, ("采购目的", "业务背景", "用途", "用于"))
    if not _add_field(fields, field_name="purpose", display_name="采购目的", value=purpose):
        missing_fields.append(_missing("purpose", "采购目的和业务背景", "本次采购用于什么业务？"))

    category = _purchase_category(text)
    if not _add_field(
        fields,
        field_name="category",
        display_name="采购类别",
        value=category,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(_missing("category", "采购类别", "这是货物、服务还是信息技术类采购？"))

    specification = _extract_labeled_value(
        text,
        ("规格型号", "技术规格", "规格", "型号", "服务范围"),
    )
    if not _add_field(
        fields,
        field_name="specification",
        display_name="规格、型号或服务范围",
        value=specification,
    ):
        missing_fields.append(
            _missing("specification", "规格、型号或服务范围", "请提供规格、型号或服务范围。")
        )

    unit_price = _purchase_unit_price(text)
    if not _add_field(
        fields,
        field_name="estimated_unit_price",
        display_name="预计单价（元）",
        value=unit_price,
    ):
        missing_fields.append(
            _missing("estimated_unit_price", "预计单价", "预计单价是多少人民币？")
        )

    explicit_total = _extract_amount_for_labels(
        text,
        ("预计总金额", "采购总金额", "总金额", "总价", "预算金额"),
    )
    calculated_total = (
        unit_price * quantity if unit_price is not None and quantity is not None else None
    )
    total_amount = (
        explicit_total
        if explicit_total is not None
        else calculated_total
    )
    total_source = (
        DraftFieldSource.USER_INPUT
        if explicit_total is not None
        else DraftFieldSource.CALCULATED
    )
    if not _add_field(
        fields,
        field_name="estimated_total_amount",
        display_name="预计总金额（元）",
        value=total_amount,
        source=total_source,
    ):
        missing_fields.append(
            _missing("estimated_total_amount", "预计总金额", "预计采购总金额是多少人民币？")
        )

    if explicit_total is not None and calculated_total is not None:
        _add_field(
            fields,
            field_name="calculated_total_amount",
            display_name="按数量和单价计算的总金额（元）",
            value=calculated_total,
            source=DraftFieldSource.CALCULATED,
        )
        if explicit_total != calculated_total:
            issues.append(
                DraftValidationIssue(
                    code="PURCHASE_TOTAL_MISMATCH",
                    severity=ValidationSeverity.ERROR,
                    message=(
                        f"数量乘以单价为{calculated_total}元，"
                        f"与用户填写的总金额{explicit_total}元不一致。"
                    ),
                    blocking=True,
                )
            )

    budget_code = _extract_code(text, ("预算编号", "预算代码"))
    cost_center = _extract_code(text, ("成本中心",))
    has_budget = _add_field(
        fields,
        field_name="budget_code",
        display_name="预算编号",
        value=budget_code,
    )
    has_cost_center = _add_field(
        fields,
        field_name="cost_center",
        display_name="成本中心",
        value=cost_center,
    )
    if not (has_budget or has_cost_center):
        missing_fields.append(
            _missing("budget_or_cost_center", "预算编号或成本中心", "请提供预算编号或成本中心。")
        )

    delivery_date = _extract_labeled_date(text, ("期望交付日期", "预计交付日期", "交付日期"))
    if not _add_field(
        fields,
        field_name="expected_delivery_date",
        display_name="期望交付日期",
        value=delivery_date.isoformat() if delivery_date is not None else None,
    ):
        missing_fields.append(
            _missing("expected_delivery_date", "期望交付日期", "期望在什么日期交付？")
        )

    location = _extract_labeled_value(text, ("使用地点", "交付地点", "使用位置"))
    if not _add_field(fields, field_name="delivery_location", display_name="使用地点", value=location):
        missing_fields.append(_missing("delivery_location", "使用地点", "采购物品或服务的使用地点是哪里？"))

    supplier = _extract_labeled_value(text, ("推荐供应商", "供应商名称", "供应商"))
    if not _add_field(fields, field_name="supplier_name", display_name="推荐供应商", value=supplier):
        missing_fields.append(_missing("supplier_name", "推荐供应商", "推荐供应商是谁？如未确定请明确说明。"))

    supplier_reason = _extract_labeled_value(text, ("推荐理由", "供应商理由"))
    if not _add_field(
        fields,
        field_name="supplier_reason",
        display_name="供应商推荐理由",
        value=supplier_reason,
    ):
        missing_fields.append(
            _missing("supplier_reason", "供应商推荐理由", "为什么推荐该供应商？")
        )

    it_flag = _purchase_it_flag(text)
    if not _add_field(
        fields,
        field_name="involves_it_or_data",
        display_name="是否涉及信息系统或数据处理",
        value=it_flag,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(
            _missing(
                "involves_it_or_data",
                "是否涉及信息系统或数据处理",
                "本次采购是否涉及信息系统或数据处理？",
            )
        )

    emergency_flag = _purchase_emergency_flag(text)
    if not _add_field(
        fields,
        field_name="is_emergency",
        display_name="是否属于紧急采购",
        value=emergency_flag,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(
            _missing("is_emergency", "是否属于紧急采购", "本次是否属于紧急采购？")
        )

    if approval.amount is not None and total_amount is not None and approval.amount != total_amount:
        issues.append(
            DraftValidationIssue(
                code="APPROVAL_AMOUNT_MISMATCH",
                severity=ValidationSeverity.ERROR,
                message="草稿总金额与审批规则工具计算金额不一致。",
                blocking=True,
            )
        )

    return _ExtractionResult(tuple(fields), tuple(missing_fields), tuple(issues))


def _extract_city_pair(text: str) -> tuple[str | None, str | None]:
    departure = _extract_labeled_value(text, ("出发城市", "出发地"))
    destination = _extract_labeled_value(text, ("目的城市", "目的地"))
    if departure is not None or destination is not None:
        return departure, destination

    match = re.search(
        r"从(?P<departure>[\u4e00-\u9fa5]{2,12})(?:出发)?(?:到|前往)"
        r"(?P<destination>[\u4e00-\u9fa5]{2,12})(?:出差|差旅)",
        text,
    )
    if match is None:
        return None, None
    return match.group("departure"), match.group("destination")


def _extract_travel(text: str, approval: ApprovalCheckResult) -> _ExtractionResult:
    fields: list[DraftField] = []
    missing_fields: list[MissingDraftField] = []
    issues: list[DraftValidationIssue] = []

    travel_id = _extract_code(text, ("出差申请编号", "出差申请单号", "差旅申请编号"))
    if not _add_field(
        fields,
        field_name="travel_application_id",
        display_name="已审批出差申请编号",
        value=travel_id,
    ):
        missing_fields.append(
            _missing("travel_application_id", "已审批出差申请编号", "请提供已审批的出差申请编号。")
        )

    departure, destination = _extract_city_pair(text)
    if not _add_field(fields, field_name="departure_city", display_name="出发地", value=departure):
        missing_fields.append(_missing("departure_city", "出发地", "本次出差从哪里出发？"))
    if not _add_field(fields, field_name="destination_city", display_name="目的地", value=destination):
        missing_fields.append(_missing("destination_city", "目的地", "本次出差目的地是哪里？"))

    dates = _extract_dates(text)
    start_date = _extract_labeled_date(text, ("出差开始日期", "开始日期"))
    end_date = _extract_labeled_date(text, ("出差结束日期", "结束日期"))
    if start_date is None and dates:
        start_date = dates[0]
    if end_date is None and len(dates) >= 2:
        end_date = dates[1]

    if not _add_field(
        fields,
        field_name="start_date",
        display_name="出差开始日期",
        value=start_date.isoformat() if start_date is not None else None,
    ):
        missing_fields.append(_missing("start_date", "出差开始日期", "出差开始日期是哪一天？"))
    if not _add_field(
        fields,
        field_name="end_date",
        display_name="出差结束日期",
        value=end_date.isoformat() if end_date is not None else None,
    ):
        missing_fields.append(_missing("end_date", "出差结束日期", "出差结束日期是哪一天？"))
    if start_date is not None and end_date is not None and start_date > end_date:
        issues.append(
            DraftValidationIssue(
                code="TRAVEL_DATE_ORDER_INVALID",
                severity=ValidationSeverity.ERROR,
                message="出差开始日期不能晚于结束日期。",
                blocking=True,
            )
        )

    purpose = _extract_labeled_value(text, ("出差事由", "出差目的", "业务目的", "事由"))
    if not _add_field(fields, field_name="business_purpose", display_name="出差事由", value=purpose):
        missing_fields.append(_missing("business_purpose", "出差事由", "本次出差的业务事由是什么？"))

    project = _extract_labeled_value(text, ("项目名称", "所属项目"))
    cost_center = _extract_code(text, ("成本中心",))
    has_project = _add_field(fields, field_name="project_name", display_name="项目名称", value=project)
    has_cost_center = _add_field(
        fields,
        field_name="cost_center",
        display_name="成本中心",
        value=cost_center,
    )
    if not (has_project or has_cost_center):
        missing_fields.append(
            _missing("project_or_cost_center", "项目名称或成本中心", "请提供项目名称或成本中心。")
        )

    total_amount = (
        approval.amount
        if approval.amount is not None
        else _extract_amount_for_labels(
            text,
            ("报销总金额", "总报销金额", "报销金额", "总金额"),
        )
    )
    if not _add_field(
        fields,
        field_name="total_reimbursement_amount",
        display_name="报销总金额（元）",
        value=total_amount,
    ):
        missing_fields.append(
            _missing("total_reimbursement_amount", "报销总金额", "本次差旅报销总金额是多少？")
        )

    expense_details = _extract_labeled_value(text, ("费用明细", "报销明细"))
    if not _add_field(
        fields,
        field_name="expense_details",
        display_name="费用明细",
        value=expense_details,
    ):
        missing_fields.append(
            _missing("expense_details", "费用明细", "请说明交通、住宿、餐补等费用明细。")
        )

    return _ExtractionResult(tuple(fields), tuple(missing_fields), tuple(issues))


def _detect_leave_type(text: str) -> str | None:
    for aliases, leave_type in _LEAVE_TYPES:
        if any(alias in text for alias in aliases):
            return leave_type
    return None


def _leave_periods(text: str) -> tuple[str | None, str | None]:
    if "全天" in text or "整天" in text:
        return "FULL_DAY", "FULL_DAY"

    start = _extract_labeled_value(text, ("开始时段", "开始时间"))
    end = _extract_labeled_value(text, ("结束时段", "结束时间"))
    return start, end


def _leave_emergency_flag(text: str) -> bool | None:
    if any(cue in text for cue in ("非紧急请假", "不是紧急请假", "普通请假")):
        return False
    if "紧急请假" in text:
        return True
    return None


def _extract_leave(text: str, approval: ApprovalCheckResult) -> _ExtractionResult:
    fields: list[DraftField] = []
    missing_fields: list[MissingDraftField] = []
    issues: list[DraftValidationIssue] = []

    leave_type = _detect_leave_type(text)
    if not _add_field(
        fields,
        field_name="leave_type",
        display_name="请假类型",
        value=leave_type,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(_missing("leave_type", "请假类型", "请说明年假、病假、事假等具体类型。"))

    dates = _extract_dates(text)
    start_date = _extract_labeled_date(text, ("请假开始日期", "开始日期"))
    end_date = _extract_labeled_date(text, ("请假结束日期", "结束日期"))
    if start_date is None and dates:
        start_date = dates[0]
    if end_date is None and len(dates) >= 2:
        end_date = dates[1]

    if not _add_field(
        fields,
        field_name="start_date",
        display_name="请假开始日期",
        value=start_date.isoformat() if start_date is not None else None,
    ):
        missing_fields.append(_missing("start_date", "请假开始日期", "请假从哪一天开始？"))
    if not _add_field(
        fields,
        field_name="end_date",
        display_name="请假结束日期",
        value=end_date.isoformat() if end_date is not None else None,
    ):
        missing_fields.append(_missing("end_date", "请假结束日期", "请假到哪一天结束？"))
    if start_date is not None and end_date is not None and start_date > end_date:
        issues.append(
            DraftValidationIssue(
                code="LEAVE_DATE_ORDER_INVALID",
                severity=ValidationSeverity.ERROR,
                message="请假开始日期不能晚于结束日期。",
                blocking=True,
            )
        )

    start_period, end_period = _leave_periods(text)
    if not _add_field(
        fields,
        field_name="start_period",
        display_name="开始时段",
        value=start_period,
    ):
        missing_fields.append(_missing("start_period", "开始时段", "开始时段是上午、下午还是全天？"))
    if not _add_field(
        fields,
        field_name="end_period",
        display_name="结束时段",
        value=end_period,
    ):
        missing_fields.append(_missing("end_period", "结束时段", "结束时段是上午、下午还是全天？"))

    leave_days = approval.leave_days
    if not _add_field(
        fields,
        field_name="leave_days",
        display_name="请假工作日数",
        value=leave_days,
    ):
        missing_fields.append(_missing("leave_days", "请假天数", "本次请几个工作日？"))

    reason = _extract_labeled_value(text, ("请假原因", "原因", "事由"))
    if not _add_field(fields, field_name="reason", display_name="请假原因", value=reason):
        missing_fields.append(_missing("reason", "请假原因", "请填写请假原因。"))

    handover = _extract_labeled_value(text, ("工作交接人", "交接人", "代办人"))
    if not _add_field(fields, field_name="handover_person", display_name="工作交接人", value=handover):
        missing_fields.append(_missing("handover_person", "工作交接人", "休假期间由谁进行工作交接？"))

    emergency_contact = _extract_labeled_value(text, ("紧急联系人",))
    if not _add_field(
        fields,
        field_name="emergency_contact",
        display_name="紧急联系人",
        value=emergency_contact,
        sensitive=True,
    ):
        missing_fields.append(_missing("emergency_contact", "紧急联系人", "请提供紧急联系人。"))

    emergency_flag = _leave_emergency_flag(text)
    if not _add_field(
        fields,
        field_name="is_emergency",
        display_name="是否属于紧急请假",
        value=emergency_flag,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(
            _missing("is_emergency", "是否属于紧急请假", "本次是否属于紧急请假？")
        )

    return _ExtractionResult(tuple(fields), tuple(missing_fields), tuple(issues))


def _detect_expense_category(text: str) -> str | None:
    for aliases, category in _EXPENSE_CATEGORIES:
        if any(alias in text for alias in aliases):
            return category
    return _extract_labeled_value(text, ("费用类别", "报销类别"))


def _explicit_boolean(text: str, subject: str) -> bool | None:
    if any(cue in text for cue in (f"不涉及{subject}", f"没有{subject}", f"无{subject}")):
        return False
    if f"涉及{subject}" in text:
        return True
    return None


def _extract_expense(text: str, approval: ApprovalCheckResult) -> _ExtractionResult:
    fields: list[DraftField] = []
    missing_fields: list[MissingDraftField] = []

    category = _detect_expense_category(text)
    if not _add_field(
        fields,
        field_name="expense_category",
        display_name="费用类别",
        value=category,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(_missing("expense_category", "费用类别", "本次报销属于哪类费用？"))

    amount = (
        approval.amount
        if approval.amount is not None
        else _extract_amount_for_labels(
            text,
            ("报销总金额", "费用金额", "报销金额", "总金额"),
        )
    )
    if not _add_field(
        fields,
        field_name="amount",
        display_name="报销金额（元）",
        value=amount,
    ):
        missing_fields.append(_missing("amount", "报销金额", "本张报销单的总金额是多少？"))

    purpose = _extract_labeled_value(text, ("业务目的", "业务事由", "费用用途", "用途"))
    if not _add_field(fields, field_name="business_purpose", display_name="业务目的", value=purpose):
        missing_fields.append(_missing("business_purpose", "业务目的", "发生这笔费用的业务目的是什么？"))

    expense_date = _extract_labeled_date(text, ("费用发生日期", "发生日期", "消费日期"))
    if not _add_field(
        fields,
        field_name="expense_date",
        display_name="费用发生日期",
        value=expense_date.isoformat() if expense_date is not None else None,
    ):
        missing_fields.append(_missing("expense_date", "费用发生日期", "费用发生日期是哪一天？"))

    budget_code = _extract_code(text, ("预算编号", "预算代码"))
    cost_center = _extract_code(text, ("成本中心",))
    has_budget = _add_field(fields, field_name="budget_code", display_name="预算编号", value=budget_code)
    has_cost_center = _add_field(
        fields,
        field_name="cost_center",
        display_name="成本中心",
        value=cost_center,
    )
    if not (has_budget or has_cost_center):
        missing_fields.append(
            _missing("budget_or_cost_center", "预算编号或成本中心", "请提供预算编号或成本中心。")
        )

    payee = _extract_labeled_value(text, ("收款对象", "收款方", "商户", "供应商"))
    if not _add_field(fields, field_name="payee", display_name="收款对象", value=payee):
        missing_fields.append(_missing("payee", "收款对象", "这笔费用支付给谁？"))

    contract_flag = _explicit_boolean(text, "合同")
    if not _add_field(
        fields,
        field_name="involves_contract",
        display_name="是否涉及合同",
        value=contract_flag,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(_missing("involves_contract", "是否涉及合同", "这笔费用是否涉及合同？"))

    purchase_flag = _explicit_boolean(text, "采购")
    if not _add_field(
        fields,
        field_name="involves_purchase",
        display_name="是否涉及采购",
        value=purchase_flag,
        source=DraftFieldSource.DETERMINISTIC_RULE,
    ):
        missing_fields.append(_missing("involves_purchase", "是否涉及采购", "这笔费用是否涉及采购？"))

    return _ExtractionResult(tuple(fields), tuple(missing_fields), ())


def _extract_fields(
    application_type: ApplicationType,
    text: str,
    approval: ApprovalCheckResult,
) -> _ExtractionResult:
    if application_type is ApplicationType.PURCHASE:
        return _extract_purchase(text, approval)
    if application_type is ApplicationType.TRAVEL_REIMBURSEMENT:
        return _extract_travel(text, approval)
    if application_type is ApplicationType.LEAVE:
        return _extract_leave(text, approval)
    return _extract_expense(text, approval)


def _renumber_citations(citations: Iterable[PolicyCitation]) -> tuple[PolicyCitation, ...]:
    unique: list[PolicyCitation] = []
    seen_chunk_ids: set[str] = set()
    for citation in citations:
        if citation.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(citation.chunk_id)
        unique.append(citation)

    return tuple(
        PolicyCitation(
            source_id=f"S{index}",
            chunk_id=citation.chunk_id,
            document_title=citation.document_title,
            chapter_title=citation.chapter_title,
            article_label=citation.article_label,
            article_title=citation.article_title,
            score=citation.score,
        )
        for index, citation in enumerate(unique, start=1)
    )


def _materials_ready(result: MaterialCheckResult) -> bool:
    if result.clarification_question is not None:
        return False
    if not result.required_materials:
        return True
    return result.materials_complete is True


def _has_blocking_issue(issues: Sequence[DraftValidationIssue]) -> bool:
    return any(issue.blocking for issue in issues)


def _build_clarification_question(
    extraction: _ExtractionResult,
    material: MaterialCheckResult,
    approval: ApprovalCheckResult,
) -> str | None:
    blocking = [issue.message for issue in extraction.validation_issues if issue.blocking]
    if blocking:
        return "请先修正以下数据问题：" + "；".join(blocking)

    if extraction.missing_fields:
        names = "、".join(item.display_name for item in extraction.missing_fields)
        return f"请补充以下必填信息：{names}。"

    if approval.clarification_question is not None:
        return approval.clarification_question

    if material.clarification_question is not None:
        return material.clarification_question

    if material.missing_materials:
        names = "、".join(item.display_name for item in material.missing_materials)
        return f"请补充以下材料后再确认草稿：{names}。"

    if material.required_materials and material.materials_complete is not True:
        return "请列出已经准备的材料，以便确认草稿是否具备后续办理条件。"

    return None


def _format_value(value: str | int | Decimal | bool) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, Decimal):
        formatted = f"{value:,.2f}"
        return formatted.rstrip("0").rstrip(".")
    return str(value)


def _field_map(
    draft: ApplicationDraft,
) -> dict[str, DraftField]:
    return {field.field_name: field for field in draft.fields}


def _field_text(
    fields: dict[str, DraftField],
    field_name: str,
) -> str | None:
    field = fields.get(field_name)
    if field is None:
        return None
    return _format_value(field.value)


def _append_labeled(
    parts: list[str],
    fields: dict[str, DraftField],
    field_name: str,
    label: str,
) -> None:
    value = _field_text(fields, field_name)
    if value is not None:
        parts.append(f"{label}为{value}")


def _purchase_revision_overrides(
    text: str,
) -> tuple[DraftField, ...]:
    """补足原始单轮抽取器无法识别的常见采购修改短句。"""

    overrides: list[DraftField] = []
    quantity_match = re.search(
        r"(?:采购数量|数量)\s*(?:修改|更改|调整|变更|改)?"
        r"(?:成|为|到|是|：|:)?\s*"
        r"(?P<count>\d+|[一二两三四五六七八九十]+)\s*"
        r"(?P<unit>台|个|套|件|份)?",
        text,
    )
    if quantity_match is not None:
        parsed = _parse_small_number(quantity_match.group("count"))
        if parsed is not None and parsed == parsed.to_integral_value():
            overrides.append(
                DraftField(
                    field_name="quantity",
                    display_name="采购数量",
                    value=int(parsed),
                    source=DraftFieldSource.USER_INPUT,
                )
            )
            unit = quantity_match.group("unit")
            if unit is not None:
                overrides.append(
                    DraftField(
                        field_name="unit",
                        display_name="计量单位",
                        value=unit,
                        source=DraftFieldSource.USER_INPUT,
                    )
                )

    item_match = re.search(
        r"(?:采购事项|采购物品|物品名称|商品名称)\s*"
        r"(?:修改|更改|调整|变更|改)?(?:成|为|到|是|：|:)?\s*"
        rf"(?P<item>{_VALUE_BOUNDARY})",
        text,
    )
    if item_match is not None:
        item = _normalize_text_value(item_match.group("item"))
        if item is not None:
            overrides.append(
                DraftField(
                    field_name="item_name",
                    display_name="采购事项",
                    value=item,
                    source=DraftFieldSource.USER_INPUT,
                )
            )

    return tuple(overrides)


def _normalize_revision_text(text: str) -> str:
    normalized = text.strip()
    normalized = _REVISION_WORD_PATTERN.sub("为", normalized)
    return normalized


def _render_purchase_request(
    fields: dict[str, DraftField],
) -> str:
    parts = ["帮我生成采购申请草稿"]
    item = _field_text(fields, "item_name")
    quantity = _field_text(fields, "quantity")
    unit = _field_text(fields, "unit") or "件"
    if item is not None and quantity is not None:
        parts.append(f"采购{quantity}{unit}{item}")

    unit_price = _field_text(fields, "estimated_unit_price")
    if unit_price is not None:
        parts.append(f"每{unit}{unit_price}元")

    total = fields.get("estimated_total_amount")
    if total is not None and total.source is DraftFieldSource.USER_INPUT:
        parts.append(f"预计总金额为{_format_value(total.value)}元")

    _append_labeled(parts, fields, "purpose", "采购目的")
    category = _field_text(fields, "category")
    if category is not None:
        parts.append(
            "采购类别为"
            + _PURCHASE_CATEGORY_TEXT.get(category, category)
        )
    _append_labeled(parts, fields, "specification", "规格")
    _append_labeled(parts, fields, "budget_code", "预算编号")
    _append_labeled(parts, fields, "cost_center", "成本中心")
    _append_labeled(
        parts,
        fields,
        "expected_delivery_date",
        "交付日期",
    )
    _append_labeled(parts, fields, "delivery_location", "使用地点")
    _append_labeled(parts, fields, "supplier_name", "推荐供应商")
    _append_labeled(parts, fields, "supplier_reason", "推荐理由")

    it_field = fields.get("involves_it_or_data")
    if it_field is not None:
        parts.append(
            "涉及信息系统或数据处理"
            if it_field.value is True
            else "不涉及信息系统，不涉及数据处理"
        )
    emergency_field = fields.get("is_emergency")
    if emergency_field is not None:
        parts.append(
            "紧急采购"
            if emergency_field.value is True
            else "普通采购"
        )
    return "，".join(parts) + "。"


def _render_travel_request(
    fields: dict[str, DraftField],
) -> str:
    parts = ["帮我生成差旅报销草稿"]
    _append_labeled(
        parts,
        fields,
        "travel_application_id",
        "出差申请编号",
    )
    _append_labeled(parts, fields, "departure_city", "出发地")
    _append_labeled(parts, fields, "destination_city", "目的地")
    _append_labeled(parts, fields, "start_date", "开始日期")
    _append_labeled(parts, fields, "end_date", "结束日期")
    _append_labeled(parts, fields, "business_purpose", "出差事由")
    _append_labeled(parts, fields, "project_name", "项目名称")
    _append_labeled(parts, fields, "cost_center", "成本中心")
    amount = _field_text(fields, "total_reimbursement_amount")
    if amount is not None:
        parts.append(f"报销总金额为{amount}元")
    _append_labeled(parts, fields, "expense_details", "费用明细")
    return "，".join(parts) + "。"


def _render_leave_request(
    fields: dict[str, DraftField],
) -> str:
    parts = ["帮我生成请假申请草稿"]
    leave_type = _field_text(fields, "leave_type")
    leave_days = _field_text(fields, "leave_days")
    if leave_type is not None:
        leave_label = _LEAVE_TYPE_TEXT.get(leave_type, leave_type)
        if leave_days is not None:
            parts.append(f"请{leave_days}天{leave_label}")
        else:
            parts.append(leave_label)
    _append_labeled(parts, fields, "start_date", "开始日期")
    _append_labeled(parts, fields, "end_date", "结束日期")

    start_period = _field_text(fields, "start_period")
    end_period = _field_text(fields, "end_period")
    if start_period == "FULL_DAY" and end_period == "FULL_DAY":
        parts.append("全天")
    else:
        _append_labeled(parts, fields, "start_period", "开始时段")
        _append_labeled(parts, fields, "end_period", "结束时段")

    _append_labeled(parts, fields, "reason", "请假原因")
    _append_labeled(parts, fields, "handover_person", "交接人")
    _append_labeled(parts, fields, "emergency_contact", "紧急联系人")
    emergency_field = fields.get("is_emergency")
    if emergency_field is not None:
        parts.append(
            "紧急请假"
            if emergency_field.value is True
            else "普通请假"
        )
    return "，".join(parts) + "。"


def _render_expense_request(
    fields: dict[str, DraftField],
) -> str:
    parts = ["帮我生成费用报销草稿"]
    category = _field_text(fields, "expense_category")
    if category is not None:
        parts.append(
            "费用类别为"
            + _EXPENSE_CATEGORY_TEXT.get(category, category)
        )
    amount = _field_text(fields, "amount")
    if amount is not None:
        parts.append(f"报销金额为{amount}元")
    _append_labeled(parts, fields, "business_purpose", "业务目的")
    _append_labeled(parts, fields, "expense_date", "发生日期")
    _append_labeled(parts, fields, "budget_code", "预算编号")
    _append_labeled(parts, fields, "cost_center", "成本中心")
    _append_labeled(parts, fields, "payee", "收款对象")

    contract_field = fields.get("involves_contract")
    if contract_field is not None:
        parts.append(
            "涉及合同"
            if contract_field.value is True
            else "不涉及合同"
        )
    purchase_field = fields.get("involves_purchase")
    if purchase_field is not None:
        parts.append(
            "涉及采购"
            if purchase_field.value is True
            else "不涉及采购"
        )
    return "，".join(parts) + "。"


def _render_revision_request(
    application_type: ApplicationType,
    fields: dict[str, DraftField],
    context_messages: Sequence[str],
) -> str:
    if application_type is ApplicationType.PURCHASE:
        canonical = _render_purchase_request(fields)
    elif application_type is ApplicationType.TRAVEL_REIMBURSEMENT:
        canonical = _render_travel_request(fields)
    elif application_type is ApplicationType.LEAVE:
        canonical = _render_leave_request(fields)
    else:
        canonical = _render_expense_request(fields)

    context = "\n".join(
        message.strip()
        for message in context_messages
        if message.strip()
    )
    if not context:
        return canonical
    return f"{canonical}\n历史补充记录：\n{context}"


def _summary_lines(
    title: str,
    extraction: _ExtractionResult,
    material: MaterialCheckResult,
    approval: ApprovalCheckResult,
) -> tuple[str, ...]:
    lines = [f"草稿类型：{title}"]
    lines.extend(
        f"{field.display_name}：{_format_value(field.value)}"
        for field in extraction.fields
        if not field.sensitive and field.field_name != "calculated_total_amount"
    )

    if extraction.missing_fields:
        lines.append(
            "待补字段：" + "、".join(item.display_name for item in extraction.missing_fields)
        )

    if approval.steps:
        lines.append("审批路线：" + " → ".join(step.display_name for step in approval.steps))
    else:
        lines.append("审批路线：待补充关键信息后计算")

    if not material.required_materials:
        lines.append("材料状态：现有制度未列出固定证明附件")
    elif material.materials_complete is True:
        lines.append("材料状态：按当前声明已齐全")
    elif material.missing_materials:
        lines.append(
            "材料状态：缺少" + "、".join(item.display_name for item in material.missing_materials)
        )
    else:
        lines.append("材料状态：尚未完成逐项比对")
    return tuple(lines)


def _warnings(
    material: MaterialCheckResult,
    approval: ApprovalCheckResult,
    issues: Sequence[DraftValidationIssue],
) -> tuple[str, ...]:
    warnings = [
        "草稿只在本次响应中返回，当前尚未写入数据库。",
        "申请人身份来自可信演示上下文，不会被用户消息中的自述覆盖。",
        "草稿尚未确认，也没有提交审批。",
    ]
    warnings.extend(issue.message for issue in issues)
    warnings.extend(material.notes)
    warnings.extend(approval.notes)
    return tuple(dict.fromkeys(warnings))


def _format_reply(
    draft: ApplicationDraft | None,
    clarification_question: str | None,
    citations: Sequence[PolicyCitation],
) -> str:
    if draft is None:
        return clarification_question or "请说明需要生成哪类申请草稿。"

    if draft.ready_for_confirmation:
        opening = f"已生成{draft.title}，内容已具备人工核对条件。"
    else:
        opening = f"已生成{draft.title}的部分草稿，但还不能进入确认环节。"

    lines = [opening, *draft.summary_lines]
    if clarification_question is not None:
        lines.append(clarification_question)
    lines.append("安全状态：未确认、未提交；当前版本不会自动发起审批。")
    if citations:
        lines.append("制度依据：" + " ".join(f"[{item.source_id}]" for item in citations))
    return "\n".join(lines)


class ApplicationDraftGenerator:
    """生成无副作用、可追溯、不可自动提交的结构化申请草稿。"""

    def __init__(
        self,
        *,
        material_checker: _MaterialChecker,
        approval_checker: _ApprovalChecker,
        catalog: DraftPolicyCatalog,
        user_context: DraftUserContext,
        clock: Callable[[], datetime] | None = None,
        session_id: str = "STATELESS-DEMO",
    ) -> None:
        if not user_context.employee_id.strip():
            raise ValueError("trusted user_context.employee_id must not be blank")
        if not user_context.employee_name.strip():
            raise ValueError("trusted user_context.employee_name must not be blank")
        if not user_context.department.strip():
            raise ValueError("trusted user_context.department must not be blank")
        if not user_context.roles:
            raise ValueError("trusted user_context.roles must not be empty")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")

        self._material_checker = material_checker
        self._approval_checker = approval_checker
        self._catalog = catalog
        self._user_context = user_context
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_id = session_id

    @classmethod
    def from_policy_directory(
        cls,
        directory: str | Path,
        *,
        material_checker: _MaterialChecker,
        approval_checker: _ApprovalChecker,
        user_context: DraftUserContext,
        clock: Callable[[], datetime] | None = None,
        session_id: str = "STATELESS-DEMO",
    ) -> ApplicationDraftGenerator:
        return cls(
            material_checker=material_checker,
            approval_checker=approval_checker,
            catalog=DraftPolicyCatalog.from_directory(directory),
            user_context=user_context,
            clock=clock,
            session_id=session_id,
        )

    async def generate(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
    ) -> DraftGenerationAnswer:
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")
        active_session_id = (
            session_id.strip()
            if session_id is not None
            else self._session_id
        )
        if not active_session_id:
            raise ValueError("session_id must not be blank")

        application_type = _detect_application_type(normalized_input)
        if application_type is None:
            question = "请说明要生成哪类草稿：采购、差旅报销、普通费用报销还是请假。"
            result = DraftGenerationResult(
                application_type=None,
                draft=None,
                clarification_question=question,
                citations=(),
            )
            return DraftGenerationAnswer(
                request=normalized_input,
                result=result,
                reply=_format_reply(None, question, ()),
            )

        material_answer, approval_answer = await asyncio.gather(
            self._material_checker.check(normalized_input),
            self._approval_checker.check(normalized_input),
        )
        extraction = _extract_fields(
            application_type,
            normalized_input,
            approval_answer.result,
        )

        base_citations = tuple(
            self._catalog.citation(ref) for ref in _POLICY_REFS[application_type]
        )
        citations = _renumber_citations(
            (*base_citations, *material_answer.result.citations, *approval_answer.result.citations)
        )
        policy_snapshots = tuple(
            self._catalog.snapshot(document_id)
            for document_id in dict.fromkeys(ref.document_id for ref in _POLICY_REFS[application_type])
        )

        materials_ready = _materials_ready(material_answer.result)
        ready_for_confirmation = (
            not extraction.missing_fields
            and not _has_blocking_issue(extraction.validation_issues)
            and approval_answer.result.clarification_question is None
            and materials_ready
        )
        if (
            extraction.missing_fields
            or _has_blocking_issue(extraction.validation_issues)
            or approval_answer.result.clarification_question is not None
        ):
            status = DraftStatus.WAITING_FOR_INFORMATION
        elif not materials_ready:
            status = DraftStatus.WAITING_FOR_MATERIALS
        else:
            status = DraftStatus.WAITING_FOR_CONFIRMATION

        clarification_question = _build_clarification_question(
            extraction,
            material_answer.result,
            approval_answer.result,
        )
        digest = sha256(
            (
                f"{application_type.value}\0{self._user_context.employee_id}\0"
                f"{normalized_input}"
            ).encode()
        ).hexdigest().upper()
        created_at = self._clock()
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        draft_id = f"{_DRAFT_PREFIXES[application_type]}-DRAFT-{digest[:12]}"
        draft = ApplicationDraft(
            draft_id=draft_id,
            application_type=application_type,
            title=_DRAFT_TITLES[application_type],
            status=status,
            applicant=self._user_context,
            fields=extraction.fields,
            missing_fields=extraction.missing_fields,
            material_check=material_answer.result,
            approval_check=approval_answer.result,
            policy_snapshots=policy_snapshots,
            validation_issues=extraction.validation_issues,
            summary_lines=_summary_lines(
                _DRAFT_TITLES[application_type],
                extraction,
                material_answer.result,
                approval_answer.result,
            ),
            warnings=_warnings(
                material_answer.result,
                approval_answer.result,
                extraction.validation_issues,
            ),
            ready_for_confirmation=ready_for_confirmation,
            confirmation_required=True,
            user_confirmed=False,
            submitted=False,
            audit_metadata=DraftAuditMetadata(
                session_id=active_session_id,
                request_id=f"REQUEST-{digest[:16]}",
                idempotency_key=(
                    f"draft:{application_type.value}:"
                    f"{self._user_context.employee_id}:{digest[:20]}"
                ),
                created_at=created_at,
                created_by=self._user_context.employee_id,
                identity_source=self._user_context.identity_source,
                persisted=False,
            ),
        )
        result = DraftGenerationResult(
            application_type=application_type,
            draft=draft,
            clarification_question=clarification_question,
            citations=citations,
        )
        return DraftGenerationAnswer(
            request=normalized_input,
            result=result,
            reply=_format_reply(draft, clarification_question, citations),
        )

    async def revise(
        self,
        previous_draft: ApplicationDraft,
        user_input: str,
        *,
        session_id: str | None = None,
        context_messages: Sequence[str] = (),
    ) -> DraftGenerationAnswer:
        """合并一轮补充或修改，并重新执行材料、审批与草稿校验。"""

        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")

        normalized_revision = _normalize_revision_text(
            normalized_input
        )
        previous_fields = _field_map(previous_draft)
        cue = _APPLICATION_TYPE_CUES[
            previous_draft.application_type
        ]
        probe_parts = [cue]
        if previous_draft.application_type is ApplicationType.PURCHASE:
            direct_overrides = _purchase_revision_overrides(
                normalized_revision,
            )
            direct_map = {
                field.field_name: field
                for field in direct_overrides
            }
            item = _field_text(
                {**previous_fields, **direct_map},
                "item_name",
            )
            quantity = _field_text(
                {**previous_fields, **direct_map},
                "quantity",
            )
            unit = _field_text(
                {**previous_fields, **direct_map},
                "unit",
            ) or "件"
            if item is not None and quantity is not None:
                probe_parts.append(
                    f"采购{quantity}{unit}{item}"
                )
        else:
            direct_overrides = ()
        probe_parts.append(normalized_revision)

        probe_answer = await self.generate(
            "，".join(probe_parts),
            session_id=session_id,
        )
        merged_fields = dict(previous_fields)
        if probe_answer.result.draft is not None:
            for field in probe_answer.result.draft.fields:
                if field.field_name == "calculated_total_amount":
                    continue
                merged_fields[field.field_name] = field
        for field in direct_overrides:
            merged_fields[field.field_name] = field

        revision_request = _render_revision_request(
            previous_draft.application_type,
            merged_fields,
            (*context_messages, normalized_input),
        )
        revised_answer = await self.generate(
            revision_request,
            session_id=session_id,
        )
        revised_draft = revised_answer.result.draft
        if revised_draft is None:
            raise RuntimeError(
                "draft revision unexpectedly lost application type"
            )

        revised_audit = replace(
            revised_draft.audit_metadata,
            session_id=(
                session_id.strip()
                if session_id is not None
                else previous_draft.audit_metadata.session_id
            ),
            idempotency_key=(
                previous_draft.audit_metadata.idempotency_key
            ),
            created_at=previous_draft.audit_metadata.created_at,
            created_by=previous_draft.audit_metadata.created_by,
            identity_source=(
                previous_draft.audit_metadata.identity_source
            ),
            persisted=False,
        )
        revised_draft = replace(
            revised_draft,
            draft_id=previous_draft.draft_id,
            audit_metadata=revised_audit,
            revision=previous_draft.revision + 1,
            confirmed_at=None,
            cancelled_at=None,
            user_confirmed=False,
            submitted=False,
        )
        revised_result = replace(
            revised_answer.result,
            draft=revised_draft,
        )
        return DraftGenerationAnswer(
            request=normalized_input,
            result=revised_result,
            reply=(
                f"已更新{revised_draft.title}（第"
                f"{revised_draft.revision}版）。\n"
                + _format_reply(
                    revised_draft,
                    revised_result.clarification_question,
                    revised_result.citations,
                )
            ),
        )
