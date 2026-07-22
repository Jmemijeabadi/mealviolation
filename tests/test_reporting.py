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


def test_employee_summary_does_not_merge_duplicate_names_with_different_keys() -> None:
    workdays = pd.DataFrame(
        [
            {
                "Employee Key": "100",
                "Employee": "Alex Smith",
                "Payroll ID": "100",
                "Business Date": date(2026, 7, 1),
                "Worked Hours": 6.5,
                "Confirmed Meals": 0,
                "Probable Meals": 0,
                "Potential Premium Workday": True,
                "Estimated Meal Premium": 20.0,
            },
            {
                "Employee Key": "200",
                "Employee": "Alex Smith",
                "Payroll ID": "200",
                "Business Date": date(2026, 7, 1),
                "Worked Hours": 4.0,
                "Confirmed Meals": 0,
                "Probable Meals": 0,
                "Potential Premium Workday": False,
                "Estimated Meal Premium": 0.0,
            },
        ]
    )
    violations = pd.DataFrame(
        [{"Employee Key": "100", "Employee": "Alex Smith", "Violation": "FIRST_MEAL_MISSING"}]
    )
    summary = build_employee_summary(
        workdays=workdays,
        violations=violations,
        reviews=pd.DataFrame(),
        punch_errors=pd.DataFrame(),
        raw_timecards=pd.DataFrame(),
        adjustments=pd.DataFrame(),
    )
    assert len(summary) == 2
    assert set(summary["Payroll ID"]) == {"100", "200"}
    assert summary.loc[summary["Payroll ID"] == "100", "Missing Meals"].iloc[0] == 1
    assert summary.loc[summary["Payroll ID"] == "200", "Missing Meals"].iloc[0] == 0


def test_violation_employee_summary_groups_reasons_and_dates() -> None:
    from compliance.reporting import build_violation_employee_summary

    violations = pd.DataFrame(
        [
            {
                "Employee Key": "123",
                "Employee": "Jane Doe",
                "Payroll ID": "123",
                "Legal Workday Date": date(2026, 7, 1),
                "Location": "Downtown",
                "Violation": "FIRST_MEAL_MISSING",
            },
            {
                "Employee Key": "123",
                "Employee": "Jane Doe",
                "Payroll ID": "123",
                "Legal Workday Date": date(2026, 7, 2),
                "Location": "Downtown",
                "Violation": "FIRST_MEAL_MISSING",
            },
            {
                "Employee Key": "123",
                "Employee": "Jane Doe",
                "Payroll ID": "123",
                "Legal Workday Date": date(2026, 7, 2),
                "Location": "Uptown",
                "Violation": "SECOND_MEAL_LATE",
            },
        ]
    )
    summary = build_violation_employee_summary(violations)
    row = summary.iloc[0]
    assert row["Violations"] == 3
    assert row["Principal Reason Code"] == "FIRST_MEAL_MISSING"
    assert row["Reason Breakdown"] == "FIRST_MEAL_MISSING:2 | SECOND_MEAL_LATE:1"
    assert row["Affected Days"] == 2
    assert row["Affected Dates"] == "2026-07-01, 2026-07-02"
    assert row["Locations"] == "Downtown, Uptown"


def test_violation_employee_summary_keeps_same_name_separate_by_key() -> None:
    from compliance.reporting import build_violation_employee_summary

    violations = pd.DataFrame(
        [
            {
                "Employee Key": "100",
                "Employee": "Alex Smith",
                "Payroll ID": "100",
                "Business Date": date(2026, 7, 1),
                "Violation": "FIRST_MEAL_MISSING",
            },
            {
                "Employee Key": "200",
                "Employee": "Alex Smith",
                "Payroll ID": "200",
                "Business Date": date(2026, 7, 1),
                "Violation": "FIRST_MEAL_LATE",
            },
        ]
    )
    summary = build_violation_employee_summary(violations)
    assert len(summary) == 2
    assert set(summary["Payroll ID"]) == {"100", "200"}


def test_review_summary_counts_unique_workdays_not_control_rows() -> None:
    from compliance.reporting import build_review_summary

    reviews = pd.DataFrame(
        [
            {
                "Employee Key": "1",
                "Legal Workday Date": date(2026, 7, 1),
                "Review": "EMPLOYEE_CLASSIFICATION_UNVERIFIED",
            },
            {
                "Employee Key": "1",
                "Legal Workday Date": date(2026, 7, 1),
                "Review": "WORKDAY_CONFIGURATION_UNVERIFIED",
            },
            {
                "Employee Key": "2",
                "Legal Workday Date": date(2026, 7, 1),
                "Review": "EMPLOYEE_CLASSIFICATION_UNVERIFIED",
            },
        ]
    )
    summary = build_review_summary(reviews)
    admin = summary[summary["Category"] == "Configuración administrativa"].iloc[0]
    assert admin["Workdays"] == 2
    assert admin["Review Records"] == 3


def test_location_coverage_summary_keeps_selected_location_with_zero_timecards() -> None:
    from compliance.reporting import build_location_coverage_summary

    coverage = pd.DataFrame(
        [
            {
                "Location Ref": "BYC301",
                "Business Date": date(2026, 7, 1),
                "Response Present": True,
                "Timecards Returned": 4,
            },
            {
                "Location Ref": "BYC307",
                "Business Date": date(2026, 7, 1),
                "Response Present": True,
                "Timecards Returned": 0,
            },
        ]
    )
    raw = pd.DataFrame(
        [
            {
                "location_ref": "BYC301",
                "location_name": "Eastlake",
                "timecard_id": "1",
            }
        ]
    )
    summary = build_location_coverage_summary(
        coverage,
        raw,
        selected_locations=[
            {"ref": "BYC301", "label": "Eastlake"},
            {"ref": "BYC307", "label": "Mission Viejo"},
        ],
    )
    zero = summary[summary["Location Ref"] == "BYC307"].iloc[0]
    assert zero["Timecards"] == 0
    assert zero["Status"] == "Valid responses — zero timecards"


def test_probable_and_second_meal_queues_are_separate() -> None:
    from compliance.reporting import build_probable_meal_queue, build_second_meal_review_queue

    workdays = pd.DataFrame(
        [
            {
                "Location": "Eastlake",
                "Employee Key": "1",
                "Employee": "Jane",
                "Payroll ID": "1",
                "Legal Workday Date": date(2026, 7, 1),
                "Worked Hours": 10.5,
                "Confirmed Meals": 1,
                "Probable Meals": 1,
            }
        ]
    )
    meals = pd.DataFrame(
        [
            {
                "Employee Key": "1",
                "Legal Workday Date": date(2026, 7, 1),
                "Duration Minutes": 35.0,
                "Confirmed by Punch": False,
                "Paid": False,
            }
        ]
    )
    reviews = pd.DataFrame(
        [
            {
                "Employee Key": "1",
                "Legal Workday Date": date(2026, 7, 1),
                "Review": "SECOND_MEAL_WAIVER_UNVERIFIED",
            }
        ]
    )
    probable = build_probable_meal_queue(workdays, meals)
    second = build_second_meal_review_queue(workdays, reviews, pd.DataFrame())
    assert len(probable) == 1
    assert probable.iloc[0]["Longest Probable Gap"] == 35.0
    assert len(second) == 1
    assert "SECOND_MEAL_WAIVER_UNVERIFIED" in second.iloc[0]["Second Meal Status"]
