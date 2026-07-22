from __future__ import annotations

from typing import Any

import pandas as pd


MISSING_CODES = {"FIRST_MEAL_MISSING", "SECOND_MEAL_MISSING"}
LATE_CODES = {"FIRST_MEAL_LATE", "SECOND_MEAL_LATE"}
SHORT_CODES = {"FIRST_MEAL_SHORT", "SECOND_MEAL_SHORT"}


def _count_codes(df: pd.DataFrame, code_column: str, codes: set[str]) -> pd.Series:
    if df.empty or code_column not in df.columns or "Employee" not in df.columns:
        return pd.Series(dtype="int64")
    mask = df[code_column].astype(str).isin(codes)
    return df.loc[mask].groupby("Employee").size()


def _group_count(df: pd.DataFrame, column: str = "Employee") -> pd.Series:
    if df.empty or column not in df.columns:
        return pd.Series(dtype="int64")
    return df.groupby(column).size()


def _expected_meals(worked_hours: Any) -> int:
    try:
        hours = float(worked_hours)
    except (TypeError, ValueError):
        return 0
    if hours > 10:
        return 2
    if hours > 5:
        return 1
    return 0


def build_employee_summary(
    *,
    workdays: pd.DataFrame,
    violations: pd.DataFrame,
    reviews: pd.DataFrame,
    punch_errors: pd.DataFrame,
    raw_timecards: pd.DataFrame,
    adjustments: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "Employee",
        "Payroll ID",
        "Workdays",
        "Worked Hours",
        "Meals Expected by Hours",
        "Confirmed Meals",
        "Probable Meals",
        "Missing Meals",
        "Late Meals",
        "Short Meals",
        "Automatic Violations",
        "Review Cases",
        "Punch Errors",
        "Adjusted Timecards",
        "Adjustment Records",
        "Managers Involved",
        "Premium Workdays",
        "Estimated Premium",
        "Meal Coverage %",
        "Status",
    ]
    if workdays.empty:
        return pd.DataFrame(columns=columns)

    source = workdays.copy()
    source["Expected Meals"] = source.get("Worked Hours", 0).map(_expected_meals)
    source["Potential Premium Workday"] = source.get(
        "Potential Premium Workday", pd.Series(False, index=source.index)
    ).fillna(False).astype(bool)

    base = source.groupby("Employee", as_index=False).agg(
        **{
            "Payroll ID": ("Payroll ID", lambda values: next((str(v) for v in values if str(v).strip()), "")),
            "Workdays": ("Business Date", "nunique"),
            "Worked Hours": ("Worked Hours", "sum"),
            "Meals Expected by Hours": ("Expected Meals", "sum"),
            "Confirmed Meals": ("Confirmed Meals", "sum"),
            "Probable Meals": ("Probable Meals", "sum"),
            "Premium Workdays": ("Potential Premium Workday", "sum"),
            "Estimated Premium": ("Estimated Meal Premium", "sum"),
        }
    ).set_index("Employee")

    base["Missing Meals"] = _count_codes(violations, "Violation", MISSING_CODES)
    base["Late Meals"] = _count_codes(violations, "Violation", LATE_CODES)
    base["Short Meals"] = _count_codes(violations, "Violation", SHORT_CODES)
    base["Automatic Violations"] = _group_count(violations)
    base["Review Cases"] = _group_count(reviews)
    base["Punch Errors"] = _group_count(punch_errors)

    if raw_timecards.empty or "employee_name" not in raw_timecards.columns:
        adjusted_timecards = pd.Series(dtype="int64")
    else:
        adjustment_count = pd.to_numeric(
            raw_timecards.get("adjustment_count", pd.Series(0, index=raw_timecards.index)),
            errors="coerce",
        ).fillna(0)
        adjusted_timecards = raw_timecards.loc[adjustment_count > 0].groupby("employee_name")[
            "timecard_id"
        ].nunique()
    base["Adjusted Timecards"] = adjusted_timecards

    base["Adjustment Records"] = _group_count(adjustments)
    if adjustments.empty or "Manager" not in adjustments.columns:
        base["Managers Involved"] = 0
    else:
        base["Managers Involved"] = adjustments.groupby("Employee")["Manager"].nunique()

    numeric_columns = [
        "Missing Meals",
        "Late Meals",
        "Short Meals",
        "Automatic Violations",
        "Review Cases",
        "Punch Errors",
        "Adjusted Timecards",
        "Adjustment Records",
        "Managers Involved",
    ]
    for column in numeric_columns:
        base[column] = pd.to_numeric(base[column], errors="coerce").fillna(0).astype(int)

    expected = pd.to_numeric(base["Meals Expected by Hours"], errors="coerce").fillna(0)
    confirmed = pd.to_numeric(base["Confirmed Meals"], errors="coerce").fillna(0)
    base["Meal Coverage %"] = [
        round(min(100.0, (confirmed_value / expected_value) * 100), 1)
        if expected_value > 0
        else 100.0
        for confirmed_value, expected_value in zip(confirmed, expected)
    ]

    def status(row: pd.Series) -> str:
        if int(row["Automatic Violations"]) > 0:
            return "Atención inmediata"
        if int(row["Review Cases"]) > 0 or int(row["Punch Errors"]) > 0:
            return "Revisión requerida"
        if int(row["Adjustment Records"]) > 0:
            return "Ajustes detectados"
        return "Sin hallazgos"

    base["Status"] = base.apply(status, axis=1)
    result = base.reset_index()
    result["Worked Hours"] = pd.to_numeric(result["Worked Hours"], errors="coerce").fillna(0).round(2)
    result["Estimated Premium"] = pd.to_numeric(
        result["Estimated Premium"], errors="coerce"
    ).fillna(0).round(2)

    return result[columns].sort_values(
        ["Automatic Violations", "Review Cases", "Adjustment Records", "Employee"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
