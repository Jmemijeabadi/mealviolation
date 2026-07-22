from __future__ import annotations

from datetime import date

import pandas as pd

from compliance.audit import build_adjustment_result_history, reconstruct_timecard_adjustments
from compliance.engine import analyze_timecards
from compliance.snapshot import compare_snapshot_to_bundle, create_snapshot_bytes, load_snapshot_bytes


def adjusted_card() -> dict:
    return {
        "location_ref": "A",
        "location_name": "Test",
        "business_date": date(2026, 7, 1),
        "legal_workday_date": date(2026, 7, 1),
        "workday_start": "04:00",
        "workday_config_verified": True,
        "business_date_match": True,
        "timecard_id": "1",
        "source_timecard_id": "1",
        "is_primary_segment": True,
        "segment_count": 1,
        "employee_key": "123",
        "employee_name": "Jane Doe",
        "employee_name_resolved": True,
        "payroll_id": "123",
        "employee_num": 1,
        "job_code": "Server",
        "job_code_num": 1,
        "rvc_num": "1",
        "shift_type": 0,
        "clock_in_status": 84,
        "clock_out_status": 84,
        "clock_in_local": pd.Timestamp("2026-07-01 08:00"),
        "clock_out_local": pd.Timestamp("2026-07-01 14:30"),
        "pay_rate": 20.0,
        "premium_hours": 0.0,
        "premium_pay": 0.0,
        "adjustment_count": 2,
        "last_updated_utc": pd.Timestamp("2026-07-01 22:00", tz="UTC"),
        "adjustments": [
            {"adjId": 1, "adjUTC": "2026-07-01T20:00:00Z", "mgrName": "M1", "prevClkOutLcl": "2026-07-01T14:00:00"},
            {"adjId": 2, "adjUTC": "2026-07-01T21:00:00Z", "mgrName": "M2", "prevClkOutLcl": "2026-07-01T14:15:00"},
        ],
    }


def test_reverse_reconstruction_creates_intermediate_states() -> None:
    chain = reconstruct_timecard_adjustments(adjusted_card())
    assert len(chain) == 2
    assert chain[0]["before"]["clock_out_local"] == pd.Timestamp("2026-07-01 14:00")
    assert chain[0]["after"]["clock_out_local"] == pd.Timestamp("2026-07-01 14:15")
    assert chain[1]["before"]["clock_out_local"] == pd.Timestamp("2026-07-01 14:15")
    assert chain[1]["after"]["clock_out_local"] == pd.Timestamp("2026-07-01 14:30")


def test_adjustment_history_detects_result_change() -> None:
    history = build_adjustment_result_history(pd.DataFrame([adjusted_card()]), default_classification="NON_EXEMPT")
    assert len(history) == 2
    assert history["Compliance Result Changed"].any()


def test_snapshot_roundtrip_and_change_detection() -> None:
    before_df = pd.DataFrame([adjusted_card()])
    before_df.loc[0, "clock_out_local"] = pd.Timestamp("2026-07-01 14:00")
    before_bundle = analyze_timecards(before_df, default_classification="NON_EXEMPT")
    snapshot = load_snapshot_bytes(create_snapshot_bytes(before_bundle, app_version="test"))
    after_bundle = analyze_timecards(pd.DataFrame([adjusted_card()]), default_classification="NON_EXEMPT")
    changes = compare_snapshot_to_bundle(snapshot, after_bundle)
    assert not changes.empty
    assert "clock_out_local" in set(changes["Field"])


def test_snapshot_compared_to_same_bundle_has_no_changes() -> None:
    from compliance.snapshot import compare_snapshot_to_bundle, create_snapshot_bytes, load_snapshot_bytes

    df = pd.DataFrame([adjusted_card()])
    bundle = analyze_timecards(df, default_classification="NON_EXEMPT")
    previous = load_snapshot_bytes(create_snapshot_bytes(bundle, app_version="3.3.0"))
    changes = compare_snapshot_to_bundle(previous, bundle)
    assert changes.empty


def test_snapshot_includes_coverage_and_anonymized_executive_export() -> None:
    from compliance.snapshot import create_executive_snapshot_bytes
    import json

    bundle = analyze_timecards(pd.DataFrame([adjusted_card()]), default_classification="NON_EXEMPT")
    bundle.coverage = pd.DataFrame(
        [
            {
                "Location Ref": "A",
                "Business Date": date(2026, 7, 1),
                "Response Present": True,
                "Timecards Returned": 1,
            }
        ]
    )
    context = {
        "location_refs": ["A"],
        "selected_locations": [{"ref": "A", "label": "Test"}],
    }
    full = json.loads(create_snapshot_bytes(bundle, app_version="3.7.0", context=context))
    assert full["schema_version"] == "1.1"
    assert "coverage" in full
    assert "location_summary" in full

    executive = json.loads(
        create_executive_snapshot_bytes(bundle, app_version="3.7.0", context=context)
    )
    serialized = json.dumps(executive)
    assert executive["snapshot_type"] == "executive_anonymized"
    assert "Jane Doe" not in serialized
    assert '"Payroll ID"' not in serialized
