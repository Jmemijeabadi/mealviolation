from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.engine import analyze_timecards
from compliance.models import ResultCode


BASE_DATE = date(2026, 7, 1)


def row(
    tc_id: int,
    start: str,
    end: str | None,
    *,
    shift_type: int = 0,
    out_status: int | None = 84,
    adjustments: int = 0,
) -> dict:
    return {
        "location_ref": "8",
        "location_name": "Black 8",
        "business_date": BASE_DATE,
        "timecard_id": str(tc_id),
        "employee_num": 100,
        "employee_key": "12345",
        "employee_name": "Test Employee",
        "payroll_id": "12345",
        "job_code": "Server",
        "shift_type": shift_type,
        "clock_in_local": pd.Timestamp(start),
        "clock_out_local": pd.Timestamp(end) if end else pd.NaT,
        "clock_out_status": out_status,
        "pay_rate": 20.0,
        "premium_hours": 0.0,
        "premium_pay": 0.0,
        "adjustment_count": adjustments,
    }


def codes(bundle, column: str) -> set[str]:
    if bundle.workdays.empty:
        return set()
    value = str(bundle.workdays.iloc[0][column] or "")
    return {part.strip() for part in value.split(",") if part.strip()}


def test_five_hours_exactly_does_not_require_meal() -> None:
    df = pd.DataFrame([row(1, "2026-07-01 08:00", "2026-07-01 13:00")])
    bundle = analyze_timecards(df)
    assert bundle.stats["automatic_violations"] == 0
    assert ResultCode.COMPLIANT.value in codes(bundle, "Result")


def test_five_and_half_hours_without_waiver_is_review() -> None:
    df = pd.DataFrame([row(1, "2026-07-01 08:00", "2026-07-01 13:30")])
    bundle = analyze_timecards(df)
    assert bundle.stats["automatic_violations"] == 0
    assert ResultCode.FIRST_MEAL_WAIVER_UNVERIFIED.value in codes(bundle, "Reviews")


def test_six_and_half_hours_without_meal_is_violation() -> None:
    df = pd.DataFrame([row(1, "2026-07-01 08:00", "2026-07-01 14:30")])
    bundle = analyze_timecards(df)
    assert ResultCode.FIRST_MEAL_MISSING.value in codes(bundle, "Automatic Violations")
    assert bundle.stats["premium_workdays"] == 1
    assert bundle.stats["estimated_premium"] == 20.0


def test_first_meal_at_five_worked_hours_is_timely() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 13:00", out_status=66),
            row(2, "2026-07-01 13:00", "2026-07-01 13:30", shift_type=2, out_status=84),
            row(3, "2026-07-01 13:30", "2026-07-01 16:30"),
        ]
    )
    bundle = analyze_timecards(df)
    assert bundle.stats["automatic_violations"] == 0
    assert bundle.meals.iloc[0]["Worked Hours Before"] == 5.0


def test_first_meal_after_five_worked_hours_is_late() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 13:06", out_status=66),
            row(2, "2026-07-01 13:06", "2026-07-01 13:36", shift_type=2),
            row(3, "2026-07-01 13:36", "2026-07-01 16:30"),
        ]
    )
    bundle = analyze_timecards(df)
    assert ResultCode.FIRST_MEAL_LATE.value in codes(bundle, "Automatic Violations")


def test_second_meal_waiver_is_unverified_between_ten_and_twelve() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 13:00", out_status=66),
            row(2, "2026-07-01 13:00", "2026-07-01 13:30", shift_type=2),
            row(3, "2026-07-01 13:30", "2026-07-01 19:00"),
        ]
    )
    bundle = analyze_timecards(df)
    assert ResultCode.SECOND_MEAL_WAIVER_UNVERIFIED.value in codes(bundle, "Reviews")
    assert bundle.stats["automatic_violations"] == 0


def test_second_meal_missing_after_twelve_hours() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 06:00", "2026-07-01 11:00", out_status=66),
            row(2, "2026-07-01 11:00", "2026-07-01 11:30", shift_type=2),
            row(3, "2026-07-01 11:30", "2026-07-01 19:30"),
        ]
    )
    bundle = analyze_timecards(df)
    assert ResultCode.SECOND_MEAL_MISSING.value in codes(bundle, "Automatic Violations")


def test_second_meal_after_ten_worked_hours_is_late() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 13:00", out_status=66),
            row(2, "2026-07-01 13:00", "2026-07-01 13:30", shift_type=2),
            row(3, "2026-07-01 13:30", "2026-07-01 18:36", out_status=66),
            row(4, "2026-07-01 18:36", "2026-07-01 19:06", shift_type=2),
            row(5, "2026-07-01 19:06", "2026-07-01 20:00"),
        ]
    )
    bundle = analyze_timecards(df)
    assert ResultCode.SECOND_MEAL_LATE.value in codes(bundle, "Automatic Violations")


def test_unlabeled_thirty_minute_gap_is_review_not_violation() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 12:30", out_status=84),
            row(2, "2026-07-01 13:00", "2026-07-01 16:00"),
        ]
    )
    bundle = analyze_timecards(df)
    assert bundle.stats["automatic_violations"] == 0
    assert ResultCode.MEAL_PROBABLE_TIMESTAMP_ONLY.value in codes(bundle, "Reviews")


def test_open_timecard_suppresses_automatic_conclusion() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 15:00"),
            row(2, "2026-07-01 15:00", None, out_status=None),
        ]
    )
    bundle = analyze_timecards(df)
    assert bundle.stats["automatic_violations"] == 0
    assert ResultCode.INCOMPLETE_TIMECARD.value in codes(bundle, "Reviews")
    assert ResultCode.INCONCLUSIVE.value in codes(bundle, "Reviews")


def test_active_first_meal_waiver_clears_five_to_six_hour_review() -> None:
    df = pd.DataFrame([row(1, "2026-07-01 08:00", "2026-07-01 13:30")])
    waivers = {
        "12345": [
            {
                "employee_key": "12345",
                "first_meal_waiver": True,
                "second_meal_waiver": False,
                "on_duty_meal_agreement": False,
                "effective_date": date(2026, 1, 1),
                "expiration_date": None,
            }
        ]
    }
    bundle = analyze_timecards(df, waiver_records=waivers)
    assert bundle.stats["automatic_violations"] == 0
    assert ResultCode.FIRST_MEAL_WAIVER_UNVERIFIED.value not in codes(bundle, "Reviews")


def test_probable_second_meal_after_confirmed_first_is_review() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 13:00", out_status=66),
            row(2, "2026-07-01 13:00", "2026-07-01 13:30", shift_type=2),
            row(3, "2026-07-01 13:30", "2026-07-01 18:30", out_status=84),
            row(4, "2026-07-01 19:00", "2026-07-01 21:30"),
        ]
    )
    bundle = analyze_timecards(df)
    assert ResultCode.SECOND_MEAL_MISSING.value not in codes(bundle, "Automatic Violations")
    assert ResultCode.INCONCLUSIVE.value in codes(bundle, "Reviews")


def test_second_meal_waiver_does_not_clear_without_confirmed_first_meal() -> None:
    df = pd.DataFrame([row(1, "2026-07-01 08:00", "2026-07-01 19:00")])
    waivers = {
        "12345": [
            {
                "employee_key": "12345",
                "first_meal_waiver": False,
                "second_meal_waiver": True,
                "on_duty_meal_agreement": False,
                "effective_date": date(2026, 1, 1),
                "expiration_date": None,
            }
        ]
    }
    bundle = analyze_timecards(df, waiver_records=waivers)
    assert ResultCode.SECOND_MEAL_WAIVER_UNVERIFIED.value in codes(bundle, "Reviews")


def test_strict_controls_retain_candidate_violation_for_auditor() -> None:
    frame = pd.DataFrame(
        [
            {
                "location_ref": "BYC301",
                "location_name": "Eastlake",
                "business_date": date(2026, 7, 1),
                "legal_workday_date": date(2026, 7, 1),
                "employee_key": "100",
                "employee_name": "Test Employee",
                "payroll_id": "100",
                "employee_name_resolved": True,
                "workday_config_verified": False,
                "business_date_match": True,
                "workday_start": "00:00",
                "shift_type": 0,
                "job_code": "Cook",
                "clock_in_local": pd.Timestamp("2026-07-01 08:00:00"),
                "clock_out_local": pd.Timestamp("2026-07-01 15:00:00"),
                "clock_in_status": 84,
                "clock_out_status": 84,
                "pay_rate": 20.0,
                "premium_hours": 0.0,
                "premium_pay": 0.0,
                "adjustment_count": 0,
                "timecard_id": "1",
                "source_timecard_id": "1",
                "is_primary_segment": True,
            }
        ]
    )

    bundle = analyze_timecards(frame, default_classification="UNKNOWN")

    assert bundle.violations.empty
    assert len(bundle.candidates) == 1
    assert bundle.candidates.iloc[0]["Candidate Violation"] == "FIRST_MEAL_MISSING"
    assert bool(bundle.candidates.iloc[0]["Pending Validation"]) is True
    assert bundle.stats["candidate_violations"] == 1
    assert bundle.stats["pending_candidate_violations"] == 1


def test_zero_duration_on_break_marker_is_not_a_punch_error() -> None:
    df = pd.DataFrame(
        [
            row(1, "2026-07-01 08:00", "2026-07-01 08:00", out_status=66),
            row(2, "2026-07-01 08:30", "2026-07-01 15:00", out_status=84),
        ]
    )
    bundle = analyze_timecards(df)
    assert bundle.stats["structural_break_markers"] == 1
    assert bundle.stats["punch_errors"] == 0
    assert bundle.stats["punch_error_workdays"] == 0
    assert bundle.meals.iloc[0]["Duration Minutes"] == 30.0
    assert bool(bundle.meals.iloc[0]["Confirmed by Punch"]) is True


def test_completed_timecard_without_clock_out_status_is_historical_review_not_open() -> None:
    df = pd.DataFrame(
        [row(1, "2026-07-01 08:00", "2026-07-01 14:30", out_status=None)]
    )
    bundle = analyze_timecards(df)
    assert bundle.stats["open_timecards"] == 0
    assert bundle.stats["historical_clock_out_status_missing"] == 1
    assert bundle.stats["punch_error_workdays"] == 1
    assert set(bundle.punch_errors["Punch Review Type"]) == {"CLOCK_OUT_STATUS_MISSING"}


def test_candidate_exposure_is_reported_when_controls_suppress_presumed_violation() -> None:
    frame = pd.DataFrame(
        [
            {
                **row(1, "2026-07-01 08:00", "2026-07-01 15:00"),
                "legal_workday_date": BASE_DATE,
                "employee_name_resolved": True,
                "workday_config_verified": False,
                "business_date_match": True,
                "workday_start": "00:00",
            }
        ]
    )
    bundle = analyze_timecards(frame, default_classification="UNKNOWN")
    assert bundle.stats["candidate_violations"] == 1
    assert bundle.stats["candidate_premium_workdays"] == 1
    assert bundle.stats["candidate_estimated_premium"] == 20.0
    assert bundle.stats["estimated_premium"] == 0.0
