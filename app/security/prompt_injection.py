from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

PROMPT_SECURITY_RULE_SET_VERSION = "day29-v1"

_ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


class PromptInjectionCategory(StrEnum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    SECRET_EXFILTRATION = "secret_exfiltration"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    ROLE_OVERRIDE = "role_override"
    TOOL_MANIPULATION = "tool_manipulation"
    ENCODED_INSTRUCTION = "encoded_instruction"
    PROMPT_BOUNDARY_SPOOFING = "prompt_boundary_spoofing"


@dataclass(frozen=True, slots=True)
class PromptInjectionAssessment:
    blocked: bool
    categories: tuple[PromptInjectionCategory, ...]
    matched_rule_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptSecurityMetricsSnapshot:
    rule_set_version: str
    user_inputs_checked: int
    user_inputs_blocked: int
    evidence_chunks_checked: int
    evidence_chunks_quarantined: int
    llm_calls_avoided: int


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    category: PromptInjectionCategory
    pattern: re.Pattern[str]


def _rule(
    rule_id: str,
    category: PromptInjectionCategory,
    pattern: str,
) -> _Rule:
    return _Rule(
        rule_id=rule_id,
        category=category,
        pattern=re.compile(pattern, flags=re.IGNORECASE),
    )


_RULES = (
    _rule(
        "PI001",
        PromptInjectionCategory.INSTRUCTION_OVERRIDE,
        r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,48}"
        r"\b(?:previous|prior|above|system|developer|security|safety)\b.{0,32}"
        r"\b(?:instructions?|prompts?|rules?|policies|guardrails?)\b",
    ),
    _rule(
        "PI002",
        PromptInjectionCategory.INSTRUCTION_OVERRIDE,
        r"(?:忽略|无视|忘掉|覆盖|绕过).{0,20}"
        r"(?:之前|以上|前面|系统|开发者|安全|权限).{0,16}"
        r"(?:指令|提示词?|规则|限制|策略)",
    ),
    _rule(
        "PI003",
        PromptInjectionCategory.SECRET_EXFILTRATION,
        r"\b(?:reveal|print|repeat|dump|expose|return)\b.{0,48}"
        r"(?:system prompt|developer message|hidden instructions?|api[- ]?keys?|"
        r"access tokens?|secret keys?|passwords?)",
    ),
    _rule(
        "PI004",
        PromptInjectionCategory.SECRET_EXFILTRATION,
        r"(?:给出|输出|打印|复述|暴露|告诉我).{0,28}"
        r"(?:系统提示词?|开发者消息|隐藏指令|API.?Key|访问令牌|密钥|密码)",
    ),
    _rule(
        "PI005",
        PromptInjectionCategory.PRIVILEGE_ESCALATION,
        r"\b(?:i am|i'm|treat me as|make me|grant me)\b.{0,28}"
        r"\b(?:administrator|admin|root|finance|security officer|hr manager)\b"
        r".{0,40}\b(?:access|permission|privilege|confidential|restricted|core)\b",
    ),
    _rule(
        "PI006",
        PromptInjectionCategory.PRIVILEGE_ESCALATION,
        r"(?:我是|将我设为|把我当作|授予我).{0,20}"
        r"(?:管理员|财务|人力|安全员|最高权限|核心权限).{0,24}"
        r"(?:查看|访问|给我|权限|核心|敏感|保密)",
    ),
    _rule(
        "PI007",
        PromptInjectionCategory.ROLE_OVERRIDE,
        r"\b(?:you are now|act as|switch to)\b.{0,32}"
        r"\b(?:unrestricted|developer|system|administrator|root|dan|jailbroken)\b",
    ),
    _rule(
        "PI008",
        PromptInjectionCategory.ROLE_OVERRIDE,
        r"(?:你现在是|扮演|切换为|进入).{0,20}"
        r"(?:无约束|开发者|系统|管理员|DAN|越狱)"
        r"(?:模式|角色|身份)?",
    ),
    _rule(
        "PI009",
        PromptInjectionCategory.TOOL_MANIPULATION,
        r"\b(?:call|invoke|execute|run)\b.{0,32}\b(?:tool|function|command)\b"
        r".{0,32}\b(?:without|bypass|ignore|skip)\b",
    ),
    _rule(
        "PI010",
        PromptInjectionCategory.TOOL_MANIPULATION,
        r"(?:调用|执行|运行).{0,24}(?:工具|函数|命令).{0,24}"
        r"(?:无需|绕过|跳过|忽略)(?:确认|权限|审批|限制)?",
    ),
    _rule(
        "PI011",
        PromptInjectionCategory.ENCODED_INSTRUCTION,
        r"\b(?:decode|base64|rot13)\b.{0,32}\b(?:execute|follow|obey)\b"
        r".{0,24}\b(?:content|instructions?)\b",
    ),
    _rule(
        "PI012",
        PromptInjectionCategory.ENCODED_INSTRUCTION,
        r"(?:解码|Base64|ROT13).{0,24}(?:执行|遵循|服从).{0,16}(?:内容|指令)",
    ),
    _rule(
        "PI013",
        PromptInjectionCategory.PROMPT_BOUNDARY_SPOOFING,
        r"(?:<system>|\[system\]|###\s*system|###\s*developer).{0,80}"
        r"(?:ignore|execute|instruction|you are)",
    ),
)


def _normalized_scan_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _ZERO_WIDTH_PATTERN.sub("", normalized)
    return " ".join(normalized.split())


def assess_prompt_injection(text: str) -> PromptInjectionAssessment:
    """Return rule identifiers only; never retain or expose the matched text."""

    candidate = _normalized_scan_text(text)
    matches = tuple(rule for rule in _RULES if rule.pattern.search(candidate))
    categories = tuple(dict.fromkeys(rule.category for rule in matches))
    return PromptInjectionAssessment(
        blocked=bool(matches),
        categories=categories,
        matched_rule_ids=tuple(rule.rule_id for rule in matches),
    )


class PromptInjectionBlockedError(ValueError):
    """Raised before any LLM, retrieval, Web Search, or workflow execution."""

    def __init__(self, assessment: PromptInjectionAssessment) -> None:
        super().__init__("request rejected by prompt security policy")
        self.assessment = assessment


class PromptInjectionGuard:
    """Deterministic prompt-injection guard with content-free process metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._user_inputs_checked = 0
        self._user_inputs_blocked = 0
        self._evidence_chunks_checked = 0
        self._evidence_chunks_quarantined = 0
        self._llm_calls_avoided = 0

    def assess_user_input(self, text: str) -> PromptInjectionAssessment:
        assessment = assess_prompt_injection(text)
        with self._lock:
            self._user_inputs_checked += 1
            if assessment.blocked:
                self._user_inputs_blocked += 1
        return assessment

    def enforce_user_input(self, text: str) -> None:
        assessment = self.assess_user_input(text)
        if assessment.blocked:
            with self._lock:
                self._llm_calls_avoided += 1
            raise PromptInjectionBlockedError(assessment)

    def assess_evidence(self, text: str) -> PromptInjectionAssessment:
        assessment = assess_prompt_injection(text)
        with self._lock:
            self._evidence_chunks_checked += 1
            if assessment.blocked:
                self._evidence_chunks_quarantined += 1
        return assessment

    def snapshot(self) -> PromptSecurityMetricsSnapshot:
        with self._lock:
            return PromptSecurityMetricsSnapshot(
                rule_set_version=PROMPT_SECURITY_RULE_SET_VERSION,
                user_inputs_checked=self._user_inputs_checked,
                user_inputs_blocked=self._user_inputs_blocked,
                evidence_chunks_checked=self._evidence_chunks_checked,
                evidence_chunks_quarantined=self._evidence_chunks_quarantined,
                llm_calls_avoided=self._llm_calls_avoided,
            )
