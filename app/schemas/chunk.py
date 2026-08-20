from __future__ import annotations

from datetime import date
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.policy import (
    PolicyStatus,
    SecurityLevel,
)


class ChunkType(StrEnum):
    """知识库切片类型。"""

    ARTICLE = "article"


class PolicyChunk(BaseModel):
    """
    一条可独立检索和引用的制度条款。

    当前阶段采用：
    一条制度条款 = 一个 PolicyChunk
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(
        min_length=1,
        description="切片唯一且稳定的编号",
    )
    chunk_type: ChunkType = ChunkType.ARTICLE
    chunk_index: int = Field(
        ge=1,
        description="该制度内的切片顺序，从1开始",
    )

    document_id: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    document_version: str = Field(min_length=1)
    document_status: PolicyStatus
    issuing_department: str = Field(min_length=1)

    chapter_index: int = Field(
        ge=1,
        description="条款所属章节顺序",
    )
    chapter_title: str = Field(
        min_length=1,
        description="完整章节标题，如第一章 总则",
    )

    article_label: str = Field(
        min_length=1,
        description="条款编号，如第一条",
    )
    article_title: str = Field(
        min_length=1,
        description="条款标题，如制定目的",
    )

    content: str = Field(
        min_length=1,
        description="原始条款 Markdown 内容",
    )
    retrieval_text: str = Field(
        min_length=1,
        description="后续用于向量化和检索的增强文本",
    )

    source_path: Path
    source_media_type: str = Field(
        default="text/markdown",
        min_length=1,
    )
    metadata_source_path: Path | None = None
    source_line_start: int = Field(ge=1)
    source_line_end: int = Field(ge=1)
    source_page_start: int | None = Field(default=None, ge=1)
    source_page_end: int | None = Field(default=None, ge=1)
    source_block_start: int | None = Field(default=None, ge=1)
    source_block_end: int | None = Field(default=None, ge=1)

    effective_date: date
    expiry_date: date | None = None
    allowed_departments: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    security_level: SecurityLevel
    region: str | None = None

    char_count: int = Field(ge=1)
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        description="条款内容的 SHA-256 摘要",
    )

    @field_validator(
        "chapter_title",
        "article_label",
        "article_title",
        "content",
        "retrieval_text",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("字段内容不能为空")

        return normalized

    @model_validator(mode="after")
    def validate_derived_fields(self) -> PolicyChunk:
        if self.source_line_end < self.source_line_start:
            raise ValueError("source_line_end 不能小于 source_line_start")

        if (self.source_page_start is None) != (self.source_page_end is None):
            raise ValueError("source_page_start 和 source_page_end 必须同时存在或同时为空")

        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_end < self.source_page_start
        ):
            raise ValueError("source_page_end 不能小于 source_page_start")

        if (self.source_block_start is None) != (self.source_block_end is None):
            raise ValueError("source_block_start 和 source_block_end 必须同时存在或同时为空")

        if (
            self.source_block_start is not None
            and self.source_block_end is not None
            and self.source_block_end < self.source_block_start
        ):
            raise ValueError("source_block_end 不能小于 source_block_start")

        if self.char_count != len(self.content):
            raise ValueError("char_count 必须等于 content 的实际字符数")

        expected_hash = sha256(self.content.encode("utf-8")).hexdigest()

        if self.content_hash != expected_hash:
            raise ValueError("content_hash 与 content 内容不一致")

        return self
