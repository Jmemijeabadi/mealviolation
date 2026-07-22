from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from compliance.models import EmployeePolicyRecord, RegularRateRecord, WorkdayConfigRecord
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
    if cleaned.isdigit():
        normalized_numeric = str(int(cleaned))
        if normalized_numeric not in variants:
            variants.append(normalized_numeric)
    return [f"{prefix}::{variant}" for variant in variants]


def _payload_list(payload_or_payloads: Any) -> list[dict[str, Any]]:
    if isinstance(payload_or_payloads, dict):
        return [payload_or_payloads]
    if isinstance(payload_or_payloads, Iterable):
        return [item for item in payload_or_payloads if isinstance(item, dict)]
    return []


def employee_dimension_map(payload_or_payloads: Any) -> dict[Any, dict[str, Any]]:
    """Build a multi-key enterprise employee index from one or many locations."""
    result: dict[Any, dict[str, Any]] = {}
    for payload in _payload_list(payload_or_payloads):
        for employee in payload.get("employees", []) or []:
            if not isinstance(employee, dict):
                continue
            num = employee.get("num")
            try:
                if num is not None:
                    result.setdefault(int(num), employee)
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
    employees: dict[Any, dict[str, Any]], *, emp_num: int, card: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    if emp_num >= 0 and emp_num in employees:
        return employees[emp_num], "employee.num"
    candidates = (
        ("NUM", card.get("empNum"), "employee.num (normalized)"),
        ("EMPLOYEE_ID", card.get("empNum"), "empNum matched employee.employeeId"),
        ("PAYROLL", card.get("payrollID"), "employee.payrollId"),
        ("EXTERNAL_PAYROLL", card.get("extPayrollID"), "employee.externalPayrollID"),
        ("PAYROLL", card.get("extPayrollID"), "external ID matched payrollId"),
        ("EXTERNAL_PAYROLL", card.get("payrollID"), "payrollID matched externalPayrollID"),
        ("EMPLOYEE_ID", card.get("payrollID"), "payrollID matched employeeId"),
        ("EMPLOYEE_ID", card.get("extPayrollID"), "external payroll ID matched employeeId"),
        ("PAYROLL", card.get("empNum"), "empNum matched payrollId"),
        ("EXTERNAL_PAYROLL", card.get("empNum"), "empNum matched externalPayrollID"),
    )
    for prefix, value, method in candidates:
        for key in _lookup_keys(prefix, value):
            if key in employees:
                return employees[key], method
    return {}, "unresolved"


def job_code_dimension_map(payload_or_payloads: Any) -> dict[Any, dict[str, Any]]:
    """Index job codes globally and, when possible, by location."""
    result: dict[Any, dict[str, Any]] = {}
    for payload in _payload_list(payload_or_payloads):
        loc_ref = _clean_identifier(payload.get("locRef"))
        for job in payload.get("jobCodes", []) or []:
            if not isinstance(job, dict) or job.get("num") is None:
                continue
            try:
                number = int(job["num"])
            except (TypeError, ValueError):
                continue
            result.setdefault(number, job)
            if loc_ref:
                result[f"{loc_ref}::{number}"] = job
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
    for field in ("name", "employeeName", "displayName"):
        value = str(employee.get(field) or "").strip()
        if value:
            return value
    payroll = _clean_identifier(card.get("payrollID") or card.get("extPayrollID"))
    if payroll:
        return f"Empleado {payroll}"
    return f"Empleado #{emp_num}" if emp_num >= 0 else "Empleado sin identificar"


def _location_name(location: dict[str, Any], loc_ref: str) -> str:
    return str(location.get("name") or location.get("locName") or location.get("locationName") or loc_ref)


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_timecards(
    payloads: Iterable[dict[str, Any]],
    *,
    employees: dict[Any, dict[str, Any]] | None = None,
    job_codes: dict[Any, dict[str, Any]] | None = None,
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

        employee, employee_match_method = _resolve_employee(employees, emp_num=emp_num, card=card)
        loc_ref_clean = _clean_identifier(loc_ref)
        job = job_codes.get(f"{loc_ref_clean}::{jc_num}") or job_codes.get(jc_num, {})
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
                    location.get("tz") or location.get("timeZone") or location.get("timezone") or ""
                ),
                "business_date": pd.to_datetime(bus_dt, errors="coerce").date() if bus_dt else pd.NaT,
                "timecard_id": _clean_identifier(card.get("tcId")),
                "employee_num": emp_num,
                "employee_key": employee_key,
                "employee_name": employee_name,
                "employee_name_resolved": bool(employee),
                "employee_match_method": employee_match_method,
                "payroll_id": payroll_id,
                "oracle_employee_class": str(employee.get("className") or ""),
                "job_code_num": jc_num,
                "job_code": str(job.get("name") or job.get("jobCodeName") or card.get("jobCodeRef") or jc_num),
                "rvc_num": _clean_identifier(card.get("rvcNum")),
                "shift_num": _clean_identifier(card.get("shftNum")),
                "shift_type": shift_type,
                "shift_type_label": SHIFT_TYPE.get(shift_type, f"Unknown ({shift_type})"),
                "clock_in_local": pd.to_datetime(card.get("clkInLcl"), errors="coerce"),
                "clock_out_local": pd.to_datetime(card.get("clkOutLcl"), errors="coerce"),
                "clock_in_utc": pd.to_datetime(card.get("clkInUTC"), errors="coerce", utc=True),
                "clock_out_utc": pd.to_datetime(card.get("clkOutUTC"), errors="coerce", utc=True),
                "clock_in_status": clock_in_status,
                "clock_in_status_label": CLOCK_IN_STATUS.get(clock_in_status, f"Unknown ({clock_in_status})"),
                "clock_out_status": clock_out_status,
                "clock_out_status_label": "Still Clocked In"
                if clock_out_status is None
                else CLOCK_OUT_STATUS.get(clock_out_status, f"Unknown ({clock_out_status})"),
                "regular_hours": _float(card.get("regHrs")),
                "overtime_hours": sum(_float(card.get(f"ovt{i}Hrs")) for i in range(1, 5)),
                "pay_rate": _float(card.get("payRt")) or None,
                "premium_hours": _float(card.get("premHrs")),
                "premium_pay": _float(card.get("premPay")),
                "adjustment_count": len(adjustment_items),
                "adjustments_field_present": "adjustments" in card,
                "adjustments_request_verified": card.get("_adjustmentsRequested") is True,
                "adjustments": adjustment_items,
                "added_utc": pd.to_datetime(card.get("addedUTC"), errors="coerce", utc=True),
                "last_updated_utc": pd.to_datetime(card.get("lastUpdatedUTC"), errors="coerce", utc=True),
                "raw": card,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for column in (
        "clock_in_local",
        "clock_out_local",
        "clock_in_utc",
        "clock_out_utc",
        "added_utc",
        "last_updated_utc",
    ):
        df[column] = pd.to_datetime(df[column], errors="coerce")
    return df.sort_values(
        ["location_ref", "employee_key", "business_date", "clock_in_local", "timecard_id"],
        na_position="last",
    ).reset_index(drop=True)


def _parse_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "si", "sí", "x"}


def _parse_date(value: Any) -> date | None:
    if value is None or not str(value).strip():
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _read_csv(file_obj: Any) -> pd.DataFrame:
    if file_obj is None:
        return pd.DataFrame()
    if isinstance(file_obj, pd.DataFrame):
        return file_obj.copy().fillna("")
    return pd.read_csv(file_obj, dtype=str).fillna("")


def load_employee_policy_csv(file_obj: Any) -> pd.DataFrame:
    df = _read_csv(file_obj)
    if df.empty:
        return df
    normalized = {str(column).strip().casefold(): column for column in df.columns}
    required = {"employee_key", "classification"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError("Employee policy CSV is missing columns: " + ", ".join(sorted(missing)))
    canonical = [
        "employee_key",
        "classification",
        "first_meal_waiver",
        "second_meal_waiver",
        "on_duty_meal_agreement",
        "effective_date",
        "expiration_date",
        "document_reference",
        "verified_by",
        "notes",
    ]
    rename = {normalized[name]: name for name in canonical if name in normalized}
    return df.rename(columns=rename)


def policy_rows_to_records(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if df.empty:
        return {}
    records: dict[str, list[dict[str, Any]]] = {}
    for _, row in df.iterrows():
        key = _clean_identifier(row.get("employee_key"))
        if not key:
            continue
        record = EmployeePolicyRecord(
            employee_key=key,
            classification=str(row.get("classification") or "UNKNOWN"),
            first_meal_waiver=_parse_bool(row.get("first_meal_waiver")),
            second_meal_waiver=_parse_bool(row.get("second_meal_waiver")),
            on_duty_meal_agreement=_parse_bool(row.get("on_duty_meal_agreement")),
            effective_date=_parse_date(row.get("effective_date")),
            expiration_date=_parse_date(row.get("expiration_date")),
            document_reference=str(row.get("document_reference") or "").strip(),
            verified_by=str(row.get("verified_by") or "").strip(),
            notes=str(row.get("notes") or "").strip(),
        )
        records.setdefault(key, []).append(record.__dict__)
    return records


def load_waiver_csv(file_obj: Any) -> pd.DataFrame:
    """Backwards-compatible waiver loader; classification defaults to UNKNOWN."""
    df = _read_csv(file_obj)
    if df.empty:
        return df
    if "classification" not in [str(c).strip().casefold() for c in df.columns]:
        df["classification"] = "UNKNOWN"
    return load_employee_policy_csv(df)


def waiver_rows_to_records(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    return policy_rows_to_records(df)


def _parse_time(value: Any) -> time:
    text = str(value or "00:00").strip()
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Invalid workday_start '{text}'. Use HH:MM in 24-hour time.") from exc


def load_workday_config_csv(file_obj: Any) -> pd.DataFrame:
    df = _read_csv(file_obj)
    if df.empty:
        return df
    normalized = {str(column).strip().casefold(): column for column in df.columns}
    required = {"location_ref", "workday_start", "timezone"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError("Workday configuration CSV is missing columns: " + ", ".join(sorted(missing)))
    canonical = ["location_ref", "workday_start", "timezone", "effective_date", "expiration_date", "verified_by", "source"]
    return df.rename(columns={normalized[name]: name for name in canonical if name in normalized})


def workday_rows_to_records(df: pd.DataFrame) -> dict[str, list[WorkdayConfigRecord]]:
    records: dict[str, list[WorkdayConfigRecord]] = {}
    if df.empty:
        return records
    for _, row in df.iterrows():
        loc_ref = _clean_identifier(row.get("location_ref"))
        if not loc_ref:
            continue
        timezone_name = str(row.get("timezone") or "America/Los_Angeles").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid IANA timezone '{timezone_name}' for location {loc_ref}.") from exc
        record = WorkdayConfigRecord(
            location_ref=loc_ref,
            workday_start=_parse_time(row.get("workday_start")),
            timezone=timezone_name,
            effective_date=_parse_date(row.get("effective_date")),
            expiration_date=_parse_date(row.get("expiration_date")),
            verified_by=str(row.get("verified_by") or "").strip(),
            source=str(row.get("source") or "").strip(),
        )
        records.setdefault(loc_ref, []).append(record)
    return records


def load_regular_rate_csv(file_obj: Any) -> pd.DataFrame:
    df = _read_csv(file_obj)
    if df.empty:
        return df
    normalized = {str(column).strip().casefold(): column for column in df.columns}
    required = {"employee_key", "regular_rate"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError("Regular-rate CSV is missing columns: " + ", ".join(sorted(missing)))
    canonical = ["employee_key", "regular_rate", "effective_date", "expiration_date", "source", "verified_by"]
    return df.rename(columns={normalized[name]: name for name in canonical if name in normalized})


def regular_rate_rows_to_records(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    if df.empty:
        return records
    for _, row in df.iterrows():
        key = _clean_identifier(row.get("employee_key"))
        try:
            rate = float(row.get("regular_rate"))
        except (TypeError, ValueError):
            continue
        if not key or rate <= 0:
            continue
        record = RegularRateRecord(
            employee_key=key,
            regular_rate=rate,
            effective_date=_parse_date(row.get("effective_date")),
            expiration_date=_parse_date(row.get("expiration_date")),
            source=str(row.get("source") or "").strip(),
            verified_by=str(row.get("verified_by") or "").strip(),
        )
        records.setdefault(key, []).append(record.__dict__)
    return records


def load_control_totals_csv(file_obj: Any) -> pd.DataFrame:
    df = _read_csv(file_obj)
    if df.empty:
        return df
    normalized = {str(column).strip().casefold(): column for column in df.columns}
    required = {"location_ref", "business_date", "timecards", "employees", "worked_hours"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError("MICROS control totals CSV is missing columns: " + ", ".join(sorted(missing)))
    canonical = ["location_ref", "business_date", "timecards", "employees", "worked_hours", "adjusted_timecards"]
    result = df.rename(columns={normalized[name]: name for name in canonical if name in normalized})
    result["business_date"] = pd.to_datetime(result["business_date"], errors="coerce").dt.date
    for column in ("timecards", "employees", "worked_hours", "adjusted_timecards"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _active_workday_config(
    records: dict[str, list[WorkdayConfigRecord]], loc_ref: str, calendar_date: date
) -> WorkdayConfigRecord | None:
    active = [record for record in records.get(loc_ref, []) if record.active_on(calendar_date)]
    if not active:
        return None
    return max(active, key=lambda record: record.effective_date or date.min)


def _legal_workday_date(moment: pd.Timestamp, workday_start: time) -> date:
    boundary = pd.Timestamp(datetime.combine(moment.date(), workday_start))
    return (moment - pd.Timedelta(days=1)).date() if moment < boundary else moment.date()


def _next_boundary(moment: pd.Timestamp, workday_start: time) -> pd.Timestamp:
    current_date = _legal_workday_date(moment, workday_start)
    return pd.Timestamp(datetime.combine(current_date + timedelta(days=1), workday_start))


def _local_to_utc(
    moment: pd.Timestamp,
    timezone_name: str,
    *,
    range_start: pd.Timestamp | None = None,
    range_end: pd.Timestamp | None = None,
) -> pd.Timestamp:
    """Convert a naive store-local timestamp to UTC, handling DST folds/gaps.

    For an ambiguous fall-back time, choose the candidate that falls inside the
    original Oracle UTC interval when available. For a nonexistent spring-forward
    time, pandas shifts to the first valid local instant.
    """
    if pd.isna(moment):
        return pd.NaT
    value = pd.Timestamp(moment)
    if value.tzinfo is not None:
        return value.tz_convert("UTC")
    candidates: list[pd.Timestamp] = []
    for ambiguous in (True, False):
        try:
            candidate = value.tz_localize(
                timezone_name, ambiguous=ambiguous, nonexistent="shift_forward"
            ).tz_convert("UTC")
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        return pd.NaT
    start_utc = pd.to_datetime(range_start, errors="coerce", utc=True)
    end_utc = pd.to_datetime(range_end, errors="coerce", utc=True)
    in_range = [
        candidate
        for candidate in candidates
        if (pd.isna(start_utc) or candidate >= start_utc)
        and (pd.isna(end_utc) or candidate <= end_utc)
    ]
    return (in_range or candidates)[0]


def assign_legal_workdays(
    timecards: pd.DataFrame,
    *,
    workday_configs: dict[str, list[WorkdayConfigRecord]] | None = None,
    default_workday_start: str = "00:00",
    default_timezone: str = "America/Los_Angeles",
) -> pd.DataFrame:
    """Assign and split timecards into fixed 24-hour legal workdays.

    Rows crossing a workday boundary are split proportionally. The original Oracle
    timecard identifier is retained and only the first segment carries adjustments,
    preventing duplicate adjustment audit rows.
    """
    if timecards.empty:
        return timecards.copy()
    configs = workday_configs or {}
    default_start = _parse_time(default_workday_start)
    output: list[dict[str, Any]] = []

    for _, source in timecards.iterrows():
        row = source.to_dict()
        start = pd.to_datetime(row.get("clock_in_local"), errors="coerce")
        end = pd.to_datetime(row.get("clock_out_local"), errors="coerce")
        original_start_utc = pd.to_datetime(row.get("clock_in_utc"), errors="coerce", utc=True)
        original_end_utc = pd.to_datetime(row.get("clock_out_utc"), errors="coerce", utc=True)
        loc_ref = _clean_identifier(row.get("location_ref"))
        reference_date = start.date() if pd.notna(start) else row.get("business_date")
        if not isinstance(reference_date, date):
            reference_date = date.today()
        config = _active_workday_config(configs, loc_ref, reference_date)
        workday_start = config.workday_start if config else default_start
        timezone = config.timezone if config else (str(row.get("location_timezone") or "") or default_timezone)
        verified = config is not None and config.is_verified

        if pd.isna(start):
            segment = dict(row)
            segment.update(
                {
                    "legal_workday_date": row.get("business_date"),
                    "workday_start": workday_start.strftime("%H:%M"),
                    "workday_timezone": timezone,
                    "workday_config_verified": verified,
                    "business_date_match": False,
                    "segment_index": 1,
                    "segment_count": 1,
                    "is_primary_segment": True,
                    "source_timecard_id": row.get("timecard_id"),
                    "calculation_clock_in": pd.NaT,
                    "calculation_clock_out": pd.NaT,
                    "utc_duration_adjustment_minutes": 0.0,
                }
            )
            output.append(segment)
            continue

        if pd.isna(end) or end <= start:
            windows = [(start, end)]
        else:
            windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            cursor = start
            while cursor < end:
                boundary = _next_boundary(cursor, workday_start)
                segment_end = min(end, boundary)
                windows.append((cursor, segment_end))
                if segment_end >= end:
                    break
                cursor = segment_end

        total_seconds = max(0.0, (end - start).total_seconds()) if pd.notna(end) else 0.0
        for index, (segment_start, segment_end) in enumerate(windows, start=1):
            segment = dict(row)
            segment["original_clock_in_local"] = start
            segment["original_clock_out_local"] = end
            segment["clock_in_local"] = segment_start
            segment["clock_out_local"] = segment_end
            if segment_start == start and pd.notna(original_start_utc):
                calculation_start = original_start_utc
            else:
                calculation_start = _local_to_utc(
                    segment_start, timezone, range_start=original_start_utc, range_end=original_end_utc
                )
            if pd.isna(segment_end):
                calculation_end = pd.NaT
            elif segment_end == end and pd.notna(original_end_utc):
                calculation_end = original_end_utc
            else:
                calculation_end = _local_to_utc(
                    segment_end, timezone, range_start=original_start_utc, range_end=original_end_utc
                )
            segment["calculation_clock_in"] = calculation_start
            segment["calculation_clock_out"] = calculation_end
            local_minutes = (
                max(0.0, (segment_end - segment_start).total_seconds() / 60.0)
                if pd.notna(segment_end) else 0.0
            )
            actual_minutes = (
                max(0.0, (calculation_end - calculation_start).total_seconds() / 60.0)
                if pd.notna(calculation_start) and pd.notna(calculation_end) else local_minutes
            )
            segment["utc_duration_adjustment_minutes"] = round(actual_minutes - local_minutes, 2)
            segment["legal_workday_date"] = _legal_workday_date(segment_start, workday_start)
            segment["workday_start"] = workday_start.strftime("%H:%M")
            segment["workday_timezone"] = timezone
            segment["workday_config_verified"] = verified
            segment["business_date_match"] = segment["legal_workday_date"] == row.get("business_date")
            segment["segment_index"] = index
            segment["segment_count"] = len(windows)
            segment["is_primary_segment"] = index == 1
            segment["source_timecard_id"] = row.get("timecard_id")
            if len(windows) > 1:
                segment["timecard_id"] = f"{row.get('timecard_id')}::SEG{index}"
            segment_seconds = (
                max(0.0, (segment_end - segment_start).total_seconds()) if pd.notna(segment_end) else 0.0
            )
            ratio = (segment_seconds / total_seconds) if total_seconds > 0 else (1.0 if index == 1 else 0.0)
            for field in ("regular_hours", "overtime_hours"):
                segment[field] = _float(row.get(field)) * ratio
            if index > 1:
                segment["premium_hours"] = 0.0
                segment["premium_pay"] = 0.0
                segment["adjustment_count"] = 0
                segment["adjustments"] = []
            output.append(segment)

    result = pd.DataFrame(output)
    return result.sort_values(
        ["employee_key", "legal_workday_date", "clock_in_local", "location_ref", "timecard_id"],
        na_position="last",
    ).reset_index(drop=True)
