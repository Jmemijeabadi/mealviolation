from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import pandas as pd

from oracle_bi.client import iter_timecards


CLOCK_IN_STATUS = {
    0: "None",
    65: "Early From Break",
    67: "Late From Break",
    68: "Schedule Disabled",
    69: "Early",
    76: "Late",
    78: "Not Scheduled",
    82: "Revenue Center Changed",
    84: "On Time",
}

CLOCK_OUT_STATUS = {
    0: "None",
    66: "On Break",
    68: "Schedule Disabled",
    69: "Early",
    76: "Late",
    77: "Manager Clock Out",
    78: "Not Scheduled",
    80: "Paid Break",
    82: "Revenue Center Changed",
    84: "On Time",
    85: "Auto Clock Out",
    86: "Scheduled Clock Out",
}

SHIFT_TYPE = {0: "Working", 1: "Paid Break", 2: "Unpaid Break"}


def _clean_identifier(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        try:
            return str(int(float(text)))
        except ValueError:
            pass
    return text


def _lookup_keys(prefix: str, value: Any) -> list[str]:
    cleaned = _clean_identifier(value)
    if not cleaned:
        return []
    variants = [cleaned.casefold()]
    # Oracle may serialize the same numeric identifier as 123, "123" or
    # "00123" across endpoints. Preserve the original and add a numeric form.
    if cleaned.isdigit():
        normalized_numeric = str(int(cleaned))
        if normalized_numeric not in variants:
            variants.append(normalized_numeric)
    return [f"{prefix}::{variant}" for variant in variants]


def employee_dimension_map(payload: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """Build a multi-key employee index.

    Oracle documents empNum -> employee.num, but real installations can expose
    payroll identifiers in different numeric/string forms. Indexing all stable
    identifiers prevents the UI from falling back to anonymous employee numbers
    when the object-number join is unavailable or formatted differently.
    """
    result: dict[Any, dict[str, Any]] = {}
    for employee in payload.get("employees", []) or []:
        if not isinstance(employee, dict):
            continue

        num = employee.get("num")
        try:
            if num is not None:
                result[int(num)] = employee
        except (TypeError, ValueError):
            pass

        for prefix, field in (
            ("NUM", "num"),
            ("EMPLOYEE_ID", "employeeId"),
            ("PAYROLL", "payrollId"),
            ("EXTERNAL_PAYROLL", "externalPayrollID"),
            ("UUID", "uuid"),
            ("UUID", "uuId"),
        ):
            for key in _lookup_keys(prefix, employee.get(field)):
                result.setdefault(key, employee)
    return result


def _resolve_employee(
    employees: dict[Any, dict[str, Any]],
    *,
    emp_num: int,
    card: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if emp_num >= 0 and emp_num in employees:
        return employees[emp_num], "employee.num"

    candidates = (
        ("NUM", card.get("empNum"), "employee.num (normalized)"),
        ("EMPLOYEE_ID", card.get("empNum"), "employee.employeeId"),
        ("PAYROLL", card.get("payrollID"), "employee.payrollId"),
        ("EXTERNAL_PAYROLL", card.get("extPayrollID"), "employee.externalPayrollID"),
        # Some organizations populate only one payroll field, so allow cross-match.
        ("PAYROLL", card.get("extPayrollID"), "external ID matched payrollId"),
        ("EXTERNAL_PAYROLL", card.get("payrollID"), "payrollID matched externalPayrollID"),
    )
    for prefix, value, method in candidates:
        for key in _lookup_keys(prefix, value):
            if key in employees:
                return employees[key], method
    return {}, "unresolved"


def job_code_dimension_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for job in payload.get("jobCodes", []) or []:
        if not isinstance(job, dict) or job.get("num") is None:
            continue
        try:
            key = int(job["num"])
        except (TypeError, ValueError):
            continue
        result[key] = job
    return result


def location_dimension_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for location in payload.get("locations", []) or []:
        if not isinstance(location, dict):
            continue
        loc_ref = _clean_identifier(location.get("locRef"))
        if loc_ref:
            result[loc_ref] = location
    return result


def _employee_display_name(employee: dict[str, Any], card: dict[str, Any], emp_num: int) -> str:
    first_name = str(employee.get("fName") or employee.get("firstName") or "").strip()
    last_name = str(employee.get("lName") or employee.get("lastName") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name:
        return full_name

    # Defensive fallbacks for tenants that expose a combined name property.
    for field in ("name", "employeeName", "displayName"):
        value = str(employee.get(field) or "").strip()
        if value:
            return value

    payroll = _clean_identifier(card.get("payrollID") or card.get("extPayrollID"))
    if payroll:
        return f"Empleado {payroll}"
    return f"Empleado #{emp_num}" if emp_num >= 0 else "Empleado sin identificar"


def _location_name(location: dict[str, Any], loc_ref: str) -> str:
    return str(
        location.get("name")
        or location.get("locName")
        or location.get("locationName")
        or loc_ref
    )


def normalize_timecards(
    payloads: Iterable[dict[str, Any]],
    *,
    employees: dict[Any, dict[str, Any]] | None = None,
    job_codes: dict[int, dict[str, Any]] | None = None,
    locations: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    employees = employees or {}
    job_codes = job_codes or {}
    locations = locations or {}
    rows: list[dict[str, Any]] = []

    for loc_ref, bus_dt, card in iter_timecards(payloads):
        try:
            emp_num = int(card.get("empNum"))
        except (TypeError, ValueError):
            emp_num = -1
        try:
            jc_num = int(card.get("jcNum"))
        except (TypeError, ValueError):
            jc_num = -1

        employee, employee_match_method = _resolve_employee(
            employees,
            emp_num=emp_num,
            card=card,
        )
        job = job_codes.get(jc_num, {})
        loc_ref_clean = _clean_identifier(loc_ref)
        location = locations.get(loc_ref_clean, {})

        employee_name = _employee_display_name(employee, card, emp_num)
        payroll_id = _clean_identifier(
            card.get("payrollID")
            or card.get("extPayrollID")
            or employee.get("payrollId")
            or employee.get("externalPayrollID")
        )
        employee_key = payroll_id or (f"EMP::{emp_num}" if emp_num >= 0 else "UNKNOWN")
        shift_type = int(card.get("shftType", 0) or 0)
        clock_in_status = int(card.get("clkInStatus", 0) or 0)
        clock_out_raw = card.get("clkOutStatus")
        clock_out_status = None if clock_out_raw is None else int(clock_out_raw or 0)

        adjustment_items = card.get("adjustments", []) or []
        if not isinstance(adjustment_items, list):
            adjustment_items = []

        rows.append(
            {
                "location_ref": loc_ref_clean,
                "location_name": _location_name(location, loc_ref_clean),
                "location_timezone": str(
                    location.get("tz")
                    or location.get("timeZone")
                    or location.get("timezone")
                    or ""
                ),
                "business_date": pd.to_datetime(bus_dt, errors="coerce").date()
                if bus_dt
                else pd.NaT,
                "timecard_id": _clean_identifier(card.get("tcId")),
                "employee_num": emp_num,
                "employee_key": employee_key,
                "employee_name": employee_name,
                "employee_name_resolved": bool(employee),
                "employee_match_method": employee_match_method,
                "payroll_id": payroll_id,
                "employee_class": str(employee.get("className") or ""),
                "job_code_num": jc_num,
                "job_code": str(
                    job.get("name")
                    or job.get("jobCodeName")
                    or card.get("jobCodeRef")
                    or jc_num
                ),
                "rvc_num": _clean_identifier(card.get("rvcNum")),
                "shift_num": _clean_identifier(card.get("shftNum")),
                "shift_type": shift_type,
                "shift_type_label": SHIFT_TYPE.get(shift_type, f"Unknown ({shift_type})"),
                "clock_in_local": pd.to_datetime(card.get("clkInLcl"), errors="coerce"),
                "clock_out_local": pd.to_datetime(card.get("clkOutLcl"), errors="coerce"),
                "clock_in_utc": pd.to_datetime(card.get("clkInUTC"), errors="coerce", utc=True),
                "clock_out_utc": pd.to_datetime(card.get("clkOutUTC"), errors="coerce", utc=True),
                "clock_in_status": clock_in_status,
                "clock_in_status_label": CLOCK_IN_STATUS.get(
                    clock_in_status, f"Unknown ({clock_in_status})"
                ),
                "clock_out_status": clock_out_status,
                "clock_out_status_label": "Still Clocked In"
                if clock_out_status is None
                else CLOCK_OUT_STATUS.get(clock_out_status, f"Unknown ({clock_out_status})"),
                "regular_hours": float(card.get("regHrs") or 0.0),
                "overtime_hours": sum(float(card.get(f"ovt{i}Hrs") or 0.0) for i in range(1, 5)),
                "pay_rate": float(card.get("payRt") or 0.0) or None,
                "premium_hours": float(card.get("premHrs") or 0.0),
                "premium_pay": float(card.get("premPay") or 0.0),
                "adjustment_count": len(adjustment_items),
                "adjustments": adjustment_items,
                "added_utc": pd.to_datetime(card.get("addedUTC"), errors="coerce", utc=True),
                "last_updated_utc": pd.to_datetime(
                    card.get("lastUpdatedUTC"), errors="coerce", utc=True
                ),
                "raw": card,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    datetime_columns = [
        "clock_in_local",
        "clock_out_local",
        "clock_in_utc",
        "clock_out_utc",
        "added_utc",
        "last_updated_utc",
    ]
    for column in datetime_columns:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    return df.sort_values(
        ["location_ref", "employee_key", "business_date", "clock_in_local", "timecard_id"],
        na_position="last",
    ).reset_index(drop=True)


def load_waiver_csv(file_obj: Any) -> pd.DataFrame:
    if file_obj is None:
        return pd.DataFrame()
    df = pd.read_csv(file_obj, dtype=str)
    normalized = {str(column).strip().casefold(): column for column in df.columns}
    required = {"employee_key", "first_meal_waiver", "second_meal_waiver", "on_duty_meal_agreement"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError("Waiver CSV is missing columns: " + ", ".join(sorted(missing)))

    rename = {normalized[name]: name for name in required}
    for optional in ("effective_date", "expiration_date"):
        if optional in normalized:
            rename[normalized[optional]] = optional
    df = df.rename(columns=rename)
    return df


def waiver_rows_to_records(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if df.empty:
        return {}

    def as_bool(value: Any) -> bool:
        return str(value).strip().casefold() in {"1", "true", "yes", "y", "si", "sí"}

    records: dict[str, list[dict[str, Any]]] = {}
    for _, row in df.iterrows():
        key = str(row.get("employee_key") or "").strip()
        if not key:
            continue
        record = {
            "employee_key": key,
            "first_meal_waiver": as_bool(row.get("first_meal_waiver")),
            "second_meal_waiver": as_bool(row.get("second_meal_waiver")),
            "on_duty_meal_agreement": as_bool(row.get("on_duty_meal_agreement")),
            "effective_date": pd.to_datetime(row.get("effective_date"), errors="coerce").date()
            if row.get("effective_date")
            else None,
            "expiration_date": pd.to_datetime(row.get("expiration_date"), errors="coerce").date()
            if row.get("expiration_date")
            else None,
        }
        records.setdefault(key, []).append(record)
    return records
