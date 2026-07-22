from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

import pandas as pd

from compliance.models import (
    CaliforniaMealRules,
    EmployeePolicyRecord,
    MealCandidate,
    RegularRateRecord,
    ResultCode,
    WorkdayAnalysis,
)


@dataclass
class AnalysisBundle:
    workdays: pd.DataFrame
    violations: pd.DataFrame
    reviews: pd.DataFrame
    punch_errors: pd.DataFrame
    meals: pd.DataFrame
    raw_timecards: pd.DataFrame
    stats: dict[str, Any]
    data_quality: pd.DataFrame = field(default_factory=pd.DataFrame)
    reconciliation: pd.DataFrame = field(default_factory=pd.DataFrame)
    coverage: pd.DataFrame = field(default_factory=pd.DataFrame)
    change_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    candidates: pd.DataFrame = field(default_factory=pd.DataFrame)


def _duration_hours(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return (end - start).total_seconds() / 3600.0


def _overlap_minutes(
    start_a: pd.Timestamp,
    end_a: pd.Timestamp,
    start_b: pd.Timestamp,
    end_b: pd.Timestamp,
) -> float:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    return max(0.0, (end - start).total_seconds() / 60.0)


def _append_unique(values: list[ResultCode], code: ResultCode) -> None:
    if code not in values:
        values.append(code)


def _active_policy(
    policy_records: dict[str, list[dict[str, Any]]], employee_key: str, workday_date: date
) -> EmployeePolicyRecord | None:
    allowed = EmployeePolicyRecord.__dataclass_fields__.keys()
    active = [
        EmployeePolicyRecord(**{key: value for key, value in raw.items() if key in allowed})
        for raw in policy_records.get(employee_key, [])
    ]
    active = [record for record in active if record.active_on(workday_date)]
    if not active:
        return None
    return max(active, key=lambda record: record.effective_date or date.min)


def _active_regular_rate(
    records: dict[str, list[dict[str, Any]]], employee_key: str, workday_date: date
) -> RegularRateRecord | None:
    active = [RegularRateRecord(**raw) for raw in records.get(employee_key, [])]
    active = [record for record in active if record.active_on(workday_date)]
    if not active:
        return None
    return max(active, key=lambda record: record.effective_date or date.min)


def _calculation_columns(rows: pd.DataFrame) -> tuple[str, str]:
    if {"calculation_clock_in", "calculation_clock_out"}.issubset(rows.columns):
        completed = rows[rows["clock_out_local"].notna()] if "clock_out_local" in rows.columns else rows
        if not completed.empty and completed[["calculation_clock_in", "calculation_clock_out"]].notna().all().all():
            return "calculation_clock_in", "calculation_clock_out"
    return "clock_in_local", "clock_out_local"


def _row_calculation_moment(row: pd.Series | dict[str, Any], calc_field: str, local_field: str) -> pd.Timestamp:
    value = row.get(calc_field) if hasattr(row, "get") else None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.notna(parsed):
        return parsed
    return pd.to_datetime(row.get(local_field), errors="coerce")


def _merge_intervals(rows: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start_column, end_column = _calculation_columns(rows)
    for _, row in rows.sort_values([start_column, end_column]).iterrows():
        start = row[start_column]
        end = row[end_column]
        if pd.isna(start) or pd.isna(end) or end <= start:
            continue
        if not intervals or start >= intervals[-1][1]:
            intervals.append((start, end))
        else:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
    return intervals


def _union_worked_hours(working: pd.DataFrame) -> float:
    return sum(_duration_hours(start, end) for start, end in _merge_intervals(working))


def _worked_hours_before(
    working: pd.DataFrame,
    local_moment: pd.Timestamp,
    calculation_moment: pd.Timestamp | None = None,
) -> float:
    start_column, _ = _calculation_columns(working)
    if start_column == "calculation_clock_in" and calculation_moment is not None and pd.notna(calculation_moment):
        moment = calculation_moment
    else:
        moment = local_moment
    total = 0.0
    for start, end in _merge_intervals(working):
        if start >= moment:
            continue
        total += _duration_hours(start, min(end, moment))
    return max(0.0, total)


def _meal_candidates(
    group: pd.DataFrame, rules: CaliforniaMealRules
) -> tuple[list[MealCandidate], list[MealCandidate]]:
    """Return (all meal candidates, explicit short unpaid breaks)."""
    tolerance_minutes = rules.timestamp_tolerance_seconds / 60.0
    working = group[(group["shift_type"] == 0) & group["clock_out_local"].notna()].copy()
    working = working.sort_values(["clock_in_local", "clock_out_local", "timecard_id"])
    explicit_breaks = group[
        group["shift_type"].isin([1, 2])
        & group["clock_in_local"].notna()
        & group["clock_out_local"].notna()
    ].copy()

    candidates: list[MealCandidate] = []
    short_unpaid: list[MealCandidate] = []
    used_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    for _, row in explicit_breaks.sort_values("clock_in_local").iterrows():
        start = row["clock_in_local"]
        end = row["clock_out_local"]
        calculation_start = _row_calculation_moment(row, "calculation_clock_in", "clock_in_local")
        calculation_end = _row_calculation_moment(row, "calculation_clock_out", "clock_out_local")
        duration_start = calculation_start if pd.notna(calculation_start) and pd.notna(calculation_end) else start
        duration_end = calculation_end if pd.notna(calculation_start) and pd.notna(calculation_end) else end
        minutes = max(0.0, (duration_end - duration_start).total_seconds() / 60.0)
        paid = int(row["shift_type"]) == 1
        candidate = MealCandidate(
            start=start.to_pydatetime(),
            end=end.to_pydatetime(),
            duration_minutes=minutes,
            worked_hours_before=_worked_hours_before(working, start, calculation_start),
            evidence="Oracle paid-break shift" if paid else "Oracle unpaid-break shift",
            confirmed_by_punch=not paid and minutes + tolerance_minutes >= rules.minimum_meal_minutes,
            paid=paid,
            source_timecard_id=str(row.get("source_timecard_id") or row.get("timecard_id") or ""),
            locations=str(row.get("location_name") or ""),
        )
        if not paid and minutes + tolerance_minutes < rules.minimum_meal_minutes:
            short_unpaid.append(candidate)
        elif minutes > tolerance_minutes:
            candidates.append(candidate)
        used_windows.append((start, end))

    records = list(working.to_dict("records"))
    for index in range(len(records) - 1):
        current = records[index]
        following = records[index + 1]
        start = current["clock_out_local"]
        end = following["clock_in_local"]
        if pd.isna(start) or pd.isna(end) or end <= start:
            continue
        calculation_start = _row_calculation_moment(current, "calculation_clock_out", "clock_out_local")
        calculation_end = _row_calculation_moment(following, "calculation_clock_in", "clock_in_local")
        duration_start = calculation_start if pd.notna(calculation_start) and pd.notna(calculation_end) else start
        duration_end = calculation_end if pd.notna(calculation_start) and pd.notna(calculation_end) else end
        minutes = (duration_end - duration_start).total_seconds() / 60.0
        if minutes <= tolerance_minutes:
            continue
        if any(
            _overlap_minutes(start, end, window_start, window_end) >= min(minutes, 1.0)
            for window_start, window_end in used_windows
        ):
            continue

        status = current.get("clock_out_status")
        paid = status == 80
        confirmed = status == 66 and minutes + tolerance_minutes >= rules.minimum_meal_minutes
        if status == 66:
            evidence = "Clock-out status On Break + timestamps"
        elif status == 80:
            evidence = "Clock-out status Paid Break + timestamps"
        else:
            evidence = "Timestamp gap without break status"

        candidate = MealCandidate(
            start=start.to_pydatetime(),
            end=end.to_pydatetime(),
            duration_minutes=minutes,
            worked_hours_before=_worked_hours_before(working, start, calculation_start),
            evidence=evidence,
            confirmed_by_punch=confirmed,
            paid=paid,
            source_timecard_id=str(current.get("source_timecard_id") or current.get("timecard_id") or ""),
            locations=" → ".join(
                part
                for part in (str(current.get("location_name") or ""), str(following.get("location_name") or ""))
                if part
            ),
        )
        if status == 66 and minutes + tolerance_minutes < rules.minimum_meal_minutes:
            short_unpaid.append(candidate)
        else:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (item.start, not item.confirmed_by_punch, item.paid))
    deduped: list[MealCandidate] = []
    for candidate in candidates:
        duplicate_index = next(
            (
                i
                for i, existing in enumerate(deduped)
                if abs((candidate.start - existing.start).total_seconds()) <= rules.timestamp_tolerance_seconds
                and abs(candidate.duration_minutes - existing.duration_minutes) <= 1.0
            ),
            None,
        )
        if duplicate_index is None:
            deduped.append(candidate)
        elif candidate.confirmed_by_punch and not deduped[duplicate_index].confirmed_by_punch:
            deduped[duplicate_index] = candidate
    return deduped, short_unpaid


def _validate_punches(group: pd.DataFrame, rules: CaliforniaMealRules) -> tuple[list[str], bool]:
    errors: list[str] = []
    material = False
    tolerance_seconds = rules.timestamp_tolerance_seconds

    open_rows = group[group["clock_out_local"].isna()]
    if not open_rows.empty:
        errors.append(f"{len(open_rows)} open timecard(s) without Clock Out")
        material = True

    completed = group[group["clock_out_local"].notna()].copy()
    start_column, end_column = _calculation_columns(completed)
    negative = completed[completed[end_column] < completed[start_column]]
    if not negative.empty:
        errors.append(f"{len(negative)} timecard(s) with Clock Out before Clock In")
        material = True

    zero = completed[
        (completed[end_column] - completed[start_column]).dt.total_seconds().abs()
        <= tolerance_seconds
    ]
    if not zero.empty:
        errors.append(f"{len(zero)} zero-duration timecard(s)")

    working = completed[completed["shift_type"] == 0].sort_values(start_column)
    previous_end: pd.Timestamp | None = None
    for _, row in working.iterrows():
        start = row[start_column]
        end = row[end_column]
        if previous_end is not None and start < previous_end - pd.Timedelta(seconds=tolerance_seconds):
            errors.append("Overlapping working timecards across one or more locations")
            material = True
            break
        previous_end = max(previous_end, end) if previous_end is not None else end

    auto_clockouts = completed[completed["clock_out_status"].isin([77, 85])]
    if not auto_clockouts.empty:
        errors.append(f"{len(auto_clockouts)} manager/automatic Clock Out(s) require review")

    return errors, material


def analyze_workday_group(
    group: pd.DataFrame,
    rules: CaliforniaMealRules,
    policy_records: dict[str, list[dict[str, Any]]],
    regular_rate_records: dict[str, list[dict[str, Any]]],
    *,
    default_classification: str = "NON_EXEMPT",
    global_data_blocked: bool = False,
    allow_unverified_legacy_waivers: bool = False,
) -> WorkdayAnalysis:
    group = group.sort_values(["clock_in_local", "clock_out_local", "location_ref", "timecard_id"], na_position="last")
    first = group.iloc[0]
    workday_date = first.get("legal_workday_date", first.get("business_date"))
    if isinstance(workday_date, pd.Timestamp):
        workday_date = workday_date.date()
    if not isinstance(workday_date, date):
        raise ValueError("A valid legal workday date is required for each timecard.")

    working = group[(group["shift_type"] == 0) & group["clock_out_local"].notna()].copy()
    worked_hours = _union_worked_hours(working)
    pay_rates = pd.to_numeric(group["pay_rate"], errors="coerce").dropna()
    base_pay_rate = float(pay_rates.max()) if not pay_rates.empty else None
    roles = ", ".join(sorted(set(group["job_code"].dropna().astype(str))))
    first_clock_in = group["clock_in_local"].min()
    last_clock_out = group["clock_out_local"].max()
    location_refs = sorted(set(group["location_ref"].dropna().astype(str)))
    location_names = sorted(set(group["location_name"].dropna().astype(str)))
    business_dates = sorted({str(value) for value in group["business_date"].dropna().tolist()})

    policy = _active_policy(policy_records, str(first["employee_key"]), workday_date)
    classification = (
        policy.normalized_classification
        if policy and policy.classification_verified
        else default_classification.strip().upper()
    )
    if classification not in {"NON_EXEMPT", "EXEMPT"}:
        classification = "UNKNOWN"
    policy_source = ""
    if policy:
        policy_source = policy.document_reference or policy.verified_by or "Employee policy CSV"

    verified_rate = _active_regular_rate(regular_rate_records, str(first["employee_key"]), workday_date)
    if verified_rate and verified_rate.is_verified:
        premium_rate = float(verified_rate.regular_rate)
        premium_rate_basis = "Verified regular rate"
    else:
        premium_rate = base_pay_rate
        premium_rate_basis = "Base pay-rate proxy — not final"

    analysis = WorkdayAnalysis(
        location_ref=", ".join(location_refs),
        location_name=", ".join(location_names),
        legal_workday_date=workday_date,
        business_dates=", ".join(business_dates),
        employee_key=str(first["employee_key"]),
        employee_name=str(first["employee_name"]),
        payroll_id=str(first.get("payroll_id") or ""),
        employee_classification=classification,
        policy_source=policy_source,
        roles=roles,
        first_clock_in=None if pd.isna(first_clock_in) else first_clock_in.to_pydatetime(),
        last_clock_out=None if pd.isna(last_clock_out) else last_clock_out.to_pydatetime(),
        worked_hours=worked_hours,
        base_pay_rate=base_pay_rate,
        premium_rate=premium_rate,
        premium_rate_basis=premium_rate_basis,
        oracle_premium_hours=float(pd.to_numeric(group["premium_hours"], errors="coerce").fillna(0).sum()),
        oracle_premium_pay=float(pd.to_numeric(group["premium_pay"], errors="coerce").fillna(0).sum()),
        adjustment_count=int(pd.to_numeric(group["adjustment_count"], errors="coerce").fillna(0).sum()),
        source_timecard_ids=sorted(set(str(value) for value in group.get("source_timecard_id", group["timecard_id"]).dropna().astype(str))),
    )

    utc_adjustments = pd.to_numeric(
        group.get("utc_duration_adjustment_minutes", pd.Series(0.0, index=group.index)),
        errors="coerce",
    ).fillna(0.0)
    if utc_adjustments.abs().sum() > 0.01:
        analysis.details.append(
            f"UTC timestamps adjusted worked-time calculations by {utc_adjustments.sum():.1f} minute(s), typically because of DST or timezone transitions."
        )

    if classification == "EXEMPT":
        analysis.reviews = [ResultCode.EXCLUDED_EXEMPT]
        analysis.details.append("Employee is excluded based on an active verified EXEMPT classification record.")
        analysis.result_codes = [ResultCode.EXCLUDED_EXEMPT]
        return analysis
    if classification == "UNKNOWN":
        _append_unique(analysis.reviews, ResultCode.EMPLOYEE_CLASSIFICATION_UNVERIFIED)
        analysis.details.append("No active verified exempt/non-exempt classification was supplied.")

    if not group.get("employee_name_resolved", pd.Series(True, index=group.index)).fillna(False).all():
        _append_unique(analysis.reviews, ResultCode.EMPLOYEE_NAME_UNRESOLVED)
    if not group.get("workday_config_verified", pd.Series(True, index=group.index)).fillna(False).all():
        _append_unique(analysis.reviews, ResultCode.WORKDAY_CONFIGURATION_UNVERIFIED)
    if not group.get("business_date_match", pd.Series(True, index=group.index)).fillna(False).all():
        _append_unique(analysis.reviews, ResultCode.BUSINESS_DATE_MISMATCH)

    workday_starts = set(group.get("workday_start", pd.Series("", index=group.index)).dropna().astype(str))
    if len(location_refs) > 1:
        analysis.details.append(f"Timecards from {len(location_refs)} locations were consolidated into one workday.")
        if len(workday_starts) > 1:
            _append_unique(analysis.reviews, ResultCode.MULTI_LOCATION_WORKDAY_REVIEW)
            analysis.details.append("Selected locations use different workday start definitions.")

    known_shift_types = {0, 1, 2}
    known_out_statuses = {0, 66, 68, 69, 76, 77, 78, 80, 82, 84, 85, 86}
    if not set(pd.to_numeric(group["shift_type"], errors="coerce").dropna().astype(int)).issubset(known_shift_types):
        _append_unique(analysis.reviews, ResultCode.UNKNOWN_ORACLE_CODE)
    out_values = set(pd.to_numeric(group["clock_out_status"], errors="coerce").dropna().astype(int))
    if not out_values.issubset(known_out_statuses):
        _append_unique(analysis.reviews, ResultCode.UNKNOWN_ORACLE_CODE)

    punch_errors, material_punch_error = _validate_punches(group, rules)
    analysis.punch_errors.extend(punch_errors)
    if punch_errors:
        _append_unique(analysis.reviews, ResultCode.PUNCH_ERROR)
    if group["clock_out_local"].isna().any():
        _append_unique(analysis.reviews, ResultCode.INCOMPLETE_TIMECARD)
    if analysis.adjustment_count:
        _append_unique(analysis.reviews, ResultCode.ADJUSTED_TIMECARD_REVIEW)
        analysis.details.append(f"Oracle reports {analysis.adjustment_count} timecard adjustment(s).")

    candidates, short_unpaid = _meal_candidates(group, rules)
    analysis.meals = candidates
    confirmed = sorted([meal for meal in candidates if meal.confirmed_by_punch], key=lambda meal: meal.start)
    probable = sorted([meal for meal in candidates if not meal.confirmed_by_punch and not meal.paid], key=lambda meal: meal.start)
    paid_breaks = sorted([meal for meal in candidates if meal.paid], key=lambda meal: meal.start)

    if probable:
        _append_unique(analysis.reviews, ResultCode.MEAL_PROBABLE_TIMESTAMP_ONLY)
        analysis.details.append(f"{len(probable)} gap(s) meet the duration threshold but lack unpaid-break evidence.")
    if paid_breaks:
        _append_unique(analysis.reviews, ResultCode.ON_DUTY_MEAL_AGREEMENT_UNVERIFIED)
        if policy and (policy.on_duty_meal_agreement_verified or (allow_unverified_legacy_waivers and policy.on_duty_meal_agreement)):
            analysis.details.append("An on-duty agreement is documented, but the nature-of-work and duty-free conditions require human review.")
        else:
            analysis.details.append(f"{len(paid_breaks)} paid-break interval(s) cannot automatically replace a duty-free meal.")

    tolerance_hours = rules.timestamp_tolerance_seconds / 3600.0
    first_meal: MealCandidate | None = confirmed[0] if confirmed else None
    first_meal_waived = False

    if worked_hours > rules.first_meal_required_after_hours + tolerance_hours:
        if first_meal is not None:
            if first_meal.worked_hours_before > rules.first_meal_required_after_hours + tolerance_hours:
                _append_unique(analysis.presumed_violations, ResultCode.FIRST_MEAL_LATE)
                analysis.details.append(f"First meal by punch began after {first_meal.worked_hours_before:.2f} worked hours.")
        elif worked_hours <= rules.first_meal_waiver_max_hours + tolerance_hours:
            if policy and (policy.first_meal_waiver_verified or (allow_unverified_legacy_waivers and policy.first_meal_waiver)):
                first_meal_waived = True
                analysis.details.append("Active first-meal waiver record found for this legal workday.")
            else:
                _append_unique(analysis.reviews, ResultCode.FIRST_MEAL_WAIVER_UNVERIFIED)
        elif probable or paid_breaks:
            _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)
        elif short_unpaid:
            _append_unique(analysis.presumed_violations, ResultCode.FIRST_MEAL_SHORT)
            analysis.details.append(f"Longest explicit unpaid break was {max(m.duration_minutes for m in short_unpaid):.1f} minutes.")
        else:
            _append_unique(analysis.presumed_violations, ResultCode.FIRST_MEAL_MISSING)

    if worked_hours > rules.second_meal_required_after_hours + tolerance_hours:
        second_meal = confirmed[1] if len(confirmed) >= 2 else None
        if second_meal is not None:
            if second_meal.worked_hours_before > rules.second_meal_required_after_hours + tolerance_hours:
                _append_unique(analysis.presumed_violations, ResultCode.SECOND_MEAL_LATE)
                analysis.details.append(f"Second meal by punch began after {second_meal.worked_hours_before:.2f} worked hours.")
        elif worked_hours <= rules.second_meal_waiver_max_hours + tolerance_hours and not first_meal_waived:
            if policy and (policy.second_meal_waiver_verified or (allow_unverified_legacy_waivers and policy.second_meal_waiver)) and first_meal is not None:
                analysis.details.append("Active second-meal waiver record found and the first meal was not waived.")
            else:
                _append_unique(analysis.reviews, ResultCode.SECOND_MEAL_WAIVER_UNVERIFIED)
        elif (
            first_meal is not None
            and any(candidate.start > first_meal.start for candidate in [*probable, *paid_breaks])
        ) or (first_meal is None and len(probable) + len(paid_breaks) >= 2):
            _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)
        else:
            later_short = [meal for meal in short_unpaid if first_meal is None or meal.start > first_meal.start]
            if later_short:
                _append_unique(analysis.presumed_violations, ResultCode.SECOND_MEAL_SHORT)
            else:
                _append_unique(analysis.presumed_violations, ResultCode.SECOND_MEAL_MISSING)

    # Preserve every punch-pattern finding before administrative/data-quality
    # controls decide whether it can be treated as an automatic presumed
    # violation. This prevents valid signals from disappearing from the auditor
    # dashboard while keeping the final/confirmed count conservative.
    analysis.candidate_violations = list(analysis.presumed_violations)

    suppression_reasons = False
    if material_punch_error:
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.PUNCH_ERROR)
    if classification == "UNKNOWN":
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.EMPLOYEE_CLASSIFICATION_UNVERIFIED)
    if ResultCode.WORKDAY_CONFIGURATION_UNVERIFIED in analysis.reviews:
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.WORKDAY_CONFIGURATION_UNVERIFIED)
    if ResultCode.MULTI_LOCATION_WORKDAY_REVIEW in analysis.reviews:
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.MULTI_LOCATION_WORKDAY_REVIEW)
    if ResultCode.UNKNOWN_ORACLE_CODE in analysis.reviews:
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.UNKNOWN_ORACLE_CODE)
    if global_data_blocked:
        analysis.data_blocked = True
        suppression_reasons = True
        _append_unique(analysis.blocking_reasons, ResultCode.DATA_INTEGRITY_BLOCKED)
        _append_unique(analysis.reviews, ResultCode.DATA_INTEGRITY_BLOCKED)

    if suppression_reasons and analysis.presumed_violations:
        suppressed = ", ".join(code.value for code in analysis.presumed_violations)
        analysis.details.append(
            "Punch-pattern findings retained as pending validation because required controls are incomplete: "
            + suppressed
        )
        analysis.presumed_violations.clear()
        _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)

    if analysis.presumed_violations and not (verified_rate and verified_rate.is_verified):
        _append_unique(analysis.reviews, ResultCode.REGULAR_RATE_UNVERIFIED)
        analysis.details.append("Premium uses Oracle payRt as a base-rate proxy; a verified regular rate was not supplied.")

    analysis.result_codes = [*analysis.presumed_violations, *analysis.reviews]
    if not analysis.result_codes:
        analysis.result_codes = [ResultCode.COMPLIANT_BY_PUNCH]
    return analysis


def analyze_timecards(
    timecards: pd.DataFrame,
    *,
    rules: CaliforniaMealRules | None = None,
    waiver_records: dict[str, list[dict[str, Any]]] | None = None,
    policy_records: dict[str, list[dict[str, Any]]] | None = None,
    regular_rate_records: dict[str, list[dict[str, Any]]] | None = None,
    default_classification: str = "NON_EXEMPT",
    global_data_blocked: bool = False,
) -> AnalysisBundle:
    rules = rules or CaliforniaMealRules()
    legacy_waiver_mode = policy_records is None and waiver_records is not None
    policies = policy_records if policy_records is not None else (waiver_records or {})
    regular_rates = regular_rate_records or {}

    if timecards.empty:
        empty = pd.DataFrame()
        return AnalysisBundle(
            workdays=empty,
            violations=empty,
            reviews=empty,
            punch_errors=empty,
            meals=empty,
            raw_timecards=timecards.copy(),
            candidates=empty,
            stats={
                "timecards": 0,
                "workdays": 0,
                "presumed_violations": 0,
                "automatic_violations": 0,
                "premium_workdays": 0,
                "reviews": 0,
                "punch_errors": 0,
                "estimated_premium": 0.0,
                "verified_premium": 0.0,
            },
        )

    required = {
        "location_ref",
        "employee_key",
        "employee_name",
        "shift_type",
        "clock_in_local",
        "clock_out_local",
    }
    missing = required.difference(timecards.columns)
    if missing:
        raise ValueError("Normalized timecards are missing columns: " + ", ".join(sorted(missing)))

    group_date = "legal_workday_date" if "legal_workday_date" in timecards.columns else "business_date"
    analyses: list[WorkdayAnalysis] = []
    grouped = timecards.groupby([group_date, "employee_key"], sort=True, dropna=False)
    for _, group in grouped:
        analyses.append(
            analyze_workday_group(
                group,
                rules,
                policies,
                regular_rates,
                default_classification=default_classification,
                global_data_blocked=global_data_blocked,
                allow_unverified_legacy_waivers=legacy_waiver_mode,
            )
        )

    workdays = pd.DataFrame([analysis.to_row() for analysis in analyses])
    violation_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    punch_rows: list[dict[str, Any]] = []
    meal_rows: list[dict[str, Any]] = []

    for analysis in analyses:
        base = {
            "Location Ref": analysis.location_ref,
            "Location": analysis.location_name,
            "Legal Workday Date": analysis.legal_workday_date,
            "Business Date": analysis.legal_workday_date,
            "Oracle Business Dates": analysis.business_dates,
            "Employee": analysis.employee_name,
            "Employee Key": analysis.employee_key,
            "Payroll ID": analysis.payroll_id,
            "Employee Classification": analysis.employee_classification,
            "Worked Hours": round(analysis.worked_hours, 2),
            "First Clock In": analysis.first_clock_in,
            "Last Clock Out": analysis.last_clock_out,
            "Role(s)": analysis.roles,
        }
        for code in analysis.candidate_violations:
            is_ready = code in analysis.presumed_violations
            candidate_rows.append(
                {
                    **base,
                    "Candidate Violation": code.value,
                    "Presumed Violation": code.value,
                    "Violation": code.value,
                    "Validation Status": (
                        "Detected — controls complete" if is_ready else "Pending administrative validation"
                    ),
                    "Pending Validation": not is_ready,
                    "Blocked By": ", ".join(reason.value for reason in analysis.blocking_reasons),
                    "Potential Premium Workday": True,
                    "Premium Estimate": round(analysis.premium_rate or 0.0, 2),
                    "Estimated Meal Premium": round(analysis.premium_rate or 0.0, 2),
                    "Premium Rate Basis": analysis.premium_rate_basis,
                    "Details": " | ".join(analysis.details),
                }
            )
        for code in analysis.presumed_violations:
            violation_rows.append(
                {
                    **base,
                    "Presumed Violation": code.value,
                    "Violation": code.value,
                    "Potential Premium Workday": True,
                    "Premium Estimate": round(analysis.premium_rate or 0.0, 2),
                    "Estimated Meal Premium": round(analysis.premium_rate or 0.0, 2),
                    "Premium Rate Basis": analysis.premium_rate_basis,
                    "Details": " | ".join(analysis.details),
                }
            )
        for code in analysis.reviews:
            review_rows.append({**base, "Review": code.value, "Details": " | ".join(analysis.details)})
        for error in analysis.punch_errors:
            punch_rows.append({**base, "Punch Error": error})
        for index, meal in enumerate(sorted(analysis.meals, key=lambda item: item.start), start=1):
            meal_rows.append(
                {
                    **base,
                    "Meal Sequence": index,
                    "Meal Start": meal.start,
                    "Meal End": meal.end,
                    "Duration Minutes": round(meal.duration_minutes, 2),
                    "Worked Hours Before": round(meal.worked_hours_before, 2),
                    "Evidence": meal.evidence,
                    "Confirmed by Punch": meal.confirmed_by_punch,
                    "Confirmed Duty-Free Timestamp": meal.confirmed_by_punch,
                    "Duty-Free Verified": False,
                    "Paid": meal.paid,
                    "Meal Location(s)": meal.locations,
                    "Source Timecard ID": meal.source_timecard_id,
                }
            )

    violations = pd.DataFrame(violation_rows)
    candidates = pd.DataFrame(candidate_rows)
    reviews = pd.DataFrame(review_rows)
    punch_errors = pd.DataFrame(punch_rows)
    meals = pd.DataFrame(meal_rows)

    premium_workdays = sum(1 for analysis in analyses if analysis.premium_workday)
    estimated_premium = sum((analysis.premium_rate or 0.0) for analysis in analyses if analysis.premium_workday)
    verified_premium = sum(
        (analysis.premium_rate or 0.0)
        for analysis in analyses
        if analysis.premium_workday and analysis.premium_rate_basis == "Verified regular rate"
    )
    primary = timecards[
        timecards.get("is_primary_segment", pd.Series(True, index=timecards.index)).fillna(True)
    ]
    stats = {
        "timecards": int(primary.get("source_timecard_id", primary["timecard_id"]).nunique()),
        "segments": int(len(timecards)),
        "workdays": int(len(analyses)),
        "employees": int(timecards["employee_key"].nunique()),
        "candidate_violations": int(len(candidates)),
        "pending_candidate_violations": int(
            candidates.get("Pending Validation", pd.Series(dtype=bool)).fillna(False).sum()
        ) if not candidates.empty else 0,
        "candidate_workdays": int(
            candidates[["Employee Key", "Legal Workday Date"]].drop_duplicates().shape[0]
        ) if not candidates.empty else 0,
        "candidate_employees": int(candidates["Employee Key"].nunique()) if not candidates.empty else 0,
        "presumed_violations": int(len(violations)),
        "automatic_violations": int(len(violations)),
        "premium_workdays": int(premium_workdays),
        "reviews": int(len(reviews)),
        "punch_errors": int(len(punch_errors)),
        "adjusted_timecards": int((pd.to_numeric(primary["adjustment_count"], errors="coerce").fillna(0) > 0).sum()),
        "open_timecards": int(primary["clock_out_local"].isna().sum()),
        "estimated_premium": round(float(estimated_premium), 2),
        "verified_premium": round(float(verified_premium), 2),
        "oracle_premium_pay": round(float(pd.to_numeric(primary["premium_pay"], errors="coerce").fillna(0).sum()), 2),
        "excluded_exempt_workdays": int(sum(ResultCode.EXCLUDED_EXEMPT in a.reviews for a in analyses)),
        "classification_unverified_workdays": int(sum(ResultCode.EMPLOYEE_CLASSIFICATION_UNVERIFIED in a.reviews for a in analyses)),
        "multi_location_workdays": int(sum(len(a.location_ref.split(", ")) > 1 for a in analyses)),
    }

    return AnalysisBundle(
        workdays=workdays,
        violations=violations,
        reviews=reviews,
        punch_errors=punch_errors,
        meals=meals,
        raw_timecards=timecards.copy(),
        stats=stats,
        candidates=candidates,
    )
