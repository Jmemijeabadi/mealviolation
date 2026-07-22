from __future__ import annotations

from typing import Any

import pandas as pd


TIME_FIELDS = ("prevClkInLcl", "prevClkOutLcl")


def _as_timestamp(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, errors="coerce")


def _identifier(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        try:
            return str(int(float(text)))
        except ValueError:
            pass
    return text


def _job_name(job_codes: dict[int, dict[str, Any]], value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _identifier(value)
    job = job_codes.get(number, {})
    return str(job.get("name") or job.get("jobCodeName") or number)


def _minutes_delta(current: Any, previous: Any) -> float | None:
    current_ts = _as_timestamp(current)
    previous_ts = _as_timestamp(previous)
    if pd.isna(current_ts) or pd.isna(previous_ts):
        return None
    return round((current_ts - previous_ts).total_seconds() / 60.0, 2)


def _duration_minutes(start: Any, end: Any) -> float | None:
    start_ts = _as_timestamp(start)
    end_ts = _as_timestamp(end)
    if pd.isna(start_ts) or pd.isna(end_ts):
        return None
    return round((end_ts - start_ts).total_seconds() / 60.0, 2)


def _changed_fields(adjustment: dict[str, Any]) -> list[str]:
    labels = {
        "prevClkInLcl": "Clock In",
        "prevClkOutLcl": "Clock Out",
        "prevJcNum": "Puesto",
        "prevRVCNum": "Revenue Center",
        "prevDrctTips": "Propinas directas",
        "prevIndirTipsPd": "Propinas indirectas",
    }
    changed: list[str] = []
    for field, label in labels.items():
        value = adjustment.get(field)
        if value not in (None, "", 0, 0.0):
            changed.append(label)
    return changed


def _impact_classification(row: pd.Series, changed: list[str]) -> tuple[str, str, str]:
    changed_time = "Clock In" in changed or "Clock Out" in changed
    is_break = int(row.get("shift_type", 0) or 0) in {1, 2} or row.get("clock_out_status") in {66, 80}

    if changed_time and is_break:
        return (
            "Alto",
            "Puede cambiar la hora o duración de un meal/break.",
            "Ajuste de meal/break",
        )
    if changed_time:
        return (
            "Alto",
            "Puede cambiar las horas trabajadas y la elegibilidad o tardanza del meal.",
            "Ajuste de horario",
        )
    if "Puesto" in changed or "Revenue Center" in changed:
        return (
            "Medio",
            "No cambia timestamps, pero debe validarse la asignación operativa del turno.",
            "Ajuste operativo",
        )
    return (
        "Bajo",
        "No se detectó un cambio temporal con impacto directo en meals.",
        "Otro ajuste",
    )


def build_adjustment_audit(
    timecards: pd.DataFrame,
    *,
    job_codes: dict[int, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Expand Oracle timecard adjustments into an audit-ready table.

    Oracle returns previous values for the fields changed by each adjustment and
    the current timecard contains the final values. For timecards with multiple
    sequential adjustments, the before/after duration comparison is therefore an
    estimate against the final record, not a reconstruction of every intermediate
    state.
    """

    columns = [
        "Location Ref",
        "Location",
        "Business Date",
        "Employee",
        "Payroll ID",
        "Employee Num",
        "Timecard ID",
        "Adjustment ID",
        "Adjustment UTC",
        "Manager",
        "Manual Adjustment",
        "Reason",
        "Changed Fields",
        "Adjustment Type",
        "Risk",
        "Meal Impact",
        "Previous Clock In",
        "Current Clock In",
        "Clock In Delta Minutes",
        "Previous Clock Out",
        "Current Clock Out",
        "Clock Out Delta Minutes",
        "Estimated Previous Duration Minutes",
        "Current Duration Minutes",
        "Estimated Duration Delta Minutes",
        "Previous Job",
        "Current Job",
        "Previous Revenue Center",
        "Current Revenue Center",
        "Last Updated UTC",
    ]
    if timecards.empty or "adjustments" not in timecards.columns:
        return pd.DataFrame(columns=columns)

    job_codes = job_codes or {}
    rows: list[dict[str, Any]] = []

    for _, card in timecards.iterrows():
        adjustments = card.get("adjustments") or []
        if not isinstance(adjustments, list):
            continue

        current_in = card.get("clock_in_local")
        current_out = card.get("clock_out_local")
        current_duration = _duration_minutes(current_in, current_out)

        for adjustment in adjustments:
            if not isinstance(adjustment, dict):
                continue

            changed = _changed_fields(adjustment)
            risk, meal_impact, adjustment_type = _impact_classification(card, changed)
            previous_in = adjustment.get("prevClkInLcl")
            previous_out = adjustment.get("prevClkOutLcl")

            estimated_previous_in = previous_in if previous_in not in (None, "") else current_in
            estimated_previous_out = previous_out if previous_out not in (None, "") else current_out
            previous_duration = _duration_minutes(estimated_previous_in, estimated_previous_out)
            duration_delta = (
                round(current_duration - previous_duration, 2)
                if current_duration is not None and previous_duration is not None
                else None
            )

            manager = str(adjustment.get("mgrName") or "").strip()
            previous_job_value = adjustment.get("prevJcNum")
            current_job_value = card.get("job_code_num")

            rows.append(
                {
                    "Location Ref": card.get("location_ref", ""),
                    "Location": card.get("location_name", ""),
                    "Business Date": card.get("business_date"),
                    "Employee": card.get("employee_name", ""),
                    "Payroll ID": card.get("payroll_id", ""),
                    "Employee Num": card.get("employee_num", ""),
                    "Timecard ID": card.get("timecard_id", ""),
                    "Adjustment ID": _identifier(adjustment.get("adjId")),
                    "Adjustment UTC": _as_timestamp(adjustment.get("adjUTC")),
                    "Manager": manager or "No informado por Oracle",
                    "Manual Adjustment": "Sí" if manager else "No confirmado",
                    "Reason": str(adjustment.get("rsn") or "Sin motivo informado").strip(),
                    "Changed Fields": ", ".join(changed) if changed else "No especificados",
                    "Adjustment Type": adjustment_type,
                    "Risk": risk,
                    "Meal Impact": meal_impact,
                    "Previous Clock In": _as_timestamp(previous_in),
                    "Current Clock In": _as_timestamp(current_in),
                    "Clock In Delta Minutes": _minutes_delta(current_in, previous_in),
                    "Previous Clock Out": _as_timestamp(previous_out),
                    "Current Clock Out": _as_timestamp(current_out),
                    "Clock Out Delta Minutes": _minutes_delta(current_out, previous_out),
                    "Estimated Previous Duration Minutes": previous_duration,
                    "Current Duration Minutes": current_duration,
                    "Estimated Duration Delta Minutes": duration_delta,
                    "Previous Job": _job_name(job_codes, previous_job_value)
                    if previous_job_value not in (None, "", 0)
                    else "",
                    "Current Job": str(card.get("job_code") or ""),
                    "Previous Revenue Center": _identifier(adjustment.get("prevRVCNum")),
                    "Current Revenue Center": _identifier(card.get("rvc_num")),
                    "Last Updated UTC": card.get("last_updated_utc"),
                }
            )

    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    return result.sort_values(
        ["Adjustment UTC", "Employee", "Timecard ID"],
        ascending=[False, True, True],
        na_position="last",
    ).reset_index(drop=True)
