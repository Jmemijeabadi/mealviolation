from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd

from compliance.normalize import CLOCK_IN_STATUS, CLOCK_OUT_STATUS, SHIFT_TYPE


@dataclass
class ValidationReport:
    issues: pd.DataFrame
    reconciliation: pd.DataFrame
    coverage: pd.DataFrame
    blocking_global: bool
    stats: dict[str, Any]


ISSUE_COLUMNS = [
    "Severity",
    "Blocking",
    "Issue Code",
    "Location Ref",
    "Business Date",
    "Employee",
    "Payroll ID",
    "Timecard ID",
    "Detail",
    "Recommended Action",
]


def _issue(
    *,
    severity: str,
    blocking: bool,
    code: str,
    detail: str,
    action: str,
    row: pd.Series | dict[str, Any] | None = None,
) -> dict[str, Any]:
    if row is None:
        row = {}
    getter = row.get if hasattr(row, "get") else lambda _key, default="": default
    return {
        "Severity": severity,
        "Blocking": bool(blocking),
        "Issue Code": code,
        "Location Ref": getter("location_ref", ""),
        "Business Date": getter("business_date", getter("legal_workday_date", "")),
        "Employee": getter("employee_name", ""),
        "Payroll ID": getter("payroll_id", ""),
        "Timecard ID": getter("source_timecard_id", getter("timecard_id", "")),
        "Detail": detail,
        "Recommended Action": action,
    }


def build_source_coverage(
    payloads: Iterable[dict[str, Any]],
    *,
    expected_locations: Iterable[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, date]] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        loc_ref = str(payload.get("locRef") or "")
        cur_utc = payload.get("curUTC")
        business_dates = payload.get("businessDates", []) or []
        if not business_dates and payload.get("busDt"):
            business_dates = [{"busDt": payload.get("busDt"), "timeCardDetails": payload.get("timeCardDetails", [])}]
        for item in business_dates:
            if not isinstance(item, dict):
                continue
            parsed = pd.to_datetime(item.get("busDt"), errors="coerce")
            if pd.isna(parsed):
                continue
            bus_date = parsed.date()
            cards = item.get("timeCardDetails", []) or []
            rows.append(
                {
                    "Location Ref": loc_ref,
                    "Business Date": bus_date,
                    "Response Present": True,
                    "Timecards Returned": len(cards) if isinstance(cards, list) else 0,
                    "Oracle Cursor UTC": cur_utc,
                }
            )
            seen.add((loc_ref, bus_date))

    if expected_locations and start_date and end_date:
        current = start_date
        while current <= end_date:
            for loc_ref in expected_locations:
                key = (str(loc_ref), current)
                if key not in seen:
                    rows.append(
                        {
                            "Location Ref": str(loc_ref),
                            "Business Date": current,
                            "Response Present": False,
                            "Timecards Returned": 0,
                            "Oracle Cursor UTC": "",
                        }
                    )
            current += timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=["Location Ref", "Business Date", "Response Present", "Timecards Returned", "Oracle Cursor UTC"])
    return pd.DataFrame(rows).sort_values(["Location Ref", "Business Date"]).reset_index(drop=True)


def reconcile_control_totals(timecards: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Location Ref",
        "Business Date",
        "Metric",
        "MICROS Control",
        "API Calculated",
        "Difference",
        "Matches",
    ]
    if controls is None or controls.empty:
        return pd.DataFrame(columns=columns)

    source = timecards.copy()
    if "is_primary_segment" in source.columns:
        primary = source[source["is_primary_segment"].fillna(True)].copy()
    else:
        primary = source
    if {"calculation_clock_in", "calculation_clock_out"}.issubset(source.columns):
        calculation_start = pd.to_datetime(source["calculation_clock_in"], errors="coerce", utc=True)
        calculation_end = pd.to_datetime(source["calculation_clock_out"], errors="coerce", utc=True)
        use_calculation = calculation_start.notna() & calculation_end.notna()
        local_hours = (
            pd.to_datetime(source["clock_out_local"], errors="coerce")
            - pd.to_datetime(source["clock_in_local"], errors="coerce")
        ).dt.total_seconds().div(3600)
        utc_hours = (calculation_end - calculation_start).dt.total_seconds().div(3600)
        source["worked_clock_hours"] = utc_hours.where(use_calculation, local_hours)
    else:
        source["worked_clock_hours"] = (
            pd.to_datetime(source["clock_out_local"], errors="coerce")
            - pd.to_datetime(source["clock_in_local"], errors="coerce")
        ).dt.total_seconds().div(3600)
    source["worked_clock_hours"] = source["worked_clock_hours"].clip(lower=0).fillna(0)

    grouped = source.groupby(["location_ref", "business_date"], dropna=False).agg(
        timecards=("source_timecard_id" if "source_timecard_id" in source.columns else "timecard_id", "nunique"),
        employees=("employee_key", "nunique"),
        worked_hours=("worked_clock_hours", "sum"),
    )
    if "adjustment_count" in primary.columns:
        adjusted = primary.assign(_adjusted=pd.to_numeric(primary["adjustment_count"], errors="coerce").fillna(0) > 0)
        adjusted = adjusted.groupby(["location_ref", "business_date"])["_adjusted"].sum()
        grouped["adjusted_timecards"] = adjusted
    else:
        grouped["adjusted_timecards"] = 0

    rows: list[dict[str, Any]] = []
    tolerance = {"timecards": 0.0, "employees": 0.0, "worked_hours": 0.05, "adjusted_timecards": 0.0}
    for _, control in controls.iterrows():
        key = (str(control.get("location_ref") or ""), control.get("business_date"))
        actual = grouped.loc[key] if key in grouped.index else pd.Series(dtype=float)
        for metric in ("timecards", "employees", "worked_hours", "adjusted_timecards"):
            if metric not in controls.columns or pd.isna(control.get(metric)):
                continue
            expected = float(control.get(metric))
            calculated = float(actual.get(metric, 0.0))
            difference = calculated - expected
            rows.append(
                {
                    "Location Ref": key[0],
                    "Business Date": key[1],
                    "Metric": metric,
                    "MICROS Control": round(expected, 2),
                    "API Calculated": round(calculated, 2),
                    "Difference": round(difference, 2),
                    "Matches": abs(difference) <= tolerance[metric],
                }
            )
    return pd.DataFrame(rows, columns=columns)


def build_data_quality_report(
    timecards: pd.DataFrame,
    *,
    coverage: pd.DataFrame | None = None,
    control_totals: pd.DataFrame | None = None,
    location_scope_complete: bool | None = True,
    location_scope_detail: str = "",
) -> ValidationReport:
    issues: list[dict[str, Any]] = []
    coverage = coverage if coverage is not None else pd.DataFrame()

    if location_scope_complete is False:
        issues.append(
            _issue(
                severity="Critical",
                blocking=True,
                code="LOCATION_SCOPE_INCOMPLETE",
                detail=(
                    "The selected locations do not include the full authorized enterprise scope. "
                    + (location_scope_detail or "Cross-location work may be missing.")
                ),
                action=(
                    "Select all authorized locations for a final audit. A partial-location run "
                    "cannot rule out additional hours worked elsewhere."
                ),
            )
        )
    elif location_scope_complete is None:
        issues.append(
            _issue(
                severity="High",
                blocking=False,
                code="LOCATION_SCOPE_UNVERIFIED",
                detail="The source does not prove that all authorized locations were included.",
                action="Validate the location scope before relying on cross-location totals.",
            )
        )

    if timecards.empty:
        issues.append(
            _issue(
                severity="Critical",
                blocking=True,
                code="NO_TIMECARDS",
                detail="Oracle did not return any timecards for the selected scope.",
                action="Confirm the location/date scope and compare against MICROS before relying on the result.",
            )
        )
    else:
        unresolved = timecards[~timecards.get("employee_name_resolved", pd.Series(False, index=timecards.index)).fillna(False)]
        for _, row in unresolved.drop_duplicates(["employee_key"]).iterrows():
            issues.append(
                _issue(
                    severity="High",
                    blocking=False,
                    code="EMPLOYEE_NAME_UNRESOLVED",
                    detail="The timecard could not be linked to an Employee Dimensions record.",
                    action="Verify empNum/payroll ID and refresh Employee Dimensions.",
                    row=row,
                )
            )

        missing_payroll = timecards[timecards.get("payroll_id", pd.Series("", index=timecards.index)).astype(str).str.strip().eq("")]
        for _, row in missing_payroll.drop_duplicates(["employee_key"]).iterrows():
            issues.append(
                _issue(
                    severity="High",
                    blocking=False,
                    code="PAYROLL_ID_MISSING",
                    detail="The employee has no stable payroll identifier; cross-location consolidation may be unreliable.",
                    action="Populate Payroll ID in Oracle or the employee policy file.",
                    row=row,
                )
            )

        if "workday_config_verified" in timecards.columns:
            unverified = timecards[~timecards["workday_config_verified"].fillna(False)]
            for _, row in unverified.drop_duplicates(["location_ref"]).iterrows():
                issues.append(
                    _issue(
                        severity="Critical",
                        blocking=False,
                        code="WORKDAY_CONFIG_UNVERIFIED",
                        detail="No verified fixed 24-hour workday configuration was supplied for this location.",
                        action="Upload the workday configuration CSV approved by payroll/HR.",
                        row=row,
                    )
                )

        if "business_date_match" in timecards.columns:
            mismatch = timecards[~timecards["business_date_match"].fillna(False)]
            for _, row in mismatch.drop_duplicates(["location_ref", "business_date", "legal_workday_date"]).iterrows():
                issues.append(
                    _issue(
                        severity="High",
                        blocking=False,
                        code="BUSINESS_DATE_MISMATCH",
                        detail=f"Oracle business date {row.get('business_date')} does not match calculated legal workday {row.get('legal_workday_date')}.",
                        action="Confirm the Oracle business-day cutoff and the legally designated workday start.",
                        row=row,
                    )
                )

        unknown_shift = timecards[~timecards["shift_type"].isin(SHIFT_TYPE)]
        for _, row in unknown_shift.drop_duplicates(["shift_type"]).iterrows():
            issues.append(
                _issue(
                    severity="Critical",
                    blocking=False,
                    code="UNKNOWN_SHIFT_TYPE",
                    detail=f"Unknown Oracle shift type: {row.get('shift_type')}",
                    action="Confirm the code with Oracle documentation before classifying meals.",
                    row=row,
                )
            )
        unknown_in = timecards[~timecards["clock_in_status"].isin(CLOCK_IN_STATUS)]
        for _, row in unknown_in.drop_duplicates(["clock_in_status"]).iterrows():
            issues.append(
                _issue(
                    severity="High",
                    blocking=False,
                    code="UNKNOWN_CLOCK_IN_STATUS",
                    detail=f"Unknown Oracle Clock In status: {row.get('clock_in_status')}",
                    action="Update the status map after confirming the Oracle definition.",
                    row=row,
                )
            )
        completed = timecards[timecards["clock_out_status"].notna()]
        unknown_out = completed[~completed["clock_out_status"].isin(CLOCK_OUT_STATUS)]
        for _, row in unknown_out.drop_duplicates(["clock_out_status"]).iterrows():
            issues.append(
                _issue(
                    severity="High",
                    blocking=False,
                    code="UNKNOWN_CLOCK_OUT_STATUS",
                    detail=f"Unknown Oracle Clock Out status: {row.get('clock_out_status')}",
                    action="Update the status map after confirming the Oracle definition.",
                    row=row,
                )
            )

        # Oracle omits the adjustments array when a timecard has no adjustments,
        # even when includeAdjustments=true. Therefore field absence is not an
        # error. We validate the request metadata injected by the API client.
        if "adjustments_request_verified" in timecards.columns:
            unverified_adjustment_request = ~timecards["adjustments_request_verified"].fillna(False)
            if unverified_adjustment_request.any():
                issues.append(
                    _issue(
                        severity="High",
                        blocking=False,
                        code="ADJUSTMENT_SCOPE_UNVERIFIED",
                        detail=(
                            "The source payload does not prove that includeAdjustments=true "
                            "was used for every timecard request."
                        ),
                        action=(
                            "Run through the Oracle API mode or preserve request metadata "
                            "before treating the adjustment audit as complete."
                        ),
                    )
                )

        if "utc_duration_adjustment_minutes" in timecards.columns:
            adjusted_duration = timecards[
                pd.to_numeric(timecards["utc_duration_adjustment_minutes"], errors="coerce")
                .fillna(0.0)
                .abs()
                > 0.01
            ]
            for _, row in adjusted_duration.drop_duplicates(
                ["location_ref", "business_date"]
            ).iterrows():
                issues.append(
                    _issue(
                        severity="Info",
                        blocking=False,
                        code="UTC_DURATION_ADJUSTED",
                        detail=(
                            "UTC timestamps were used instead of naive local duration for one or more "
                            "timecards, normally because of a DST or timezone transition."
                        ),
                        action="No correction is required; retain the UTC fields in the audit evidence.",
                        row=row,
                    )
                )

        id_column = "source_timecard_id" if "source_timecard_id" in timecards.columns else "timecard_id"
        primary = timecards[timecards.get("is_primary_segment", pd.Series(True, index=timecards.index)).fillna(True)]
        fingerprints = primary.assign(
            _fingerprint=primary[["clock_in_local", "clock_out_local", "employee_key", "shift_type"]]
            .astype(str)
            .agg("|".join, axis=1)
        ).groupby(["location_ref", id_column])["_fingerprint"].nunique()
        conflicts = fingerprints[fingerprints > 1]
        for (loc_ref, timecard_id), count in conflicts.items():
            issues.append(
                _issue(
                    severity="Critical",
                    blocking=True,
                    code="CONFLICTING_DUPLICATE_TIMECARD",
                    detail=f"Timecard {timecard_id} has {count} conflicting current states in the same response.",
                    action="Stop the run and reconcile the API payload against MICROS.",
                    row={"location_ref": loc_ref, "timecard_id": timecard_id},
                )
            )

        if {"employee_key", "legal_workday_date", "location_ref"}.issubset(timecards.columns):
            multi = timecards.groupby(["employee_key", "legal_workday_date"])["location_ref"].nunique()
            for (employee_key, workday_date), count in multi[multi > 1].items():
                sample = timecards[(timecards["employee_key"] == employee_key) & (timecards["legal_workday_date"] == workday_date)].iloc[0]
                issues.append(
                    _issue(
                        severity="Info",
                        blocking=False,
                        code="MULTI_LOCATION_WORKDAY",
                        detail=f"The employee worked across {count} locations; the engine consolidated all segments into one legal workday.",
                        action="Confirm all selected locations share the approved workday definition.",
                        row=sample,
                    )
                )

    if not coverage.empty:
        missing_coverage = coverage[~coverage["Response Present"].fillna(False)]
        for _, row in missing_coverage.iterrows():
            issues.append(
                _issue(
                    severity="Critical",
                    blocking=True,
                    code="SOURCE_COVERAGE_INCOMPLETE",
                    detail="No Oracle response was captured for this requested location/date.",
                    action="Re-run the API request before using period totals.",
                    row={"location_ref": row.get("Location Ref"), "business_date": row.get("Business Date")},
                )
            )

    reconciliation = reconcile_control_totals(timecards, control_totals if control_totals is not None else pd.DataFrame())
    if not reconciliation.empty:
        for _, row in reconciliation[~reconciliation["Matches"].fillna(False)].iterrows():
            issues.append(
                _issue(
                    severity="Critical",
                    blocking=True,
                    code="MICROS_RECONCILIATION_MISMATCH",
                    detail=(
                        f"{row['Metric']} differs from MICROS control totals: "
                        f"control={row['MICROS Control']}, API={row['API Calculated']}."
                    ),
                    action="Do not finalize the audit until the discrepancy is explained.",
                    row={"location_ref": row.get("Location Ref"), "business_date": row.get("Business Date")},
                )
            )

    issue_df = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    blocking_global = bool((issue_df["Blocking"] == True).any()) if not issue_df.empty else False  # noqa: E712
    stats = {
        "issues": int(len(issue_df)),
        "critical": int((issue_df["Severity"] == "Critical").sum()) if not issue_df.empty else 0,
        "blocking": int(issue_df["Blocking"].sum()) if not issue_df.empty else 0,
        "coverage_missing": int((coverage.get("Response Present", pd.Series(dtype=bool)) == False).sum()) if not coverage.empty else 0,  # noqa: E712
        "reconciliation_mismatches": int((reconciliation.get("Matches", pd.Series(dtype=bool)) == False).sum()) if not reconciliation.empty else 0,  # noqa: E712
    }
    return ValidationReport(
        issues=issue_df,
        reconciliation=reconciliation,
        coverage=coverage,
        blocking_global=blocking_global,
        stats=stats,
    )
