from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.audit import build_adjustment_audit


def test_adjustment_audit_expands_manager_and_time_changes() -> None:
    timecards = pd.DataFrame(
        [
            {
                "location_ref": "BYC304",
                "location_name": "San Marcos",
                "business_date": date(2026, 7, 20),
                "employee_name": "Jane Doe",
                "payroll_id": "12345",
                "employee_num": 100,
                "timecard_id": "9001",
                "job_code_num": 10,
                "job_code": "Server",
                "rvc_num": "1",
                "shift_type": 0,
                "clock_out_status": 84,
                "clock_in_local": pd.Timestamp("2026-07-20 08:00"),
                "clock_out_local": pd.Timestamp("2026-07-20 14:30"),
                "last_updated_utc": pd.Timestamp("2026-07-20 22:00", tz="UTC"),
                "adjustments": [
                    {
                        "adjId": 7,
                        "adjUTC": "2026-07-20T22:00:00Z",
                        "mgrName": "Manager One",
                        "prevClkOutLcl": "2026-07-20T14:00:00",
                        "rsn": "Forgot clock out",
                    }
                ],
            }
        ]
    )
    audit = build_adjustment_audit(timecards, job_codes={10: {"name": "Server"}})
    assert len(audit) == 1
    row = audit.iloc[0]
    assert row["Manager"] == "Manager One"
    assert row["Manual Adjustment"] == "Sí"
    assert row["Risk"] == "Alto"
    assert row["Clock Out Delta Minutes"] == 30.0
    assert row["Estimated Duration Delta Minutes"] == 30.0
    assert "elegibilidad" in row["Meal Impact"]


def test_break_adjustment_is_high_meal_impact() -> None:
    timecards = pd.DataFrame(
        [
            {
                "location_ref": "8",
                "location_name": "Black 8",
                "business_date": date(2026, 7, 20),
                "employee_name": "Jane Doe",
                "payroll_id": "12345",
                "employee_num": 100,
                "timecard_id": "9002",
                "job_code_num": 10,
                "job_code": "Server",
                "rvc_num": "1",
                "shift_type": 2,
                "clock_out_status": 84,
                "clock_in_local": pd.Timestamp("2026-07-20 13:00"),
                "clock_out_local": pd.Timestamp("2026-07-20 13:30"),
                "last_updated_utc": pd.Timestamp("2026-07-20 22:00", tz="UTC"),
                "adjustments": [
                    {
                        "adjId": 8,
                        "adjUTC": "2026-07-20T22:00:00Z",
                        "mgrName": "Manager One",
                        "prevClkInLcl": "2026-07-20T13:05:00",
                    }
                ],
            }
        ]
    )
    audit = build_adjustment_audit(timecards)
    assert audit.iloc[0]["Adjustment Type"] == "Ajuste de meal/break"
    assert "duración" in audit.iloc[0]["Meal Impact"]
