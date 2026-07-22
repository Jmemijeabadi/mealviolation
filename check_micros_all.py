#!/usr/bin/env python3
r"""
Oracle MICROS / Simphony — comprobación integral de capacidades
===============================================================

Este script realiza una sola auditoría de conectividad y disponibilidad para:

1. Business Intelligence API (BI API)
2. Configuration and Content API (CCAPI), solo cuando existe una URL propia
3. Labor Management REST API
4. Labor Management SOAP / MyLabor
5. Campos necesarios para una auditoría de meal compliance

Características:
- Solo lectura.
- No guarda credenciales, tokens, empleados ni timecards completos.
- No se detiene si un servicio falla.
- Puede leer BI y Labor desde archivos TOML separados.
- Detecta versiones REST de Labor cuando el endpoint las publica.
- Prueba variantes seguras de autenticación SOAP sin ejecutar operaciones de escritura.

Estructura esperada:

.streamlit/bi_secrets.toml

    [oracle_bi]
    auth_server = "https://..."
    application_server = "https://..."
    org_identifier = "BYC"
    client_id = "..."
    username = "..."
    password = "..."
    application_name = "Meal Compliance Dashboard"
    timeout_seconds = 45
    verify_ssl = true

    [oracle_ccapi]
    base_url = ""

.streamlit/labor_secrets.toml

    [oracle_labor]
    soap_url = "https://mtu5-ohra.oracleindustry.com/ws/mylabor"
    wsdl_url = "https://mtu5-ohra.oracleindustry.com/ws/mylabor?wsdl"
    rest_base_url = "https://mtu5-ohra.oracleindustry.com/rest/services"
    api_token = "..."
    password = "..."
    timeout_seconds = 45
    verify_ssl = true

Uso:

    python check_micros_all.py ^
      --bi-secrets .streamlit\bi_secrets.toml ^
      --labor-secrets .streamlit\labor_secrets.toml ^
      --business-date 2026-07-10 ^
      --start-date 2026-07-01 ^
      --end-date 2026-07-10 ^
      --location-ref BYC301

PowerShell:

    python .\check_micros_all.py `
      --bi-secrets .\.streamlit\bi_secrets.toml `
      --labor-secrets .\.streamlit\labor_secrets.toml `
      --business-date 2026-07-10 `
      --start-date 2026-07-01 `
      --end-date 2026-07-10 `
      --location-ref BYC301
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets as pysecrets
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests

try:
    import tomllib
except ModuleNotFoundError as exc:
    raise SystemExit("Este script requiere Python 3.11 o superior.") from exc

try:
    from oracle_bi.client import OracleBIClient, OracleBIConfig
except ImportError as exc:
    raise SystemExit(
        "No se encontró oracle_bi/client.py. Coloca este script en la raíz "
        "del repositorio mealviolation."
    ) from exc


APP_NAME = "Meal Compliance Full Capability Check"
DEFAULT_BI_SECRETS = Path(".streamlit/bi_secrets.toml")
DEFAULT_LABOR_SECRETS = Path(".streamlit/labor_secrets.toml")
DEFAULT_OUTPUT = Path("micros_full_capability_report.json")

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
WSU = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
USERNAME_TOKEN_PROFILE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0"
)
PASSWORD_TEXT_TYPE = USERNAME_TOKEN_PROFILE + "#PasswordText"
PASSWORD_DIGEST_TYPE = USERNAME_TOKEN_PROFILE + "#PasswordDigest"
BASE64_BINARY_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)
MYLABOR_NS = "http://net.mymicros/mylabor"

ET.register_namespace("soapenv", SOAP_ENV)
ET.register_namespace("wsse", WSSE)
ET.register_namespace("wsu", WSU)
ET.register_namespace("myl", MYLABOR_NS)


@dataclass
class ProbeResult:
    service: str
    check: str
    status: str
    http_status: int | None = None
    latency_ms: int | None = None
    record_count: int | None = None
    detail: str = ""
    response_keys: list[str] | None = None
    sample_fields: list[str] | None = None
    authentication_mode: str | None = None


@dataclass
class ConfigurationStatus:
    source: str
    status: str
    detail: str = ""


@dataclass
class Capability:
    name: str
    status: str
    evidence: list[str] = field(default_factory=list)
    limitation: str = ""


def sanitize_text(value: Any, limit: int = 650) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(
        r"(?i)(password|passwd|secret|token|authorization|client[_ -]?id|username)"
        r"\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:limit]


def hostname(value: str) -> str | None:
    if not value:
        return None
    return urlparse(value).netloc or None


def first_nonempty(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def bool_value(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_toml_safely(path: Path) -> tuple[dict[str, Any], ConfigurationStatus]:
    if not path.exists():
        return {}, ConfigurationStatus(
            source=str(path),
            status="NOT_FOUND",
            detail="El archivo no existe.",
        )

    try:
        with path.open("rb") as fh:
            payload = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return {}, ConfigurationStatus(
            source=str(path),
            status="INVALID_TOML",
            detail=sanitize_text(exc),
        )
    except OSError as exc:
        return {}, ConfigurationStatus(
            source=str(path),
            status="READ_ERROR",
            detail=sanitize_text(exc),
        )

    if not isinstance(payload, dict):
        return {}, ConfigurationStatus(
            source=str(path),
            status="INVALID_CONTENT",
            detail="El TOML no contiene una tabla raíz válida.",
        )

    return payload, ConfigurationStatus(source=str(path), status="AVAILABLE")


def merge_sections(
    main_payload: dict[str, Any],
    labor_payload: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(main_payload)

    labor_section = labor_payload.get("oracle_labor")
    if isinstance(labor_section, dict):
        existing = merged.get("oracle_labor")
        combined = dict(existing) if isinstance(existing, dict) else {}
        combined.update(labor_section)
        merged["oracle_labor"] = combined

    return merged


def required_values(
    section: dict[str, Any],
    names: Iterable[str],
) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    missing: list[str] = []

    for name in names:
        value = str(section.get(name, "")).strip()
        if not value or value.startswith("REPLACE_WITH_"):
            missing.append(name)
        else:
            values[name] = value

    return values, missing


def status_from_http(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "AVAILABLE"
    if status_code == 400:
        return "REQUEST_REJECTED"
    if status_code == 401:
        return "AUTH_FAILED"
    if status_code == 403:
        return "NO_PERMISSION"
    if status_code == 404:
        return "NOT_FOUND_OR_NOT_ENABLED"
    if status_code == 405:
        return "METHOD_NOT_ALLOWED"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in {502, 503, 504}:
        return "TEMPORARILY_UNAVAILABLE"
    return "HTTP_ERROR"


def safe_response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return sanitize_text(response.text)

    if isinstance(payload, dict):
        for key in (
            "detail",
            "message",
            "title",
            "error",
            "description",
            "o:errorDetails",
        ):
            if payload.get(key):
                return sanitize_text(payload[key])

    return sanitize_text(payload)


def count_records(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None

    for key in ("count", "totalResults"):
        value = payload.get(key)
        if isinstance(value, int):
            return value

    for key in ("locations", "employees", "jobCodes", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)

    business_dates = payload.get("businessDates")
    if isinstance(business_dates, list):
        total = 0
        for day in business_dates:
            if not isinstance(day, dict):
                continue
            for key in ("timeCardDetails", "timecards", "items"):
                records = day.get(key)
                if isinstance(records, list):
                    total += len(records)
                    break
        return total

    return None


def first_record(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key in (
            "items",
            "employees",
            "locations",
            "jobCodes",
            "timeCardDetails",
        ):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]

        business_dates = payload.get("businessDates")
        if isinstance(business_dates, list):
            for day in business_dates:
                if not isinstance(day, dict):
                    continue
                for key in ("timeCardDetails", "timecards", "items"):
                    value = day.get(key)
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        return value[0]

    return None


def collect_field_paths(
    value: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    limit: int = 160,
) -> list[str]:
    paths: list[str] = []

    def walk(current: Any, current_prefix: str, current_depth: int) -> None:
        if len(paths) >= limit or current_depth > max_depth:
            return

        if isinstance(current, dict):
            for key, child in current.items():
                path = f"{current_prefix}.{key}" if current_prefix else str(key)
                paths.append(path)
                if len(paths) >= limit:
                    return
                walk(child, path, current_depth + 1)
        elif isinstance(current, list) and current:
            walk(current[0], current_prefix + "[]", current_depth + 1)

    walk(value, prefix, depth)
    return sorted(set(paths))[:limit]


def payload_metadata(payload: Any) -> tuple[int | None, list[str], list[str]]:
    if not isinstance(payload, dict):
        return None, [], []

    top_keys = sorted(payload.keys())[:50]
    record = first_record(payload)
    fields = collect_field_paths(record) if record else []
    return count_records(payload), top_keys, fields


def timed_call(
    *,
    service: str,
    check: str,
    fn: Callable[[], Any],
) -> tuple[ProbeResult, Any]:
    started = time.perf_counter()

    try:
        payload = fn()
        count, keys, fields = payload_metadata(payload)
        return (
            ProbeResult(
                service=service,
                check=check,
                status="AVAILABLE",
                latency_ms=round((time.perf_counter() - started) * 1000),
                record_count=count,
                response_keys=keys,
                sample_fields=fields,
            ),
            payload,
        )
    except Exception as exc:
        return (
            ProbeResult(
                service=service,
                check=check,
                status="ERROR",
                latency_ms=round((time.perf_counter() - started) * 1000),
                detail=sanitize_text(exc),
            ),
            None,
        )


def build_bi_config(payload: dict[str, Any]) -> tuple[OracleBIConfig | None, str]:
    section = payload.get("oracle_bi")
    if not isinstance(section, dict):
        legacy = payload.get("oracle")
        section = legacy if isinstance(legacy, dict) else None
    if not isinstance(section, dict):
        return None, "No existe la sección [oracle_bi] en el archivo BI."

    values, missing = required_values(
        section,
        (
            "auth_server",
            "application_server",
            "org_identifier",
            "client_id",
            "username",
            "password",
        ),
    )
    if missing:
        return None, "Faltan valores en [oracle]: " + ", ".join(missing)

    config = OracleBIConfig(
        auth_server=values["auth_server"],
        application_server=values["application_server"],
        org_identifier=values["org_identifier"],
        client_id=values["client_id"],
        username=values["username"],
        password=values["password"],
        application_name=str(
            section.get("application_name", APP_NAME)
        ).strip() or APP_NAME,
        timeout_seconds=int_value(section.get("timeout_seconds"), 45),
        verify_ssl=bool_value(section.get("verify_ssl"), True),
    )
    return config, ""


def run_bi_checks(
    payload: dict[str, Any],
    *,
    location_ref: str,
    business_date: date,
) -> tuple[list[ProbeResult], dict[str, Any], OracleBIClient | None]:
    results: list[ProbeResult] = []
    evidence: dict[str, Any] = {}

    config, config_error = build_bi_config(payload)
    if config is None:
        results.append(
            ProbeResult(
                service="BI API",
                check="Configuration",
                status="NOT_CONFIGURED",
                detail=config_error,
            )
        )
        return results, evidence, None

    client = OracleBIClient(config)

    auth_result, token_bundle = timed_call(
        service="BI API",
        check="OIDC + PKCE authentication",
        fn=lambda: client.authenticate(force_full=True),
    )
    # TokenBundle is not JSON/dict, so clean its generic metadata.
    auth_result.record_count = None
    auth_result.response_keys = None
    auth_result.sample_fields = None
    results.append(auth_result)

    if auth_result.status != "AVAILABLE":
        return results, evidence, client

    checks: list[tuple[str, Callable[[], dict[str, Any]], str]] = [
        ("getLocationDimensions", client.get_locations, "locations"),
        (
            f"getEmployeeDimensions ({location_ref})",
            lambda: client.get_employees(location_ref),
            "employees",
        ),
        (
            f"getJobCodeDimensions ({location_ref})",
            lambda: client.get_job_codes(location_ref),
            "job_codes",
        ),
        (
            f"getLatestBusDt ({location_ref})",
            lambda: client.get_latest_business_date(location_ref),
            "latest_business_date",
        ),
        (
            f"getTimeCardDetails ({location_ref}, {business_date.isoformat()})",
            lambda: client.get_timecards(
                location_ref,
                business_date,
                include_adjustments=True,
            ),
            "timecards",
        ),
    ]

    for check_name, fn, evidence_name in checks:
        result, response_payload = timed_call(
            service="BI API",
            check=check_name,
            fn=fn,
        )
        results.append(result)

        if isinstance(response_payload, dict):
            evidence[evidence_name] = {
                "record_count": result.record_count,
                "response_keys": result.response_keys,
                "sample_fields": result.sample_fields,
            }

    return results, evidence, client


def post_json_probe(
    session: requests.Session,
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
    verify_ssl: bool,
    service: str,
    check: str,
) -> tuple[ProbeResult, Any]:
    started = time.perf_counter()

    try:
        response = session.post(
            url,
            json=body,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return (
            ProbeResult(
                service=service,
                check=check,
                status="NETWORK_ERROR",
                latency_ms=round((time.perf_counter() - started) * 1000),
                detail=sanitize_text(exc),
            ),
            None,
        )

    elapsed = round((time.perf_counter() - started) * 1000)
    status = status_from_http(response.status_code)

    try:
        payload = response.json()
    except ValueError:
        payload = None

    count, keys, fields = payload_metadata(payload)
    detail = "" if status == "AVAILABLE" else safe_response_detail(response)

    return (
        ProbeResult(
            service=service,
            check=check,
            status=status,
            http_status=response.status_code,
            latency_ms=elapsed,
            record_count=count,
            detail=detail,
            response_keys=keys,
            sample_fields=fields,
        ),
        payload,
    )


def ccapi_base_url(payload: dict[str, Any]) -> str:
    section = payload.get("oracle_ccapi")
    if isinstance(section, dict):
        value = str(section.get("base_url", "")).strip()
        if value:
            return value.rstrip("/")

    return ""


def run_ccapi_checks(
    payload: dict[str, Any],
    *,
    bi_client: OracleBIClient | None,
) -> tuple[list[ProbeResult], dict[str, Any]]:
    results: list[ProbeResult] = []
    evidence: dict[str, Any] = {}
    base_url = ccapi_base_url(payload)

    if not base_url:
        results.append(
            ProbeResult(
                service="CCAPI",
                check="Configuration",
                status="NOT_CONFIGURED",
                detail=(
                    "No hay una URL específica de Configuration and Content API. "
                    "No se reutiliza el servidor BI porque ya produjo HTTP 405."
                ),
            )
        )
        return results, evidence

    if bi_client is None:
        results.append(
            ProbeResult(
                service="CCAPI",
                check="Authentication dependency",
                status="BLOCKED",
                detail="No se obtuvo un token OIDC de BI/API account.",
            )
        )
        return results, evidence

    try:
        tokens = bi_client.authenticate()
    except Exception as exc:
        results.append(
            ProbeResult(
                service="CCAPI",
                check="Authentication dependency",
                status="BLOCKED",
                detail=sanitize_text(exc),
            )
        )
        return results, evidence

    headers = {
        "Authorization": f"Bearer {tokens.id_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "MealCompliance-Full-Check/1.0",
    }

    timeout = bi_client.config.timeout_seconds
    verify_ssl = bi_client.config.verify_ssl
    session = requests.Session()

    checks = [
        (
            "/config/sim/v2/hierarchy/getHierarchy",
            "Get Hierarchy v2",
            {"includeAll": "basic", "offset": 0, "limit": 5},
        ),
        (
            "/config/sim/v2/hierarchy/getLocations",
            "Get Locations v2",
            {"includeAll": "basic", "offset": 0, "limit": 5},
        ),
        (
            "/config/sim/v2/employees/getEmployees",
            "Get Employees v2",
            {"includeAll": "basic", "offset": 0, "limit": 5},
        ),
        (
            "/config/sim/v2/employees/getClasses",
            "Get Employee Classes v2",
            {"includeAll": "basic", "offset": 0, "limit": 5},
        ),
        (
            "/config/sim/v2/setup/getDataExtensions",
            "Get Data Extensions v2",
            {"includeAll": "basic", "offset": 0, "limit": 5},
        ),
    ]

    for path, check, body in checks:
        result, response_payload = post_json_probe(
            session,
            url=base_url + path,
            headers=headers,
            body=body,
            timeout=timeout,
            verify_ssl=verify_ssl,
            service="CCAPI",
            check=check,
        )
        results.append(result)
        evidence[check] = {
            "record_count": result.record_count,
            "response_keys": result.response_keys,
            "sample_fields": result.sample_fields,
        }

    return results, evidence


def labor_section(payload: dict[str, Any]) -> dict[str, Any]:
    section = payload.get("oracle_labor")
    return section if isinstance(section, dict) else {}


def derive_rest_base(soap_url: str) -> str:
    if "/ws/" in soap_url:
        return soap_url.split("/ws/", 1)[0] + "/rest/services"
    return ""


def get_json_probe(
    session: requests.Session,
    *,
    url: str,
    headers: dict[str, str],
    timeout: int,
    verify_ssl: bool,
    service: str,
    check: str,
    params: dict[str, str] | None = None,
) -> tuple[ProbeResult, Any]:
    started = time.perf_counter()

    try:
        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            verify=verify_ssl,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return (
            ProbeResult(
                service=service,
                check=check,
                status="NETWORK_ERROR",
                latency_ms=round((time.perf_counter() - started) * 1000),
                detail=sanitize_text(exc),
            ),
            None,
        )

    elapsed = round((time.perf_counter() - started) * 1000)
    status = status_from_http(response.status_code)

    try:
        payload = response.json()
    except ValueError:
        payload = None

    count, keys, fields = payload_metadata(payload)
    detail = "" if status == "AVAILABLE" else safe_response_detail(response)

    return (
        ProbeResult(
            service=service,
            check=check,
            status=status,
            http_status=response.status_code,
            latency_ms=elapsed,
            record_count=count,
            detail=detail,
            response_keys=keys,
            sample_fields=fields,
        ),
        payload,
    )


def discover_latest_rest_version(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "v1"

    items = payload.get("items")
    if not isinstance(items, list):
        return "v1"

    for item in items:
        if isinstance(item, dict) and item.get("isLatest") is True:
            version = str(item.get("version", "")).strip()
            if re.fullmatch(r"v\d+", version):
                return version

    versions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        version = str(item.get("version", "")).strip()
        if re.fullmatch(r"v\d+", version):
            versions.append(version)

    if versions:
        return sorted(
            versions,
            key=lambda item: int(item[1:]),
            reverse=True,
        )[0]

    return "v1"


def run_labor_rest_checks(
    payload: dict[str, Any],
    *,
    location_ref: str,
    start_date: date,
    end_date: date,
) -> tuple[list[ProbeResult], dict[str, Any]]:
    results: list[ProbeResult] = []
    evidence: dict[str, Any] = {}
    section = labor_section(payload)

    values, missing = required_values(section, ("api_token", "password"))
    soap_url = str(section.get("soap_url", "")).strip()
    rest_base_url = str(section.get("rest_base_url", "")).strip()
    if not rest_base_url:
        rest_base_url = derive_rest_base(soap_url)

    if not rest_base_url:
        missing.append("rest_base_url or soap_url")

    if missing:
        results.append(
            ProbeResult(
                service="Labor REST",
                check="Configuration",
                status="NOT_CONFIGURED",
                detail="Faltan valores en [oracle_labor]: " + ", ".join(sorted(set(missing))),
            )
        )
        return results, evidence

    timeout = int_value(section.get("timeout_seconds"), 45)
    verify_ssl = bool_value(section.get("verify_ssl"), True)
    rest_base_url = rest_base_url.rstrip("/")

    headers = {
        "username": values["api_token"],
        "password": values["password"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "MealCompliance-Full-Check/1.0",
    }

    session = requests.Session()

    root_result, root_payload = get_json_probe(
        session,
        url=rest_base_url + "/",
        headers=headers,
        timeout=timeout,
        verify_ssl=verify_ssl,
        service="Labor REST",
        check="Discover API versions",
    )
    results.append(root_result)
    evidence["version_discovery"] = {
        "response_keys": root_result.response_keys,
        "sample_fields": root_result.sample_fields,
    }

    version = discover_latest_rest_version(root_payload)
    evidence["selected_version"] = version

    checks: list[tuple[str, str, dict[str, str] | None]] = [
        (f"/{version}", f"Get API version ({version})", None),
        (f"/{version}/locations", "Get all locations", None),
        (
            f"/{version}/locations/{location_ref}",
            f"Get location ({location_ref})",
            None,
        ),
        (
            f"/{version}/locations/{location_ref}/employees",
            f"Get employees ({location_ref})",
            None,
        ),
        (
            f"/{version}/locations/{location_ref}/timecards",
            f"Get timecards ({location_ref})",
            {
                "startBusinessDate": start_date.isoformat(),
                "endBusinessDate": end_date.isoformat(),
            },
        ),
    ]

    for path, check, params in checks:
        result, response_payload = get_json_probe(
            session,
            url=rest_base_url + path,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
            service="Labor REST",
            check=check,
            params=params,
        )
        results.append(result)
        evidence[check] = {
            "record_count": result.record_count,
            "response_keys": result.response_keys,
            "sample_fields": result.sample_fields,
        }

    return results, evidence


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def build_soap_envelope(
    *,
    operation: str,
    token: str | None,
    password: str | None,
    auth_mode: str,
    parameters: dict[str, Any] | None = None,
) -> bytes:
    envelope = ET.Element(ET.QName(SOAP_ENV, "Envelope"))
    header = ET.SubElement(envelope, ET.QName(SOAP_ENV, "Header"))

    if auth_mode in {"WSSE_PASSWORD_TEXT", "WSSE_PASSWORD_DIGEST"}:
        security = ET.SubElement(
            header,
            ET.QName(WSSE, "Security"),
            {ET.QName(SOAP_ENV, "mustUnderstand"): "1"},
        )

        now = datetime.now(timezone.utc)
        timestamp = ET.SubElement(
            security,
            ET.QName(WSU, "Timestamp"),
            {ET.QName(WSU, "Id"): f"TS-{uuid.uuid4()}"},
        )
        created = ET.SubElement(timestamp, ET.QName(WSU, "Created"))
        created.text = utc_text(now)
        expires = ET.SubElement(timestamp, ET.QName(WSU, "Expires"))
        expires.text = utc_text(now + timedelta(minutes=5))

        username_token = ET.SubElement(
            security,
            ET.QName(WSSE, "UsernameToken"),
            {ET.QName(WSU, "Id"): f"UT-{uuid.uuid4()}"},
        )
        username = ET.SubElement(username_token, ET.QName(WSSE, "Username"))
        username.text = token or ""

        if auth_mode == "WSSE_PASSWORD_DIGEST":
            nonce_bytes = pysecrets.token_bytes(18)
            created_text = utc_text(now)
            digest = base64.b64encode(
                hashlib.sha1(
                    nonce_bytes
                    + created_text.encode("utf-8")
                    + (password or "").encode("utf-8")
                ).digest()
            ).decode("ascii")

            password_node = ET.SubElement(
                username_token,
                ET.QName(WSSE, "Password"),
                {"Type": PASSWORD_DIGEST_TYPE},
            )
            password_node.text = digest

            nonce = ET.SubElement(
                username_token,
                ET.QName(WSSE, "Nonce"),
                {"EncodingType": BASE64_BINARY_TYPE},
            )
            nonce.text = base64.b64encode(nonce_bytes).decode("ascii")

            created_node = ET.SubElement(
                username_token,
                ET.QName(WSU, "Created"),
            )
            created_node.text = created_text
        else:
            password_node = ET.SubElement(
                username_token,
                ET.QName(WSSE, "Password"),
                {"Type": PASSWORD_TEXT_TYPE},
            )
            password_node.text = password or ""

    body = ET.SubElement(envelope, ET.QName(SOAP_ENV, "Body"))
    operation_node = ET.SubElement(body, ET.QName(MYLABOR_NS, operation))

    # El WSDL declara elementFormDefault="unqualified".
    for key, value in (parameters or {}).items():
        if value is None:
            continue
        child = ET.SubElement(operation_node, key)
        child.text = str(value)

    return ET.tostring(
        envelope,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def soap_fault_detail(root: ET.Element) -> str | None:
    fault = root.find(f".//{{{SOAP_ENV}}}Fault")
    if fault is None:
        return None

    parts = []
    for node in fault.iter():
        text = (node.text or "").strip()
        if text:
            parts.append(text)

    return sanitize_text(" ".join(parts))


def soap_return_count(root: ET.Element | None) -> int | None:
    if root is None:
        return None
    returns = [
        node for node in root.iter()
        if xml_local_name(node.tag) == "return"
    ]
    return len(returns)


def soap_probe(
    session: requests.Session,
    *,
    soap_url: str,
    operation: str,
    token: str | None,
    password: str | None,
    auth_mode: str,
    timeout: int,
    verify_ssl: bool,
    parameters: dict[str, Any] | None = None,
) -> tuple[ProbeResult, ET.Element | None]:
    body = build_soap_envelope(
        operation=operation,
        token=token,
        password=password,
        auth_mode=auth_mode,
        parameters=parameters,
    )

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "Accept": "text/xml, application/xml",
        "SOAPAction": '""',
        "User-Agent": "MealCompliance-Full-Check/1.0",
    }
    if auth_mode == "HTTP_HEADERS":
        headers["username"] = token or ""
        headers["password"] = password or ""

    started = time.perf_counter()

    try:
        response = session.post(
            soap_url,
            data=body,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return (
            ProbeResult(
                service="Labor SOAP",
                check=operation,
                status="NETWORK_ERROR",
                latency_ms=round((time.perf_counter() - started) * 1000),
                detail=sanitize_text(exc),
                authentication_mode=auth_mode,
            ),
            None,
        )

    elapsed = round((time.perf_counter() - started) * 1000)

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        return (
            ProbeResult(
                service="Labor SOAP",
                check=operation,
                status="INVALID_XML_RESPONSE",
                http_status=response.status_code,
                latency_ms=elapsed,
                detail=sanitize_text(
                    f"{exc}; response={response.text[:300]}"
                ),
                authentication_mode=auth_mode,
            ),
            None,
        )

    fault = soap_fault_detail(root)
    if fault:
        lowered = fault.lower()
        if "no authentication data" in lowered:
            status = "AUTH_HEADER_NOT_RECOGNIZED"
        elif "authfailure" in lowered or "authentication" in lowered:
            status = "AUTH_FAILED"
        elif "invalidrequest" in lowered:
            status = "REQUEST_REJECTED"
        else:
            status = "SOAP_FAULT"

        return (
            ProbeResult(
                service="Labor SOAP",
                check=operation,
                status=status,
                http_status=response.status_code,
                latency_ms=elapsed,
                detail=fault,
                authentication_mode=auth_mode,
            ),
            root,
        )

    status = status_from_http(response.status_code)
    return (
        ProbeResult(
            service="Labor SOAP",
            check=operation,
            status=status,
            http_status=response.status_code,
            latency_ms=elapsed,
            record_count=soap_return_count(root),
            authentication_mode=auth_mode,
        ),
        root,
    )


def run_labor_soap_checks(
    payload: dict[str, Any],
) -> tuple[list[ProbeResult], dict[str, Any]]:
    results: list[ProbeResult] = []
    evidence: dict[str, Any] = {}
    section = labor_section(payload)

    soap_url = str(section.get("soap_url", "")).strip()
    wsdl_url = str(section.get("wsdl_url", "")).strip()
    if soap_url and not wsdl_url:
        wsdl_url = soap_url + "?wsdl"

    values, missing = required_values(section, ("api_token", "password"))
    if not soap_url:
        missing.append("soap_url")

    if missing:
        results.append(
            ProbeResult(
                service="Labor SOAP",
                check="Configuration",
                status="NOT_CONFIGURED",
                detail="Faltan valores en [oracle_labor]: " + ", ".join(sorted(set(missing))),
            )
        )
        return results, evidence

    timeout = int_value(section.get("timeout_seconds"), 45)
    verify_ssl = bool_value(section.get("verify_ssl"), True)
    session = requests.Session()

    if wsdl_url:
        started = time.perf_counter()
        try:
            response = session.get(
                wsdl_url,
                timeout=timeout,
                verify=verify_ssl,
            )
            is_wsdl = (
                200 <= response.status_code < 300
                and (
                    b"wsdl:definitions" in response.content
                    or b"<definitions" in response.content
                )
            )
            results.append(
                ProbeResult(
                    service="Labor SOAP",
                    check="WSDL discovery",
                    status="AVAILABLE" if is_wsdl else "INVALID_WSDL_RESPONSE",
                    http_status=response.status_code,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    detail="" if is_wsdl else sanitize_text(response.text[:400]),
                )
            )
        except requests.RequestException as exc:
            results.append(
                ProbeResult(
                    service="Labor SOAP",
                    check="WSDL discovery",
                    status="NETWORK_ERROR",
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    detail=sanitize_text(exc),
                )
            )

    ping_result, _ = soap_probe(
        session,
        soap_url=soap_url,
        operation="ping",
        token=None,
        password=None,
        auth_mode="NONE",
        timeout=timeout,
        verify_ssl=verify_ssl,
    )
    results.append(ping_result)

    protected_success = False
    successful_mode = ""

    for auth_mode in (
        "WSSE_PASSWORD_TEXT",
        "WSSE_PASSWORD_DIGEST",
        "HTTP_HEADERS",
    ):
        result, _ = soap_probe(
            session,
            soap_url=soap_url,
            operation="getLocationList",
            token=values["api_token"],
            password=values["password"],
            auth_mode=auth_mode,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )
        result.check = f"getLocationList [{auth_mode}]"
        results.append(result)

        if result.status == "AVAILABLE":
            protected_success = True
            successful_mode = auth_mode
            break

    evidence["successful_authentication_mode"] = successful_mode or None

    if protected_success:
        for operation in (
            "getLocationConfiguration",
            "getBreakConfig",
            "getBreakRules",
        ):
            result, _ = soap_probe(
                session,
                soap_url=soap_url,
                operation=operation,
                token=values["api_token"],
                password=values["password"],
                auth_mode=successful_mode,
                timeout=timeout,
                verify_ssl=verify_ssl,
            )
            results.append(result)

    return results, evidence


def result_map(results: list[ProbeResult]) -> dict[str, list[ProbeResult]]:
    grouped: dict[str, list[ProbeResult]] = {}
    for result in results:
        grouped.setdefault(result.service, []).append(result)
    return grouped


def available(results: list[ProbeResult], contains: str) -> bool:
    return any(
        contains.lower() in result.check.lower()
        and result.status == "AVAILABLE"
        for result in results
    )


def fields_for(
    results: list[ProbeResult],
    *,
    service: str,
    check_contains: str,
) -> set[str]:
    output: set[str] = set()
    for result in results:
        if result.service != service:
            continue
        if check_contains.lower() not in result.check.lower():
            continue
        output.update(result.sample_fields or [])
    return output


def contains_field(fields: set[str], candidates: Iterable[str]) -> bool:
    lowered = {field.lower() for field in fields}
    for candidate in candidates:
        candidate_lower = candidate.lower()
        if any(
            item == candidate_lower
            or item.endswith("." + candidate_lower)
            or item.endswith("[]" + "." + candidate_lower)
            for item in lowered
        ):
            return True
    return False


def build_capabilities(results: list[ProbeResult]) -> list[Capability]:
    capabilities: list[Capability] = []

    bi_timecard_fields = fields_for(
        results,
        service="BI API",
        check_contains="getTimeCardDetails",
    )
    bi_employee_fields = fields_for(
        results,
        service="BI API",
        check_contains="getEmployeeDimensions",
    )
    labor_timecard_fields = fields_for(
        results,
        service="Labor REST",
        check_contains="Get timecards",
    )
    labor_employee_fields = fields_for(
        results,
        service="Labor REST",
        check_contains="Get employees",
    )

    bi_timecards = available(results, "getTimeCardDetails")
    labor_timecards = available(results, "Get timecards")
    labor_employees = available(results, "Get employees")
    labor_locations = available(results, "Get all locations")

    capabilities.append(
        Capability(
            name="Timecards y punches",
            status="CONFIRMED" if bi_timecards or labor_timecards else "NOT_CONFIRMED",
            evidence=[
                service
                for service, ok in (
                    ("BI API", bi_timecards),
                    ("Labor REST", labor_timecards),
                )
                if ok
            ],
        )
    )

    adjustment_available = (
        contains_field(
            bi_timecard_fields,
            (
                "adjustments",
                "timeCardAdjustments",
                "adjustment",
                "isAdjusted",
            ),
        )
        or bi_timecards
    )
    capabilities.append(
        Capability(
            name="Ajustes de timecards",
            status="CONFIRMED" if adjustment_available else "NOT_CONFIRMED",
            evidence=["BI API includeAdjustments=true"] if bi_timecards else [],
            limitation=(
                "La presencia exacta de cada campo depende de los registros devueltos."
                if adjustment_available
                else ""
            ),
        )
    )

    break_status = contains_field(
        bi_timecard_fields | labor_timecard_fields,
        (
            "clockInStatus",
            "clockOutStatus",
            "shiftType",
            "break",
            "breakType",
        ),
    )
    capabilities.append(
        Capability(
            name="Estados de break",
            status="CONFIRMED" if break_status else "NOT_CONFIRMED",
            evidence=(
                ["Campos de status/shift detectados"]
                if break_status else []
            ),
        )
    )

    employee_fields = labor_employee_fields | bi_employee_fields
    salaried = contains_field(employee_fields, ("isSalaried", "salaried"))
    employee_class = contains_field(
        employee_fields,
        ("employeeClass", "className", "classNum"),
    )
    capabilities.append(
        Capability(
            name="Indicador operativo salaried / employee class",
            status=(
                "CONFIRMED" if salaried else "PARTIAL" if employee_class else "NOT_CONFIRMED"
            ),
            evidence=(
                ["isSalaried disponible"]
                if salaried
                else ["className/classNum disponibles en BI"]
                if employee_class
                else []
            ),
            limitation=(
                "Un indicador operativo no sustituye automáticamente la "
                "clasificación legal exento/no exento."
            ),
        )
    )

    pay_rate = contains_field(
        labor_timecard_fields | labor_employee_fields | bi_timecard_fields,
        (
            "payRate",
            "payRt",
            "regularPayRate",
            "jobRates",
            "regularPay",
            "regPay",
            "overtimePay",
        ),
    )
    capabilities.append(
        Capability(
            name="Pay rate y componentes de pago",
            status="CONFIRMED" if pay_rate else "NOT_CONFIRMED",
            evidence=["Labor REST/BI fields"] if pay_rate else [],
        )
    )

    start_of_day = (
        labor_locations
        or available(results, "getLocationConfiguration")
        or any(
            result.service == "CCAPI"
            and result.status == "AVAILABLE"
            and "Location" in result.check
            for result in results
        )
    )
    capabilities.append(
        Capability(
            name="Configuración de ubicación / jornada operativa",
            status="PARTIAL" if start_of_day else "NOT_CONFIRMED",
            evidence=[
                item
                for item, ok in (
                    ("Labor location information", labor_locations),
                    (
                        "Labor SOAP location configuration",
                        available(results, "getLocationConfiguration"),
                    ),
                )
                if ok
            ],
            limitation=(
                "Debe verificarse que el inicio operativo configurado coincida "
                "con el workday legal usado por nómina."
            ),
        )
    )

    capabilities.append(
        Capability(
            name="Meal waivers y on-duty agreements",
            status="NOT_CONFIRMED",
            limitation=(
                "No se confirmó una fuente API explícita para acuerdos o renuncias "
                "firmadas. Puede requerir HR/payroll o Data Extensions."
            ),
        )
    )

    capabilities.append(
        Capability(
            name="Clasificación legal exento/no exento",
            status="NOT_CONFIRMED",
            limitation=(
                "No debe inferirse únicamente por job code, salario o isSalaried."
            ),
        )
    )

    return capabilities


def service_summary(results: list[ProbeResult]) -> dict[str, Any]:
    grouped = result_map(results)
    summary: dict[str, Any] = {}

    for service, items in grouped.items():
        statuses = [item.status for item in items]
        if any(status == "AVAILABLE" for status in statuses):
            if all(
                status in {"AVAILABLE", "NOT_CONFIGURED", "SKIPPED"}
                for status in statuses
            ):
                overall = "AVAILABLE"
            else:
                overall = "PARTIAL"
        elif all(status == "NOT_CONFIGURED" for status in statuses):
            overall = "NOT_CONFIGURED"
        elif any(status in {"AUTH_FAILED", "AUTH_HEADER_NOT_RECOGNIZED"} for status in statuses):
            overall = "AUTHENTICATION_UNRESOLVED"
        else:
            overall = "UNAVAILABLE_OR_FAILED"

        summary[service] = {
            "overall_status": overall,
            "checks": len(items),
            "available_checks": sum(
                1 for item in items if item.status == "AVAILABLE"
            ),
            "statuses": sorted(set(statuses)),
        }

    return summary


def print_console_report(
    configuration: list[ConfigurationStatus],
    results: list[ProbeResult],
    capabilities: list[Capability],
    output_path: Path,
) -> None:
    print("\nORACLE MICROS — COMPROBACIÓN INTEGRAL")
    print("=" * 116)

    print("\nCONFIGURACIÓN")
    for item in configuration:
        print(f"{item.source:<55} {item.status}")
        if item.detail:
            print(f"  {item.detail}")

    print("\nSERVICIOS")
    for item in results:
        http = "-" if item.http_status is None else str(item.http_status)
        count = "-" if item.record_count is None else str(item.record_count)
        auth = f" auth={item.authentication_mode}" if item.authentication_mode else ""
        print(
            f"{item.service:<13} {item.check[:55]:<56} "
            f"{item.status:<30} HTTP={http:<3} records={count}{auth}"
        )
        if item.detail:
            print(f"  {item.detail}")

    print("\nCAPACIDADES PARA MEAL COMPLIANCE")
    for item in capabilities:
        print(f"{item.name:<52} {item.status}")
        if item.evidence:
            print("  Evidencia: " + ", ".join(item.evidence))
        if item.limitation:
            print("  Límite: " + item.limitation)

    print(f"\nReporte: {output_path.resolve()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Comprueba en una sola ejecución BI API, CCAPI y Labor "
            "Management REST/SOAP."
        )
    )
    parser.add_argument(
        "--bi-secrets",
        type=Path,
        default=DEFAULT_BI_SECRETS,
        help="Archivo exclusivo de BI API con [oracle_bi].",
    )
    parser.add_argument(
        "--labor-secrets",
        type=Path,
        default=DEFAULT_LABOR_SECRETS,
        help=(
            "Archivo exclusivo de Labor Management con [oracle_labor]."
        ),
    )
    parser.add_argument("--location-ref", default="BYC301")
    parser.add_argument("--business-date", type=date.fromisoformat, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    if args.end_date < args.start_date:
        raise SystemExit("end-date no puede ser anterior a start-date.")

    if (args.end_date - args.start_date).days + 1 > 14:
        raise SystemExit(
            "El intervalo Labor no puede superar 14 días por solicitud."
        )

    bi_payload, bi_status = load_toml_safely(args.bi_secrets)
    labor_payload, labor_status = load_toml_safely(args.labor_secrets)

    # Mantener las credenciales completamente separadas.
    payload: dict[str, Any] = {}

    bi_section = bi_payload.get("oracle_bi")
    if not isinstance(bi_section, dict):
        legacy_bi = bi_payload.get("oracle")
        bi_section = legacy_bi if isinstance(legacy_bi, dict) else None
    if isinstance(bi_section, dict):
        payload["oracle_bi"] = dict(bi_section)

    ccapi_section = bi_payload.get("oracle_ccapi")
    if isinstance(ccapi_section, dict):
        payload["oracle_ccapi"] = dict(ccapi_section)

    labor_section_payload = labor_payload.get("oracle_labor")
    if isinstance(labor_section_payload, dict):
        payload["oracle_labor"] = dict(labor_section_payload)

    configuration = [bi_status, labor_status]

    all_results: list[ProbeResult] = []
    evidence: dict[str, Any] = {}

    bi_results, bi_evidence, bi_client = run_bi_checks(
        payload,
        location_ref=args.location_ref,
        business_date=args.business_date,
    )
    all_results.extend(bi_results)
    evidence["bi_api"] = bi_evidence

    ccapi_results, ccapi_evidence = run_ccapi_checks(
        payload,
        bi_client=bi_client,
    )
    all_results.extend(ccapi_results)
    evidence["ccapi"] = ccapi_evidence

    labor_rest_results, labor_rest_evidence = run_labor_rest_checks(
        payload,
        location_ref=args.location_ref,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    all_results.extend(labor_rest_results)
    evidence["labor_rest"] = labor_rest_evidence

    labor_soap_results, labor_soap_evidence = run_labor_soap_checks(payload)
    all_results.extend(labor_soap_results)
    evidence["labor_soap"] = labor_soap_evidence

    capabilities = build_capabilities(all_results)
    services = service_summary(all_results)

    report = {
        "schema_version": "2.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "location_ref": args.location_ref,
            "business_date": args.business_date.isoformat(),
            "labor_start_date": args.start_date.isoformat(),
            "labor_end_date": args.end_date.isoformat(),
        },
        "environment": {
            "bi_application_host": hostname(
                str(
                    (
                        payload.get("oracle_bi", {})
                        if isinstance(payload.get("oracle_bi"), dict)
                        else {}
                    ).get("application_server", "")
                )
            ),
            "bi_auth_host": hostname(
                str(
                    (
                        payload.get("oracle_bi", {})
                        if isinstance(payload.get("oracle_bi"), dict)
                        else {}
                    ).get("auth_server", "")
                )
            ),
            "ccapi_host": hostname(ccapi_base_url(payload)),
            "labor_soap_host": hostname(
                str(labor_section(payload).get("soap_url", ""))
            ),
            "labor_rest_host": hostname(
                str(
                    labor_section(payload).get("rest_base_url", "")
                    or derive_rest_base(
                        str(labor_section(payload).get("soap_url", ""))
                    )
                )
            ),
        },
        "configuration": [asdict(item) for item in configuration],
        "service_summary": services,
        "results": [asdict(item) for item in all_results],
        "capabilities": [asdict(item) for item in capabilities],
        "evidence": evidence,
        "interpretation": {
            "BI API": (
                "Confirmada cuando autenticación, dimensiones y timecards "
                "aparecen AVAILABLE."
            ),
            "CCAPI": (
                "Solo se considera probada con una URL específica de CCAPI; "
                "no se usa automáticamente el host de BI."
            ),
            "Labor REST": (
                "Es la vía preferente para empleados, ubicaciones y punches "
                "cuando responde AVAILABLE."
            ),
            "Labor SOAP": (
                "El WSDL y ping solo demuestran que el servicio existe. "
                "Una operación protegida debe responder AVAILABLE para confirmar acceso."
            ),
        },
        "security": {
            "credentials_included": False,
            "tokens_included": False,
            "employee_records_included": False,
            "timecard_records_included": False,
            "raw_api_bodies_included": False,
        },
    }

    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    print_console_report(
        configuration,
        all_results,
        capabilities,
        args.output,
    )

    # Código 0: el diagnóstico se ejecutó y produjo reporte, aunque algunos
    # servicios no estén configurados o disponibles.
    return 0


if __name__ == "__main__":
    sys.exit(main())
