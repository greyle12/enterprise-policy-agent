from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.schemas.chunk import PolicyChunk
from app.schemas.policy import PolicyStatus, SecurityLevel

_SECURITY_RANK = {
    SecurityLevel.PUBLIC: 0,
    SecurityLevel.INTERNAL: 1,
    SecurityLevel.SENSITIVE: 2,
    SecurityLevel.CORE: 3,
}
_ALL = "ALL"


class TrustedIdentitySource(StrEnum):
    """Server-owned identity sources accepted by the policy boundary."""

    AUTHENTICATION = "authentication"
    TEST_FIXTURE = "test_fixture"
    TRUSTED_DEMO_CONTEXT = "trusted_demo_context"


class PolicyAccessDenialReason(StrEnum):
    """Stable reasons used by offline tests, never exposed with policy content."""

    DOCUMENT_NOT_EFFECTIVE = "document_not_effective"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    EXPIRED = "expired"
    CLEARANCE = "clearance"
    DEPARTMENT = "department"
    ROLE = "role"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class PolicyAccessContext:
    """Trusted identity attributes supplied by the server, not the chat message."""

    employee_id: str
    department: str
    roles: tuple[str, ...]
    security_clearance: SecurityLevel
    region: str
    identity_source: TrustedIdentitySource

    def __post_init__(self) -> None:
        employee_id = self.employee_id.strip()
        department = self.department.strip()
        region = self.region.strip()
        roles = tuple(dict.fromkeys(role.strip().upper() for role in self.roles if role.strip()))
        if not employee_id:
            raise ValueError("employee_id must not be blank")
        if not department:
            raise ValueError("department must not be blank")
        if not roles:
            raise ValueError("roles must not be empty")
        if not region:
            raise ValueError("region must not be blank")
        object.__setattr__(self, "employee_id", employee_id)
        object.__setattr__(self, "department", department)
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "region", region)


@dataclass(frozen=True, slots=True)
class PolicyAccessDecision:
    allowed: bool
    denial_reasons: tuple[PolicyAccessDenialReason, ...]


def _normalized_scope(values: list[str]) -> frozenset[str]:
    return frozenset(value.strip().upper() for value in values if value.strip())


def evaluate_policy_access(
    chunk: PolicyChunk,
    context: PolicyAccessContext,
    *,
    as_of_date: date,
) -> PolicyAccessDecision:
    """Evaluate every metadata boundary before a chunk can be scored or prompted."""

    reasons: list[PolicyAccessDenialReason] = []
    if chunk.document_status is not PolicyStatus.EFFECTIVE:
        reasons.append(PolicyAccessDenialReason.DOCUMENT_NOT_EFFECTIVE)
    if chunk.effective_date > as_of_date:
        reasons.append(PolicyAccessDenialReason.NOT_YET_EFFECTIVE)
    if chunk.expiry_date is not None and chunk.expiry_date < as_of_date:
        reasons.append(PolicyAccessDenialReason.EXPIRED)
    if _SECURITY_RANK[chunk.security_level] > _SECURITY_RANK[context.security_clearance]:
        reasons.append(PolicyAccessDenialReason.CLEARANCE)

    departments = _normalized_scope(chunk.allowed_departments)
    if departments and _ALL not in departments:
        if context.department.upper() not in departments:
            reasons.append(PolicyAccessDenialReason.DEPARTMENT)

    roles = _normalized_scope(chunk.allowed_roles)
    if roles and _ALL not in roles and roles.isdisjoint(context.roles):
        reasons.append(PolicyAccessDenialReason.ROLE)

    if chunk.region is not None:
        chunk_region = chunk.region.strip().casefold()
        if chunk_region and chunk_region != context.region.casefold():
            reasons.append(PolicyAccessDenialReason.REGION)

    return PolicyAccessDecision(
        allowed=not reasons,
        denial_reasons=tuple(reasons),
    )


def authorized_chunk_ids(
    chunks: tuple[PolicyChunk, ...],
    context: PolicyAccessContext,
    *,
    as_of_date: date,
) -> frozenset[str]:
    """Return only IDs allowed by lifecycle, clearance, department, role, and region."""

    return frozenset(
        chunk.chunk_id
        for chunk in chunks
        if evaluate_policy_access(
            chunk,
            context,
            as_of_date=as_of_date,
        ).allowed
    )
