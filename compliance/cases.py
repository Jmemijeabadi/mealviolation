from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd


CASE_PREFIX = "MV"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def stable_case_id(
    *,
    employee_key: Any,
    workday_date: Any,
    violation_code: Any,
    location_ref: Any = "",
) -> str:
    """Build a deterministic, non-identifying case identifier.

    The raw employee key is hashed and is never embedded in the displayed ID.
    Re-running the same scope produces the same case ID, which lets an auditor
    reconcile reviews and snapshots without exposing payroll identifiers in the
    identifier itself.
    """
    material = "|".join(
        (
            _clean(employee_key).casefold(),
            _clean(workday_date),
            _clean(violation_code).upper(),
            _clean(location_ref).casefold(),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:14].upper()
    return f"{CASE_PREFIX}-{digest}"


def add_case_ids(
    frame: pd.DataFrame,
    *,
    code_column: str,
    date_column: str = "Legal Workday Date",
) -> pd.DataFrame:
    """Return a copy with a stable ``Case ID`` column.

    The helper tolerates legacy column names so snapshots from earlier releases
    can still be displayed.
    """
    if frame.empty:
        result = frame.copy()
        if "Case ID" not in result.columns:
            result["Case ID"] = pd.Series(dtype="string")
        return result

    result = frame.copy()
    actual_date_column = (
        date_column
        if date_column in result.columns
        else "Business Date"
        if "Business Date" in result.columns
        else date_column
    )

    result["Case ID"] = result.apply(
        lambda row: stable_case_id(
            employee_key=row.get("Employee Key") or row.get("Payroll ID") or row.get("Employee"),
            workday_date=row.get(actual_date_column),
            violation_code=row.get(code_column),
            location_ref=row.get("Location Ref") or row.get("Location"),
        ),
        axis=1,
    )
    return result
