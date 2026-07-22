from __future__ import annotations

from check_micros_all import ProbeResult, build_capabilities


def test_capability_report_recognizes_bi_payrt_and_employee_class() -> None:
    results = [
        ProbeResult(
            service="BI API",
            check="getEmployeeDimensions (BYC301)",
            status="AVAILABLE",
            sample_fields=["className", "classNum", "payrollId"],
        ),
        ProbeResult(
            service="BI API",
            check="getTimeCardDetails (BYC301, 2026-07-10)",
            status="AVAILABLE",
            sample_fields=[
                "clkInLcl",
                "clkOutLcl",
                "clkOutStatus",
                "shiftType",
                "adjustments",
                "payRt",
                "regPay",
            ],
        ),
    ]

    capabilities = {item.name: item for item in build_capabilities(results)}

    assert capabilities["Timecards y punches"].status == "CONFIRMED"
    assert capabilities["Ajustes de timecards"].status == "CONFIRMED"
    assert capabilities["Estados de break"].status == "CONFIRMED"
    assert capabilities["Indicador operativo salaried / employee class"].status == "PARTIAL"
    assert capabilities["Pay rate y componentes de pago"].status == "CONFIRMED"
