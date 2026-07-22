from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

import pandas as pd


class ExcelImportError(ValueError):
    """Raised when the fallback workbook cannot be converted safely."""


@dataclass(frozen=True)
class ExcelImportResult:
    timecard_payloads: list[dict[str, Any]]
    employee_payloads: list[dict[str, Any]]
    job_payloads: list[dict[str, Any]]
    diagnostics: dict[str, Any]


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "location": (
        "location", "location name", "location ref", "location code", "store", "store name",
        "store number", "restaurant", "unit", "site", "sucursal", "ubicacion", "ubicación",
    ),
    "business_date": (
        "business date", "work date", "date", "shift date", "fecha", "fecha negocio",
        "fecha de negocio", "día", "dia",
    ),
    "employee_name": (
        "employee", "employee name", "name", "full name", "team member", "worker",
        "empleado", "nombre empleado", "nombre del empleado",
    ),
    "payroll_id": (
        "payroll id", "payroll", "employee id", "employee number", "employee no", "emp id",
        "emp number", "badge", "id empleado", "numero empleado", "número empleado",
    ),
    "clock_in": (
        "clock in", "clock-in", "clockin", "in", "time in", "punch in", "start time",
        "shift start", "entrada", "hora entrada", "hora de entrada",
    ),
    "clock_out": (
        "clock out", "clock-out", "clockout", "out", "time out", "punch out", "end time",
        "shift end", "salida", "hora salida", "hora de salida",
    ),
    "meal_start": (
        "meal start", "meal out", "meal clock out", "lunch out", "break out", "break start",
        "meal 1 start", "first meal start", "inicio meal", "salida comida", "inicio comida",
    ),
    "meal_end": (
        "meal end", "meal in", "meal clock in", "lunch in", "break in", "break end",
        "meal 1 end", "first meal end", "fin meal", "regreso comida", "fin comida",
    ),
    "second_meal_start": (
        "second meal start", "meal 2 start", "second meal out", "meal 2 out",
        "inicio segundo meal", "inicio segunda comida",
    ),
    "second_meal_end": (
        "second meal end", "meal 2 end", "second meal in", "meal 2 in",
        "fin segundo meal", "fin segunda comida",
    ),
    "job_code": (
        "job", "job code", "job name", "position", "role", "department", "puesto", "rol",
    ),
    "pay_rate": (
        "pay rate", "rate", "hourly rate", "base rate", "wage", "tarifa", "sueldo hora",
    ),
    "regular_hours": (
        "regular hours", "reg hours", "hours", "worked hours", "total hours", "horas",
        "horas regulares", "horas trabajadas",
    ),
    "clock_out_status": (
        "clock out status", "out status", "status", "punch status", "estado salida",
    ),
    "shift_type": (
        "shift type", "segment type", "time type", "tipo turno", "tipo segmento",
    ),
}


TIME_CARD_DETAIL_HEADERS = {
    "name",
    "payroll id",
    "clock in date and time",
    "clock out date and time",
    "clock out status",
    "regular hours",
}


def _is_blank(value: Any) -> bool:
    return value is None or pd.isna(value) or str(value).strip() in {"", "-"}


def _excel_datetime(value: Any) -> pd.Timestamp | None:
    if _is_blank(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if 20_000 <= numeric <= 100_000:
            parsed = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
        else:
            parsed = pd.to_datetime(value, errors="coerce")
    else:
        parsed = pd.to_datetime(str(value).strip(), errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed)


def _find_time_card_detail_header(raw: pd.DataFrame) -> int | None:
    for index in range(min(len(raw), 40)):
        values = {normalize_header(value) for value in raw.iloc[index].tolist() if not _is_blank(value)}
        if TIME_CARD_DETAIL_HEADERS.issubset(values):
            return index
    return None


def _report_metadata(raw: pd.DataFrame, header_index: int) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for index in range(header_index):
        row = raw.iloc[index].tolist()
        if not row:
            continue
        key = normalize_header(row[0])
        value = "" if len(row) < 2 or _is_blank(row[1]) else str(row[1]).strip()
        if key in {"locations", "location", "ubicaciones", "ubicacion"}:
            metadata["location"] = value
        elif key in {"business dates", "business date", "fechas", "fecha de negocio"}:
            metadata["business_dates"] = value
    return metadata


def _normalize_time_card_detail(raw: pd.DataFrame, header_index: int) -> pd.DataFrame:
    headers = [str(value).strip() if not _is_blank(value) else f"Unnamed {i}" for i, value in enumerate(raw.iloc[header_index].tolist())]
    data = raw.iloc[header_index + 1:].copy()
    data.columns = headers
    data = data.dropna(how="all").reset_index(drop=True)
    metadata = _report_metadata(raw, header_index)
    location = metadata.get("location", "")

    rows: list[dict[str, Any]] = []
    current_employee = ""
    current_payroll = ""

    for _, source_row in data.iterrows():
        name = "" if _is_blank(source_row.get("Name")) else str(source_row.get("Name")).strip()
        if not name or normalize_header(name) == "total":
            continue

        clock_in = _excel_datetime(source_row.get("Clock in Date and Time"))
        clock_out = _excel_datetime(source_row.get("Clock Out Date and Time"))
        raw_payroll = "" if _is_blank(source_row.get("Payroll ID")) else str(source_row.get("Payroll ID")).strip()
        if raw_payroll in {"0", "0.0"}:
            raw_payroll = ""

        # Employee summary rows have no timestamps. The following detail rows contain
        # the job title in the Name column and inherit the employee identity.
        if clock_in is None and clock_out is None:
            current_employee = name
            current_payroll = raw_payroll
            continue

        employee_name = current_employee or name
        payroll_id = current_payroll or raw_payroll
        regular_hours = _to_float(source_row.get("Regular Hours"))
        regular_pay = _to_float(source_row.get("Regular Pay"))
        pay_rate = None
        if regular_hours is not None and regular_hours > 0 and regular_pay is not None:
            pay_rate = round(regular_pay / regular_hours, 4)

        rows.append(
            {
                "Location": location,
                "Business Date": clock_in.date().isoformat() if clock_in is not None else "",
                "Employee Name": employee_name,
                "Payroll ID": payroll_id,
                "Clock In": clock_in,
                "Clock Out": clock_out,
                "Clock Out Status": source_row.get("Clock Out Status"),
                "Adjustment Count": source_row.get("Adjustment Count"),
                "Regular Hours": regular_hours,
                "Regular Pay": regular_pay,
                "Overtime Hours": _to_float(source_row.get("Overtime Hours")),
                "Overtime Pay": _to_float(source_row.get("Overtime Pay")),
                "Gross Sales": _to_float(source_row.get("Gross Sales")),
                "Tips": _to_float(source_row.get("Tips")),
                "Job Code": name,
                "Pay Rate": pay_rate,
            }
        )

    frame = pd.DataFrame(rows)
    frame.attrs["source_format"] = "oracle_time_card_detail"
    frame.attrs["source_location"] = location
    frame.attrs["source_business_dates"] = metadata.get("business_dates", "")
    frame.attrs["header_row"] = header_index + 1
    return frame


def normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def suggest_mapping(columns: Iterable[Any]) -> dict[str, str | None]:
    normalized = {normalize_header(column): str(column) for column in columns}
    result: dict[str, str | None] = {}
    for field, aliases in FIELD_ALIASES.items():
        match = None
        for alias in aliases:
            candidate = normalized.get(normalize_header(alias))
            if candidate is not None:
                match = candidate
                break
        result[field] = match
    return result


def workbook_sheet_names(file_bytes: bytes, filename: str) -> list[str]:
    suffix = filename.casefold().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        return ["CSV"]
    try:
        excel = pd.ExcelFile(io.BytesIO(file_bytes))
    except Exception as exc:
        raise ExcelImportError(f"No fue posible abrir el Excel: {exc}") from exc
    return list(excel.sheet_names)


def read_workbook_sheet(file_bytes: bytes, filename: str, sheet_name: str | None = None) -> pd.DataFrame:
    suffix = filename.casefold().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if suffix == "csv":
            frame = pd.read_csv(io.BytesIO(file_bytes), dtype=object)
            frame = frame.dropna(how="all").copy()
            frame.columns = [str(column).strip() for column in frame.columns]
            frame.attrs["source_format"] = "generic"
            return frame

        raw = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=sheet_name or 0,
            header=None,
            dtype=object,
        )
        header_index = _find_time_card_detail_header(raw)
        if header_index is not None:
            return _normalize_time_card_detail(raw, header_index)

        frame = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=sheet_name or 0,
            dtype=object,
        )
    except Exception as exc:
        raise ExcelImportError(f"No fue posible leer la hoja seleccionada: {exc}") from exc
    frame = frame.dropna(how="all").copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    frame.attrs["source_format"] = "generic"
    return frame


def build_template_bytes() -> bytes:
    rows = [
        {
            "Location": "Del Mar",
            "Business Date": "2026-07-20",
            "Employee Name": "Example Employee",
            "Payroll ID": "1001",
            "Clock In": "07:00",
            "Meal Start": "12:00",
            "Meal End": "12:30",
            "Clock Out": "15:30",
            "Job Code": "Server",
            "Pay Rate": 16.90,
        },
        {
            "Location": "Mission Viejo",
            "Business Date": "2026-07-20",
            "Employee Name": "Second Example",
            "Payroll ID": "1002",
            "Clock In": "08:00",
            "Clock Out": "13:30",
            "Job Code": "Cook",
            "Pay Rate": 20.00,
        },
    ]
    notes = pd.DataFrame(
        {
            "Instruction": [
                "Use one row per full shift, with optional Meal Start/Meal End columns.",
                "Alternatively, use one row per worked segment; multiple rows for the same employee/date are consolidated.",
                "Location may be the Oracle location name or locRef (for example BYC308).",
                "Excel is used only for selected locations that return zero timecards from Oracle for the full query range.",
                "Do not include the same location in Excel when Oracle already returns timecards; Oracle always takes priority.",
            ]
        }
    )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Timecards", index=False)
        notes.to_excel(writer, sheet_name="Instructions", index=False)
    return output.getvalue()


def _clean_identifier(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        try:
            return str(int(float(text)))
        except ValueError:
            pass
    return text


def _stable_number(value: str, minimum: int = 100_000) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return minimum + int(digest[:10], 16) % 1_900_000_000


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _combine_datetime(day: date, value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    if isinstance(value, pd.Timestamp):
        parsed = value
    elif isinstance(value, datetime):
        parsed = pd.Timestamp(value)
    elif isinstance(value, time):
        return pd.Timestamp(datetime.combine(day, value))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if 0 <= numeric < 1:
            seconds = int(round(numeric * 86400)) % 86400
            return pd.Timestamp(datetime.combine(day, time.min) + timedelta(seconds=seconds))
        parsed = pd.to_datetime(value, unit="D", origin="1899-12-30", errors="coerce")
    else:
        text = str(value).strip()
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(f"{day.isoformat()} {text}", errors="coerce")
    if pd.isna(parsed):
        return None
    parsed = pd.Timestamp(parsed)
    if parsed.year in {1899, 1900} or (parsed.date() != day and not re.search(r"\d{4}", str(value))):
        parsed = pd.Timestamp(datetime.combine(day, parsed.time()))
    return parsed


def _resolve_location(value: Any, location_labels: dict[str, str], fallback_refs: list[str]) -> str | None:
    cleaned = _clean_identifier(value)
    if not cleaned:
        return fallback_refs[0] if len(fallback_refs) == 1 else None
    normalized = normalize_header(cleaned)
    normalized_without_byc = re.sub(r"^byc\s+", "", normalized).strip()
    candidates: dict[str, str] = {}
    for ref, label in location_labels.items():
        aliases = {
            ref,
            label,
            f"{label} {ref}",
            f"{ref} {label}",
            f"BYC {label}",
            f"The Broken Yolk {label}",
        }
        for alias in aliases:
            candidates[normalize_header(alias)] = ref
    return candidates.get(normalized) or candidates.get(normalized_without_byc)


def _status_code(value: Any, *, default: int | None = 84) -> int | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return default
    text = normalize_header(value)
    names = {
        "on break": 66,
        "break": 66,
        "paid break": 80,
        "manager clock out": 77,
        "auto clock out": 85,
        "scheduled clock out": 86,
        "on time": 84,
    }
    if text in names:
        return names[text]
    try:
        return int(float(str(value)))
    except ValueError:
        return default


def _shift_type(value: Any) -> int:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return 0
    text = normalize_header(value)
    if text in {"paid break", "paid"}:
        return 1
    if text in {"unpaid break", "unpaid", "meal", "break"}:
        return 2
    try:
        number = int(float(str(value)))
        return number if number in {0, 1, 2} else 0
    except ValueError:
        return 0


def _segments(clock_in: pd.Timestamp, clock_out: pd.Timestamp | None, meal_pairs: list[tuple[pd.Timestamp | None, pd.Timestamp | None]]) -> list[tuple[pd.Timestamp, pd.Timestamp | None, int | None]]:
    if clock_out is None:
        return [(clock_in, None, None)]
    boundaries = []
    for start, end in meal_pairs:
        if start is None or end is None:
            continue
        if end <= start:
            end += pd.Timedelta(days=1)
        if clock_in < start < end < clock_out:
            boundaries.append((start, end))
    boundaries.sort(key=lambda pair: pair[0])
    segments: list[tuple[pd.Timestamp, pd.Timestamp | None, int | None]] = []
    current = clock_in
    for meal_start, meal_end in boundaries:
        if meal_start > current:
            segments.append((current, meal_start, 66))
        current = meal_end
    if current < clock_out:
        segments.append((current, clock_out, 84))
    return segments or [(clock_in, clock_out, 84)]


def convert_excel_to_payloads(
    frame: pd.DataFrame,
    *,
    mapping: dict[str, str | None],
    location_labels: dict[str, str],
    fallback_refs: list[str],
    start_date: date,
    end_date: date,
    source_name: str,
) -> ExcelImportResult:
    required = ["business_date", "clock_in", "clock_out"]
    missing = [field for field in required if not mapping.get(field)]
    if missing:
        raise ExcelImportError("Faltan columnas requeridas: " + ", ".join(missing))
    if not mapping.get("employee_name") and not mapping.get("payroll_id"):
        raise ExcelImportError("Selecciona Employee Name o Payroll ID.")
    if len(fallback_refs) > 1 and not mapping.get("location"):
        raise ExcelImportError("El Excel debe incluir Location cuando se usan varias sucursales de fallback.")

    cards_by_key: dict[tuple[str, date], list[dict[str, Any]]] = {}
    employees_by_location: dict[str, dict[str, dict[str, Any]]] = {ref: {} for ref in fallback_refs}
    jobs_by_location: dict[str, dict[int, dict[str, Any]]] = {ref: {} for ref in fallback_refs}
    skipped: list[dict[str, Any]] = []
    used_rows = 0
    generated_segments = 0

    for excel_index, row in frame.iterrows():
        row_number = int(excel_index) + 2 if isinstance(excel_index, int) else str(excel_index)
        loc_ref = _resolve_location(row.get(mapping.get("location")) if mapping.get("location") else None, location_labels, fallback_refs)
        if loc_ref not in fallback_refs:
            skipped.append({"row": row_number, "reason": "Location no coincide con una sucursal de fallback"})
            continue
        business_date = _parse_date(row.get(mapping["business_date"]))
        if business_date is None:
            skipped.append({"row": row_number, "reason": "Business Date inválida"})
            continue
        if business_date < start_date or business_date > end_date:
            continue
        clock_in = _combine_datetime(business_date, row.get(mapping["clock_in"]))
        clock_out = _combine_datetime(business_date, row.get(mapping["clock_out"]))
        if clock_in is None:
            skipped.append({"row": row_number, "reason": "Clock In inválido"})
            continue
        if clock_out is not None and clock_out < clock_in:
            clock_out += pd.Timedelta(days=1)

        payroll_id = _clean_identifier(row.get(mapping.get("payroll_id"))) if mapping.get("payroll_id") else ""
        employee_name = str(row.get(mapping.get("employee_name")) or "").strip() if mapping.get("employee_name") else ""
        employee_key = payroll_id or normalize_header(employee_name)
        if not employee_key:
            skipped.append({"row": row_number, "reason": "Empleado no identificado"})
            continue
        employee_name = employee_name or f"Empleado {payroll_id}"
        emp_num = _stable_number(f"EMP|{employee_key}")
        job_name = str(row.get(mapping.get("job_code")) or "Excel import").strip() if mapping.get("job_code") else "Excel import"
        job_num = _stable_number(f"JOB|{loc_ref}|{job_name}", minimum=10_000)
        pay_rate = _to_float(row.get(mapping.get("pay_rate"))) if mapping.get("pay_rate") else None
        regular_hours = _to_float(row.get(mapping.get("regular_hours"))) if mapping.get("regular_hours") else None
        out_status = _status_code(row.get(mapping.get("clock_out_status")), default=84) if mapping.get("clock_out_status") else 84
        segment_shift_type = _shift_type(row.get(mapping.get("shift_type"))) if mapping.get("shift_type") else 0

        meal_pairs = []
        for start_field, end_field in (("meal_start", "meal_end"), ("second_meal_start", "second_meal_end")):
            meal_start = _combine_datetime(business_date, row.get(mapping.get(start_field))) if mapping.get(start_field) else None
            meal_end = _combine_datetime(business_date, row.get(mapping.get(end_field))) if mapping.get(end_field) else None
            meal_pairs.append((meal_start, meal_end))
        row_segments = _segments(clock_in, clock_out, meal_pairs)

        employees_by_location[loc_ref][employee_key] = {
            "num": emp_num,
            "employeeId": payroll_id or employee_key,
            "payrollId": payroll_id,
            "externalPayrollID": payroll_id,
            "name": employee_name,
            "className": "",
        }
        jobs_by_location[loc_ref][job_num] = {"num": job_num, "name": job_name}

        for segment_index, (segment_in, segment_out, generated_status) in enumerate(row_segments, start=1):
            duration_hours = None
            if segment_out is not None:
                duration_hours = max((segment_out - segment_in).total_seconds() / 3600, 0.0)
            card = {
                "tcId": f"EXCEL-{loc_ref}-{business_date.isoformat()}-{row_number}-{segment_index}",
                "empNum": emp_num,
                "payrollID": payroll_id,
                "extPayrollID": payroll_id,
                "jcNum": job_num,
                "rvcNum": "EXCEL",
                "shftType": segment_shift_type,
                "clkInLcl": segment_in.isoformat(),
                "clkOutLcl": segment_out.isoformat() if segment_out is not None else None,
                "clkInStatus": 84,
                "clkOutStatus": generated_status if len(row_segments) > 1 else (None if segment_out is None else out_status),
                "regHrs": round(duration_hours if duration_hours is not None else (regular_hours or 0.0), 4),
                "payRt": pay_rate,
                "premHrs": 0.0,
                "premPay": 0.0,
                "adjustments": [],
                "_sourceSystem": "Excel fallback",
                "_sourceFile": source_name,
                "_sourceRow": row_number,
            }
            cards_by_key.setdefault((loc_ref, business_date), []).append(card)
            generated_segments += 1
        used_rows += 1

    locations_with_rows = sorted(
        {
            loc_ref
            for (loc_ref, _business_date), cards in cards_by_key.items()
            if cards
        }
    )
    locations_without_rows = [ref for ref in fallback_refs if ref not in locations_with_rows]

    payloads: list[dict[str, Any]] = []
    current = start_date
    while current <= end_date:
        for loc_ref in locations_with_rows:
            payloads.append(
                {
                    "locRef": loc_ref,
                    "_requestedBusDt": current.isoformat(),
                    "_includeAdjustmentsRequested": False,
                    "_sourceSystem": "Excel fallback",
                    "businessDates": [
                        {
                            "busDt": current.isoformat(),
                            "timeCardDetails": cards_by_key.get((loc_ref, current), []),
                        }
                    ],
                }
            )
        current += timedelta(days=1)

    employee_payloads = [
        {"locRef": ref, "employees": list(employees_by_location.get(ref, {}).values())}
        for ref in locations_with_rows
    ]
    job_payloads = [
        {"locRef": ref, "jobCodes": list(jobs_by_location.get(ref, {}).values())}
        for ref in locations_with_rows
    ]
    diagnostics = {
        "source_file": source_name,
        "rows_read": int(len(frame)),
        "rows_used": used_rows,
        "rows_skipped": len(skipped),
        "segments_generated": generated_segments,
        "fallback_locations": fallback_refs,
        "locations_with_rows": locations_with_rows,
        "locations_without_rows": locations_without_rows,
        "skipped_examples": skipped[:20],
    }
    if used_rows == 0:
        raise ExcelImportError(
            "El archivo no produjo timecards para las ubicaciones y fechas de fallback seleccionadas."
        )
    return ExcelImportResult(payloads, employee_payloads, job_payloads, diagnostics)
