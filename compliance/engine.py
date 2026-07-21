from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from compliance.models import (
    CaliforniaMealRules,
    MealCandidate,
    ResultCode,
    WaiverRecord,
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


def _waiver_for_date(
    waiver_records: dict[str, list[dict[str, Any]]],
    employee_key: str,
    business_date: date,
) -> WaiverRecord | None:
    for raw in waiver_records.get(employee_key, []):
        record = WaiverRecord(**raw)
        if record.active_on(business_date):
            return record
    return None


def _worked_hours_before(working: pd.DataFrame, moment: pd.Timestamp) -> float:
    total = 0.0
    for _, row in working.iterrows():
        start = row["clock_in_local"]
        end = row["clock_out_local"]
        if pd.isna(start) or pd.isna(end) or start >= moment:
            continue
        total += _duration_hours(start, min(end, moment))
    return max(0.0, total)


def _meal_candidates(group: pd.DataFrame, rules: CaliforniaMealRules) -> tuple[list[MealCandidate], list[MealCandidate]]:
    """Return (all candidates, short explicit unpaid breaks)."""
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

    # Explicit Simphony break timecards are the strongest timestamp evidence.
    for _, row in explicit_breaks.sort_values("clock_in_local").iterrows():
        start = row["clock_in_local"]
        end = row["clock_out_local"]
        minutes = max(0.0, (end - start).total_seconds() / 60.0)
        paid = int(row["shift_type"]) == 1
        candidate = MealCandidate(
            start=start.to_pydatetime(),
            end=end.to_pydatetime(),
            duration_minutes=minutes,
            worked_hours_before=_worked_hours_before(working, start),
            evidence="Oracle paid-break shift" if paid else "Oracle unpaid-break shift",
            confirmed=not paid and minutes + tolerance_minutes >= rules.minimum_meal_minutes,
            paid=paid,
            source_timecard_id=str(row["timecard_id"]),
        )
        if not paid and minutes + tolerance_minutes < rules.minimum_meal_minutes:
            short_unpaid.append(candidate)
        elif minutes > tolerance_minutes:
            candidates.append(candidate)
        used_windows.append((start, end))

    # Gaps between working shifts can represent a meal even if Simphony did not
    # create a dedicated break timecard. Keep unlabeled gaps as probable only.
    records = list(working.to_dict("records"))
    for index in range(len(records) - 1):
        current = records[index]
        following = records[index + 1]
        start = current["clock_out_local"]
        end = following["clock_in_local"]
        if pd.isna(start) or pd.isna(end) or end <= start:
            continue
        minutes = (end - start).total_seconds() / 60.0
        if minutes <= tolerance_minutes:
            continue
        # Avoid duplicating an explicit break row that occupies the same interval.
        if any(_overlap_minutes(start, end, window_start, window_end) >= min(minutes, 1.0) for window_start, window_end in used_windows):
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
            worked_hours_before=_worked_hours_before(working, start),
            evidence=evidence,
            confirmed=confirmed,
            paid=paid,
            source_timecard_id=str(current.get("timecard_id") or ""),
        )
        if status == 66 and minutes + tolerance_minutes < rules.minimum_meal_minutes:
            short_unpaid.append(candidate)
        else:
            candidates.append(candidate)

    # Deduplicate near-identical candidates, preferring confirmed evidence.
    candidates.sort(key=lambda item: (item.start, not item.confirmed, item.paid))
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
        elif candidate.confirmed and not deduped[duplicate_index].confirmed:
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
    negative = completed[completed["clock_out_local"] < completed["clock_in_local"]]
    if not negative.empty:
        errors.append(f"{len(negative)} timecard(s) with Clock Out before Clock In")
        material = True

    zero = completed[
        (completed["clock_out_local"] - completed["clock_in_local"]).dt.total_seconds().abs()
        <= tolerance_seconds
    ]
    if not zero.empty:
        errors.append(f"{len(zero)} zero-duration timecard(s)")

    working = completed[completed["shift_type"] == 0].sort_values("clock_in_local")
    previous_end: pd.Timestamp | None = None
    for _, row in working.iterrows():
        start = row["clock_in_local"]
        end = row["clock_out_local"]
        if previous_end is not None and start < previous_end - pd.Timedelta(seconds=tolerance_seconds):
            errors.append("Overlapping working timecards")
            material = True
            break
        previous_end = max(previous_end, end) if previous_end is not None else end

    auto_clockouts = completed[completed["clock_out_status"].isin([77, 85])]
    if not auto_clockouts.empty:
        errors.append(f"{len(auto_clockouts)} manager/automatic Clock Out(s) require review")

    return errors, material


def _append_unique(values: list[ResultCode], code: ResultCode) -> None:
    if code not in values:
        values.append(code)


def analyze_workday_group(
    group: pd.DataFrame,
    rules: CaliforniaMealRules,
    waiver_records: dict[str, list[dict[str, Any]]],
) -> WorkdayAnalysis:
    group = group.sort_values(["clock_in_local", "clock_out_local", "timecard_id"], na_position="last")
    first = group.iloc[0]
    business_date = first["business_date"]
    if isinstance(business_date, pd.Timestamp):
        business_date = business_date.date()
    if not isinstance(business_date, date):
        raise ValueError("A valid Oracle business date is required for each timecard.")

    working = group[(group["shift_type"] == 0) & group["clock_out_local"].notna()].copy()
    worked_hours = sum(
        max(0.0, _duration_hours(row["clock_in_local"], row["clock_out_local"]))
        for _, row in working.iterrows()
        if pd.notna(row["clock_in_local"]) and pd.notna(row["clock_out_local"])
    )

    pay_rates = pd.to_numeric(group["pay_rate"], errors="coerce").dropna()
    pay_rate = float(pay_rates.max()) if not pay_rates.empty else None
    roles = ", ".join(sorted(set(group["job_code"].dropna().astype(str))))
    first_clock_in = group["clock_in_local"].min()
    last_clock_out = group["clock_out_local"].max()

    analysis = WorkdayAnalysis(
        location_ref=str(first["location_ref"]),
        location_name=str(first["location_name"]),
        business_date=business_date,
        employee_key=str(first["employee_key"]),
        employee_name=str(first["employee_name"]),
        payroll_id=str(first["payroll_id"] or ""),
        roles=roles,
        first_clock_in=None if pd.isna(first_clock_in) else first_clock_in.to_pydatetime(),
        last_clock_out=None if pd.isna(last_clock_out) else last_clock_out.to_pydatetime(),
        worked_hours=worked_hours,
        pay_rate=pay_rate,
        oracle_premium_hours=float(pd.to_numeric(group["premium_hours"], errors="coerce").fillna(0).sum()),
        oracle_premium_pay=float(pd.to_numeric(group["premium_pay"], errors="coerce").fillna(0).sum()),
        adjustment_count=int(pd.to_numeric(group["adjustment_count"], errors="coerce").fillna(0).sum()),
        source_timecard_ids=[str(value) for value in group["timecard_id"].dropna().astype(str).tolist()],
    )

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
    confirmed = sorted([meal for meal in candidates if meal.confirmed], key=lambda meal: meal.start)
    probable = sorted(
        [meal for meal in candidates if not meal.confirmed and not meal.paid],
        key=lambda meal: meal.start,
    )
    paid_breaks = sorted([meal for meal in candidates if meal.paid], key=lambda meal: meal.start)

    if probable:
        _append_unique(analysis.reviews, ResultCode.MEAL_PROBABLE_TIMESTAMP_ONLY)
        analysis.details.append(
            f"{len(probable)} gap(s) meet the duration threshold but lack confirmed unpaid-break evidence."
        )
    if paid_breaks:
        _append_unique(analysis.reviews, ResultCode.ON_DUTY_MEAL_AGREEMENT_UNVERIFIED)
        analysis.details.append(
            f"{len(paid_breaks)} paid-break interval(s) cannot automatically replace a duty-free meal."
        )

    waiver = _waiver_for_date(waiver_records, analysis.employee_key, business_date)
    tolerance_hours = rules.timestamp_tolerance_seconds / 3600.0

    # First meal assessment.
    first_meal: MealCandidate | None = confirmed[0] if confirmed else None
    first_meal_waived = False
    if worked_hours > rules.first_meal_required_after_hours + tolerance_hours:
        if first_meal is not None:
            if first_meal.worked_hours_before > rules.first_meal_required_after_hours + tolerance_hours:
                _append_unique(analysis.automatic_violations, ResultCode.FIRST_MEAL_LATE)
                analysis.details.append(
                    f"First confirmed meal began after {first_meal.worked_hours_before:.2f} worked hours."
                )
        elif worked_hours <= rules.first_meal_waiver_max_hours + tolerance_hours:
            if waiver and waiver.first_meal_waiver:
                first_meal_waived = True
                analysis.details.append("Active first-meal waiver found for this business date.")
            else:
                _append_unique(analysis.reviews, ResultCode.FIRST_MEAL_WAIVER_UNVERIFIED)
        elif probable or paid_breaks:
            # Evidence exists, but it is insufficient to declare compliance or a violation.
            _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)
        else:
            if short_unpaid:
                _append_unique(analysis.automatic_violations, ResultCode.FIRST_MEAL_SHORT)
                analysis.details.append(
                    f"Longest explicit unpaid break was {max(m.duration_minutes for m in short_unpaid):.1f} minutes."
                )
            else:
                _append_unique(analysis.automatic_violations, ResultCode.FIRST_MEAL_MISSING)

    # Second meal assessment. The second confirmed meal must be a separate event.
    if worked_hours > rules.second_meal_required_after_hours + tolerance_hours:
        second_meal = confirmed[1] if len(confirmed) >= 2 else None
        if second_meal is not None:
            if second_meal.worked_hours_before > rules.second_meal_required_after_hours + tolerance_hours:
                _append_unique(analysis.automatic_violations, ResultCode.SECOND_MEAL_LATE)
                analysis.details.append(
                    f"Second confirmed meal began after {second_meal.worked_hours_before:.2f} worked hours."
                )
        elif worked_hours <= rules.second_meal_waiver_max_hours + tolerance_hours and not first_meal_waived:
            if waiver and waiver.second_meal_waiver and first_meal is not None:
                analysis.details.append("Active second-meal waiver found for this business date.")
            else:
                _append_unique(analysis.reviews, ResultCode.SECOND_MEAL_WAIVER_UNVERIFIED)
        elif (
            first_meal is not None
            and any(candidate.start > first_meal.start for candidate in [*probable, *paid_breaks])
        ) or (first_meal is None and len(probable) + len(paid_breaks) >= 2):
            _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)
        else:
            # A short break after the first meal is evidence of a short second meal.
            later_short = [
                meal
                for meal in short_unpaid
                if first_meal is None or meal.start > first_meal.start
            ]
            if later_short:
                _append_unique(analysis.automatic_violations, ResultCode.SECOND_MEAL_SHORT)
            else:
                _append_unique(analysis.automatic_violations, ResultCode.SECOND_MEAL_MISSING)

    # Material punch errors suppress automatic legal conclusions.
    if material_punch_error and analysis.automatic_violations:
        suppressed = ", ".join(code.value for code in analysis.automatic_violations)
        analysis.details.append(
            "Automatic conclusions suppressed because material timecard errors exist: " + suppressed
        )
        analysis.automatic_violations.clear()
        _append_unique(analysis.reviews, ResultCode.INCONCLUSIVE)

    analysis.result_codes = [*analysis.automatic_violations, *analysis.reviews]
    if not analysis.result_codes:
        analysis.result_codes = [ResultCode.COMPLIANT]
    return analysis


def analyze_timecards(
    timecards: pd.DataFrame,
    *,
    rules: CaliforniaMealRules | None = None,
    waiver_records: dict[str, list[dict[str, Any]]] | None = None,
) -> AnalysisBundle:
    rules = rules or CaliforniaMealRules()
    waiver_records = waiver_records or {}

    if timecards.empty:
        empty = pd.DataFrame()
        return AnalysisBundle(
            workdays=empty,
            violations=empty,
            reviews=empty,
            punch_errors=empty,
            meals=empty,
            raw_timecards=timecards.copy(),
            stats={
                "timecards": 0,
                "workdays": 0,
                "automatic_violations": 0,
                "premium_workdays": 0,
                "reviews": 0,
                "punch_errors": 0,
                "estimated_premium": 0.0,
            },
        )

    required = {
        "location_ref",
        "business_date",
        "employee_key",
        "employee_name",
        "shift_type",
        "clock_in_local",
        "clock_out_local",
    }
    missing = required.difference(timecards.columns)
    if missing:
        raise ValueError("Normalized timecards are missing columns: " + ", ".join(sorted(missing)))

    analyses: list[WorkdayAnalysis] = []
    grouped = timecards.groupby(
        ["location_ref", "business_date", "employee_key"],
        sort=True,
        dropna=False,
    )
    for _, group in grouped:
        analyses.append(analyze_workday_group(group, rules, waiver_records))

    workday_rows = [analysis.to_row() for analysis in analyses]
    workdays = pd.DataFrame(workday_rows)

    violation_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    punch_rows: list[dict[str, Any]] = []
    meal_rows: list[dict[str, Any]] = []

    for analysis in analyses:
        base = {
            "Location Ref": analysis.location_ref,
            "Location": analysis.location_name,
            "Business Date": analysis.business_date,
            "Employee": analysis.employee_name,
            "Employee Key": analysis.employee_key,
            "Payroll ID": analysis.payroll_id,
            "Worked Hours": round(analysis.worked_hours, 2),
            "First Clock In": analysis.first_clock_in,
            "Last Clock Out": analysis.last_clock_out,
            "Role(s)": analysis.roles,
        }
        for code in analysis.automatic_violations:
            violation_rows.append(
                {
                    **base,
                    "Violation": code.value,
                    "Potential Premium Workday": True,
                    "Estimated Meal Premium": round(analysis.pay_rate or 0.0, 2),
                    "Details": " | ".join(analysis.details),
                }
            )
        for code in analysis.reviews:
            review_rows.append(
                {
                    **base,
                    "Review": code.value,
                    "Details": " | ".join(analysis.details),
                }
            )
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
                    "Confirmed Duty-Free Timestamp": meal.confirmed,
                    "Paid": meal.paid,
                    "Source Timecard ID": meal.source_timecard_id,
                }
            )

    violations = pd.DataFrame(violation_rows)
    reviews = pd.DataFrame(review_rows)
    punch_errors = pd.DataFrame(punch_rows)
    meals = pd.DataFrame(meal_rows)

    premium_workdays = sum(1 for analysis in analyses if analysis.premium_workday)
    estimated_premium = sum((analysis.pay_rate or 0.0) for analysis in analyses if analysis.premium_workday)
    stats = {
        "timecards": int(len(timecards)),
        "workdays": int(len(analyses)),
        "employees": int(timecards["employee_key"].nunique()),
        "automatic_violations": int(len(violations)),
        "premium_workdays": int(premium_workdays),
        "reviews": int(len(reviews)),
        "punch_errors": int(len(punch_errors)),
        "adjusted_timecards": int((pd.to_numeric(timecards["adjustment_count"], errors="coerce").fillna(0) > 0).sum()),
        "open_timecards": int(timecards["clock_out_local"].isna().sum()),
        "estimated_premium": round(float(estimated_premium), 2),
        "oracle_premium_pay": round(float(pd.to_numeric(timecards["premium_pay"], errors="coerce").fillna(0).sum()), 2),
    }

    return AnalysisBundle(
        workdays=workdays,
        violations=violations,
        reviews=reviews,
        punch_errors=punch_errors,
        meals=meals,
        raw_timecards=timecards.copy(),
        stats=stats,
    )
