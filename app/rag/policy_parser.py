from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentDecodingError,
    DocumentLoadError,
    DocumentLoaderRegistry,
    DocumentNotFoundError,
    DocumentPathError,
    EmptyDocumentError,
    LoadedDocument,
    UnsupportedDocumentFormatError,
)
from app.schemas.policy import PolicyDocument, PolicyMetadata


class PolicyParseError(ValueError):
    """制度文件无法被正确解析。"""


def _split_front_matter(raw_text: str) -> tuple[str, str]:
    """
    将制度文件拆分为 YAML 元数据和 Markdown 正文。

    文件必须采用以下格式：

    ---
    document_id: POLICY_001
    ...
    ---
    # 制度正文
    """

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    if not normalized.startswith("---\n"):
        raise PolicyParseError("制度文件必须以 YAML front matter 分隔符 '---' 开头")

    closing_marker = "\n---\n"
    closing_index = normalized.find(
        closing_marker,
        len("---\n"),
    )

    if closing_index == -1:
        raise PolicyParseError("没有找到 YAML front matter 的结束分隔符 '---'")

    yaml_text = normalized[len("---\n") : closing_index].strip()
    markdown_content = normalized[closing_index + len(closing_marker) :].strip()

    if not yaml_text:
        raise PolicyParseError("YAML 元数据不能为空")

    if not markdown_content:
        raise PolicyParseError("制度正文不能为空")

    return yaml_text, markdown_content


def _parse_yaml_metadata(yaml_text: str) -> dict[str, Any]:
    """解析 YAML，并保证结果是对象结构。"""

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise PolicyParseError(f"YAML 元数据解析失败：{exc}") from exc

    if not isinstance(parsed, dict):
        raise PolicyParseError("YAML 元数据必须解析为键值对象")

    return parsed


def parse_policy_text(
    raw_text: str,
    *,
    source_path: Path,
    metadata_text: str | None = None,
    metadata_source_path: Path | None = None,
    source_media_type: str = "text/markdown",
    source_loader_name: str = "markdown",
    source_page_count: int | None = None,
    content_page_numbers: tuple[int, ...] = (),
) -> PolicyDocument:
    """Parse normalized policy text with inline or trusted external metadata."""

    if not raw_text.strip():
        raise PolicyParseError("制度文件内容为空")

    if metadata_text is None:
        yaml_text, markdown_content = _split_front_matter(raw_text)
        normalized_page_numbers: tuple[int, ...] = ()
    else:
        if not metadata_text.strip():
            raise PolicyParseError("制度元数据不能为空")
        normalized_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized_text.splitlines()
        page_numbers = list(content_page_numbers)
        if page_numbers and len(page_numbers) != len(lines):
            raise PolicyParseError("PDF 页码映射必须与 Loader 文本行数一致")
        while lines and not lines[0].strip():
            lines.pop(0)
            if page_numbers:
                page_numbers.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
            if page_numbers:
                page_numbers.pop()
        markdown_content = "\n".join(lines)
        if not markdown_content.strip():
            raise PolicyParseError("制度正文不能为空")
        yaml_text = metadata_text.strip()
        normalized_page_numbers = tuple(page_numbers)

    metadata_dict = _parse_yaml_metadata(yaml_text)

    try:
        metadata = PolicyMetadata.model_validate(metadata_dict)
    except ValidationError as exc:
        raise PolicyParseError(f"制度元数据校验失败：{exc}") from exc

    return PolicyDocument(
        metadata=metadata,
        content=markdown_content,
        source_path=source_path,
        source_media_type=source_media_type,
        source_loader_name=source_loader_name,
        metadata_source_path=metadata_source_path,
        source_page_count=source_page_count,
        content_page_numbers=normalized_page_numbers,
        raw_text=raw_text,
    )


def parse_loaded_policy(loaded: LoadedDocument) -> PolicyDocument:
    """Convert one format-neutral Loader result into a policy document."""

    return parse_policy_text(
        loaded.text,
        source_path=loaded.source_path,
        metadata_text=loaded.metadata_text,
        metadata_source_path=loaded.metadata_source_path,
        source_media_type=loaded.media_type,
        source_loader_name=loaded.loader_name,
        source_page_count=loaded.page_count,
        content_page_numbers=loaded.line_page_numbers,
    )


def parse_policy_file(
    path: str | Path,
    *,
    loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
) -> PolicyDocument:
    """Load and parse one policy file through the configured document loaders."""

    source_path = Path(path)

    try:
        loaded = loader_registry.load(source_path)
    except DocumentNotFoundError as exc:
        raise PolicyParseError(f"制度文件不存在：{source_path}") from exc
    except DocumentPathError as exc:
        raise PolicyParseError(f"制度路径不是文件：{source_path}") from exc
    except UnsupportedDocumentFormatError as exc:
        supported = ", ".join(loader_registry.supported_extensions)
        raise PolicyParseError(
            f"不支持的制度文件格式：{source_path.suffix.lower()}，已注册：{supported}"
        ) from exc
    except DocumentDecodingError as exc:
        raise PolicyParseError(f"制度文件不是有效的 UTF-8 文本：{source_path}") from exc
    except EmptyDocumentError as exc:
        raise PolicyParseError("制度文件内容为空") from exc
    except DocumentLoadError as exc:
        raise PolicyParseError(f"读取制度文件失败：{source_path}，原因：{exc}") from exc

    return parse_loaded_policy(loaded)


def parse_policy_directory(
    directory: str | Path,
    *,
    loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
) -> list[PolicyDocument]:
    """Parse every supported policy file in a directory."""

    policy_directory = Path(directory)

    if not policy_directory.exists():
        raise PolicyParseError(f"制度目录不存在：{policy_directory}")

    if not policy_directory.is_dir():
        raise PolicyParseError(f"制度路径不是目录：{policy_directory}")

    policy_paths = loader_registry.discover(policy_directory)

    if not policy_paths:
        supported = ", ".join(loader_registry.supported_extensions)
        raise PolicyParseError(f"制度目录中没有受支持的制度文件（{supported}）：{policy_directory}")

    documents = [
        parse_policy_file(
            path,
            loader_registry=loader_registry,
        )
        for path in policy_paths
    ]

    document_ids = [document.metadata.document_id for document in documents]

    duplicate_ids = {
        document_id for document_id in document_ids if document_ids.count(document_id) > 1
    }

    if duplicate_ids:
        raise PolicyParseError("发现重复制度编号：" + ", ".join(sorted(duplicate_ids)))

    return documents
