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


def test_employee_name_falls_back_to_payroll_dimension_match() -> None:
    from compliance.normalize import employee_dimension_map

    payload = {
        "locRef": "8",
        "employees": [
            {
                "num": 999,
                "employeeId": 555,
                "fName": "Maria",
                "lName": "Lopez",
                "payrollId": "A-123",
                "externalPayrollID": "EXT-123",
            }
        ],
    }
    timecards = {
        "locRef": "8",
        "businessDates": [
            {
                "busDt": "2026-07-01",
                "timeCardDetails": [
                    {
                        "tcId": 2,
                        "empNum": 111,
                        "payrollID": "A-123",
                        "jcNum": 10,
                        "shftType": 0,
                        "clkInLcl": "2026-07-01T08:00:00",
                        "clkOutLcl": "2026-07-01T12:00:00",
                    }
                ],
            }
        ],
    }
    df = normalize_timecards(
        [timecards],
        employees=employee_dimension_map(payload),
        job_codes={10: {"num": 10, "name": "Server"}},
        locations={"8": {"locRef": "8", "name": "Black 8"}},
    )
    assert df.iloc[0]["employee_name"] == "Maria Lopez"
    assert bool(df.iloc[0]["employee_name_resolved"]) is True
    assert df.iloc[0]["employee_match_method"] == "employee.payrollId"


def test_unresolved_employee_uses_payroll_id_in_display_name() -> None:
    timecards = {
        "locRef": "8",
        "businessDates": [
            {
                "busDt": "2026-07-01",
                "timeCardDetails": [
                    {
                        "tcId": 3,
                        "empNum": 222,
                        "payrollID": "P-222",
                        "jcNum": 10,
                        "shftType": 0,
                        "clkInLcl": "2026-07-01T08:00:00",
                        "clkOutLcl": "2026-07-01T12:00:00",
                    }
                ],
            }
        ],
    }
    df = normalize_timecards([timecards])
    assert df.iloc[0]["employee_name"] == "Empleado P-222"
    assert bool(df.iloc[0]["employee_name_resolved"]) is False


def test_employee_name_falls_back_when_payroll_matches_employee_id() -> None:
    from compliance.normalize import employee_dimension_map

    employees_payload = {
        "employees": [
            {
                "num": 999,
                "employeeId": 777,
                "fName": "Alex",
                "lName": "Rivera",
                "payrollId": 888,
            }
        ]
    }
    timecards = {
        "locRef": "8",
        "businessDates": [
            {
                "busDt": "2026-07-01",
                "timeCardDetails": [
                    {
                        "tcId": 4,
                        "empNum": 111,
                        "payrollID": 777,
                        "jcNum": 10,
                        "shftType": 0,
                        "clkInLcl": "2026-07-01T08:00:00",
                        "clkOutLcl": "2026-07-01T12:00:00",
                    }
                ],
            }
        ],
    }
    df = normalize_timecards(
        [timecards], employees=employee_dimension_map(employees_payload)
    )
    assert df.iloc[0]["employee_name"] == "Alex Rivera"
    assert df.iloc[0]["employee_match_method"] == "payrollID matched employeeId"
