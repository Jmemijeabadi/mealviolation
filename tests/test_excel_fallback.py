from datetime import date

import pandas as pd

from compliance.excel_import import convert_excel_to_payloads, read_workbook_sheet, suggest_mapping
from compliance.normalize import employee_dimension_map, job_code_dimension_map, normalize_timecards


def test_suggest_mapping_recognizes_common_excel_headers():
    mapping = suggest_mapping(
        [
            "Location",
            "Business Date",
            "Employee Name",
            "Payroll ID",
            "Clock In",
            "Meal Start",
            "Meal End",
            "Clock Out",
            "Job Code",
            "Pay Rate",
        ]
    )
    assert mapping["location"] == "Location"
    assert mapping["business_date"] == "Business Date"
    assert mapping["clock_in"] == "Clock In"
    assert mapping["clock_out"] == "Clock Out"
    assert mapping["meal_start"] == "Meal Start"
    assert mapping["meal_end"] == "Meal End"


def test_excel_full_shift_with_meal_generates_two_segments_and_confirmed_break_gap():
    frame = pd.DataFrame(
        [
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
            }
        ]
    )
    mapping = suggest_mapping(frame.columns)
    result = convert_excel_to_payloads(
        frame,
        mapping=mapping,
        location_labels={"BYC308": "Del Mar"},
        fallback_refs=["BYC308"],
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        source_name="fallback.xlsx",
    )
    cards = result.timecard_payloads[0]["businessDates"][0]["timeCardDetails"]
    assert len(cards) == 2
    assert cards[0]["clkOutStatus"] == 66
    assert cards[0]["clkOutLcl"].endswith("12:00:00")
    assert cards[1]["clkInLcl"].endswith("12:30:00")
    assert result.diagnostics["rows_used"] == 1
    assert result.diagnostics["segments_generated"] == 2

    normalized = normalize_timecards(
        result.timecard_payloads,
        employees=employee_dimension_map(result.employee_payloads),
        job_codes=job_code_dimension_map(result.job_payloads),
        locations={"BYC308": {"locRef": "BYC308", "name": "Del Mar"}},
    )
    assert len(normalized) == 2
    assert set(normalized["source_system"]) == {"Excel fallback"}
    assert normalized.iloc[0]["payroll_id"] == "1001"


def test_excel_without_location_is_allowed_for_one_fallback_location():
    frame = pd.DataFrame(
        [
            {
                "Date": "2026-07-20",
                "Employee": "Example Employee",
                "Clock In": "08:00",
                "Clock Out": "13:00",
            }
        ]
    )
    mapping = suggest_mapping(frame.columns)
    result = convert_excel_to_payloads(
        frame,
        mapping=mapping,
        location_labels={"BYC307": "Mission Viejo"},
        fallback_refs=["BYC307"],
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        source_name="fallback.xlsx",
    )
    assert result.timecard_payloads[0]["locRef"] == "BYC307"
    assert result.diagnostics["rows_used"] == 1


def test_excel_rows_for_oracle_locations_are_ignored():
    frame = pd.DataFrame(
        [
            {
                "Location": "Eastlake",
                "Business Date": "2026-07-20",
                "Employee Name": "Not a fallback employee",
                "Clock In": "08:00",
                "Clock Out": "13:00",
            },
            {
                "Location": "Del Mar",
                "Business Date": "2026-07-20",
                "Employee Name": "Fallback employee",
                "Clock In": "08:00",
                "Clock Out": "13:00",
            },
        ]
    )
    mapping = suggest_mapping(frame.columns)
    result = convert_excel_to_payloads(
        frame,
        mapping=mapping,
        location_labels={"BYC301": "Eastlake", "BYC308": "Del Mar"},
        fallback_refs=["BYC308"],
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        source_name="fallback.xlsx",
    )
    cards = result.timecard_payloads[0]["businessDates"][0]["timeCardDetails"]
    assert len(cards) == 1
    assert result.diagnostics["rows_used"] == 1
    assert result.diagnostics["rows_skipped"] == 1



def test_time_card_detail_report_is_recognized_and_flattened():
    import io

    rows = [
        [None] * 12,
        ["0"] + [None] * 11,
        ["Business Dates", "6/27/2026 - 7/10/2026"] + [None] * 10,
        ["Locations", "San Marcos"] + [None] * 10,
        [None] * 12,
        ["Total", "10"] + [None] * 10,
        [None] * 12,
        ["Amount", "100"] + [None] * 10,
        [None] * 12,
        [
            "Name", "Payroll ID", "Clock in Date and Time", "Clock Out Date and Time",
            "Clock Out Status", "Adjustment Count", "Regular Hours", "Regular Pay",
            "Overtime Hours", "Overtime Pay", "Gross Sales", "Tips",
        ],
        ["Total", "0", "-", "-", "-", 0, 8, 136, 0, 0, 0, 0],
        ["Arellanes, Julian", "sncqxf", "-", "-", "-", 0, 8, 136, 0, 0, 0, 0],
        ["Prep Cook", "0", "2026-06-27 07:00", "2026-06-27 09:30", "On break", 0, 2.5, 42.5, 0, 0, 0, 0],
        ["Prep Cook", "0", "2026-06-27 10:00", "2026-06-27 15:30", "On time", 0, 5.5, 93.5, 0, 0, 0, 0],
    ]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Reports", index=False, header=False)

    frame = read_workbook_sheet(output.getvalue(), "Time Card Detail.xlsx", "Reports")
    assert frame.attrs["source_format"] == "oracle_time_card_detail"
    assert frame.attrs["source_location"] == "San Marcos"
    assert len(frame) == 2
    assert frame.iloc[0]["Employee Name"] == "Arellanes, Julian"
    assert frame.iloc[0]["Payroll ID"] == "sncqxf"
    assert frame.iloc[0]["Job Code"] == "Prep Cook"
    assert frame.iloc[0]["Business Date"] == "2026-06-27"
    assert frame.iloc[0]["Pay Rate"] == 17.0


def test_excel_diagnostics_report_only_locations_with_valid_rows():
    frame = pd.DataFrame(
        [
            {
                "Location": "Del Mar",
                "Business Date": "2026-07-20",
                "Employee Name": "Fallback employee",
                "Clock In": "08:00",
                "Clock Out": "13:00",
            },
        ]
    )
    mapping = suggest_mapping(frame.columns)
    result = convert_excel_to_payloads(
        frame,
        mapping=mapping,
        location_labels={"BYC308": "Del Mar", "BYC307": "Mission Viejo"},
        fallback_refs=["BYC308", "BYC307"],
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        source_name="fallback.xlsx",
    )
    assert result.diagnostics["locations_with_rows"] == ["BYC308"]
    assert result.diagnostics["locations_without_rows"] == ["BYC307"]
    assert {payload["locRef"] for payload in result.timecard_payloads} == {"BYC308"}


def test_time_card_detail_location_with_byc_prefix_matches_label():
    frame = pd.DataFrame(
        [
            {
                "Location": "BYC SAN MARCOS",
                "Business Date": "2026-07-20",
                "Employee Name": "Fallback employee",
                "Clock In": "08:00",
                "Clock Out": "13:00",
            },
        ]
    )
    mapping = suggest_mapping(frame.columns)
    result = convert_excel_to_payloads(
        frame,
        mapping=mapping,
        location_labels={"BYC304": "San Marcos"},
        fallback_refs=["BYC304"],
        start_date=date(2026, 7, 20),
        end_date=date(2026, 7, 20),
        source_name="fallback.xlsx",
    )
    assert result.diagnostics["locations_with_rows"] == ["BYC304"]
