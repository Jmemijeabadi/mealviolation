from __future__ import annotations

from datetime import date, time

import pandas as pd

from compliance.models import WorkdayConfigRecord
from compliance.normalize import assign_legal_workdays
from compliance.validation import build_data_quality_report, build_source_coverage, reconcile_control_totals


def raw_card(start: str, end: str, *, tc: str = "1", loc: str = "A") -> dict:
    return {
        "location_ref": loc,
        "location_name": "Test",
        "location_timezone": "America/Los_Angeles",
        "business_date": date(2026, 7, 1),
        "timecard_id": tc,
        "employee_key": "123",
        "employee_name": "Jane Doe",
        "employee_name_resolved": True,
        "payroll_id": "123",
        "shift_type": 0,
        "clock_in_status": 84,
        "clock_out_status": 84,
        "clock_in_local": pd.Timestamp(start),
        "clock_out_local": pd.Timestamp(end),
        "regular_hours": 0.0,
        "overtime_hours": 0.0,
        "adjustment_count": 0,
        "adjustments": [],
        "adjustments_field_present": True,
        "pay_rate": 20.0,
        "premium_hours": 0.0,
        "premium_pay": 0.0,
        "job_code": "Server",
        "job_code_num": 1,
        "rvc_num": "1",
    }


def test_timecard_crossing_workday_boundary_is_split() -> None:
    df = pd.DataFrame([raw_card("2026-07-01 02:00", "2026-07-01 06:00")])
    configs = {"A": [WorkdayConfigRecord(location_ref="A", workday_start=time(4, 0), verified_by="Payroll", source="Workday policy")]}
    result = assign_legal_workdays(df, workday_configs=configs)
    assert len(result) == 2
    assert list(result["legal_workday_date"]) == [date(2026, 6, 30), date(2026, 7, 1)]
    assert result["workday_config_verified"].all()


def test_unconfigured_location_is_marked_unverified() -> None:
    result = assign_legal_workdays(pd.DataFrame([raw_card("2026-07-01 08:00", "2026-07-01 12:00")]))
    assert not bool(result.iloc[0]["workday_config_verified"])


def test_missing_api_coverage_is_blocking() -> None:
    coverage = build_source_coverage([], expected_locations=["A"], start_date=date(2026, 7, 1), end_date=date(2026, 7, 2))
    report = build_data_quality_report(pd.DataFrame(), coverage=coverage)
    assert report.blocking_global
    assert report.stats["coverage_missing"] == 2


def test_conflicting_duplicate_timecard_is_blocking() -> None:
    one = raw_card("2026-07-01 08:00", "2026-07-01 12:00")
    two = raw_card("2026-07-01 08:00", "2026-07-01 13:00")
    df = assign_legal_workdays(pd.DataFrame([one, two]))
    report = build_data_quality_report(df)
    assert report.blocking_global
    assert "CONFLICTING_DUPLICATE_TIMECARD" in set(report.issues["Issue Code"])


def test_control_totals_match() -> None:
    df = assign_legal_workdays(pd.DataFrame([raw_card("2026-07-01 08:00", "2026-07-01 12:00")]))
    controls = pd.DataFrame([{"location_ref": "A", "business_date": date(2026, 7, 1), "timecards": 1, "employees": 1, "worked_hours": 4.0, "adjusted_timecards": 0}])
    result = reconcile_control_totals(df, controls)
    assert result["Matches"].all()


def test_control_totals_mismatch_blocks_report() -> None:
    df = assign_legal_workdays(pd.DataFrame([raw_card("2026-07-01 08:00", "2026-07-01 12:00")]))
    controls = pd.DataFrame([{"location_ref": "A", "business_date": date(2026, 7, 1), "timecards": 2, "employees": 1, "worked_hours": 4.0, "adjusted_timecards": 0}])
    report = build_data_quality_report(df, control_totals=controls)
    assert report.blocking_global
    assert "MICROS_RECONCILIATION_MISMATCH" in set(report.issues["Issue Code"])


def test_missing_adjustments_array_is_not_error_when_request_was_verified() -> None:
    card = raw_card("2026-07-01 08:00", "2026-07-01 12:00")
    card["adjustments_field_present"] = False
    card["adjustments_request_verified"] = True
    df = assign_legal_workdays(pd.DataFrame([card]))
    report = build_data_quality_report(df)
    assert "ADJUSTMENTS_NOT_RETURNED" not in set(report.issues.get("Issue Code", []))
    assert "ADJUSTMENT_SCOPE_UNVERIFIED" not in set(report.issues.get("Issue Code", []))


def test_partial_location_scope_blocks_final_conclusions() -> None:
    df = assign_legal_workdays(pd.DataFrame([raw_card("2026-07-01 08:00", "2026-07-01 12:00")]))
    report = build_data_quality_report(
        df,
        location_scope_complete=False,
        location_scope_detail="Missing authorized location refs: B",
    )
    assert report.blocking_global
    assert "LOCATION_SCOPE_INCOMPLETE" in set(report.issues["Issue Code"])


def test_dst_transition_uses_utc_elapsed_time() -> None:
    card = raw_card("2026-03-08 01:30", "2026-03-08 03:30", loc="A")
    card["business_date"] = date(2026, 3, 8)
    card["clock_in_utc"] = pd.Timestamp("2026-03-08 09:30", tz="UTC")
    card["clock_out_utc"] = pd.Timestamp("2026-03-08 10:30", tz="UTC")
    configs = {
        "A": [
            WorkdayConfigRecord(
                location_ref="A",
                workday_start=time(0, 0),
                timezone="America/Los_Angeles",
                verified_by="Payroll",
                source="Workday policy",
            )
        ]
    }
    result = assign_legal_workdays(pd.DataFrame([card]), workday_configs=configs)
    from compliance.engine import analyze_timecards

    bundle = analyze_timecards(result, default_classification="NON_EXEMPT")
    assert bundle.workdays.iloc[0]["Worked Hours"] == 1.0
    assert result.iloc[0]["utc_duration_adjustment_minutes"] == -60.0


def test_workday_record_without_verification_is_not_trusted() -> None:
    df = pd.DataFrame([raw_card("2026-07-01 08:00", "2026-07-01 12:00")])
    configs = {"A": [WorkdayConfigRecord(location_ref="A", workday_start=time(4, 0))]}
    result = assign_legal_workdays(df, workday_configs=configs)
    assert not bool(result.iloc[0]["workday_config_verified"])


def test_source_coverage_treats_successful_empty_oracle_response_as_present() -> None:
    from compliance.validation import build_source_coverage

    coverage = build_source_coverage(
        [
            {
                "locRef": "BYC301",
                "_requestedBusDt": "2026-07-01",
                "_includeAdjustmentsRequested": True,
                "businessDates": [],
            }
        ],
        expected_locations=["BYC301"],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
    )

    assert len(coverage) == 1
    assert bool(coverage.iloc[0]["Response Present"]) is True
    assert int(coverage.iloc[0]["Timecards Returned"]) == 0
