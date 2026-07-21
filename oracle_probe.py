from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from oracle_bi.client import OracleBIClient, OracleBIConfig


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Oracle BI connectivity probe. Credentials are read from environment variables."
    )
    parser.add_argument("--loc-ref", help="Location reference to query")
    parser.add_argument("--business-date", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("oracle_probe_output.json"))
    args = parser.parse_args()

    config = OracleBIConfig(
        auth_server=required_env("ORACLE_AUTH_SERVER"),
        application_server=required_env("ORACLE_APPLICATION_SERVER"),
        org_identifier=required_env("ORACLE_ORG_IDENTIFIER"),
        client_id=required_env("ORACLE_CLIENT_ID"),
        username=required_env("ORACLE_BI_USERNAME"),
        password=required_env("ORACLE_BI_PASSWORD"),
        application_name="Meal Compliance Connectivity Probe",
    )
    client = OracleBIClient(config)
    client.authenticate()
    locations = client.get_locations()

    output = {"locations": locations}
    if args.loc_ref and args.business_date:
        output["employees"] = client.get_employees(args.loc_ref)
        output["jobCodes"] = client.get_job_codes(args.loc_ref)
        output["timecards"] = client.get_timecards(
            args.loc_ref,
            args.business_date,
            include_adjustments=True,
        )

    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Oracle probe completed. Response written to {args.output}")


if __name__ == "__main__":
    main()
