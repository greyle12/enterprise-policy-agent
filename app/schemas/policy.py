from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
            normalized = [str(item).strip() for item in value if str(item).strip()]
            return normalized

        raise ValueError("必须是字符串或字符串列表")


class PolicyDocument(BaseModel):
    """一份完整制度文档。"""

    metadata: PolicyMetadata
    content: str = Field(
        min_length=1,
        description="移除元数据后的规范化制度正文",
    )
    source_path: Path
    source_media_type: str = Field(
        default="text/markdown",
        min_length=1,
    )
    source_loader_name: str = Field(
        default="markdown",
        min_length=1,
    )
    metadata_source_path: Path | None = None
    source_page_count: int | None = Field(
        default=None,
        ge=1,
    )
    content_page_numbers: tuple[int, ...] = ()
    source_block_count: int | None = Field(
        default=None,
        ge=1,
    )
    content_block_numbers: tuple[int, ...] = ()
    raw_text: str = Field(
        min_length=1,
        description="Document Loader 输出的规范化全文",
    )

    @model_validator(mode="after")
    def validate_page_mapping(self) -> PolicyDocument:
        if self.content_page_numbers:
            if self.source_page_count is None:
                raise ValueError("content_page_numbers 需要 source_page_count")
            if len(self.content_page_numbers) != len(self.content.splitlines()):
                raise ValueError("content_page_numbers 必须与 content 行数一致")
            if any(
                page_number < 1 or page_number > self.source_page_count
                for page_number in self.content_page_numbers
            ):
                raise ValueError("content_page_numbers 必须位于 PDF 页码范围内")
        if self.content_block_numbers:
            if self.source_block_count is None:
                raise ValueError("content_block_numbers 需要 source_block_count")
            if len(self.content_block_numbers) != len(self.content.splitlines()):
                raise ValueError("content_block_numbers 必须与 content 行数一致")
            if any(
                block_number < 1 or block_number > self.source_block_count
                for block_number in self.content_block_numbers
            ):
                raise ValueError("content_block_numbers 必须位于 DOCX 块序号范围内")
        return self

    @property
    def document_id(self) -> str:
        return self.metadata.document_id

    @property
    def title(self) -> str:
        return self.metadata.title
