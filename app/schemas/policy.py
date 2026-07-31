from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyStatus(StrEnum):
    """制度当前状态。"""

    DRAFT = "draft"
    EFFECTIVE = "effective"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class SecurityLevel(StrEnum):
    """制度或数据的安全等级。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    CORE = "core"


class PolicyMetadata(BaseModel):
    """从制度 YAML 头部解析出的元数据。"""

    model_config = ConfigDict(extra="allow")

    document_id: str = Field(
        min_length=1,
        description="制度唯一编号",
    )
    document_type: str = Field(
        min_length=1,
        description="文档类型，例如 policy 或 guide",
    )
    title: str = Field(
        min_length=1,
        description="制度标题",
    )
    version: str = Field(
        min_length=1,
        description="制度版本号",
    )
    status: PolicyStatus
    issuing_department: str = Field(
        min_length=1,
        description="制度发布部门",
    )
    effective_date: date
    expiry_date: date | None = None
    allowed_departments: list[str] = Field(
        default_factory=list,
    )
    allowed_roles: list[str] = Field(
        default_factory=list,
    )
    security_level: SecurityLevel
    region: str | None = None

    @field_validator(
        "allowed_departments",
        "allowed_roles",
        mode="before",
    )
    @classmethod
    def normalize_string_list(
        cls,
        value: object,
    ) -> list[str]:
        """将 YAML 中的字符串或列表统一转换为字符串列表。"""

        if value is None:
            return []

        if isinstance(value, str):
            return [value.strip()]

        if isinstance(value, list):
            normalized = [
                str(item).strip()
                for item in value
                if str(item).strip()
            ]
            return normalized

        raise ValueError("必须是字符串或字符串列表")


class PolicyDocument(BaseModel):
    """一份完整制度文档。"""

    metadata: PolicyMetadata
    content: str = Field(
        min_length=1,
        description="删除 YAML 头后的 Markdown 正文",
    )
    source_path: Path
    raw_text: str = Field(
        min_length=1,
        description="原始制度全文",
    )

    @property
    def document_id(self) -> str:
        return self.metadata.document_id

    @property
    def title(self) -> str:
        return self.metadata.title
