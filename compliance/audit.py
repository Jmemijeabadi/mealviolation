from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from compliance.engine import analyze_timecards
from compliance.models import CaliforniaMealRules


PREVIOUS_FIELD_MAP = {
    "prevClkInLcl": "clock_in_local",
    "prevClkOutLcl": "clock_out_local",
    "prevJcNum": "job_code_num",
    "prevRVCNum": "rvc_num",
}


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


def _job_name(job_codes: dict[Any, dict[str, Any]], value: Any, loc_ref: str = "") -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _identifier(value)
    job = job_codes.get(f"{loc_ref}::{number}") or job_codes.get(number, {})
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


def _has_previous(adjustment: dict[str, Any], field: str) -> bool:
    return field in adjustment and adjustment.get(field) not in (None, "")


def _changed_fields(adjustment: dict[str, Any]) -> list[str]:
    labels = {
        "prevClkInLcl": "Clock In",
        "prevClkOutLcl": "Clock Out",
        "prevJcNum": "Puesto",
        "prevRVCNum": "Revenue Center",
        "prevDrctTips": "Propinas directas",
        "prevIndirTipsPd": "Propinas indirectas",
    }
    return [label for field, label in labels.items() if _has_previous(adjustment, field)]


def _impact_classification(state_after: dict[str, Any], changed: list[str]) -> tuple[str, str, str]:
    changed_time = "Clock In" in changed or "Clock Out" in changed
    is_break = int(state_after.get("shift_type", 0) or 0) in {1, 2} or state_after.get("clock_out_status") in {66, 80}
    if changed_time and is_break:
        return "Alto", "Puede cambiar la hora o duración de un meal/break.", "Ajuste de meal/break"
    if changed_time:
        return "Alto", "Puede cambiar horas trabajadas, elegibilidad o tardanza del meal.", "Ajuste de horario"
    if "Puesto" in changed or "Revenue Center" in changed:
        return "Medio", "No cambia timestamps, pero modifica la asignación operativa.", "Ajuste operativo"
    return "Bajo", "No se detectó impacto temporal directo en meals.", "Otro ajuste"


def _sorted_adjustments(adjustments: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    return sorted(
        [item for item in adjustments if isinstance(item, dict)],
        key=lambda item: (_as_timestamp(item.get("adjUTC")), _identifier(item.get("adjId"))),
        reverse=reverse,
    )


def _apply_previous_state(state_after: dict[str, Any], adjustment: dict[str, Any]) -> dict[str, Any]:
    state_before = deepcopy(state_after)
    for previous_field, normalized_field in PREVIOUS_FIELD_MAP.items():
        if not _has_previous(adjustment, previous_field):
            continue
        value = adjustment.get(previous_field)
        if normalized_field in {"clock_in_local", "clock_out_local"}:
            value = _as_timestamp(value)
        elif normalized_field == "job_code_num":
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        else:
            value = _identifier(value)
        state_before[normalized_field] = value
    return state_before


def reconstruct_timecard_adjustments(card: pd.Series | dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct exact before/after states by reverse-applying Oracle previous values.

    Starting from the final timecard, adjustments are traversed newest-to-oldest.
    Each Oracle `prev*` value restores the state immediately before that adjustment.
    This is exact for the fields Oracle returns, provided the complete adjustment
    array is present. Other fields remain unchanged because Oracle did not report a
    previous value for them.
    """
    source = card.to_dict() if isinstance(card, pd.Series) else dict(card)
    adjustments = source.get("adjustments") or []
    if not isinstance(adjustments, list):
        return []

    rolling_after = deepcopy(source)
    reconstructed_desc: list[dict[str, Any]] = []
    sorted_desc = _sorted_adjustments(adjustments, reverse=True)
    for adjustment in sorted_desc:
        before = _apply_previous_state(rolling_after, adjustment)
        complete_identity = bool(_identifier(adjustment.get("adjId"))) and pd.notna(_as_timestamp(adjustment.get("adjUTC")))
        reconstructed_desc.append(
            {
                "adjustment": adjustment,
                "before": before,
                "after": deepcopy(rolling_after),
                "confidence": "Alta" if complete_identity else "Media",
            }
        )
        rolling_after = before
    return list(reversed(reconstructed_desc))


def build_adjustment_audit(
    timecards: pd.DataFrame,
    *,
    job_codes: dict[Any, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    columns = [
        "Location Ref",
        "Location",
        "Legal Workday Date",
        "Business Date",
        "Employee",
        "Employee Key",
        "Payroll ID",
        "Employee Num",
        "Timecard ID",
        "Adjustment Sequence",
        "Adjustment ID",
        "Adjustment UTC",
        "Manager",
        "Manual Adjustment",
        "Reason",
        "Changed Fields",
        "Adjustment Type",
        "Risk",
        "Meal Impact",
        "Reconstruction Confidence",
        "Previous Clock In",
        "New Clock In",
        "Clock In Delta Minutes",
        "Previous Clock Out",
        "New Clock Out",
        "Clock Out Delta Minutes",
        "Previous Duration Minutes",
        "New Duration Minutes",
        "Duration Delta Minutes",
        "Estimated Duration Delta Minutes",
        "Previous Job",
        "New Job",
        "Previous Revenue Center",
        "New Revenue Center",
        "Last Updated UTC",
    ]
    if timecards.empty or "adjustments" not in timecards.columns:
        return pd.DataFrame(columns=columns)

    job_codes = job_codes or {}
    source = timecards.copy()
    if "is_primary_segment" in source.columns:
        source = source[source["is_primary_segment"].fillna(True)]
    source = source.drop_duplicates(["location_ref", "source_timecard_id" if "source_timecard_id" in source.columns else "timecard_id"])
    rows: list[dict[str, Any]] = []

    for _, card in source.iterrows():
        chain = reconstruct_timecard_adjustments(card)
        for sequence, item in enumerate(chain, start=1):
            adjustment = item["adjustment"]
            before = item["before"]
            after = item["after"]
            changed = _changed_fields(adjustment)
            risk, meal_impact, adjustment_type = _impact_classification(after, changed)
            before_duration = _duration_minutes(before.get("clock_in_local"), before.get("clock_out_local"))
            after_duration = _duration_minutes(after.get("clock_in_local"), after.get("clock_out_local"))
            duration_delta = (
                round(after_duration - before_duration, 2)
                if after_duration is not None and before_duration is not None
                else None
            )
            manager = str(adjustment.get("mgrName") or "").strip()
            loc_ref = str(card.get("location_ref") or "")
            rows.append(
                {
                    "Location Ref": loc_ref,
                    "Location": card.get("location_name", ""),
                    "Legal Workday Date": card.get("legal_workday_date", card.get("business_date")),
                    "Business Date": card.get("business_date"),
                    "Employee": card.get("employee_name", ""),
                    "Employee Key": card.get("employee_key", ""),
                    "Payroll ID": card.get("payroll_id", ""),
                    "Employee Num": card.get("employee_num", ""),
                    "Timecard ID": card.get("source_timecard_id", card.get("timecard_id", "")),
                    "Adjustment Sequence": sequence,
                    "Adjustment ID": _identifier(adjustment.get("adjId")),
                    "Adjustment UTC": _as_timestamp(adjustment.get("adjUTC")),
                    "Manager": manager or "No informado por Oracle",
                    "Manual Adjustment": "Sí" if manager else "No confirmado",
                    "Reason": str(adjustment.get("rsn") or "Sin motivo informado").strip(),
                    "Changed Fields": ", ".join(changed) if changed else "No especificados",
                    "Adjustment Type": adjustment_type,
                    "Risk": risk,
                    "Meal Impact": meal_impact,
                    "Reconstruction Confidence": item["confidence"],
                    "Previous Clock In": before.get("clock_in_local"),
                    "New Clock In": after.get("clock_in_local"),
                    "Clock In Delta Minutes": _minutes_delta(after.get("clock_in_local"), before.get("clock_in_local")),
                    "Previous Clock Out": before.get("clock_out_local"),
                    "New Clock Out": after.get("clock_out_local"),
                    "Clock Out Delta Minutes": _minutes_delta(after.get("clock_out_local"), before.get("clock_out_local")),
                    "Previous Duration Minutes": before_duration,
                    "New Duration Minutes": after_duration,
                    "Duration Delta Minutes": duration_delta,
                    "Estimated Duration Delta Minutes": duration_delta,
                    "Previous Job": _job_name(job_codes, before.get("job_code_num"), loc_ref),
                    "New Job": _job_name(job_codes, after.get("job_code_num"), loc_ref),
                    "Previous Revenue Center": _identifier(before.get("rvc_num")),
                    "New Revenue Center": _identifier(after.get("rvc_num")),
                    "Last Updated UTC": card.get("last_updated_utc"),
                }
            )

    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    return result.sort_values(
        ["Adjustment UTC", "Employee", "Timecard ID", "Adjustment Sequence"],
        ascending=[False, True, True, True],
        na_position="last",
    ).reset_index(drop=True)


def _result_signature(bundle: Any) -> tuple[str, str]:
    if bundle.workdays.empty:
        return "", ""
    row = bundle.workdays.iloc[0]
    return str(row.get("Presumed Violations") or row.get("Automatic Violations") or ""), str(row.get("Reviews") or "")


def build_adjustment_result_history(
    timecards: pd.DataFrame,
    *,
    rules: CaliforniaMealRules | None = None,
    policy_records: dict[str, list[dict[str, Any]]] | None = None,
    regular_rate_records: dict[str, list[dict[str, Any]]] | None = None,
    default_classification: str = "UNKNOWN",
) -> pd.DataFrame:
    """Re-analyze each legal workday before and after every Oracle adjustment."""
    columns = [
        "Legal Workday Date",
        "Employee",
        "Employee Key",
        "Payroll ID",
        "Location(s)",
        "Timecard ID",
        "Adjustment ID",
        "Adjustment UTC",
        "Manager",
        "Reason",
        "Presumed Violations Before",
        "Presumed Violations After",
        "Reviews Before",
        "Reviews After",
        "Compliance Result Changed",
        "Impact Summary",
        "Reconstruction Confidence",
    ]
    if timecards.empty or "adjustments" not in timecards.columns:
        return pd.DataFrame(columns=columns)

    rules = rules or CaliforniaMealRules()
    policies = policy_records or {}
    regular_rates = regular_rate_records or {}
    group_date = "legal_workday_date" if "legal_workday_date" in timecards.columns else "business_date"
    rows: list[dict[str, Any]] = []

    for (_, _), group in timecards.groupby([group_date, "employee_key"], dropna=False):
        rolling = group.copy(deep=True)
        events: list[tuple[pd.Timestamp, str, dict[str, Any], str]] = []
        primary = rolling[rolling.get("is_primary_segment", pd.Series(True, index=rolling.index)).fillna(True)]
        for idx, card in primary.iterrows():
            for adjustment in card.get("adjustments") or []:
                if isinstance(adjustment, dict):
                    events.append((_as_timestamp(adjustment.get("adjUTC")), str(idx), adjustment, str(card.get("source_timecard_id") or card.get("timecard_id"))))
        events.sort(key=lambda item: (item[0], _identifier(item[2].get("adjId"))), reverse=True)
        if not events:
            continue

        for event_time, index_text, adjustment, timecard_id in events:
            index = int(index_text) if index_text.isdigit() else index_text
            after_bundle = analyze_timecards(
                rolling,
                rules=rules,
                policy_records=policies,
                regular_rate_records=regular_rates,
                default_classification=default_classification,
            )
            before_state = _apply_previous_state(rolling.loc[index].to_dict(), adjustment)
            for field in ("clock_in_local", "clock_out_local", "job_code_num", "rvc_num"):
                if field in before_state:
                    rolling.at[index, field] = before_state[field]
            before_bundle = analyze_timecards(
                rolling,
                rules=rules,
                policy_records=policies,
                regular_rate_records=regular_rates,
                default_classification=default_classification,
            )
            before_violations, before_reviews = _result_signature(before_bundle)
            after_violations, after_reviews = _result_signature(after_bundle)
            changed = (before_violations, before_reviews) != (after_violations, after_reviews)
            card = group.loc[index]
            confidence = "Media" if int(card.get("segment_count", 1) or 1) > 1 else "Alta"
            if changed:
                if before_violations != after_violations:
                    impact = "El ajuste cambió la clasificación de presuntas violaciones."
                else:
                    impact = "El ajuste cambió los casos que requieren revisión."
            else:
                impact = "No cambió el resultado de meal compliance para el workday."
            rows.append(
                {
                    "Legal Workday Date": card.get(group_date),
                    "Employee": card.get("employee_name", ""),
                    "Employee Key": card.get("employee_key", ""),
                    "Payroll ID": card.get("payroll_id", ""),
                    "Location(s)": ", ".join(sorted(set(group["location_name"].dropna().astype(str)))),
                    "Timecard ID": timecard_id,
                    "Adjustment ID": _identifier(adjustment.get("adjId")),
                    "Adjustment UTC": event_time,
                    "Manager": str(adjustment.get("mgrName") or "No informado por Oracle"),
                    "Reason": str(adjustment.get("rsn") or "Sin motivo informado"),
                    "Presumed Violations Before": before_violations,
                    "Presumed Violations After": after_violations,
                    "Reviews Before": before_reviews,
                    "Reviews After": after_reviews,
                    "Compliance Result Changed": changed,
                    "Impact Summary": impact,
                    "Reconstruction Confidence": confidence,
                }
            )

    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    return result.sort_values("Adjustment UTC", ascending=False, na_position="last").reset_index(drop=True)
