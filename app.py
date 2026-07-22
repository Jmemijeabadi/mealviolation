from __future__ import annotations

import base64
import hashlib
import html
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from compliance.audit import build_adjustment_audit
from compliance.engine import AnalysisBundle, analyze_timecards
from compliance.models import CaliforniaMealRules
from compliance.normalize import (
    employee_dimension_map,
    job_code_dimension_map,
    load_waiver_csv,
    location_dimension_map,
    normalize_timecards,
    waiver_rows_to_records,
)
from compliance.reporting import build_employee_summary
from oracle_bi.client import OracleBIClient, OracleBIConfig, OracleBIError


APP_VERSION = "3.2.0"
MAX_RANGE_DAYS = 31
BRAND_BLUE = "#009EFB"
BRAND_BLUE_DARK = "#007ACC"
BRAND_TEXT = "#172033"
BRAND_MUTED = "#667085"

RESULT_LABELS = {
    "COMPLIANT": "Cumple",
    "FIRST_MEAL_MISSING": "Primer meal no registrado",
    "FIRST_MEAL_LATE": "Primer meal tardío",
    "FIRST_MEAL_SHORT": "Primer meal menor a 30 minutos",
    "FIRST_MEAL_WAIVER_UNVERIFIED": "Waiver del primer meal por confirmar",
    "SECOND_MEAL_MISSING": "Segundo meal no registrado",
    "SECOND_MEAL_LATE": "Segundo meal tardío",
    "SECOND_MEAL_SHORT": "Segundo meal menor a 30 minutos",
    "SECOND_MEAL_WAIVER_UNVERIFIED": "Waiver del segundo meal por confirmar",
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Paid/on-duty break por confirmar",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Meal probable por timestamps",
    "PUNCH_ERROR": "Error de marcación",
    "INCOMPLETE_TIMECARD": "Timecard incompleto",
    "ADJUSTED_TIMECARD_REVIEW": "Timecard ajustado",
    "INCONCLUSIVE": "Resultado no concluyente",
}

RESULT_ACTIONS = {
    "FIRST_MEAL_MISSING": "Confirmar que no hubo meal y revisar el premium correspondiente.",
    "FIRST_MEAL_LATE": "Validar la hora real del primer meal y revisar el premium.",
    "FIRST_MEAL_SHORT": "Confirmar duración; un meal menor a 30 minutos requiere revisión.",
    "FIRST_MEAL_WAIVER_UNVERIFIED": "Verificar si existe waiver vigente para esa fecha.",
    "SECOND_MEAL_MISSING": "Confirmar que no hubo segundo meal y revisar el premium.",
    "SECOND_MEAL_LATE": "Validar la hora real del segundo meal y revisar el premium.",
    "SECOND_MEAL_SHORT": "Confirmar duración del segundo meal.",
    "SECOND_MEAL_WAIVER_UNVERIFIED": "Verificar waiver de segundo meal y que el primero sí se haya tomado.",
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Confirmar acuerdo on-duty y que el empleado haya sido relevado de funciones.",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Confirmar con MICROS o el supervisor que el gap fue un meal duty-free.",
    "PUNCH_ERROR": "Corregir o confirmar la marcación en MICROS antes de cerrar el caso.",
    "INCOMPLETE_TIMECARD": "Esperar el Clock Out o corregir el timecard.",
    "ADJUSTED_TIMECARD_REVIEW": "Revisar quién hizo el ajuste, el motivo y los valores anteriores.",
    "INCONCLUSIVE": "Revisar manualmente los punches y la evidencia disponible.",
    "COMPLIANT": "Sin acción requerida.",
}

SHIFT_LABELS = {
    "Working": "Trabajo",
    "Paid Break": "Break pagado",
    "Unpaid Break": "Meal / break no pagado",
}

CLOCK_OUT_LABELS = {
    "Still Clocked In": "Turno abierto",
    "On Break": "En break",
    "Paid Break": "Break pagado",
    "Manager Clock Out": "Clock Out de manager",
    "Auto Clock Out": "Clock Out automático",
    "Scheduled Clock Out": "Clock Out programado",
    "On Time": "A tiempo",
    "None": "Sin status",
}


# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------

def safe_csv_bytes(df: pd.DataFrame) -> bytes:
    safe = df.copy()
    dangerous_prefixes = ("=", "+", "-", "@")
    for column in safe.columns:
        if pd.api.types.is_object_dtype(safe[column]) or pd.api.types.is_string_dtype(safe[column]):
            safe[column] = safe[column].map(
                lambda value: (
                    "'" + str(value)
                    if not pd.isna(value) and str(value).startswith(dangerous_prefixes)
                    else value
                )
            )
    return safe.to_csv(index=False).encode("utf-8-sig")


def config_from_secrets() -> OracleBIConfig:
    try:
        oracle = st.secrets["oracle"]
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError("Oracle secrets are not configured.") from exc

    return OracleBIConfig(
        auth_server=str(oracle.get("auth_server", "")),
        application_server=str(oracle.get("application_server", "")),
        org_identifier=str(oracle.get("org_identifier", "")),
        client_id=str(oracle.get("client_id", "")),
        username=str(oracle.get("username", "")),
        password=str(oracle.get("password", "")),
        application_name=str(oracle.get("application_name", "Meal Compliance Dashboard")),
        timeout_seconds=int(oracle.get("timeout_seconds", 45)),
        verify_ssl=bool(oracle.get("verify_ssl", True)),
    )


def config_fingerprint(config: OracleBIConfig) -> str:
    material = "|".join(
        [
            config.auth_server,
            config.application_server,
            config.org_identifier,
            config.client_id,
            config.username,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def get_or_create_client(config: OracleBIConfig) -> OracleBIClient:
    fingerprint = config_fingerprint(config)
    if st.session_state.get("oracle_client_fingerprint") != fingerprint:
        st.session_state.oracle_client = OracleBIClient(config)
        st.session_state.oracle_client_fingerprint = fingerprint
        st.session_state.pop("locations_payload", None)
    return st.session_state.oracle_client


def reset_state() -> None:
    for key in (
        "oracle_client",
        "oracle_client_fingerprint",
        "locations_payload",
        "analysis_bundle",
        "dimension_payloads",
        "analysis_context",
    ):
        st.session_state.pop(key, None)


def load_json_upload(file_obj: Any) -> Any:
    if file_obj is None:
        return None
    return json.loads(file_obj.getvalue().decode("utf-8-sig"))


def _logo_data_uri() -> str:
    logo_path = Path(__file__).parent / "assets" / "broken_yolk_logo.png"
    if not logo_path.exists():
        return ""
    encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _split_codes(value: Any) -> list[str]:
    if pd.isna(value) or not str(value).strip():
        return []
    return [piece.strip() for piece in str(value).split(",") if piece.strip()]


def _labels_for_codes(codes: list[str]) -> str:
    return " · ".join(RESULT_LABELS.get(code, code) for code in codes)


def _action_for_codes(codes: list[str]) -> str:
    actions: list[str] = []
    for code in codes:
        action = RESULT_ACTIONS.get(code, "Revisar el caso.")
        if action not in actions:
            actions.append(action)
    return " ".join(actions) if actions else "Sin acción requerida."


def _format_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%m/%d/%Y")


def _format_datetime(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%m/%d/%Y %I:%M %p")


def _format_time(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%I:%M %p")


def _filter_employee(df: pd.DataFrame, selected_employee: str, column: str = "Employee") -> pd.DataFrame:
    if df.empty or selected_employee == "Todos los empleados" or column not in df.columns:
        return df.copy()
    return df[df[column].astype(str) == selected_employee].copy()


def _metric_row(metrics: list[tuple[str, Any, str | None]]) -> None:
    columns = st.columns(len(metrics))
    for column, (label, value, help_text) in zip(columns, metrics):
        column.metric(label, value, help=help_text)


# ---------------------------------------------------------------------------
# Branding and UI shell
# ---------------------------------------------------------------------------

def render_global_styles() -> None:
    st.markdown(
        f"""
        <style>
        :root {{
            --brand-blue: {BRAND_BLUE};
            --brand-blue-dark: {BRAND_BLUE_DARK};
            --brand-text: {BRAND_TEXT};
            --brand-muted: {BRAND_MUTED};
            --surface: #ffffff;
            --border: #e4e7ec;
            --background: #f3f5f8;
        }}
        html, body, [class*="css"] {{ font-family: Inter, Arial, sans-serif; }}
        .stApp {{ background: var(--background); }}
        .block-container {{
            padding-top: 1.2rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }}
        [data-testid="stSidebar"] {{
            background: #eef1f5;
            border-right: 1px solid #dfe3e8;
        }}
        [data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}
        .by-hero {{
            width: 100%;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 20px 24px;
            margin: 0 0 18px 0;
            display: flex;
            align-items: center;
            gap: 20px;
            box-shadow: 0 8px 25px rgba(16, 24, 40, .06);
            overflow: hidden;
        }}
        .by-hero-logo {{
            width: 92px;
            min-width: 92px;
            height: auto;
            object-fit: contain;
        }}
        .by-hero-copy {{ min-width: 0; }}
        .by-hero-title {{
            color: var(--brand-text);
            font-size: clamp(1.45rem, 2.2vw, 2.05rem);
            line-height: 1.15;
            font-weight: 800;
            margin: 0;
            overflow-wrap: anywhere;
        }}
        .by-hero-subtitle {{
            color: var(--brand-muted);
            font-size: 1rem;
            margin-top: 7px;
        }}
        .by-hero-author {{
            color: var(--brand-blue-dark);
            font-size: .9rem;
            font-weight: 700;
            margin-top: 8px;
        }}
        .context-banner {{
            background: #ffffff;
            border-left: 5px solid var(--brand-blue);
            border-radius: 12px;
            padding: 13px 17px;
            margin: 6px 0 14px 0;
            color: var(--brand-text);
            box-shadow: 0 3px 12px rgba(16, 24, 40, .04);
        }}
        .section-title {{
            color: var(--brand-text);
            font-size: 1.25rem;
            font-weight: 800;
            margin: 8px 0 4px 0;
        }}
        .section-kicker {{ color: var(--brand-muted); margin-bottom: 12px; }}
        div[data-testid="stMetric"] {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 15px 16px;
            box-shadow: 0 4px 14px rgba(16, 24, 40, .05);
        }}
        div[data-testid="stMetric"] label {{ color: var(--brand-muted); }}
        .stButton > button, .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] button {{
            border-radius: 10px;
            font-weight: 700;
            min-height: 2.7rem;
        }}
        button[kind="primary"] {{
            background: var(--brand-blue) !important;
            border-color: var(--brand-blue) !important;
        }}
        button[kind="primary"]:hover {{
            background: var(--brand-blue-dark) !important;
            border-color: var(--brand-blue-dark) !important;
        }}
        .stTabs [data-baseweb="tab-list"] {{
            gap: 6px;
            background: #ffffff;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 5px;
            overflow-x: auto;
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 8px;
            padding-left: 14px;
            padding-right: 14px;
            white-space: nowrap;
        }}
        .stTabs [aria-selected="true"] {{
            background: #e8f6ff;
            color: var(--brand-blue-dark);
        }}
        [data-testid="stDataFrame"] {{
            background: #ffffff;
            border-radius: 12px;
        }}
        .audit-note {{
            background: #fff7e6;
            border: 1px solid #fedf89;
            color: #7a2e0e;
            padding: 12px 14px;
            border-radius: 10px;
            margin-bottom: 12px;
        }}
        .footer-note {{
            text-align: center;
            color: #98a2b3;
            font-size: .82rem;
            padding-top: 22px;
        }}
        @media (max-width: 760px) {{
            .block-container {{ padding-left: .8rem; padding-right: .8rem; }}
            .by-hero {{ padding: 16px; gap: 14px; align-items: flex-start; }}
            .by-hero-logo {{ width: 66px; min-width: 66px; }}
            .by-hero-subtitle {{ font-size: .9rem; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    logo = _logo_data_uri()
    logo_html = f'<img class="by-hero-logo" src="{logo}" alt="The Broken Yolk Cafe">' if logo else ""
    st.markdown(
        f"""
        <div class="by-hero">
            {logo_html}
            <div class="by-hero-copy">
                <div class="by-hero-title">Meal Violations Dashboard</div>
                <div class="by-hero-subtitle">Oracle MICROS Simphony · Auditoría de meals en California</div>
                <div class="by-hero-author">The Broken Yolk Cafe · By Jordan Memije</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_rules() -> CaliforniaMealRules:
    with st.sidebar.expander("Reglas aplicadas", expanded=False):
        st.markdown(
            """
- Primer meal: cuando se trabajan más de 5 horas.
- Waiver del primer meal: únicamente si el total no supera 6 horas.
- Segundo meal: cuando se trabajan más de 10 horas.
- Waiver del segundo meal: únicamente si el total no supera 12 horas y el primero no fue renunciado.
- Duración mínima: 30 minutos.
- Un gap sin status de break se manda a revisión; no se confirma automáticamente.
            """
        )
    return CaliforniaMealRules()


# ---------------------------------------------------------------------------
# Friendly tables
# ---------------------------------------------------------------------------

def _friendly_workdays(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        automatic = _split_codes(row.get("Automatic Violations"))
        reviews = _split_codes(row.get("Reviews"))
        if automatic:
            status = "🔴 Violación confirmada"
            priority = "Alta"
            codes = automatic
        elif reviews:
            status = "🟠 Revisión requerida"
            priority = "Media"
            codes = reviews
        else:
            status = "🟢 Sin hallazgos"
            priority = "Sin acción"
            codes = ["COMPLIANT"]
        rows.append(
            {
                "Prioridad": priority,
                "Empleado": row.get("Employee", ""),
                "ID nómina": row.get("Payroll ID", ""),
                "Fecha": _format_date(row.get("Business Date")),
                "Puesto(s)": row.get("Role(s)", ""),
                "Entrada": _format_time(row.get("First Clock In")),
                "Salida": _format_time(row.get("Last Clock Out")),
                "Horas": row.get("Worked Hours", 0),
                "Meals confirmados": row.get("Confirmed Meals", 0),
                "Meals probables": row.get("Probable Meals", 0),
                "Estado": status,
                "Hallazgo": _labels_for_codes(codes),
                "Acción recomendada": _action_for_codes(codes),
                "Premium estimado": row.get("Estimated Meal Premium", 0),
                "Ajustes": row.get("Adjustment Count", 0),
            }
        )
    return pd.DataFrame(rows)


def _friendly_cases(df: pd.DataFrame, code_column: str, *, violation: bool) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = str(row.get(code_column) or "")
        rows.append(
            {
                "Empleado": row.get("Employee", ""),
                "ID nómina": row.get("Payroll ID", ""),
                "Fecha": _format_date(row.get("Business Date")),
                "Puesto(s)": row.get("Role(s)", ""),
                "Entrada": _format_time(row.get("First Clock In")),
                "Salida": _format_time(row.get("Last Clock Out")),
                "Horas": row.get("Worked Hours", 0),
                "Hallazgo": RESULT_LABELS.get(code, code),
                "Acción recomendada": RESULT_ACTIONS.get(code, "Revisar el caso."),
                "Premium estimado": row.get("Estimated Meal Premium", 0) if violation else 0,
            }
        )
    return pd.DataFrame(rows)


def _friendly_punches(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    result = pd.DataFrame(
        {
            "Empleado": df.get("Employee", ""),
            "ID nómina": df.get("Payroll ID", ""),
            "Fecha": df.get("Business Date", pd.Series(dtype=object)).map(_format_date),
            "Puesto(s)": df.get("Role(s)", ""),
            "Entrada": df.get("First Clock In", pd.Series(dtype=object)).map(_format_time),
            "Salida": df.get("Last Clock Out", pd.Series(dtype=object)).map(_format_time),
            "Horas": df.get("Worked Hours", 0),
            "Error de marcación": df.get("Punch Error", ""),
            "Acción recomendada": "Corregir o confirmar la marcación en MICROS.",
        }
    )
    replacements = {
        "open timecard(s) without Clock Out": "timecard(s) abierto(s) sin Clock Out",
        "timecard(s) with Clock Out before Clock In": "timecard(s) con Clock Out anterior al Clock In",
        "zero-duration timecard(s)": "timecard(s) de duración cero",
        "Overlapping working timecards": "Timecards de trabajo traslapados",
        "manager/automatic Clock Out(s) require review": "Clock Out(s) de manager/automático que requieren revisión",
    }
    for old, new in replacements.items():
        result["Error de marcación"] = result["Error de marcación"].astype(str).str.replace(old, new, regex=False)
    return result


def _friendly_meals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    evidence_map = {
        "Oracle paid-break shift": "Break pagado registrado en Oracle",
        "Oracle unpaid-break shift": "Meal/break no pagado registrado en Oracle",
        "Clock-out status On Break + timestamps": "Status On Break + timestamps",
        "Clock-out status Paid Break + timestamps": "Status Paid Break + timestamps",
        "Timestamp gap without break status": "Gap de timestamps sin status de break",
    }
    return pd.DataFrame(
        {
            "Empleado": df.get("Employee", ""),
            "ID nómina": df.get("Payroll ID", ""),
            "Fecha": df.get("Business Date", pd.Series(dtype=object)).map(_format_date),
            "Meal #": df.get("Meal Sequence", ""),
            "Inicio": df.get("Meal Start", pd.Series(dtype=object)).map(_format_time),
            "Fin": df.get("Meal End", pd.Series(dtype=object)).map(_format_time),
            "Duración (min)": df.get("Duration Minutes", 0),
            "Horas antes": df.get("Worked Hours Before", 0),
            "Evidencia": df.get("Evidence", "").map(lambda value: evidence_map.get(str(value), value)),
            "Confirmado": df.get("Confirmed Duty-Free Timestamp", False).map(lambda value: "Sí" if bool(value) else "No"),
            "Pagado": df.get("Paid", False).map(lambda value: "Sí" if bool(value) else "No"),
        }
    )


def _friendly_timecards(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Empleado": df.get("employee_name", ""),
            "ID nómina": df.get("payroll_id", ""),
            "Employee Num": df.get("employee_num", ""),
            "Fecha": df.get("business_date", pd.Series(dtype=object)).map(_format_date),
            "Puesto": df.get("job_code", ""),
            "Tipo": df.get("shift_type_label", "").map(lambda value: SHIFT_LABELS.get(str(value), value)),
            "Clock In": df.get("clock_in_local", pd.Series(dtype=object)).map(_format_datetime),
            "Clock Out": df.get("clock_out_local", pd.Series(dtype=object)).map(_format_datetime),
            "Horas regulares": df.get("regular_hours", 0),
            "Overtime": df.get("overtime_hours", 0),
            "Status de salida": df.get("clock_out_status_label", "").map(lambda value: CLOCK_OUT_LABELS.get(str(value), value)),
            "Ajustes": df.get("adjustment_count", 0),
            "Última actualización UTC": df.get("last_updated_utc", pd.Series(dtype=object)).map(_format_datetime),
            "Nombre vinculado": df.get("employee_name_resolved", False).map(lambda value: "Sí" if bool(value) else "No"),
        }
    )


def _friendly_adjustments(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    result = df.copy()
    for column in ("Business Date",):
        if column in result:
            result[column] = result[column].map(_format_date)
    for column in (
        "Adjustment UTC",
        "Previous Clock In",
        "Current Clock In",
        "Previous Clock Out",
        "Current Clock Out",
        "Last Updated UTC",
    ):
        if column in result:
            result[column] = result[column].map(_format_datetime)
    result = result.rename(
        columns={
            "Business Date": "Fecha",
            "Employee": "Empleado",
            "Payroll ID": "ID nómina",
            "Timecard ID": "Timecard ID",
            "Adjustment ID": "Adjustment ID",
            "Adjustment UTC": "Fecha del ajuste (UTC)",
            "Manager": "Manager",
            "Manual Adjustment": "Ajuste manual",
            "Reason": "Motivo",
            "Changed Fields": "Campos modificados",
            "Adjustment Type": "Tipo de ajuste",
            "Risk": "Riesgo",
            "Meal Impact": "Impacto en meal",
            "Previous Clock In": "Clock In anterior",
            "Current Clock In": "Clock In actual",
            "Clock In Delta Minutes": "Cambio Clock In (min)",
            "Previous Clock Out": "Clock Out anterior",
            "Current Clock Out": "Clock Out actual",
            "Clock Out Delta Minutes": "Cambio Clock Out (min)",
            "Estimated Previous Duration Minutes": "Duración anterior estimada (min)",
            "Current Duration Minutes": "Duración actual (min)",
            "Estimated Duration Delta Minutes": "Cambio estimado de duración (min)",
            "Previous Job": "Puesto anterior",
            "Current Job": "Puesto actual",
            "Previous Revenue Center": "RVC anterior",
            "Current Revenue Center": "RVC actual",
        }
    )
    preferred = [
        "Riesgo",
        "Empleado",
        "ID nómina",
        "Fecha",
        "Manager",
        "Fecha del ajuste (UTC)",
        "Motivo",
        "Campos modificados",
        "Tipo de ajuste",
        "Impacto en meal",
        "Clock In anterior",
        "Clock In actual",
        "Cambio Clock In (min)",
        "Clock Out anterior",
        "Clock Out actual",
        "Cambio Clock Out (min)",
        "Duración anterior estimada (min)",
        "Duración actual (min)",
        "Cambio estimado de duración (min)",
        "Puesto anterior",
        "Puesto actual",
        "RVC anterior",
        "RVC actual",
        "Timecard ID",
        "Adjustment ID",
        "Ajuste manual",
    ]
    return result[[column for column in preferred if column in result.columns]]


def render_name_resolution(bundle: AnalysisBundle) -> None:
    raw = bundle.raw_timecards
    if raw.empty or "employee_name_resolved" not in raw.columns:
        return

    total = len(raw)
    unresolved = raw[~raw["employee_name_resolved"].fillna(False).astype(bool)]
    resolved_count = total - len(unresolved)
    employee_payload = (st.session_state.get("dimension_payloads") or {}).get("employees_payload", {})
    employee_catalog_count = len(employee_payload.get("employees", []) or []) if isinstance(employee_payload, dict) else 0

    if unresolved.empty:
        st.success(
            f"Nombres vinculados correctamente: {resolved_count:,} de {total:,} timecards. "
            f"Oracle devolvió {employee_catalog_count:,} empleados en el catálogo."
        )
        return

    unique_unresolved = unresolved[["employee_num", "payroll_id", "employee_name"]].drop_duplicates()
    st.warning(
        f"No fue posible vincular el nombre de {len(unique_unresolved):,} empleado(s). "
        f"La app usa el ID de nómina como respaldo. Oracle devolvió {employee_catalog_count:,} empleados en getEmployeeDimensions."
    )
    with st.expander("Ver empleados sin nombre vinculado"):
        st.dataframe(
            unique_unresolved.rename(
                columns={
                    "employee_num": "Employee Num",
                    "payroll_id": "ID nómina",
                    "employee_name": "Nombre mostrado",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Results and dashboards
# ---------------------------------------------------------------------------

def _employee_options(summary: pd.DataFrame) -> list[str]:
    if summary.empty:
        return ["Todos los empleados"]
    return ["Todos los empleados", *sorted(summary["Employee"].dropna().astype(str).unique())]


def _findings_chart_data(violations: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    if not violations.empty:
        piece = violations[["Violation"]].copy()
        piece["Tipo"] = "Violación"
        piece = piece.rename(columns={"Violation": "Código"})
        pieces.append(piece)
    if not reviews.empty:
        piece = reviews[["Review"]].copy()
        piece["Tipo"] = "Revisión"
        piece = piece.rename(columns={"Review": "Código"})
        pieces.append(piece)
    if not pieces:
        return pd.DataFrame()
    findings = pd.concat(pieces, ignore_index=True)
    findings["Hallazgo"] = findings["Código"].map(lambda code: RESULT_LABELS.get(str(code), str(code)))
    return findings.groupby(["Hallazgo", "Tipo"]).size().unstack(fill_value=0).sort_values(
        list(findings["Tipo"].unique()), ascending=False
    )


def render_dashboard_tab(
    *,
    employee_summary: pd.DataFrame,
    workdays: pd.DataFrame,
    violations: pd.DataFrame,
    reviews: pd.DataFrame,
    punches: pd.DataFrame,
    adjustments: pd.DataFrame,
) -> None:
    st.markdown('<div class="section-title">Resumen visual</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-kicker">La vista prioriza meals faltantes, tardíos, cortos y ajustes que pueden modificar el resultado.</div>',
        unsafe_allow_html=True,
    )

    expected_meals = int(employee_summary.get("Meals Expected by Hours", pd.Series(dtype=float)).sum())
    confirmed_meals = int(employee_summary.get("Confirmed Meals", pd.Series(dtype=float)).sum())
    missing_meals = int(employee_summary.get("Missing Meals", pd.Series(dtype=float)).sum())
    late_meals = int(employee_summary.get("Late Meals", pd.Series(dtype=float)).sum())
    short_meals = int(employee_summary.get("Short Meals", pd.Series(dtype=float)).sum())
    affected = int((employee_summary.get("Automatic Violations", pd.Series(dtype=float)) > 0).sum())
    premium_days = (
        violations[["Employee", "Business Date"]].drop_duplicates().shape[0]
        if not violations.empty and {"Employee", "Business Date"}.issubset(violations.columns)
        else 0
    )
    premium_amount = float(
        workdays.loc[
            workdays.get("Potential Premium Workday", pd.Series(False, index=workdays.index)).fillna(False).astype(bool),
            "Estimated Meal Premium",
        ].sum()
        if not workdays.empty and "Estimated Meal Premium" in workdays
        else 0.0
    )

    _metric_row(
        [
            ("Meals esperados", expected_meals, "Estimación por horas trabajadas: 1 después de 5 h y 2 después de 10 h."),
            ("Meals confirmados", confirmed_meals, "Meals con evidencia de unpaid break o status On Break suficiente."),
            ("Meals faltantes", missing_meals, "Primeros o segundos meals no registrados."),
            ("Meals tardíos", late_meals, "Meals confirmados después de la hora límite."),
        ]
    )
    _metric_row(
        [
            ("Meals cortos", short_meals, "Meals explícitos menores a 30 minutos."),
            ("Empleados afectados", affected, "Empleados con al menos una violación automática."),
            ("Workdays con premium", premium_days, "Se consolida por empleado y business date."),
            ("Premium estimado", f"${premium_amount:,.2f}", "Estimación usando la tarifa regular disponible en Oracle."),
        ]
    )

    if premium_days:
        st.error(f"Se detectaron {premium_days} workday(s) con violación automática y {affected} empleado(s) afectados.")
    elif len(reviews) or len(punches):
        st.warning("No hay violaciones automáticas en el filtro actual, pero sí casos pendientes de revisión.")
    else:
        st.success("No se detectaron hallazgos que requieran acción en el filtro actual.")

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("### Meals por empleado")
        if employee_summary.empty:
            st.info("No hay empleados para mostrar.")
        else:
            chart = employee_summary.head(15).set_index("Employee")[[
                "Confirmed Meals",
                "Missing Meals",
                "Late Meals",
                "Short Meals",
            ]]
            chart = chart.rename(
                columns={
                    "Confirmed Meals": "Confirmados",
                    "Missing Meals": "Faltantes",
                    "Late Meals": "Tardíos",
                    "Short Meals": "Cortos",
                }
            )
            st.bar_chart(chart, horizontal=True, stack=True, height=max(320, min(620, 38 * len(chart))))

    with col_right:
        st.markdown("### Hallazgos por tipo")
        findings = _findings_chart_data(violations, reviews)
        if findings.empty:
            st.info("No hay hallazgos para graficar.")
        else:
            st.bar_chart(findings, horizontal=True, stack=True, height=max(320, min(620, 42 * len(findings))))

    col_daily, col_managers = st.columns(2)
    with col_daily:
        st.markdown("### Violaciones por día")
        if violations.empty:
            st.info("No hay violaciones automáticas.")
        else:
            daily = violations.copy()
            daily["Fecha"] = pd.to_datetime(daily["Business Date"], errors="coerce")
            daily = daily.groupby("Fecha").size().rename("Violaciones")
            st.line_chart(daily, height=280)

    with col_managers:
        st.markdown("### Ajustes por manager")
        if adjustments.empty:
            st.info("Oracle no devolvió ajustes para el filtro actual.")
        else:
            manager_counts = adjustments.groupby("Manager").size().sort_values(ascending=False).head(12)
            st.bar_chart(manager_counts, horizontal=True, height=280)

    st.markdown("### Empleados que requieren atención")
    if employee_summary.empty:
        st.info("No hay datos para mostrar.")
    else:
        attention = employee_summary[
            employee_summary["Status"].isin(["Atención inmediata", "Revisión requerida"])
        ].copy()
        if attention.empty:
            st.success("No hay empleados pendientes en el filtro actual.")
        else:
            attention["Status"] = attention["Status"].map(
                {"Atención inmediata": "🔴 Atención inmediata", "Revisión requerida": "🟠 Revisión requerida"}
            )
            display_columns = [
                "Status",
                "Employee",
                "Payroll ID",
                "Meals Expected by Hours",
                "Confirmed Meals",
                "Missing Meals",
                "Late Meals",
                "Short Meals",
                "Review Cases",
                "Adjustment Records",
                "Premium Workdays",
            ]
            st.dataframe(
                attention[display_columns].rename(
                    columns={
                        "Status": "Estado",
                        "Employee": "Empleado",
                        "Payroll ID": "ID nómina",
                        "Meals Expected by Hours": "Meals esperados",
                        "Confirmed Meals": "Confirmados",
                        "Missing Meals": "Faltantes",
                        "Late Meals": "Tardíos",
                        "Short Meals": "Cortos",
                        "Review Cases": "Revisiones",
                        "Adjustment Records": "Ajustes",
                        "Premium Workdays": "Workdays premium",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )


def render_employee_tab(
    *,
    employee_summary: pd.DataFrame,
    selected_employee: str,
    workdays: pd.DataFrame,
    meals: pd.DataFrame,
    adjustments: pd.DataFrame,
) -> None:
    st.markdown('<div class="section-title">Meals por empleado</div>', unsafe_allow_html=True)
    st.caption(
        "Meals esperados se estima por horas trabajadas. Un waiver vigente puede reducir la obligación y debe verificarse por separado."
    )
    if employee_summary.empty:
        st.info("No hay empleados para mostrar.")
        return

    display = employee_summary.copy()
    display["Status"] = display["Status"].map(
        {
            "Atención inmediata": "🔴 Atención inmediata",
            "Revisión requerida": "🟠 Revisión requerida",
            "Ajustes detectados": "🔵 Ajustes detectados",
            "Sin hallazgos": "🟢 Sin hallazgos",
        }
    )
    display = display.rename(
        columns={
            "Employee": "Empleado",
            "Payroll ID": "ID nómina",
            "Workdays": "Jornadas",
            "Worked Hours": "Horas trabajadas",
            "Meals Expected by Hours": "Meals esperados",
            "Confirmed Meals": "Meals confirmados",
            "Probable Meals": "Meals probables",
            "Missing Meals": "Meals faltantes",
            "Late Meals": "Meals tardíos",
            "Short Meals": "Meals cortos",
            "Automatic Violations": "Violaciones",
            "Review Cases": "Revisiones",
            "Punch Errors": "Punch errors",
            "Adjusted Timecards": "Timecards ajustados",
            "Adjustment Records": "Ajustes registrados",
            "Managers Involved": "Managers",
            "Premium Workdays": "Workdays premium",
            "Estimated Premium": "Premium estimado",
            "Meal Coverage %": "Cobertura de meals",
            "Status": "Estado",
        }
    )
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Horas trabajadas": st.column_config.NumberColumn(format="%.2f"),
            "Premium estimado": st.column_config.NumberColumn(format="$%.2f"),
            "Cobertura de meals": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    if selected_employee == "Todos los empleados":
        st.info("Selecciona un empleado en el filtro superior para ver su detalle individual.")
        return

    row = employee_summary[employee_summary["Employee"].astype(str) == selected_employee]
    if row.empty:
        return
    employee = row.iloc[0]
    st.markdown(f"### Detalle de {html.escape(selected_employee)}")
    _metric_row(
        [
            ("Jornadas", int(employee["Workdays"]), None),
            ("Meals esperados", int(employee["Meals Expected by Hours"]), None),
            ("Meals confirmados", int(employee["Confirmed Meals"]), None),
            ("Meals faltantes", int(employee["Missing Meals"]), None),
            ("Meals tardíos/cortos", int(employee["Late Meals"] + employee["Short Meals"]), None),
            ("Ajustes", int(employee["Adjustment Records"]), None),
        ]
    )

    detail_tabs = st.tabs(["Jornadas", "Meals detectados", "Ajustes"])
    with detail_tabs[0]:
        friendly = _friendly_workdays(workdays)
        st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("Sin jornadas.")
    with detail_tabs[1]:
        friendly = _friendly_meals(meals)
        st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("Sin meals detectados.")
    with detail_tabs[2]:
        friendly = _friendly_adjustments(adjustments)
        st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("Sin ajustes registrados.")


def render_violations_tab(
    *, violations: pd.DataFrame, reviews: pd.DataFrame, punches: pd.DataFrame
) -> None:
    violation_tab, review_tab, punch_tab = st.tabs(["Violaciones automáticas", "Revisión manual", "Punch errors"])
    with violation_tab:
        friendly = _friendly_cases(violations, "Violation", violation=True)
        if friendly.empty:
            st.info("No hay violaciones automáticas.")
        else:
            st.dataframe(
                friendly,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Horas": st.column_config.NumberColumn(format="%.2f"),
                    "Premium estimado": st.column_config.NumberColumn(format="$%.2f"),
                },
            )
    with review_tab:
        friendly = _friendly_cases(reviews, "Review", violation=False)
        st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("No hay revisiones pendientes.")
    with punch_tab:
        friendly = _friendly_punches(punches)
        st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("No hay punch errors.")


def render_adjustment_audit_tab(adjustments: pd.DataFrame) -> None:
    st.markdown('<div class="section-title">Auditoría de ajustes manuales</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="audit-note"><strong>Cómo leer esta sección:</strong> Oracle devuelve quién hizo el ajuste, cuándo ocurrió y los valores anteriores de los campos modificados. Cuando existen varios ajustes sobre el mismo timecard, la comparación de duración anterior contra el valor actual es una estimación respecto al estado final.</div>',
        unsafe_allow_html=True,
    )
    if adjustments.empty:
        st.info("Oracle no devolvió ajustes para el periodo seleccionado.")
        return

    manual = int((adjustments["Manual Adjustment"] == "Sí").sum())
    managers = int(adjustments["Manager"].nunique())
    high_risk = int((adjustments["Risk"] == "Alto").sum())
    adjusted_cards = int(adjustments["Timecard ID"].nunique())
    _metric_row(
        [
            ("Ajustes registrados", len(adjustments), None),
            ("Ajustes con manager", manual, "Oracle identificó el nombre del manager que realizó el ajuste."),
            ("Managers involucrados", managers, None),
            ("Timecards ajustados", adjusted_cards, None),
            ("Posible impacto en meal", high_risk, "Cambios de Clock In/Out que pueden modificar meals u horas trabajadas."),
        ]
    )

    col_employee, col_manager, col_risk, col_type = st.columns(4)
    with col_employee:
        employee_options = ["Todos", *sorted(adjustments["Employee"].dropna().astype(str).unique())]
        audit_employee = st.selectbox("Empleado", employee_options, key="audit_employee")
    with col_manager:
        manager_options = ["Todos", *sorted(adjustments["Manager"].dropna().astype(str).unique())]
        audit_manager = st.selectbox("Manager", manager_options, key="audit_manager")
    with col_risk:
        risk_options = ["Todos", *sorted(adjustments["Risk"].dropna().astype(str).unique())]
        audit_risk = st.selectbox("Riesgo", risk_options, key="audit_risk")
    with col_type:
        type_options = ["Todos", *sorted(adjustments["Adjustment Type"].dropna().astype(str).unique())]
        audit_type = st.selectbox("Tipo", type_options, key="audit_type")

    filtered = adjustments.copy()
    if audit_employee != "Todos":
        filtered = filtered[filtered["Employee"].astype(str) == audit_employee]
    if audit_manager != "Todos":
        filtered = filtered[filtered["Manager"].astype(str) == audit_manager]
    if audit_risk != "Todos":
        filtered = filtered[filtered["Risk"].astype(str) == audit_risk]
    if audit_type != "Todos":
        filtered = filtered[filtered["Adjustment Type"].astype(str) == audit_type]

    col_manager_chart, col_type_chart = st.columns(2)
    with col_manager_chart:
        st.markdown("### Ajustes por manager")
        manager_counts = filtered.groupby("Manager").size().sort_values(ascending=False).head(15)
        st.bar_chart(manager_counts, horizontal=True, height=300) if not manager_counts.empty else st.info("Sin datos.")
    with col_type_chart:
        st.markdown("### Ajustes por tipo")
        type_counts = filtered.groupby("Adjustment Type").size().sort_values(ascending=False)
        st.bar_chart(type_counts, horizontal=True, height=300) if not type_counts.empty else st.info("Sin datos.")

    st.markdown("### Bitácora de ajustes")
    friendly = _friendly_adjustments(filtered)
    st.dataframe(
        friendly,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Cambio Clock In (min)": st.column_config.NumberColumn(format="%.2f"),
            "Cambio Clock Out (min)": st.column_config.NumberColumn(format="%.2f"),
            "Cambio estimado de duración (min)": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def render_downloads(
    bundle: AnalysisBundle,
    employee_summary: pd.DataFrame,
    adjustments: pd.DataFrame,
) -> None:
    tables = [
        ("Resumen por empleado", employee_summary, "employee_meal_summary.csv"),
        ("Auditoría de ajustes", _friendly_adjustments(adjustments), "timecard_adjustment_audit.csv"),
        ("Violaciones", _friendly_cases(bundle.violations, "Violation", violation=True), "meal_violations.csv"),
        ("Revisiones", _friendly_cases(bundle.reviews, "Review", violation=False), "meal_reviews.csv"),
        ("Punch errors", _friendly_punches(bundle.punch_errors), "punch_errors.csv"),
        ("Jornadas", _friendly_workdays(bundle.workdays), "workdays.csv"),
        ("Meals", _friendly_meals(bundle.meals), "meal_candidates.csv"),
        ("Timecards", _friendly_timecards(bundle.raw_timecards), "normalized_timecards.csv"),
    ]
    columns = st.columns(3)
    for index, (label, df, filename) in enumerate(tables):
        with columns[index % 3]:
            st.download_button(
                f"Descargar {label}",
                data=safe_csv_bytes(df),
                file_name=filename,
                mime="text/csv",
                use_container_width=True,
                key=f"download_{index}_{APP_VERSION}",
            )


def render_results(bundle: AnalysisBundle) -> None:
    context = st.session_state.get("analysis_context", {})
    location_label = context.get("location_label", "")
    date_label = context.get("date_label", "")
    if location_label or date_label:
        st.markdown(
            f'<div class="context-banner"><strong>{html.escape(str(location_label))}</strong>'
            f'{" · " if location_label and date_label else ""}{html.escape(str(date_label))}</div>',
            unsafe_allow_html=True,
        )

    render_name_resolution(bundle)

    metadata = st.session_state.get("dimension_payloads") or {}
    job_codes = job_code_dimension_map(metadata.get("jobs_payload", {}) or {})
    all_adjustments = build_adjustment_audit(bundle.raw_timecards, job_codes=job_codes)
    all_employee_summary = build_employee_summary(
        workdays=bundle.workdays,
        violations=bundle.violations,
        reviews=bundle.reviews,
        punch_errors=bundle.punch_errors,
        raw_timecards=bundle.raw_timecards,
        adjustments=all_adjustments,
    )

    filter_col, status_col = st.columns([2, 1])
    with filter_col:
        selected_employee = st.selectbox(
            "Empleado",
            _employee_options(all_employee_summary),
            key=f"employee_filter_{APP_VERSION}",
        )
    with status_col:
        st.metric("Empleados en el resultado", len(all_employee_summary))

    workdays = _filter_employee(bundle.workdays, selected_employee)
    violations = _filter_employee(bundle.violations, selected_employee)
    reviews = _filter_employee(bundle.reviews, selected_employee)
    punches = _filter_employee(bundle.punch_errors, selected_employee)
    meals = _filter_employee(bundle.meals, selected_employee)
    raw_timecards = _filter_employee(bundle.raw_timecards, selected_employee, column="employee_name")
    adjustments = _filter_employee(all_adjustments, selected_employee)
    employee_summary = _filter_employee(all_employee_summary, selected_employee)

    tabs = st.tabs(
        [
            "Dashboard",
            "Empleados",
            "Violaciones",
            "Auditoría de ajustes",
            "Jornadas y meals",
            "Timecards",
            "Descargas",
        ]
    )

    with tabs[0]:
        render_dashboard_tab(
            employee_summary=employee_summary,
            workdays=workdays,
            violations=violations,
            reviews=reviews,
            punches=punches,
            adjustments=adjustments,
        )

    with tabs[1]:
        render_employee_tab(
            employee_summary=employee_summary,
            selected_employee=selected_employee,
            workdays=workdays,
            meals=meals,
            adjustments=adjustments,
        )

    with tabs[2]:
        render_violations_tab(violations=violations, reviews=reviews, punches=punches)

    with tabs[3]:
        render_adjustment_audit_tab(adjustments)

    with tabs[4]:
        workday_tab, meal_tab = st.tabs(["Jornadas", "Meals detectados"])
        with workday_tab:
            friendly = _friendly_workdays(workdays)
            st.dataframe(friendly, use_container_width=True, hide_index=True) if not friendly.empty else st.info("No hay jornadas.")
        with meal_tab:
            friendly = _friendly_meals(meals)
            if friendly.empty:
                st.info("No se identificaron candidatos de meal.")
            else:
                st.dataframe(friendly, use_container_width=True, hide_index=True)
                st.caption(
                    "Confirmado = Oracle registró un unpaid break o status On Break con duración suficiente. "
                    "Un gap sin status se conserva como probable y requiere validación."
                )

    with tabs[5]:
        friendly = _friendly_timecards(raw_timecards)
        if friendly.empty:
            st.info("No hay marcaciones para mostrar.")
        else:
            st.dataframe(
                friendly,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Horas regulares": st.column_config.NumberColumn(format="%.2f"),
                    "Overtime": st.column_config.NumberColumn(format="%.2f"),
                },
            )
        with st.expander("Ver detalle técnico"):
            technical = raw_timecards.drop(columns=["raw"], errors="ignore").copy()
            if "adjustments" in technical.columns:
                technical["adjustments"] = technical["adjustments"].map(
                    lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                )
            st.dataframe(technical, use_container_width=True, hide_index=True)

    with tabs[6]:
        render_downloads(bundle, all_employee_summary, all_adjustments)

    with st.expander("Indicadores técnicos del análisis"):
        stats = bundle.stats
        st.write(
            {
                "Versión": APP_VERSION,
                "Timecards consultados": stats.get("timecards", 0),
                "Empleados": stats.get("employees", 0),
                "Jornadas": stats.get("workdays", 0),
                "Hallazgos automáticos": stats.get("automatic_violations", 0),
                "Jornadas con premium potencial": stats.get("premium_workdays", 0),
                "Premium registrado por Oracle": stats.get("oracle_premium_pay", 0.0),
                "Timecards ajustados": stats.get("adjusted_timecards", 0),
                "Registros de ajuste": len(all_adjustments),
                "Timecards abiertos": stats.get("open_timecards", 0),
            }
        )


# ---------------------------------------------------------------------------
# Oracle and JSON sources
# ---------------------------------------------------------------------------

def analyze_api_source(
    client: OracleBIClient,
    loc_ref: str,
    start_date: date,
    end_date: date,
    waiver_records: dict[str, list[dict[str, Any]]],
    rules: CaliforniaMealRules,
) -> tuple[AnalysisBundle, dict[str, Any]]:
    employees_payload = client.get_employees(loc_ref)
    jobs_payload = client.get_job_codes(loc_ref)
    locations_payload = st.session_state.get("locations_payload") or client.get_locations()
    timecard_payloads = client.get_timecards_range(
        loc_ref,
        start_date,
        end_date,
        include_adjustments=True,
        maximum_days=MAX_RANGE_DAYS,
    )

    normalized = normalize_timecards(
        timecard_payloads,
        employees=employee_dimension_map(employees_payload),
        job_codes=job_code_dimension_map(jobs_payload),
        locations=location_dimension_map(locations_payload),
    )
    bundle = analyze_timecards(normalized, rules=rules, waiver_records=waiver_records)
    metadata = {
        "employees_payload": employees_payload,
        "jobs_payload": jobs_payload,
        "locations_payload": locations_payload,
        "timecard_payloads": timecard_payloads,
    }
    return bundle, metadata


def render_oracle_mode(rules: CaliforniaMealRules, waiver_records: dict[str, list[dict[str, Any]]]) -> None:
    try:
        config = config_from_secrets()
    except ValueError:
        st.error("Falta configurar los Secrets de Streamlit con la cuenta Business Intelligence API.")
        return

    client = get_or_create_client(config)
    with st.container(border=True):
        st.markdown("### Consulta a Oracle MICROS")
        col_connect, col_status = st.columns([1, 3])
        with col_connect:
            connect = st.button("Conectar a Oracle", type="primary", use_container_width=True)
        with col_status:
            if st.session_state.get("locations_payload"):
                st.success("Oracle BI API conectado.")
            else:
                st.info("Las credenciales se leen de Streamlit Secrets y no se muestran en pantalla.")

        if connect:
            try:
                with st.spinner("Autenticando y consultando ubicaciones..."):
                    client.authenticate()
                    st.session_state.locations_payload = client.get_locations()
                st.rerun()
            except OracleBIError as error:
                st.error(str(error))
                return

        locations_payload = st.session_state.get("locations_payload")
        if not locations_payload:
            return

        locations = [
            item
            for item in locations_payload.get("locations", []) or []
            if isinstance(item, dict) and item.get("active", True)
        ]
        if locations:
            option_map = {
                f"{item.get('name') or item.get('locName') or item.get('locRef')} — {item.get('locRef')}": str(item.get("locRef"))
                for item in locations
            }
            selected_label = st.selectbox("Ubicación", list(option_map))
            loc_ref = option_map[selected_label]
        else:
            st.warning("Oracle no devolvió ubicaciones; captura el locRef manualmente.")
            selected_label = "Ubicación manual"
            loc_ref = st.text_input("Location Ref")

        default_end = date.today() - timedelta(days=1)
        default_start = default_end - timedelta(days=6)
        col_start, col_end = st.columns(2)
        with col_start:
            start_date = st.date_input("Fecha inicial", value=default_start)
        with col_end:
            end_date = st.date_input("Fecha final", value=default_end)

        if end_date < start_date:
            st.error("La fecha final no puede ser anterior a la inicial.")
            return
        if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
            st.error(f"El rango máximo por ejecución es de {MAX_RANGE_DAYS} días.")
            return

        if st.button("Consultar y analizar", type="primary", use_container_width=True):
            if not loc_ref:
                st.error("Selecciona una ubicación.")
                return
            try:
                with st.spinner("Consultando empleados, puestos, ajustes y timecards en Oracle..."):
                    bundle, metadata = analyze_api_source(
                        client,
                        loc_ref,
                        start_date,
                        end_date,
                        waiver_records,
                        rules,
                    )
                st.session_state.analysis_bundle = bundle
                st.session_state.dimension_payloads = metadata
                st.session_state.analysis_context = {
                    "location_label": selected_label,
                    "date_label": f"{start_date.strftime('%m/%d/%Y')}–{end_date.strftime('%m/%d/%Y')}",
                }
            except (OracleBIError, ValueError) as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"No fue posible completar el análisis: {type(error).__name__}: {error}")


def render_json_mode(rules: CaliforniaMealRules, waiver_records: dict[str, list[dict[str, Any]]]) -> None:
    with st.container(border=True):
        st.info("Modo de validación: carga respuestas JSON de Oracle sin exponer credenciales.")
        timecards_file = st.file_uploader("Respuesta de getTimeCardDetails (.json)", type=["json"], key="json_timecards")
        employees_file = st.file_uploader("Respuesta de getEmployeeDimensions (.json, opcional)", type=["json"], key="json_employees")
        jobs_file = st.file_uploader("Respuesta de getJobCodeDimensions (.json, opcional)", type=["json"], key="json_jobs")
        locations_file = st.file_uploader("Respuesta de getLocationDimensions (.json, opcional)", type=["json"], key="json_locations")

        if st.button("Analizar JSON", type="primary", use_container_width=True):
            if timecards_file is None:
                st.error("Carga al menos la respuesta de getTimeCardDetails.")
                return
            try:
                raw = load_json_upload(timecards_file)
                payloads = raw if isinstance(raw, list) else [raw]
                employees_payload = load_json_upload(employees_file) or {}
                jobs_payload = load_json_upload(jobs_file) or {}
                locations_payload = load_json_upload(locations_file) or {}
                normalized = normalize_timecards(
                    payloads,
                    employees=employee_dimension_map(employees_payload),
                    job_codes=job_code_dimension_map(jobs_payload),
                    locations=location_dimension_map(locations_payload),
                )
                st.session_state.analysis_bundle = analyze_timecards(
                    normalized,
                    rules=rules,
                    waiver_records=waiver_records,
                )
                st.session_state.dimension_payloads = {
                    "employees_payload": employees_payload,
                    "jobs_payload": jobs_payload,
                    "locations_payload": locations_payload,
                    "timecard_payloads": payloads,
                }
                st.session_state.analysis_context = {"location_label": "Validación JSON", "date_label": ""}
            except (ValueError, json.JSONDecodeError) as error:
                st.error(str(error))


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Meal Violations Dashboard",
        page_icon="🍳",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_global_styles()
    render_header()

    if st.session_state.get("app_version") != APP_VERSION:
        reset_state()
        st.session_state.app_version = APP_VERSION

    st.sidebar.markdown("## Meal Compliance")
    st.sidebar.caption(f"The Broken Yolk · Versión {APP_VERSION}")
    rules = render_rules()

    if st.sidebar.button("Cerrar conexión y borrar resultados", use_container_width=True):
        reset_state()
        st.rerun()

    st.sidebar.markdown("### Waivers y acuerdos")
    waiver_file = st.sidebar.file_uploader(
        "CSV opcional de waivers",
        type=["csv"],
        help="Sin este archivo, los turnos elegibles para waiver se clasifican como pendientes.",
    )
    st.sidebar.download_button(
        "Descargar plantilla de waivers",
        data=(
            "employee_key,first_meal_waiver,second_meal_waiver,on_duty_meal_agreement,effective_date,expiration_date\n"
            "12345,false,false,false,2026-01-01,\n"
        ).encode("utf-8-sig"),
        file_name="waiver_template.csv",
        mime="text/csv",
        use_container_width=True,
    )

    try:
        waiver_df = load_waiver_csv(waiver_file)
        waiver_records = waiver_rows_to_records(waiver_df)
    except ValueError as error:
        st.sidebar.error(str(error))
        waiver_records = {}

    source = st.radio(
        "Fuente de datos",
        ["Oracle BI API", "JSON de Oracle para validación"],
        horizontal=True,
    )
    if source == "Oracle BI API":
        render_oracle_mode(rules, waiver_records)
    else:
        render_json_mode(rules, waiver_records)

    bundle = st.session_state.get("analysis_bundle")
    if bundle is not None:
        render_results(bundle)

    with st.expander("Alcance y criterios"):
        st.markdown(
            """
La aplicación consolida resultados por empleado y business date de Oracle. Los timecards abiertos, traslapados o materialmente inconsistentes no generan una conclusión automática. Los gaps de al menos 30 minutos sin evidencia de unpaid break se muestran como meal probable y requieren confirmación humana. Los paid breaks no se consideran automáticamente meals duty-free.

La auditoría de ajustes utiliza el arreglo `adjustments` que Oracle entrega cuando `includeAdjustments=true`. Oracle devuelve la fecha del ajuste, el nombre del manager y los valores anteriores de los campos modificados. El resultado es una auditoría operativa y no sustituye la revisión de nómina, waivers, acuerdos on-duty ni asesoría legal laboral.
            """
        )

    st.markdown(
        '<div class="footer-note">Meal Violations Dashboard · The Broken Yolk Cafe · Developed by Jordan Memije</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
