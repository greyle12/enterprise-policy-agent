from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.policy_parser import (
    PolicyParseError,
    parse_policy_directory,
    parse_policy_file,
    parse_policy_text,
)


POLICY_DIR = Path("data/policies")


def test_parse_single_policy_file() -> None:
    document = parse_policy_file(
        POLICY_DIR / "travel_reimbursement_policy_v1.md"
    )

    assert document.metadata.document_id == "TRAVEL_POLICY_001"
    assert document.metadata.title == "差旅报销管理制度"
    assert document.metadata.version == "1.0"
    assert document.metadata.status.value == "effective"
    assert document.metadata.security_level.value == "internal"
    assert document.content
    assert document.raw_text
    assert "第一条" in document.content


def test_parse_all_policy_files() -> None:
    documents = parse_policy_directory(POLICY_DIR)

    assert len(documents) == 5

    document_ids = {
        document.metadata.document_id
        for document in documents
    }

    assert document_ids == {
        "TRAVEL_POLICY_001",
        "PROCUREMENT_POLICY_001",
        "LEAVE_POLICY_001",
        "INFORMATION_SECURITY_POLICY_001",
        "EXPENSE_REIMBURSEMENT_GUIDE_001",
    }


def test_front_matter_is_removed_from_content() -> None:
    document = parse_policy_file(
        POLICY_DIR / "procurement_management_policy_v1.md"
    )

    assert not document.content.startswith("---")
    assert "document_id:" not in document.content
    assert document.content.startswith("#")


def test_reject_empty_document() -> None:
    with pytest.raises(
        PolicyParseError,
        match="内容为空",
    ):
        parse_policy_text(
            "",
            source_path=Path("empty.md"),
        )


def test_reject_missing_front_matter() -> None:
    with pytest.raises(
        PolicyParseError,
        match="必须以 YAML front matter",
    ):
        parse_policy_text(
            "# 没有YAML头\n\n正文",
            source_path=Path("invalid.md"),
        )


def test_reject_missing_required_metadata() -> None:
    raw_text = """---
document_type: policy
title: 缺少编号的制度
version: "1.0"
status: effective
issuing_department: 测试部门
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
security_level: internal
---
# 缺少编号的制度

第一条 测试内容。
"""

    with pytest.raises(
        PolicyParseError,
        match="元数据校验失败",
    ):
        parse_policy_text(
            raw_text,
            source_path=Path("missing_document_id.md"),
        )


def test_reject_invalid_yaml() -> None:
    raw_text = """---
document_id: TEST_POLICY_001
title: [invalid
---
# 测试制度

第一条 测试内容。
"""

    with pytest.raises(
        PolicyParseError,
        match="YAML 元数据解析失败",
    ):
        parse_policy_text(
            raw_text,
            source_path=Path("invalid_yaml.md"),
        )


def test_reject_non_markdown_file(tmp_path: Path) -> None:
    file_path = tmp_path / "policy.txt"
    file_path.write_text("test", encoding="utf-8")

    with pytest.raises(
        PolicyParseError,
        match=r"必须是 \.md 文件",
    ):
        parse_policy_file(file_path)


def test_reject_missing_policy_directory() -> None:
    with pytest.raises(
        PolicyParseError,
        match="制度目录不存在",
    ):
        parse_policy_directory(
            "data/not_existing_policies"
        )


def test_reject_duplicate_document_ids(
    tmp_path: Path,
) -> None:
    template = """---
document_id: DUPLICATE_POLICY_001
document_type: policy
title: {title}
version: "1.0"
status: effective
issuing_department: 测试部门
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
security_level: internal
---
# {title}

第一条 测试内容。
"""

    (tmp_path / "first.md").write_text(
        template.format(title="第一份制度"),
        encoding="utf-8",
    )
    (tmp_path / "second.md").write_text(
        template.format(title="第二份制度"),
        encoding="utf-8",
    )

    with pytest.raises(
        PolicyParseError,
        match="发现重复制度编号",
    ):
        parse_policy_directory(tmp_path)
