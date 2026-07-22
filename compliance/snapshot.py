from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from compliance.engine import AnalysisBundle


SNAPSHOT_SCHEMA_VERSION = "1.0"


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    clean = df.drop(columns=["raw"], errors="ignore").copy()
    return [_json_safe(record) for record in clean.to_dict("records")]


def create_snapshot_bytes(
    bundle: AnalysisBundle,
    *,
    app_version: str,
    context: dict[str, Any] | None = None,
) -> bytes:
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "app_version": app_version,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "context": _json_safe(context or {}),
        "stats": _json_safe(bundle.stats),
        "raw_timecards": _records(bundle.raw_timecards),
        "workdays": _records(bundle.workdays),
        "violations": _records(bundle.violations),
        "reviews": _records(bundle.reviews),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def load_snapshot_bytes(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("Unsupported or invalid audit snapshot.")
    return payload


def _df(payload: dict[str, Any], key: str) -> pd.DataFrame:
    frame = pd.DataFrame(payload.get(key, []) or [])
    for column in frame.columns:
        lower = column.lower()
        clock_timestamp = (
            any(token in lower for token in ("clock in", "clock out", "clock_in", "clock_out"))
            and "status" not in lower
        )
        if lower.endswith(("date", "utc")) or clock_timestamp or column in {
            "Legal Workday Date",
            "Business Date",
            "First Clock In",
            "Last Clock Out",
        }:
            converted = pd.to_datetime(frame[column], errors="coerce")
            if converted.notna().any():
                frame[column] = converted
    return frame


def compare_snapshot_to_bundle(previous: dict[str, Any], current: AnalysisBundle) -> pd.DataFrame:
    columns = [
        "Change Type",
        "Location Ref",
        "Legal Workday Date",
        "Employee",
        "Payroll ID",
        "Timecard ID",
        "Field",
        "Previous Value",
        "Current Value",
        "Compliance Impact",
    ]
    previous_cards = _df(previous, "raw_timecards")
    current_cards = current.raw_timecards.copy()
    if "is_primary_segment" in current_cards.columns:
        current_cards = current_cards[current_cards["is_primary_segment"].fillna(True)]
    if "is_primary_segment" in previous_cards.columns:
        previous_cards = previous_cards[previous_cards["is_primary_segment"].fillna(True)]

    id_col_prev = "source_timecard_id" if "source_timecard_id" in previous_cards.columns else "timecard_id"
    id_col_curr = "source_timecard_id" if "source_timecard_id" in current_cards.columns else "timecard_id"
    fields = [
        "clock_in_local",
        "clock_out_local",
        "shift_type",
        "clock_out_status",
        "job_code_num",
        "rvc_num",
        "regular_hours",
        "overtime_hours",
        "adjustment_count",
        "last_updated_utc",
    ]
    previous_index = {
        (str(row.get("location_ref") or ""), str(row.get(id_col_prev) or "")): row
        for _, row in previous_cards.iterrows()
    }
    current_index = {
        (str(row.get("location_ref") or ""), str(row.get(id_col_curr) or "")): row
        for _, row in current_cards.iterrows()
    }
    rows: list[dict[str, Any]] = []

    def base(row: Any, timecard_id: str, loc_ref: str) -> dict[str, Any]:
        return {
            "Location Ref": loc_ref,
            "Legal Workday Date": row.get("legal_workday_date", row.get("business_date", "")),
            "Employee": row.get("employee_name", ""),
            "Payroll ID": row.get("payroll_id", ""),
            "Timecard ID": timecard_id,
        }

    for key in sorted(set(previous_index) | set(current_index)):
        previous_row = previous_index.get(key)
        current_row = current_index.get(key)
        loc_ref, timecard_id = key
        if previous_row is None:
            rows.append(
                {
                    "Change Type": "Timecard added",
                    **base(current_row, timecard_id, loc_ref),
                    "Field": "timecard",
                    "Previous Value": "",
                    "Current Value": "Present",
                    "Compliance Impact": "Re-analysis required",
                }
            )
            continue
        if current_row is None:
            rows.append(
                {
                    "Change Type": "Timecard removed",
                    **base(previous_row, timecard_id, loc_ref),
                    "Field": "timecard",
                    "Previous Value": "Present",
                    "Current Value": "",
                    "Compliance Impact": "Re-analysis required",
                }
            )
            continue
        for field in fields:
            before = previous_row.get(field)
            after = current_row.get(field)
            before_text = "" if pd.isna(before) else str(before)
            after_text = "" if pd.isna(after) else str(after)
            if before_text == after_text:
                continue
            impact = "May affect meals" if field in {"clock_in_local", "clock_out_local", "shift_type", "clock_out_status"} else "Operational change"
            rows.append(
                {
                    "Change Type": "Timecard changed",
                    **base(current_row, timecard_id, loc_ref),
                    "Field": field,
                    "Previous Value": before_text,
                    "Current Value": after_text,
                    "Compliance Impact": impact,
                }
            )

    previous_workdays = _df(previous, "workdays")
    current_workdays = current.workdays.copy()
    if not previous_workdays.empty and not current_workdays.empty:
        key_fields = ["Employee Key", "Legal Workday Date"]
        if not set(key_fields).issubset(previous_workdays.columns):
            key_fields = ["Employee Key", "Business Date"]
        current_key_fields = ["Employee Key", "Legal Workday Date"] if "Legal Workday Date" in current_workdays.columns else ["Employee Key", "Business Date"]
        previous_map = {
            tuple(str(row.get(field) or "") for field in key_fields): row
            for _, row in previous_workdays.iterrows()
        }
        current_map = {
            tuple(str(row.get(field) or "") for field in current_key_fields): row
            for _, row in current_workdays.iterrows()
        }
        for key in sorted(set(previous_map) & set(current_map)):
            before = previous_map[key]
            after = current_map[key]
            before_result = str(before.get("Presumed Violations") or before.get("Automatic Violations") or "")
            after_result = str(after.get("Presumed Violations") or after.get("Automatic Violations") or "")
            if before_result != after_result:
                rows.append(
                    {
                        "Change Type": "Compliance result changed",
                        "Location Ref": after.get("Location Ref", ""),
                        "Legal Workday Date": after.get("Legal Workday Date", after.get("Business Date", "")),
                        "Employee": after.get("Employee", ""),
                        "Payroll ID": after.get("Payroll ID", ""),
                        "Timecard ID": after.get("Timecard IDs", ""),
                        "Field": "Presumed violations",
                        "Previous Value": before_result,
                        "Current Value": after_result,
                        "Compliance Impact": "Presumed-violation result changed after Oracle updates",
                    }
                )
    return pd.DataFrame(rows, columns=columns)
