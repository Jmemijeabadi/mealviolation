from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.engine import analyze_timecards
from compliance.models import ResultCode


def base_row(tc_id: str, start: str, end: str, *, loc: str = "A", employee: str = "123") -> dict:
    return {
        "location_ref": loc,
        "location_name": f"Location {loc}",
        "business_date": date(2026, 7, 1),
        "legal_workday_date": date(2026, 7, 1),
        "workday_config_verified": True,
        "business_date_match": True,
        "workday_start": "04:00",
        "timecard_id": tc_id,
        "source_timecard_id": tc_id,
        "employee_num": 1,
        "employee_key": employee,
        "employee_name": "Jane Doe",
        "employee_name_resolved": True,
        "payroll_id": employee,
        "job_code": "Server",
        "shift_type": 0,
        "clock_in_local": pd.Timestamp(start),
        "clock_out_local": pd.Timestamp(end),
        "clock_out_status": 84,
        "pay_rate": 20.0,
        "premium_hours": 0.0,
        "premium_pay": 0.0,
        "adjustment_count": 0,
        "is_primary_segment": True,
    }


def codes(bundle, column: str) -> set[str]:
    value = str(bundle.workdays.iloc[0].get(column) or "")
    return {piece.strip() for piece in value.split(",") if piece.strip()}


def test_exempt_employee_is_excluded() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 16:00")])
    policies = {
        "123": [{"employee_key": "123", "classification": "EXEMPT", "effective_date": date(2026, 1, 1), "verified_by": "HR", "document_reference": "HRIS-CLASS-123"}]
    }
    bundle = analyze_timecards(df, policy_records=policies, default_classification="UNKNOWN")
    assert bundle.stats["presumed_violations"] == 0
    assert ResultCode.EXCLUDED_EXEMPT.value in codes(bundle, "Reviews")


def test_unknown_classification_suppresses_presumed_violation() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 15:00")])
    bundle = analyze_timecards(df, default_classification="UNKNOWN")
    assert bundle.stats["presumed_violations"] == 0
    assert ResultCode.EMPLOYEE_CLASSIFICATION_UNVERIFIED.value in codes(bundle, "Reviews")
    assert ResultCode.INCONCLUSIVE.value in codes(bundle, "Reviews")


def test_verified_regular_rate_is_used_for_premium() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 15:00")])
    rates = {
        "123": [{"employee_key": "123", "regular_rate": 24.75, "effective_date": date(2026, 7, 1), "source": "Payroll", "verified_by": "Payroll"}]
    }
    bundle = analyze_timecards(df, regular_rate_records=rates, default_classification="NON_EXEMPT")
    assert bundle.stats["estimated_premium"] == 24.75
    assert bundle.stats["verified_premium"] == 24.75
    assert bundle.workdays.iloc[0]["Premium Rate Basis"] == "Verified regular rate"


def test_missing_regular_rate_is_labeled_proxy() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 15:00")])
    bundle = analyze_timecards(df, default_classification="NON_EXEMPT")
    assert bundle.stats["estimated_premium"] == 20.0
    assert bundle.stats["verified_premium"] == 0.0
    assert ResultCode.REGULAR_RATE_UNVERIFIED.value in codes(bundle, "Reviews")


def test_multi_location_hours_are_consolidated() -> None:
    df = pd.DataFrame(
        [
            base_row("1", "2026-07-01 08:00", "2026-07-01 12:00", loc="A"),
            base_row("2", "2026-07-01 12:30", "2026-07-01 16:30", loc="B"),
        ]
    )
    bundle = analyze_timecards(df, default_classification="NON_EXEMPT")
    assert bundle.stats["workdays"] == 1
    assert bundle.stats["multi_location_workdays"] == 1
    assert bundle.workdays.iloc[0]["Worked Hours"] == 8.0
    # 30-minute unlabeled gap prevents an automatic missing-meal conclusion.
    assert ResultCode.MEAL_PROBABLE_TIMESTAMP_ONLY.value in codes(bundle, "Reviews")


def test_different_workday_definitions_across_locations_block_conclusion() -> None:
    first = base_row("1", "2026-07-01 08:00", "2026-07-01 12:00", loc="A")
    second = base_row("2", "2026-07-01 12:01", "2026-07-01 16:01", loc="B")
    second["workday_start"] = "00:00"
    df = pd.DataFrame([first, second])
    bundle = analyze_timecards(df, default_classification="NON_EXEMPT")
    assert bundle.stats["presumed_violations"] == 0
    assert ResultCode.MULTI_LOCATION_WORKDAY_REVIEW.value in codes(bundle, "Reviews")


def test_global_data_block_suppresses_conclusion() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 15:00")])
    bundle = analyze_timecards(df, default_classification="NON_EXEMPT", global_data_blocked=True)
    assert bundle.stats["presumed_violations"] == 0
    assert ResultCode.DATA_INTEGRITY_BLOCKED.value in codes(bundle, "Reviews")


def test_unverified_exempt_record_does_not_exclude_employee() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 16:00")])
    policies = {
        "123": [{"employee_key": "123", "classification": "EXEMPT", "effective_date": date(2026, 1, 1)}]
    }
    bundle = analyze_timecards(df, policy_records=policies, default_classification="UNKNOWN")
    assert bundle.stats["presumed_violations"] == 0
    assert ResultCode.EXCLUDED_EXEMPT.value not in codes(bundle, "Reviews")
    assert ResultCode.EMPLOYEE_CLASSIFICATION_UNVERIFIED.value in codes(bundle, "Reviews")


def test_unverified_regular_rate_is_not_used_as_verified() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 15:00")])
    rates = {
        "123": [{"employee_key": "123", "regular_rate": 24.75, "effective_date": date(2026, 7, 1), "source": "Payroll"}]
    }
    bundle = analyze_timecards(df, regular_rate_records=rates, default_classification="NON_EXEMPT")
    assert bundle.stats["estimated_premium"] == 20.0
    assert bundle.stats["verified_premium"] == 0.0
    assert bundle.workdays.iloc[0]["Premium Rate Basis"] == "Base pay-rate proxy — not final"
    assert ResultCode.REGULAR_RATE_UNVERIFIED.value in codes(bundle, "Reviews")


def test_strict_policy_waiver_requires_verification_evidence() -> None:
    df = pd.DataFrame([base_row("1", "2026-07-01 08:00", "2026-07-01 13:30")])
    policies = {
        "123": [{
            "employee_key": "123",
            "classification": "NON_EXEMPT",
            "first_meal_waiver": True,
            "effective_date": date(2026, 1, 1),
            "verified_by": "HR",
        }]
    }
    bundle = analyze_timecards(df, policy_records=policies, default_classification="UNKNOWN")
    assert ResultCode.FIRST_MEAL_WAIVER_UNVERIFIED.value in codes(bundle, "Reviews")
