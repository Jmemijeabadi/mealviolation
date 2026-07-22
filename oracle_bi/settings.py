from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise RuntimeError("Python 3.11 or newer is required.") from exc

from oracle_bi.client import OracleBIConfig


def config_from_secret_mapping(secrets: Mapping[str, Any]) -> OracleBIConfig:
    """Build the BI configuration from Streamlit-style secrets.

    ``[oracle_bi]`` is the canonical section from v3.6 onward. ``[oracle]`` is
    retained as a backward-compatible alias so existing deployments can upgrade
    without downtime. Labor credentials are intentionally ignored.
    """
    section = secrets.get("oracle_bi")
    if not isinstance(section, Mapping):
        legacy = secrets.get("oracle")
        section = legacy if isinstance(legacy, Mapping) else None
    if not isinstance(section, Mapping):
        raise ValueError(
            "Business Intelligence API secrets are not configured. "
            "Add the [oracle_bi] section in Streamlit Secrets."
        )

    required = (
        "auth_server",
        "application_server",
        "org_identifier",
        "client_id",
        "username",
        "password",
    )
    missing = [
        key
        for key in required
        if not str(section.get(key, "")).strip()
        or str(section.get(key, "")).strip().startswith("REPLACE_WITH_")
    ]
    if missing:
        raise ValueError(
            "Business Intelligence API Secrets are incomplete: "
            + ", ".join(missing)
        )

    return OracleBIConfig(
        auth_server=str(section["auth_server"]).strip(),
        application_server=str(section["application_server"]).strip(),
        org_identifier=str(section["org_identifier"]).strip(),
        client_id=str(section["client_id"]).strip(),
        username=str(section["username"]).strip(),
        password=str(section["password"]),
        application_name=str(
            section.get("application_name", "Meal Compliance Dashboard")
        ).strip()
        or "Meal Compliance Dashboard",
        timeout_seconds=int(section.get("timeout_seconds", 45)),
        verify_ssl=bool(section.get("verify_ssl", True)),
    )


def config_from_toml_file(path: str | Path) -> OracleBIConfig:
    """Load a standalone local BI Secrets TOML file.

    This supports local development with ``.streamlit/bi_secrets.toml`` while
    Streamlit Community Cloud continues to use its normal Secrets editor.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"BI Secrets file does not exist: {file_path}")
    try:
        with file_path.open("rb") as fh:
            payload = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid BI Secrets TOML: {exc}") from exc
    return config_from_secret_mapping(payload)
