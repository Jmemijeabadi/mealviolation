from __future__ import annotations

from oracle_bi.client import OracleBIClient, iter_timecards


def test_pkce_pair_has_valid_shape() -> None:
    verifier, challenge = OracleBIClient._new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge
    assert len(challenge) == 43


def test_iter_timecards_flattens_business_dates() -> None:
    payload = {
        "locRef": "8",
        "businessDates": [
            {"busDt": "2026-07-01", "timeCardDetails": [{"tcId": 1}, {"tcId": 2}]}
        ],
    }
    rows = list(iter_timecards([payload]))
    assert rows == [
        ("8", "2026-07-01", {"tcId": 1}),
        ("8", "2026-07-01", {"tcId": 2}),
    ]


def test_iter_timecards_preserves_adjustment_request_metadata() -> None:
    payload = {
        "locRef": "8",
        "_includeAdjustmentsRequested": True,
        "businessDates": [
            {"busDt": "2026-07-01", "timeCardDetails": [{"tcId": 1}]}
        ],
    }
    rows = list(iter_timecards([payload]))
    assert rows[0][2]["_adjustmentsRequested"] is True
