from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.tools.material_check import RequiredMaterialsChecker
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_CASES = (
    {
        "input": "出差报销需要准备哪些材料？",
        "application_type": ApplicationType.TRAVEL_REIMBURSEMENT,
        "mode": MaterialCheckMode.REQUIREMENTS,
        "required_count": 7,
        "materials_complete": None,
    },
    {
        "input": (
            "我准备了出差申请单、行程单和交通票据，"
            "帮我检查还缺什么。"
        ),
        "application_type": ApplicationType.TRAVEL_REIMBURSEMENT,
        "mode": MaterialCheckMode.COMPARISON,
        "missing_count": 4,
        "materials_complete": False,
    },
    {
        "input": "采购三台显示器，每台2000元，需要哪些材料？",
        "application_type": ApplicationType.PURCHASE,
        "mode": MaterialCheckMode.REQUIREMENTS,
        "quotation_count": 2,
        "materials_complete": None,
    },
    {
        "input": "我需要请四天病假，目前还没有医院证明。",
        "application_type": ApplicationType.LEAVE,
        "mode": MaterialCheckMode.COMPARISON,
        "missing_type": "medical_proof",
        "materials_complete": False,
    },
    {
        "input": "培训费用报销需要哪些材料？",
        "application_type": ApplicationType.EXPENSE_REIMBURSEMENT,
        "mode": MaterialCheckMode.REQUIREMENTS,
        "required_count": 10,
        "materials_complete": None,
    },
)


async def _main() -> None:
    checker = RequiredMaterialsChecker.from_policy_directory(
        _POLICY_DIRECTORY
    )
    failures: list[str] = []

    for case in _CASES:
        answer = await checker.check(case["input"])
        result = answer.result
        quotation_requirement = next(
            (
                item
                for item in result.required_materials
                if item.material_type == "quotation"
            ),
            None,
        )
        passed = (
            result.application_type is case["application_type"]
            and result.mode is case["mode"]
            and result.materials_complete
            is case["materials_complete"]
            and len(result.citations) > 0
            and (
                "required_count" not in case
                or len(result.required_materials)
                == case["required_count"]
            )
            and (
                "missing_count" not in case
                or len(result.missing_materials)
                == case["missing_count"]
            )
            and (
                "quotation_count" not in case
                or (
                    quotation_requirement is not None
                    and quotation_requirement.required_count
                    == case["quotation_count"]
                )
            )
            and (
                "missing_type" not in case
                or any(
                    item.material_type == case["missing_type"]
                    for item in result.missing_materials
                )
            )
        )

        print(
            json.dumps(
                {
                    "input": case["input"],
                    "application_type": result.application_type,
                    "mode": result.mode,
                    "required_count": len(result.required_materials),
                    "provided_count": len(result.provided_materials),
                    "missing_count": len(result.missing_materials),
                    "materials_complete": result.materials_complete,
                    "citations": [
                        citation.source_id
                        for citation in result.citations
                    ],
                    "passed": passed,
                },
                ensure_ascii=False,
            )
        )

        if not passed:
            failures.append(case["input"])

    if failures:
        raise RuntimeError(
            "Material check verification failed:\n"
            + "\n".join(failures)
        )


if __name__ == "__main__":
    asyncio.run(_main())

