from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.cases import add_case_ids, stable_case_id


def test_stable_case_id_is_deterministic_and_does_not_expose_employee_key() -> None:
    first = stable_case_id(
        employee_key="PAYROLL-12345",
        workday_date=date(2026, 7, 1),
        violation_code="FIRST_MEAL_MISSING",
        location_ref="BYC301",
    )
    second = stable_case_id(
        employee_key="PAYROLL-12345",
        workday_date=date(2026, 7, 1),
        violation_code="FIRST_MEAL_MISSING",
        location_ref="BYC301",
    )

    assert first == second
    assert first.startswith("MV-")
    assert "12345" not in first


def test_case_id_changes_when_violation_code_changes() -> None:
    first = stable_case_id(
        employee_key="100",
        workday_date=date(2026, 7, 1),
        violation_code="FIRST_MEAL_MISSING",
        location_ref="BYC301",
    )
    second = stable_case_id(
        employee_key="100",
        workday_date=date(2026, 7, 1),
        violation_code="FIRST_MEAL_LATE",
        location_ref="BYC301",
    )
    assert first != second


def test_add_case_ids_supports_legacy_business_date() -> None:
    frame = pd.DataFrame(
        [
            {
                "Employee Key": "100",
                "Business Date": date(2026, 7, 1),
                "Location Ref": "BYC301",
                "Violation": "FIRST_MEAL_MISSING",
            }
        ]
    )
    result = add_case_ids(frame, code_column="Violation")
    assert result.loc[0, "Case ID"].startswith("MV-")
