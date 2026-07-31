from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

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
        raise PolicyParseError(
            "制度文件必须以 YAML front matter 分隔符 '---' 开头"
        )

    closing_marker = "\n---\n"
    closing_index = normalized.find(
        closing_marker,
        len("---\n"),
    )

    if closing_index == -1:
        raise PolicyParseError(
            "没有找到 YAML front matter 的结束分隔符 '---'"
        )

    yaml_text = normalized[len("---\n"):closing_index].strip()
    markdown_content = normalized[
        closing_index + len(closing_marker):
    ].strip()

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
        raise PolicyParseError(
            f"YAML 元数据解析失败：{exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise PolicyParseError(
            "YAML 元数据必须解析为键值对象"
        )

    return parsed


def parse_policy_text(
    raw_text: str,
    *,
    source_path: Path,
) -> PolicyDocument:
    """解析已经读取到内存中的制度文本。"""

    if not raw_text.strip():
        raise PolicyParseError("制度文件内容为空")

    yaml_text, markdown_content = _split_front_matter(raw_text)
    metadata_dict = _parse_yaml_metadata(yaml_text)

    try:
        metadata = PolicyMetadata.model_validate(metadata_dict)
    except ValidationError as exc:
        raise PolicyParseError(
            f"制度元数据校验失败：{exc}"
        ) from exc

    return PolicyDocument(
        metadata=metadata,
        content=markdown_content,
        source_path=source_path,
        raw_text=raw_text,
    )


def parse_policy_file(path: str | Path) -> PolicyDocument:
    """读取并解析一份 Markdown 制度文件。"""

    source_path = Path(path)

    if not source_path.exists():
        raise PolicyParseError(
            f"制度文件不存在：{source_path}"
        )

    if not source_path.is_file():
        raise PolicyParseError(
            f"制度路径不是文件：{source_path}"
        )

    if source_path.suffix.lower() != ".md":
        raise PolicyParseError(
            f"制度文件必须是 .md 文件：{source_path}"
        )

    try:
        raw_text = source_path.read_text(
            encoding="utf-8-sig"
        )
    except UnicodeDecodeError as exc:
        raise PolicyParseError(
            f"制度文件不是有效的 UTF-8 文本：{source_path}"
        ) from exc
    except OSError as exc:
        raise PolicyParseError(
            f"读取制度文件失败：{source_path}，原因：{exc}"
        ) from exc

    return parse_policy_text(
        raw_text,
        source_path=source_path,
    )


def parse_policy_directory(
    directory: str | Path,
) -> list[PolicyDocument]:
    """解析目录中的全部 Markdown 制度文件。"""

    policy_directory = Path(directory)

    if not policy_directory.exists():
        raise PolicyParseError(
            f"制度目录不存在：{policy_directory}"
        )

    if not policy_directory.is_dir():
        raise PolicyParseError(
            f"制度路径不是目录：{policy_directory}"
        )

    policy_paths = sorted(
        policy_directory.glob("*.md"),
        key=lambda path: path.name,
    )

    if not policy_paths:
        raise PolicyParseError(
            f"制度目录中没有 Markdown 文件：{policy_directory}"
        )

    documents = [
        parse_policy_file(path)
        for path in policy_paths
    ]

    document_ids = [
        document.metadata.document_id
        for document in documents
    ]

    duplicate_ids = {
        document_id
        for document_id in document_ids
        if document_ids.count(document_id) > 1
    }

    if duplicate_ids:
        raise PolicyParseError(
            "发现重复制度编号："
            + ", ".join(sorted(duplicate_ids))
        )

    return documents
