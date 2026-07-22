from __future__ import annotations

from typing import Any

import pandas as pd


MISSING_CODES = {"FIRST_MEAL_MISSING", "SECOND_MEAL_MISSING"}
LATE_CODES = {"FIRST_MEAL_LATE", "SECOND_MEAL_LATE"}
SHORT_CODES = {"FIRST_MEAL_SHORT", "SECOND_MEAL_SHORT"}


def _employee_group_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="string", index=df.index)
    if "Employee Key" in df.columns:
        keys = df["Employee Key"].astype("string").fillna("").str.strip()
    elif "employee_key" in df.columns:
        keys = df["employee_key"].astype("string").fillna("").str.strip()
    else:
        keys = pd.Series("", index=df.index, dtype="string")
    if "Payroll ID" in df.columns:
        payroll = df["Payroll ID"].astype("string").fillna("").str.strip()
    elif "payroll_id" in df.columns:
        payroll = df["payroll_id"].astype("string").fillna("").str.strip()
    else:
        payroll = pd.Series("", index=df.index, dtype="string")
    if "Employee" in df.columns:
        names = df["Employee"].astype("string").fillna("").str.strip()
    elif "employee_name" in df.columns:
        names = df["employee_name"].astype("string").fillna("").str.strip()
    else:
        names = pd.Series("Empleado sin identificar", index=df.index, dtype="string")
    return keys.where(keys.ne(""), payroll.where(payroll.ne(""), "NAME::" + names))


def _with_employee_group(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["_Employee Group"] = _employee_group_series(result)
    return result


def _align_employee_groups(
    df: pd.DataFrame, employee_name_map: dict[str, str] | None = None
) -> pd.DataFrame:
    source = _with_employee_group(df)
    if not employee_name_map:
        return source
    if "Employee" in source.columns:
        names = source["Employee"].astype("string").fillna("").str.strip()
    elif "employee_name" in source.columns:
        names = source["employee_name"].astype("string").fillna("").str.strip()
    else:
        return source
    fallback = source["_Employee Group"].astype(str).str.startswith("NAME::")
    mapped = names.map(employee_name_map)
    source.loc[fallback & mapped.notna(), "_Employee Group"] = mapped[fallback & mapped.notna()]
    return source


def _count_codes(
    df: pd.DataFrame,
    code_column: str,
    codes: set[str],
    employee_name_map: dict[str, str] | None = None,
) -> pd.Series:
    if df.empty or code_column not in df.columns:
        return pd.Series(dtype="int64")
    source = _align_employee_groups(df, employee_name_map)
    mask = source[code_column].astype(str).isin(codes)
    return source.loc[mask].groupby("_Employee Group").size()


def _group_count(
    df: pd.DataFrame, employee_name_map: dict[str, str] | None = None
) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="int64")
    source = _align_employee_groups(df, employee_name_map)
    return source.groupby("_Employee Group").size()


def _expected_meals(worked_hours: Any, classification: Any = "NON_EXEMPT") -> int:
    if str(classification).upper() == "EXEMPT":
        return 0
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
        "Classification",
        "Locations",
        "Workdays",
        "Worked Hours",
        "Meals Expected by Hours",
        "Confirmed Meals",
        "Probable Meals",
        "Missing Meals",
        "Late Meals",
        "Short Meals",
        "Presumed Violations",
        "Review Cases",
        "Punch Errors",
        "Adjusted Timecards",
        "Adjustment Records",
        "Adjustments Changing Result",
        "Managers Involved",
        "Premium Workdays",
        "Premium Estimate",
        "Verified Premium",
        "Meal Coverage %",
        "Status",
    ]
    if workdays.empty:
        return pd.DataFrame(columns=columns)

    source = _with_employee_group(workdays)
    if "Employee Classification" not in source.columns:
        source["Employee Classification"] = "NON_EXEMPT"
    if "Location" not in source.columns:
        source["Location"] = ""
    if "Premium Rate Basis" not in source.columns:
        source["Premium Rate Basis"] = "Base pay-rate proxy — not final"
    if "Premium Estimate" not in source.columns and "Estimated Meal Premium" not in source.columns:
        source["Premium Estimate"] = 0.0
    source["Expected Meals"] = [
        _expected_meals(hours, classification)
        for hours, classification in zip(
            source.get("Worked Hours", 0),
            source.get("Employee Classification", pd.Series("UNKNOWN", index=source.index)),
        )
    ]
    source["Potential Premium Workday"] = source.get(
        "Potential Premium Workday", pd.Series(False, index=source.index)
    ).fillna(False).astype(bool)
    source["Verified Premium Amount"] = [
        float(amount or 0)
        if str(basis) == "Verified regular rate"
        else 0.0
        for amount, basis in zip(
            source.get("Premium Estimate", source.get("Estimated Meal Premium", 0)),
            source.get("Premium Rate Basis", pd.Series("", index=source.index)),
        )
    ]

    unique_name_groups = source.groupby("Employee")["_Employee Group"].unique()
    employee_name_map = {
        str(name): str(groups[0])
        for name, groups in unique_name_groups.items()
        if len(groups) == 1
    }

    base = source.groupby("_Employee Group", as_index=False).agg(
        **{
            "Employee": ("Employee", lambda values: next((str(v) for v in values if str(v).strip()), "Empleado sin identificar")),
            "Payroll ID": ("Payroll ID", lambda values: next((str(v) for v in values if str(v).strip()), "")),
            "Classification": ("Employee Classification", lambda values: ", ".join(sorted(set(str(v) for v in values if str(v).strip())))),
            "Locations": ("Location", lambda values: ", ".join(sorted(set(part.strip() for value in values for part in str(value).split(",") if part.strip())))),
            "Workdays": ("Legal Workday Date" if "Legal Workday Date" in source.columns else "Business Date", "nunique"),
            "Worked Hours": ("Worked Hours", "sum"),
            "Meals Expected by Hours": ("Expected Meals", "sum"),
            "Confirmed Meals": ("Confirmed Meals", "sum"),
            "Probable Meals": ("Probable Meals", "sum"),
            "Premium Workdays": ("Potential Premium Workday", "sum"),
            "Premium Estimate": ("Premium Estimate" if "Premium Estimate" in source.columns else "Estimated Meal Premium", "sum"),
            "Verified Premium": ("Verified Premium Amount", "sum"),
        }
    ).set_index("_Employee Group")

    code_col = "Presumed Violation" if "Presumed Violation" in violations.columns else "Violation"
    base["Missing Meals"] = _count_codes(violations, code_col, MISSING_CODES, employee_name_map)
    base["Late Meals"] = _count_codes(violations, code_col, LATE_CODES, employee_name_map)
    base["Short Meals"] = _count_codes(violations, code_col, SHORT_CODES, employee_name_map)
    base["Presumed Violations"] = _group_count(violations, employee_name_map)
    base["Review Cases"] = _group_count(reviews, employee_name_map)
    base["Punch Errors"] = _group_count(punch_errors, employee_name_map)

    primary = raw_timecards.copy()
    if not primary.empty and "is_primary_segment" in primary.columns:
        primary = primary[primary["is_primary_segment"].fillna(True)]
    if primary.empty or "employee_name" not in primary.columns:
        adjusted_timecards = pd.Series(dtype="int64")
    else:
        adjustment_count = pd.to_numeric(primary.get("adjustment_count", pd.Series(0, index=primary.index)), errors="coerce").fillna(0)
        adjusted_source = _align_employee_groups(primary.loc[adjustment_count > 0], employee_name_map)
        adjusted_timecards = adjusted_source.groupby("_Employee Group")[
            "source_timecard_id" if "source_timecard_id" in adjusted_source.columns else "timecard_id"
        ].nunique()
    base["Adjusted Timecards"] = adjusted_timecards
    base["Adjustment Records"] = _group_count(adjustments, employee_name_map)
    if adjustments.empty or "Compliance Result Changed" not in adjustments.columns:
        base["Adjustments Changing Result"] = 0
    else:
        changed_adjustments = _align_employee_groups(adjustments[adjustments["Compliance Result Changed"].fillna(False)], employee_name_map)
        base["Adjustments Changing Result"] = changed_adjustments.groupby("_Employee Group").size()
    if adjustments.empty or "Manager" not in adjustments.columns:
        base["Managers Involved"] = 0
    else:
        adjustment_source = _align_employee_groups(adjustments, employee_name_map)
        base["Managers Involved"] = adjustment_source.groupby("_Employee Group")["Manager"].nunique()

    numeric_columns = [
        "Missing Meals",
        "Late Meals",
        "Short Meals",
        "Presumed Violations",
        "Review Cases",
        "Punch Errors",
        "Adjusted Timecards",
        "Adjustment Records",
        "Adjustments Changing Result",
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
        if "UNKNOWN" in str(row["Classification"]):
            return "Bloqueado por clasificación"
        if int(row["Presumed Violations"]) > 0:
            return "Atención inmediata"
        if int(row["Review Cases"]) > 0 or int(row["Punch Errors"]) > 0:
            return "Revisión requerida"
        if int(row["Adjustment Records"]) > 0:
            return "Ajustes detectados"
        return "Cumplimiento por marcación"

    base["Status"] = base.apply(status, axis=1)
    result = base.reset_index()
    for column in ("Worked Hours", "Premium Estimate", "Verified Premium"):
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0).round(2)

    return result[columns].sort_values(
        ["Presumed Violations", "Review Cases", "Adjustments Changing Result", "Employee"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)


def build_violation_employee_summary(violations: pd.DataFrame) -> pd.DataFrame:
    """Return one concise auditor-facing row per employee with meal violations.

    The summary preserves distinct employees that share the same display name by
    grouping with the normalized employee key/payroll fallback used elsewhere in
    the reporting module. Dates are stored as ISO strings so exports remain
    stable and easy to filter.
    """
    columns = [
        "Employee Group",
        "Employee",
        "Payroll ID",
        "Violations",
        "Principal Reason Code",
        "Reason Breakdown",
        "Affected Days",
        "Affected Dates",
        "Locations",
        "Pending Validation",
        "Ready Findings",
        "Status",
    ]
    if violations.empty:
        return pd.DataFrame(columns=columns)

    source = _with_employee_group(violations)
    code_column = (
        "Candidate Violation"
        if "Candidate Violation" in source.columns
        else "Presumed Violation"
        if "Presumed Violation" in source.columns
        else "Violation"
    )
    if code_column not in source.columns:
        return pd.DataFrame(columns=columns)

    date_column = (
        "Legal Workday Date"
        if "Legal Workday Date" in source.columns
        else "Business Date"
        if "Business Date" in source.columns
        else None
    )
    if date_column is None:
        source["_Violation Date"] = pd.NaT
    else:
        source["_Violation Date"] = pd.to_datetime(
            source[date_column], errors="coerce"
        ).dt.date

    if "Location" not in source.columns:
        source["Location"] = ""
    if "Payroll ID" not in source.columns:
        source["Payroll ID"] = ""
    if "Employee" not in source.columns:
        source["Employee"] = "Empleado sin identificar"

    rows: list[dict[str, Any]] = []
    for employee_group, group in source.groupby("_Employee Group", sort=False):
        code_counts = (
            group[code_column]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        code_counts = code_counts[code_counts.ne("")].value_counts()
        if code_counts.empty:
            continue
        max_count = int(code_counts.max())
        principal_candidates = sorted(
            str(code) for code, count in code_counts.items() if int(count) == max_count
        )
        principal = principal_candidates[0]
        breakdown = " | ".join(
            f"{code}:{int(count)}" for code, count in code_counts.items()
        )
        dates = sorted(
            {value for value in group["_Violation Date"].tolist() if pd.notna(value)}
        )
        locations = sorted(
            {
                part.strip()
                for value in group["Location"].fillna("").astype(str)
                for part in value.split(",")
                if part.strip()
            }
        )
        employee = next(
            (str(value).strip() for value in group["Employee"] if str(value).strip()),
            "Empleado sin identificar",
        )
        payroll_id = next(
            (str(value).strip() for value in group["Payroll ID"] if str(value).strip()),
            "",
        )
        pending_series = group.get(
            "Pending Validation", pd.Series(False, index=group.index)
        ).fillna(False).astype(bool)
        pending_count = int(pending_series.sum())
        ready_count = int(len(group) - pending_count)
        status = (
            "Pendiente de validación"
            if pending_count and not ready_count
            else "Mixto: revisar y validar"
            if pending_count and ready_count
            else "Detectado por marcación"
        )
        rows.append(
            {
                "Employee Group": str(employee_group),
                "Employee": employee,
                "Payroll ID": payroll_id,
                "Violations": int(len(group)),
                "Principal Reason Code": principal,
                "Reason Breakdown": breakdown,
                "Affected Days": int(len(dates)),
                "Affected Dates": ", ".join(value.isoformat() for value in dates),
                "Locations": ", ".join(locations),
                "Pending Validation": pending_count,
                "Ready Findings": ready_count,
                "Status": status,
            }
        )

    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    return result.sort_values(
        ["Violations", "Employee"], ascending=[False, True]
    ).reset_index(drop=True)


REVIEW_CATEGORY_LABELS = {
    "EMPLOYEE_CLASSIFICATION_UNVERIFIED": "Configuración administrativa",
    "WORKDAY_CONFIGURATION_UNVERIFIED": "Configuración administrativa",
    "DATA_INTEGRITY_BLOCKED": "Integridad global",
    "FIRST_MEAL_WAIVER_UNVERIFIED": "Waiver primer meal",
    "SECOND_MEAL_WAIVER_UNVERIFIED": "Waiver segundo meal",
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Acuerdo on-duty / paid meal",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Meal probable",
    "PUNCH_ERROR": "Revisión de punches",
    "INCOMPLETE_TIMECARD": "Revisión de punches",
    "ADJUSTED_TIMECARD_REVIEW": "Ajustes manuales",
    "INCONCLUSIVE": "Evidencia inconclusa",
    "BUSINESS_DATE_MISMATCH": "Workday / business date",
    "MULTI_LOCATION_WORKDAY_REVIEW": "Workday multi-location",
    "REGULAR_RATE_UNVERIFIED": "Regular rate",
    "EMPLOYEE_NAME_UNRESOLVED": "Identidad de empleado",
    "UNKNOWN_ORACLE_CODE": "Código Oracle",
}


def build_review_summary(reviews: pd.DataFrame) -> pd.DataFrame:
    """Summarize review records by actionable category and unique workday.

    The raw review table may contain several control codes for the same
    employee/workday. This summary avoids presenting those rows as independent
    incidents.
    """
    columns = [
        "Category",
        "Workdays",
        "Employees",
        "Review Records",
        "Codes",
    ]
    if reviews is None or reviews.empty or "Review" not in reviews.columns:
        return pd.DataFrame(columns=columns)

    source = reviews.copy()
    source["Category"] = source["Review"].astype(str).map(
        lambda code: REVIEW_CATEGORY_LABELS.get(code, "Otros controles")
    )
    date_col = (
        "Legal Workday Date"
        if "Legal Workday Date" in source.columns
        else "Business Date"
    )
    employee_col = (
        "Employee Key"
        if "Employee Key" in source.columns
        else "Payroll ID"
        if "Payroll ID" in source.columns
        else "Employee"
    )
    rows = []
    for category, group in source.groupby("Category", sort=False):
        workday_keys = group[[employee_col, date_col]].astype(str).drop_duplicates()
        rows.append(
            {
                "Category": category,
                "Workdays": int(len(workday_keys)),
                "Employees": int(group[employee_col].astype(str).nunique()),
                "Review Records": int(len(group)),
                "Codes": ", ".join(sorted(group["Review"].astype(str).unique())),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["Workdays", "Category"], ascending=[False, True]
    ).reset_index(drop=True)


def build_location_coverage_summary(
    coverage: pd.DataFrame,
    raw_timecards: pd.DataFrame,
    *,
    selected_locations: list[dict[str, str]] | None = None,
) -> pd.DataFrame:
    """Return one row per selected location, including zero-data locations."""
    columns = [
        "Location Ref",
        "Location",
        "Requested Days",
        "Responses Present",
        "Days With Timecards",
        "Timecards",
        "Status",
    ]
    selected_locations = selected_locations or []
    names = {
        str(item.get("ref") or item.get("location_ref") or ""): str(
            item.get("label") or item.get("name") or ""
        )
        for item in selected_locations
        if str(item.get("ref") or item.get("location_ref") or "").strip()
    }

    raw = raw_timecards.copy() if raw_timecards is not None else pd.DataFrame()
    if not raw.empty:
        raw_names = (
            raw[["location_ref", "location_name"]]
            .dropna()
            .drop_duplicates("location_ref")
        )
        for _, row in raw_names.iterrows():
            names.setdefault(str(row["location_ref"]), str(row["location_name"]))

    refs = set(names)
    if coverage is not None and not coverage.empty:
        refs.update(coverage["Location Ref"].astype(str))
    if not raw.empty and "location_ref" in raw.columns:
        refs.update(raw["location_ref"].astype(str))

    rows = []
    for ref in sorted(refs):
        loc_cov = (
            coverage[coverage["Location Ref"].astype(str) == ref]
            if coverage is not None and not coverage.empty
            else pd.DataFrame()
        )
        loc_raw = (
            raw[raw["location_ref"].astype(str) == ref]
            if not raw.empty and "location_ref" in raw.columns
            else pd.DataFrame()
        )
        requested = int(len(loc_cov))
        present = int(
            loc_cov.get("Response Present", pd.Series(dtype=bool))
            .fillna(False)
            .astype(bool)
            .sum()
        ) if not loc_cov.empty else 0
        days_with_cards = int(
            (
                pd.to_numeric(
                    loc_cov.get("Timecards Returned", pd.Series(dtype=float)),
                    errors="coerce",
                ).fillna(0)
                > 0
            ).sum()
        ) if not loc_cov.empty else 0
        timecard_id_col = (
            "source_timecard_id"
            if "source_timecard_id" in loc_raw.columns
            else "timecard_id"
        )
        timecards = int(loc_raw[timecard_id_col].nunique()) if not loc_raw.empty else 0

        if requested and present == requested and timecards > 0:
            status = "Data returned"
        elif requested and present == requested:
            status = "Valid responses — zero timecards"
        elif present > 0:
            status = "Partial API coverage"
        else:
            status = "No API response captured"

        rows.append(
            {
                "Location Ref": ref,
                "Location": names.get(ref, ref),
                "Requested Days": requested,
                "Responses Present": present,
                "Days With Timecards": days_with_cards,
                "Timecards": timecards,
                "Status": status,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_probable_meal_queue(
    workdays: pd.DataFrame,
    meals: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "Location",
        "Legal Workday Date",
        "Employee",
        "Payroll ID",
        "Worked Hours",
        "Probable Meals",
        "Longest Probable Gap",
        "Action",
    ]
    if workdays is None or workdays.empty:
        return pd.DataFrame(columns=columns)

    source = workdays.copy()
    probable_count = pd.to_numeric(
        source.get("Probable Meals", pd.Series(0, index=source.index)),
        errors="coerce",
    ).fillna(0)
    source = source[probable_count > 0].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)

    longest: dict[tuple[str, str], float] = {}
    if meals is not None and not meals.empty:
        probable = meals[
            ~meals.get(
                "Confirmed by Punch", pd.Series(False, index=meals.index)
            ).fillna(False).astype(bool)
            & ~meals.get("Paid", pd.Series(False, index=meals.index))
            .fillna(False)
            .astype(bool)
        ].copy()
        date_col = (
            "Legal Workday Date"
            if "Legal Workday Date" in probable.columns
            else "Business Date"
        )
        for (employee_key, workday_date), group in probable.groupby(
            ["Employee Key", date_col], dropna=False
        ):
            longest[(str(employee_key), str(workday_date))] = float(
                pd.to_numeric(group["Duration Minutes"], errors="coerce")
                .fillna(0)
                .max()
            )

    date_col = (
        "Legal Workday Date"
        if "Legal Workday Date" in source.columns
        else "Business Date"
    )
    source["Longest Probable Gap"] = [
        round(longest.get((str(row.get("Employee Key", "")), str(row.get(date_col, ""))), 0.0), 1)
        for _, row in source.iterrows()
    ]
    result = pd.DataFrame(
        {
            "Location": source.get("Location", ""),
            "Legal Workday Date": source.get(date_col, ""),
            "Employee": source.get("Employee", ""),
            "Payroll ID": source.get("Payroll ID", ""),
            "Worked Hours": source.get("Worked Hours", 0),
            "Probable Meals": source.get("Probable Meals", 0),
            "Longest Probable Gap": source["Longest Probable Gap"],
            "Action": "Confirm the gap was a duty-free meal with employee/supervisor evidence.",
        }
    )
    return result[columns].sort_values(
        ["Legal Workday Date", "Employee"], ascending=[False, True]
    ).reset_index(drop=True)


def build_second_meal_review_queue(
    workdays: pd.DataFrame,
    reviews: pd.DataFrame,
    candidates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "Location",
        "Legal Workday Date",
        "Employee",
        "Payroll ID",
        "Worked Hours",
        "Confirmed Meals",
        "Second Meal Status",
        "Action",
    ]
    if workdays is None or workdays.empty:
        return pd.DataFrame(columns=columns)

    second_codes = {
        "SECOND_MEAL_WAIVER_UNVERIFIED",
        "SECOND_MEAL_MISSING",
        "SECOND_MEAL_LATE",
        "SECOND_MEAL_SHORT",
    }
    keys: set[tuple[str, str]] = set()
    status_by_key: dict[tuple[str, str], set[str]] = {}

    for frame, code_col in (
        (reviews, "Review"),
        (candidates if candidates is not None else pd.DataFrame(), "Candidate Violation"),
    ):
        if frame is None or frame.empty or code_col not in frame.columns:
            continue
        date_col = (
            "Legal Workday Date"
            if "Legal Workday Date" in frame.columns
            else "Business Date"
        )
        filtered = frame[frame[code_col].astype(str).isin(second_codes)]
        for _, row in filtered.iterrows():
            key = (str(row.get("Employee Key", "")), str(row.get(date_col, "")))
            keys.add(key)
            status_by_key.setdefault(key, set()).add(str(row.get(code_col)))

    if not keys:
        return pd.DataFrame(columns=columns)

    date_col = (
        "Legal Workday Date"
        if "Legal Workday Date" in workdays.columns
        else "Business Date"
    )
    rows = []
    for _, row in workdays.iterrows():
        key = (str(row.get("Employee Key", "")), str(row.get(date_col, "")))
        if key not in keys:
            continue
        rows.append(
            {
                "Location": row.get("Location", ""),
                "Legal Workday Date": row.get(date_col, ""),
                "Employee": row.get("Employee", ""),
                "Payroll ID": row.get("Payroll ID", ""),
                "Worked Hours": row.get("Worked Hours", 0),
                "Confirmed Meals": row.get("Confirmed Meals", 0),
                "Second Meal Status": ", ".join(sorted(status_by_key.get(key, set()))),
                "Action": "Validate the second-meal waiver or confirm a second duty-free meal.",
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["Legal Workday Date", "Employee"], ascending=[False, True]
    ).reset_index(drop=True)
