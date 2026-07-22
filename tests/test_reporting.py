from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.reporting import build_employee_summary


def test_employee_summary_counts_meals_and_adjustments() -> None:
    workdays = pd.DataFrame(
        [
            {
                "Employee": "Jane Doe",
                "Payroll ID": "123",
                "Business Date": date(2026, 7, 1),
                "Worked Hours": 11.0,
                "Confirmed Meals": 1,
                "Probable Meals": 0,
                "Potential Premium Workday": True,
                "Estimated Meal Premium": 20.0,
            },
            {
                "Employee": "Jane Doe",
                "Payroll ID": "123",
                "Business Date": date(2026, 7, 2),
                "Worked Hours": 6.5,
                "Confirmed Meals": 1,
                "Probable Meals": 0,
                "Potential Premium Workday": False,
                "Estimated Meal Premium": 0.0,
            },
        ]
    )
    violations = pd.DataFrame(
        [
            {"Employee": "Jane Doe", "Violation": "SECOND_MEAL_MISSING"},
            {"Employee": "Jane Doe", "Violation": "FIRST_MEAL_LATE"},
        ]
    )
    reviews = pd.DataFrame([{"Employee": "Jane Doe", "Review": "ADJUSTED_TIMECARD_REVIEW"}])
    punch_errors = pd.DataFrame()
    raw_timecards = pd.DataFrame(
        [
            {"employee_name": "Jane Doe", "timecard_id": "1", "adjustment_count": 1},
            {"employee_name": "Jane Doe", "timecard_id": "2", "adjustment_count": 0},
        ]
    )
    adjustments = pd.DataFrame(
        [
            {"Employee": "Jane Doe", "Manager": "Manager One"},
            {"Employee": "Jane Doe", "Manager": "Manager One"},
        ]
    )
    summary = build_employee_summary(
        workdays=workdays,
        violations=violations,
        reviews=reviews,
        punch_errors=punch_errors,
        raw_timecards=raw_timecards,
        adjustments=adjustments,
    )
    row = summary.iloc[0]
    assert row["Meals Expected by Hours"] == 3
    assert row["Confirmed Meals"] == 2
    assert row["Missing Meals"] == 1
    assert row["Late Meals"] == 1
    assert row["Adjusted Timecards"] == 1
    assert row["Adjustment Records"] == 2
    assert row["Managers Involved"] == 1
    assert row["Status"] == "Atención inmediata"
