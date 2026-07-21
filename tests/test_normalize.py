from __future__ import annotations

from compliance.normalize import normalize_timecards


def test_oracle_business_dates_are_flattened() -> None:
    payload = {
        "curUTC": "2026-07-01T20:00:00",
        "locRef": "8",
        "businessDates": [
            {
                "busDt": "2026-07-01",
                "timeCardDetails": [
                    {
                        "tcId": 1,
                        "empNum": 100,
                        "jcNum": 10,
                        "rvcNum": 1,
                        "shftType": 0,
                        "clkInLcl": "2026-07-01T08:00:00",
                        "clkOutLcl": "2026-07-01T14:30:00",
                        "clkInUTC": "2026-07-01T15:00:00Z",
                        "clkOutUTC": "2026-07-01T21:30:00Z",
                        "clkInStatus": 84,
                        "clkOutStatus": 84,
                        "lastUpdatedUTC": "2026-07-01T21:30:00Z",
                        "addedUTC": "2026-07-01T15:00:00Z",
                    }
                ],
            }
        ],
    }
    employees = {100: {"num": 100, "fName": "Jane", "lName": "Doe", "payrollId": 12345}}
    jobs = {10: {"num": 10, "name": "Server"}}
    locations = {"8": {"locRef": "8", "name": "Black 8", "tz": "America/Los_Angeles"}}
    df = normalize_timecards([payload], employees=employees, job_codes=jobs, locations=locations)
    assert len(df) == 1
    assert df.iloc[0]["employee_name"] == "Jane Doe"
    assert df.iloc[0]["payroll_id"] == "12345"
    assert df.iloc[0]["job_code"] == "Server"
    assert str(df.iloc[0]["business_date"]) == "2026-07-01"
