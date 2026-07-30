from __future__ import annotations

import json
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent

POLICY_DIR = PROJECT_ROOT / "data" / "policies"
APPLICATION_DIR = PROJECT_ROOT / "data" / "samples" / "applications"
CONTRACT_DIR = PROJECT_ROOT / "docs" / "tool_contracts"
GOLDEN_TEST_PATH = (
    PROJECT_ROOT / "tests" / "evaluation" / "golden_test_cases.jsonl"
)
README_PATH = PROJECT_ROOT / "README.md"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


EXPECTED_POLICY_FILES = {
    "expense_reimbursement_guide_v1.md",
    "information_security_policy_v1.md",
    "leave_management_policy_v1.md",
    "procurement_management_policy_v1.md",
    "travel_reimbursement_policy_v1.md",
}

EXPECTED_APPLICATION_FILES = {
    "leave_application_complete.json",
    "leave_application_incomplete.json",
    "purchase_application_complete.json",
    "purchase_application_incomplete.json",
    "travel_reimbursement_complete.json",
    "travel_reimbursement_incomplete.json",
}

EXPECTED_CONTRACT_TOOLS = {
    "search_policy",
    "check_required_materials",
    "create_application_draft",
    "submit_mock_approval",
}

EXPECTED_TEST_CATEGORIES = {
    "policy_qa": 8,
    "rule_calculation": 5,
    "workflow": 8,
    "safety_permission": 5,
    "edge_case": 4,
}

REQUIRED_TEST_FIELDS = {
    "case_id",
    "category",
    "title",
    "user_role",
    "department",
    "query",
    "expected_intent",
    "expected_tools",
    "expected_policy_ids",
    "assertions",
    "must_cite",
    "should_submit",
    "risk_tags",
}

REQUIRED_README_SECTIONS = {
    "## 1. 项目背景",
    "## 4. 当前项目状态",
    "## 7. Agent 工具设计",
    "## 10. 黄金测试集",
    "## 15. 开发路线",
    "## 19. 免责声明",
}

REQUIRED_GITIGNORE_RULES = {
    ".venv/",
    ".env",
    "__pycache__/",
}


def print_result(name: str, passed: bool, detail: str = "") -> None:
    mark = "PASS" if passed else "FAIL"
    message = f"[{mark}] {name}"

    if detail:
        message += f"：{detail}"

    print(message)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_policies(errors: list[str]) -> None:
    policy_files = sorted(POLICY_DIR.glob("*.md"))
    actual_names = {path.name for path in policy_files}

    count_passed = len(policy_files) == 5
    names_passed = actual_names == EXPECTED_POLICY_FILES

    print_result("制度文件数量", count_passed, str(len(policy_files)))
    print_result("制度文件名称", names_passed)

    if not count_passed:
        errors.append(f"制度文件数量应为5，实际为{len(policy_files)}")

    if not names_passed:
        missing = EXPECTED_POLICY_FILES - actual_names
        unexpected = actual_names - EXPECTED_POLICY_FILES
        errors.append(
            f"制度文件不一致，缺少={sorted(missing)}，多出={sorted(unexpected)}"
        )

    required_metadata = [
        "document_id:",
        "document_type:",
        "title:",
        "version:",
        "effective_date:",
        "allowed_roles:",
        "security_level:",
    ]

    for path in policy_files:
        text = path.read_text(encoding="utf-8-sig")
        metadata_passed = (
            text.startswith("---")
            and all(field in text for field in required_metadata)
        )

        print_result(
            f"制度元数据 {path.name}",
            metadata_passed,
        )

        if not metadata_passed:
            errors.append(f"{path.name} 缺少必要元数据或YAML头")


def validate_applications(errors: list[str]) -> None:
    application_files = sorted(APPLICATION_DIR.glob("*.json"))
    actual_names = {path.name for path in application_files}

    count_passed = len(application_files) == 6
    names_passed = actual_names == EXPECTED_APPLICATION_FILES

    print_result("申请样例数量", count_passed, str(len(application_files)))
    print_result("申请样例名称", names_passed)

    if not count_passed:
        errors.append(f"申请样例数量应为6，实际为{len(application_files)}")

    if not names_passed:
        missing = EXPECTED_APPLICATION_FILES - actual_names
        unexpected = actual_names - EXPECTED_APPLICATION_FILES
        errors.append(
            f"申请样例不一致，缺少={sorted(missing)}，多出={sorted(unexpected)}"
        )

    for path in application_files:
        try:
            data = read_json(path)
            parsed = isinstance(data, dict)
        except (OSError, json.JSONDecodeError) as exc:
            parsed = False
            errors.append(f"{path.name} JSON解析失败：{exc}")

        print_result(f"申请JSON {path.name}", parsed)


def validate_contracts(errors: list[str]) -> None:
    contract_files = sorted(CONTRACT_DIR.glob("*.json"))
    tool_names: set[str] = set()

    print_result(
        "工具契约数量",
        len(contract_files) == 4,
        str(len(contract_files)),
    )

    if len(contract_files) != 4:
        errors.append(f"工具契约数量应为4，实际为{len(contract_files)}")

    for path in contract_files:
        try:
            data = read_json(path)
            tool_name = data["tool_name"]
            side_effect = data["side_effect"]
            requires_confirmation = data["requires_user_confirmation"]

            tool_names.add(tool_name)
            parsed = True

            detail = (
                f"{tool_name}, side_effect={side_effect}, "
                f"requires_confirmation={requires_confirmation}"
            )
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            parsed = False
            detail = str(exc)
            errors.append(f"{path.name} 契约解析失败：{exc}")

        print_result(f"工具契约 {path.name}", parsed, detail)

    tools_passed = tool_names == EXPECTED_CONTRACT_TOOLS
    print_result("工具名称集合", tools_passed, str(sorted(tool_names)))

    if not tools_passed:
        errors.append(
            "工具名称不一致，"
            f"期望={sorted(EXPECTED_CONTRACT_TOOLS)}，"
            f"实际={sorted(tool_names)}"
        )


def validate_golden_tests(errors: list[str]) -> None:
    if not GOLDEN_TEST_PATH.exists():
        print_result("黄金测试文件", False, "文件不存在")
        errors.append("黄金测试文件不存在")
        return

    cases: list[dict[str, Any]] = []

    for line_number, line in enumerate(
        GOLDEN_TEST_PATH.read_text(
            encoding="utf-8-sig"
        ).splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"黄金测试第{line_number}行解析失败：{exc}")
            continue

        cases.append(case)

    print_result("黄金测试数量", len(cases) == 30, str(len(cases)))

    if len(cases) != 30:
        errors.append(f"黄金测试数量应为30，实际为{len(cases)}")

    case_ids = [case.get("case_id") for case in cases]
    expected_ids = [f"TC{number:03d}" for number in range(1, 31)]

    ids_unique = len(case_ids) == len(set(case_ids))
    ids_continuous = case_ids == expected_ids

    print_result("测试编号不重复", ids_unique)
    print_result("测试编号连续", ids_continuous)

    if not ids_unique:
        errors.append("黄金测试编号存在重复")

    if not ids_continuous:
        errors.append("黄金测试编号不是TC001到TC030连续编号")

    invalid_structures: list[str] = []

    for case in cases:
        missing_fields = REQUIRED_TEST_FIELDS - set(case)

        if missing_fields:
            invalid_structures.append(
                f"{case.get('case_id', 'UNKNOWN')}缺少"
                f"{sorted(missing_fields)}"
            )

    structure_passed = not invalid_structures
    print_result("黄金测试结构", structure_passed)

    if invalid_structures:
        errors.extend(invalid_structures)

    category_counts = Counter(
        case.get("category")
        for case in cases
    )

    categories_passed = dict(category_counts) == EXPECTED_TEST_CATEGORIES

    print_result(
        "测试类别数量",
        categories_passed,
        str(dict(sorted(category_counts.items()))),
    )

    if not categories_passed:
        errors.append(
            "测试类别数量不正确，"
            f"期望={EXPECTED_TEST_CATEGORIES}，"
            f"实际={dict(category_counts)}"
        )

    covered_tools = {
        tool
        for case in cases
        for tool in case.get("expected_tools", [])
    }

    tool_coverage_passed = EXPECTED_CONTRACT_TOOLS.issubset(
        covered_tools
    )

    print_result(
        "4个工具测试覆盖",
        tool_coverage_passed,
        str(sorted(covered_tools)),
    )

    if not tool_coverage_passed:
        errors.append(
            "黄金测试没有覆盖全部工具，"
            f"实际覆盖={sorted(covered_tools)}"
        )

    submit_case_ids = [
        case["case_id"]
        for case in cases
        if case.get("should_submit") is True
    ]

    submit_rule_passed = submit_case_ids == ["TC020"]

    print_result(
        "首次正式提交用例",
        submit_rule_passed,
        str(submit_case_ids),
    )

    if not submit_rule_passed:
        errors.append(
            f"只有TC020应允许首次提交，实际为{submit_case_ids}"
        )


def validate_readme(errors: list[str]) -> None:
    if not README_PATH.exists():
        print_result("README", False, "文件不存在")
        errors.append("README.md不存在")
        return

    text = README_PATH.read_text(encoding="utf-8-sig")

    missing_sections = sorted(
        section
        for section in REQUIRED_README_SECTIONS
        if section not in text
    )

    readme_passed = not missing_sections

    print_result(
        "README关键章节",
        readme_passed,
        (
            "完整"
            if readme_passed
            else f"缺少={missing_sections}"
        ),
    )

    if missing_sections:
        errors.append(f"README缺少章节：{missing_sections}")

    honesty_checks = [
        "尚未实现",
        "FastAPI 业务接口",
        "向量数据库",
        "Agent 状态机",
    ]

    honest_status = all(item in text for item in honesty_checks)
    print_result("README完成度说明", honest_status)

    if not honest_status:
        errors.append("README没有清楚标记尚未实现的功能")


def validate_project_config(errors: list[str]) -> None:
    try:
        pyproject_data = tomllib.loads(
            PYPROJECT_PATH.read_text(encoding="utf-8-sig")
        )
        pyproject_passed = isinstance(pyproject_data, dict)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        pyproject_passed = False
        errors.append(f"pyproject.toml解析失败：{exc}")

    print_result("pyproject.toml", pyproject_passed)

    gitignore_text = GITIGNORE_PATH.read_text(
        encoding="utf-8-sig"
    )
    gitignore_lines = {
        line.strip()
        for line in gitignore_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    missing_rules = REQUIRED_GITIGNORE_RULES - gitignore_lines
    gitignore_passed = not missing_rules

    print_result(
        ".gitignore关键规则",
        gitignore_passed,
        (
            "完整"
            if gitignore_passed
            else f"缺少={sorted(missing_rules)}"
        ),
    )

    if missing_rules:
        errors.append(
            f".gitignore缺少规则：{sorted(missing_rules)}"
        )


def main() -> int:
    print("=" * 60)
    print("企业制度问答与流程办理 Agent - Day 1 验收")
    print(f"项目目录：{PROJECT_ROOT}")
    print("=" * 60)

    errors: list[str] = []

    validate_policies(errors)
    validate_applications(errors)
    validate_contracts(errors)
    validate_golden_tests(errors)
    validate_readme(errors)
    validate_project_config(errors)

    print("=" * 60)

    if errors:
        print(f"验收失败，共发现 {len(errors)} 个问题：")

        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")

        return 1

    print("Day 1核心资产验收通过。")
    print("制度=5，申请样例=6，工具契约=4，黄金测试=30。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
