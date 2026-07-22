from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from compliance.audit import build_adjustment_audit, build_adjustment_result_history
from compliance.engine import AnalysisBundle, analyze_timecards
from compliance.excel_import import (
    ExcelImportError,
    build_template_bytes,
    convert_excel_to_payloads,
    read_workbook_sheet,
    suggest_mapping,
    workbook_sheet_names,
)
from compliance.models import CaliforniaMealRules
from compliance.normalize import (
    assign_legal_workdays,
    employee_dimension_map,
    job_code_dimension_map,
    load_control_totals_csv,
    load_employee_policy_csv,
    load_regular_rate_csv,
    load_workday_config_csv,
    location_dimension_map,
    normalize_timecards,
    policy_rows_to_records,
    regular_rate_rows_to_records,
    workday_rows_to_records,
)
from compliance.reporting import (
    build_employee_summary,
    build_location_coverage_summary,
    build_probable_meal_queue,
    build_review_summary,
    build_second_meal_review_queue,
    build_violation_employee_summary,
)
from compliance.snapshot import (
    compare_snapshot_to_bundle,
    create_executive_snapshot_bytes,
    create_snapshot_bytes,
    load_snapshot_bytes,
)
from compliance.validation import build_data_quality_report, build_source_coverage
from oracle_bi.client import OracleBIClient, OracleBIConfig, OracleBIError
from oracle_bi.settings import config_from_secret_mapping, config_from_toml_file


APP_VERSION = "3.8.1"
MAX_RANGE_DAYS = 31

RESULT_LABELS = {
    "COMPLIANT_BY_PUNCH": "Cumplimiento por marcación",
    "EXCLUDED_EXEMPT": "Excluido por clasificación exenta",
    "FIRST_MEAL_MISSING": "Primer meal presuntamente no proporcionado",
    "FIRST_MEAL_LATE": "Primer meal presuntamente tardío",
    "FIRST_MEAL_SHORT": "Primer meal presuntamente menor a 30 minutos",
    "FIRST_MEAL_WAIVER_UNVERIFIED": "Waiver del primer meal no verificado",
    "SECOND_MEAL_MISSING": "Segundo meal presuntamente no proporcionado",
    "SECOND_MEAL_LATE": "Segundo meal presuntamente tardío",
    "SECOND_MEAL_SHORT": "Segundo meal presuntamente menor a 30 minutos",
    "SECOND_MEAL_WAIVER_UNVERIFIED": "Waiver del segundo meal no verificado",
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Paid/on-duty meal requiere validación",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Meal probable solo por timestamps",
    "PUNCH_ERROR": "Error de marcación",
    "INCOMPLETE_TIMECARD": "Timecard incompleto",
    "ADJUSTED_TIMECARD_REVIEW": "Timecard ajustado manualmente",
    "ADJUSTMENT_CHANGED_RESULT": "El ajuste cambió el resultado",
    "EMPLOYEE_CLASSIFICATION_UNVERIFIED": "Clasificación exento/no exento no verificada",
    "WORKDAY_CONFIGURATION_UNVERIFIED": "Workday legal no verificado",
    "BUSINESS_DATE_MISMATCH": "Business date no coincide con el workday calculado",
    "MULTI_LOCATION_WORKDAY_REVIEW": "Workday multi-location con configuraciones distintas",
    "REGULAR_RATE_UNVERIFIED": "Regular rate no verificado",
    "SOURCE_COVERAGE_INCOMPLETE": "Cobertura API incompleta",
    "LOCATION_SCOPE_INCOMPLETE": "Faltan ubicaciones del alcance empresarial",
    "EMPLOYEE_NAME_UNRESOLVED": "Nombre de empleado no resuelto",
    "UNKNOWN_ORACLE_CODE": "Código Oracle desconocido",
    "DATA_INTEGRITY_BLOCKED": "Conclusión bloqueada por integridad de datos",
    "INCONCLUSIVE": "Resultado no concluyente",
}

AUDITOR_REASON_LABELS = {
    "FIRST_MEAL_MISSING": "No tomó el primer meal",
    "FIRST_MEAL_LATE": "Primer meal después de la 5.ª hora",
    "FIRST_MEAL_SHORT": "Primer meal menor de 30 minutos",
    "SECOND_MEAL_MISSING": "No tomó el segundo meal",
    "SECOND_MEAL_LATE": "Segundo meal después de la 10.ª hora",
    "SECOND_MEAL_SHORT": "Segundo meal menor de 30 minutos",
}

AUDITOR_REASON_ORDER = [
    "FIRST_MEAL_MISSING",
    "FIRST_MEAL_LATE",
    "FIRST_MEAL_SHORT",
    "SECOND_MEAL_MISSING",
    "SECOND_MEAL_LATE",
    "SECOND_MEAL_SHORT",
]

AUDITOR_REVIEW_OPTIONS = [
    "Pendiente de revisión",
    "Sustentado por los registros",
    "No sustentado por los registros",
    "Requiere evidencia adicional",
]

RESULT_ACTIONS = {
    "FIRST_MEAL_MISSING": "Confirmar que no hubo meal, revisar evidencia y el premium del workday.",
    "FIRST_MEAL_LATE": "Validar el inicio real del primer meal y revisar el premium.",
    "FIRST_MEAL_SHORT": "Confirmar duración; revisar el premium si no hubo otro meal válido.",
    "FIRST_MEAL_WAIVER_UNVERIFIED": "Vincular el waiver firmado y vigente para esa fecha.",
    "SECOND_MEAL_MISSING": "Confirmar que no hubo segundo meal y revisar el premium.",
    "SECOND_MEAL_LATE": "Validar el inicio real del segundo meal.",
    "SECOND_MEAL_SHORT": "Confirmar duración del segundo meal.",
    "SECOND_MEAL_WAIVER_UNVERIFIED": "Verificar waiver vigente y que el primer meal no fue renunciado.",
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Confirmar acuerdo escrito, revocabilidad y condiciones on-duty.",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Confirmar con el empleado/supervisor que el gap fue un meal duty-free.",
    "PUNCH_ERROR": "Corregir o confirmar la marcación en MICROS.",
    "INCOMPLETE_TIMECARD": "Esperar el Clock Out o corregir el timecard.",
    "ADJUSTED_TIMECARD_REVIEW": "Revisar manager, motivo, antes/después e impacto del ajuste.",
    "EMPLOYEE_CLASSIFICATION_UNVERIFIED": "Cargar clasificación legal verificada.",
    "WORKDAY_CONFIGURATION_UNVERIFIED": "Cargar la hora fija de inicio del workday aprobada por Payroll/HR.",
    "BUSINESS_DATE_MISMATCH": "Reconciliar la fecha de negocio de Oracle contra el workday legal.",
    "MULTI_LOCATION_WORKDAY_REVIEW": "Homologar o validar la definición de workday de las ubicaciones.",
    "REGULAR_RATE_UNVERIFIED": "Cargar el regular rate calculado y verificado por Payroll.",
    "DATA_INTEGRITY_BLOCKED": "Resolver los controles críticos antes de cerrar el reporte.",
    "LOCATION_SCOPE_INCOMPLETE": "Seleccionar todas las ubicaciones autorizadas para consolidar horas del empleado.",
    "INCONCLUSIVE": "Revisar punches, políticas y evidencia documental.",
    "COMPLIANT_BY_PUNCH": "Sin anomalías visibles en los registros; la condición duty-free no se prueba solo con MICROS.",
}


# ---------------------------------------------------------------------------
# Utilities and state
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
    cloud_error: Exception | None = None
    try:
        return config_from_secret_mapping(st.secrets)
    except Exception as exc:  # Streamlit may use its own missing-Secrets exception.
        cloud_error = exc

    local_path = Path(__file__).parent / ".streamlit" / "bi_secrets.toml"
    if local_path.exists():
        return config_from_toml_file(local_path)

    raise ValueError(
        "Business Intelligence API Secrets are not configured. Add [oracle_bi] "
        "in Streamlit Cloud Secrets or create .streamlit/bi_secrets.toml locally."
    )


def config_fingerprint(config: OracleBIConfig) -> str:
    material = "|".join(
        [config.auth_server, config.application_server, config.org_identifier, config.client_id, config.username]
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
        "adjustment_audit",
        "adjustment_result_history",
        "snapshot_comparison",
        "previous_snapshot_bytes",
        "review_decisions",
    ):
        st.session_state.pop(key, None)


def _logo_data_uri() -> str:
    path = Path(__file__).parent / "assets" / "broken_yolk_logo.png"
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _format_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%m/%d/%Y")


def _format_time(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%I:%M %p")


def _split_codes(value: Any) -> list[str]:
    if pd.isna(value) or not str(value).strip():
        return []
    return [piece.strip() for piece in str(value).split(",") if piece.strip()]


def _labels(codes: list[str]) -> str:
    return " · ".join(RESULT_LABELS.get(code, code) for code in codes)


def _actions(codes: list[str]) -> str:
    values: list[str] = []
    for code in codes:
        action = RESULT_ACTIONS.get(code, "Revisar el caso.")
        if action not in values:
            values.append(action)
    return " ".join(values)


def _review_state() -> dict[str, dict[str, str]]:
    state = st.session_state.setdefault("review_decisions", {})
    return state if isinstance(state, dict) else {}


def _review_log(details: pd.DataFrame) -> pd.DataFrame:
    """Attach session review decisions to a case-detail frame."""
    if details.empty:
        result = details.copy()
        for column in ("Decisión del auditor", "Notas del auditor", "Actualizado UTC"):
            if column not in result.columns:
                result[column] = pd.Series(dtype="string")
        return result

    state = _review_state()
    result = details.copy()
    result["Decisión del auditor"] = result["Case ID"].map(
        lambda case_id: state.get(str(case_id), {}).get(
            "decision", AUDITOR_REVIEW_OPTIONS[0]
        )
    )
    result["Notas del auditor"] = result["Case ID"].map(
        lambda case_id: state.get(str(case_id), {}).get("notes", "")
    )
    result["Actualizado UTC"] = result["Case ID"].map(
        lambda case_id: state.get(str(case_id), {}).get("updated_utc", "")
    )
    return result


def render_case_review_editor(details: pd.DataFrame, *, editor_key: str) -> pd.DataFrame:
    """Render an auditable, non-legal review decision for visible cases."""
    if details.empty:
        return _review_log(details)

    editable = _review_log(details)
    disabled = [
        column
        for column in editable.columns
        if column not in {"Decisión del auditor", "Notas del auditor"}
    ]
    edited = st.data_editor(
        editable,
        key=editor_key,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=disabled,
        column_config={
            "Decisión del auditor": st.column_config.SelectboxColumn(
                options=AUDITOR_REVIEW_OPTIONS,
                required=True,
                width="medium",
            ),
            "Notas del auditor": st.column_config.TextColumn(
                width="large",
                help="Incluya la evidencia revisada o el motivo de la decisión.",
            ),
        },
    )

    state = _review_state()
    changed = False
    for _, row in edited.iterrows():
        case_id = str(row.get("Case ID") or "").strip()
        if not case_id:
            continue
        decision = str(row.get("Decisión del auditor") or AUDITOR_REVIEW_OPTIONS[0])
        notes = str(row.get("Notas del auditor") or "")
        previous = state.get(case_id, {})
        if previous.get("decision") != decision or previous.get("notes", "") != notes:
            state[case_id] = {
                "decision": decision,
                "notes": notes,
                "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            changed = True
    if changed:
        st.session_state.review_decisions = state
    return _review_log(details)


def build_readiness_table(bundle: AnalysisBundle) -> pd.DataFrame:
    """Summarize source coverage and remaining administrative controls."""
    raw = bundle.raw_timecards.copy()
    workdays = bundle.workdays.copy()
    total_rows = len(raw)
    total_workdays = len(workdays)

    def pct(count: int, total: int) -> str:
        return "—" if total <= 0 else f"{count / total:.0%}"

    names_resolved = int(
        raw.get("employee_name_resolved", pd.Series(False, index=raw.index))
        .fillna(False)
        .astype(bool)
        .sum()
    ) if total_rows else 0
    adjustments_requested = int(
        raw.get("adjustments_request_verified", pd.Series(False, index=raw.index))
        .fillna(False)
        .astype(bool)
        .sum()
    ) if total_rows else 0
    pay_rate_available = int(
        pd.to_numeric(
            raw.get("pay_rate", pd.Series(dtype=float)), errors="coerce"
        ).fillna(0).gt(0).sum()
    ) if total_rows else 0
    workday_verified = int(
        raw.get("workday_config_verified", pd.Series(False, index=raw.index))
        .fillna(False)
        .astype(bool)
        .sum()
    ) if total_rows else 0
    policy_source_present = (
        workdays.get("Policy Source", pd.Series("", index=workdays.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ) if total_workdays else pd.Series(dtype=bool)
    classification_values = (
        workdays.get(
            "Employee Classification", pd.Series("UNKNOWN", index=workdays.index)
        )
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
        .isin(["NON_EXEMPT", "EXEMPT"])
    ) if total_workdays else pd.Series(dtype=bool)
    classification_verified = int(
        (classification_values & policy_source_present).sum()
    ) if total_workdays else 0
    policy_linked = int(policy_source_present.sum()) if total_workdays else 0

    rows = [
        {
            "Control": "BI timecards",
            "Estado": "Confirmado" if total_rows else "Sin datos",
            "Cobertura": f"{bundle.stats.get('timecards', 0):,} timecards",
            "Interpretación": "Fuente operativa principal disponible.",
        },
        {
            "Control": "Nombres y Payroll ID",
            "Estado": "Confirmado" if names_resolved == total_rows and total_rows else "Parcial",
            "Cobertura": pct(names_resolved, total_rows),
            "Interpretación": "Dimensiones de empleados enlazadas a los timecards.",
        },
        {
            "Control": "Ajustes solicitados a Oracle",
            "Estado": "Confirmado" if adjustments_requested == total_rows and total_rows else "Parcial",
            "Cobertura": pct(adjustments_requested, total_rows),
            "Interpretación": "La consulta usa includeAdjustments=true.",
        },
        {
            "Control": "Base pay rate de Oracle",
            "Estado": "Disponible" if pay_rate_available else "No disponible",
            "Cobertura": pct(pay_rate_available, total_rows),
            "Interpretación": "Sirve para estimación; no sustituye el regular rate verificado por Payroll.",
        },
        {
            "Control": "Workday legal verificado",
            "Estado": "Confirmado" if workday_verified == total_rows and total_rows else "Pendiente",
            "Cobertura": pct(workday_verified, total_rows),
            "Interpretación": "Requiere la configuración aprobada por ubicación.",
        },
        {
            "Control": "Clasificación exento/no exento",
            "Estado": "Confirmado" if classification_verified == total_workdays and total_workdays else "Pendiente",
            "Cobertura": pct(classification_verified, total_workdays),
            "Interpretación": "Debe provenir de HR; no se infiere por job code o salario.",
        },
        {
            "Control": "Waivers / acuerdos on-duty",
            "Estado": "Parcial" if policy_linked else "Pendiente",
            "Cobertura": pct(policy_linked, total_workdays),
            "Interpretación": "Requiere evidencia documental vigente por empleado y fecha.",
        },
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Branding and UI
# ---------------------------------------------------------------------------


def render_global_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --blue:#009EFB; --blue-dark:#007ACC; --ink:#172033; --muted:#667085;
            --border:#E4E7EC; --bg:#F4F6F9; --red:#D92D20; --orange:#F79009;
            --green:#039855; --purple:#6941C6;
        }
        .stApp { background:var(--bg); }
        .block-container { max-width:1440px; padding-top:1.2rem; padding-bottom:2.5rem; }
        [data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid var(--border); }
        .hero {
            width:100%;
            min-height:132px;
            box-sizing:border-box;
            overflow:visible;
            background:linear-gradient(135deg,#fff 0%,#eef8ff 100%);
            border:1px solid var(--border);
            border-radius:20px;
            padding:20px 26px;
            display:flex;
            align-items:center;
            gap:20px;
            box-shadow:0 8px 28px rgba(16,24,40,.06);
            margin-bottom:18px;
        }
        .hero-logo {
            flex:0 0 74px;
            width:74px;
            min-width:74px;
            display:flex;
            align-items:center;
            justify-content:center;
            align-self:center;
            overflow:visible;
        }
        .hero-logo img {
            width:70px;
            max-width:70px;
            max-height:86px;
            height:auto;
            display:block;
            object-fit:contain;
        }
        .hero-copy {
            min-width:0;
            display:flex;
            flex-direction:column;
            justify-content:center;
            padding:2px 0;
        }
        .hero-title { color:var(--ink); font-size:2rem; line-height:1.08; font-weight:850; margin:0; }
        .hero-sub { color:var(--muted); font-size:1rem; line-height:1.35; margin-top:7px; }
        .hero-author { color:var(--blue-dark); font-weight:750; line-height:1.3; margin-top:7px; }
        .section-title { font-size:1.25rem; font-weight:800; color:var(--ink); margin:10px 0 8px; }
        .callout { border-radius:12px; padding:13px 15px; margin:8px 0 14px; border:1px solid; }
        .callout-red { background:#FEF3F2; border-color:#FECDCA; color:#912018; }
        .callout-orange { background:#FFFAEB; border-color:#FEDF89; color:#7A2E0E; }
        .callout-blue { background:#EFF8FF; border-color:#B2DDFF; color:#1849A9; }
        .callout-green { background:#ECFDF3; border-color:#ABEFC6; color:#05603A; }
        div[data-testid="stMetric"] { background:#fff; border:1px solid var(--border); border-radius:14px;
                                      padding:14px 16px; box-shadow:0 4px 14px rgba(16,24,40,.05); }
        div[data-testid="stMetric"] label { color:var(--muted); }
        .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button {
            border-radius:10px; font-weight:750; min-height:2.7rem;
        }
        button[kind="primary"] { background:var(--blue)!important; border-color:var(--blue)!important; }
        button[kind="primary"]:hover { background:var(--blue-dark)!important; }
        .stTabs [data-baseweb="tab-list"] { gap:5px; background:#fff; border:1px solid var(--border);
                                             border-radius:12px; padding:5px; overflow-x:auto; }
        .stTabs [data-baseweb="tab"] { border-radius:8px; white-space:nowrap; }
        .stTabs [aria-selected="true"] { background:#E8F6FF; color:var(--blue-dark); }
        [data-testid="stDataFrame"] { background:#fff; border-radius:12px; }
        .footer { text-align:center; color:#98A2B3; font-size:.82rem; padding-top:24px; }
        @media(max-width:760px){
            .block-container{padding-left:.75rem;padding-right:.75rem}
            .hero{min-height:auto;padding:16px 18px;gap:14px;align-items:center}
            .hero-logo{flex-basis:58px;width:58px;min-width:58px}
            .hero-logo img{width:56px;max-width:56px;max-height:70px}
            .hero-title{font-size:1.45rem;line-height:1.12}
            .hero-sub{font-size:.92rem;margin-top:5px}
            .hero-author{font-size:.92rem;margin-top:5px}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    logo = _logo_data_uri()
    image = f'<img src="{logo}" alt="The Broken Yolk Cafe">' if logo else ""
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-logo">{image}</div>
            <div class="hero-copy">
                <div class="hero-title">Meal Violations Dashboard</div>
                <div class="hero-sub">Oracle MICROS Simphony · Auditoría de Meal Violations</div>
                <div class="hero-author">Broken Yolk - By Jordan Memije</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_rules() -> CaliforniaMealRules:
    with st.sidebar.expander("Reglas aplicadas", expanded=False):
        st.markdown(
            """
- Primer meal: más de 5 horas; waiver solo hasta 6 horas.
- Segundo meal: más de 10 horas; waiver solo hasta 12 horas y sin renunciar al primero.
- Duración mínima: 30 minutos.
- El motor usa **horas trabajadas acumuladas**.
- Un gap sin status se manda a revisión.
- Un meal “confirmado” significa **confirmado por marcación**, no prueba que fue duty-free.
            """
        )
    return CaliforniaMealRules()


# ---------------------------------------------------------------------------
# Input policy files
# ---------------------------------------------------------------------------


def render_policy_inputs() -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, Any],
    dict[str, list[dict[str, Any]]],
    pd.DataFrame,
    str,
    dict[str, Any] | None,
    str,
]:
    """Render HR/Payroll controls without overwhelming the auditor view."""
    with st.sidebar.expander("Configuración avanzada · HR / Payroll", expanded=False):
        st.caption(
            "Estos controles protegen el cálculo, pero no forman parte de la revisión diaria del auditor."
        )
        classification_mode = st.selectbox(
            "Clasificación predeterminada",
            [
                "Estricto: desconocida (bloquea conclusión)",
                "Provisional: todos no exentos",
            ],
            help="La opción provisional debe usarse solo si HR confirma que todo el alcance es no exento.",
        )
        default_classification = (
            "UNKNOWN" if classification_mode.startswith("Estricto") else "NON_EXEMPT"
        )

        st.markdown("**Clasificación, waivers y acuerdos**")
        policy_file = st.file_uploader(
            "Employee policy CSV", type=["csv"], key="policy_csv"
        )
        st.download_button(
            "Plantilla de employee policy",
            data=(
                "employee_key,classification,first_meal_waiver,second_meal_waiver,on_duty_meal_agreement,effective_date,expiration_date,document_reference,verified_by,notes\n"
                "12345,NON_EXEMPT,false,false,false,2026-01-01,,HRIS-123,HR Manager,\n"
            ).encode("utf-8-sig"),
            file_name="employee_compliance_policy_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.divider()
        st.markdown("**Workday legal por ubicación**")
        workday_file = st.file_uploader(
            "Workday configuration CSV", type=["csv"], key="workday_csv"
        )
        default_start = st.text_input(
            "Fallback temporal",
            value="00:00",
            help="Solo para análisis preliminar; no se considera verificado.",
        )
        st.download_button(
            "Plantilla de workday",
            data=(
                "location_ref,workday_start,timezone,effective_date,expiration_date,verified_by,source\n"
                "BYC304,04:00,America/Los_Angeles,2026-01-01,,Payroll Manager,Payroll policy\n"
            ).encode("utf-8-sig"),
            file_name="workday_configuration_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.divider()
        st.markdown("**Regular rate verificado**")
        rate_file = st.file_uploader(
            "Regular rate CSV", type=["csv"], key="rate_csv"
        )
        st.download_button(
            "Plantilla de regular rate",
            data=(
                "employee_key,regular_rate,effective_date,expiration_date,source,verified_by\n"
                "12345,24.75,2026-07-01,2026-07-15,Payroll calculation,Payroll Manager\n"
            ).encode("utf-8-sig"),
            file_name="verified_regular_rate_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.divider()
        st.markdown("**Reconciliación y snapshots**")
        control_file = st.file_uploader(
            "MICROS control totals CSV", type=["csv"], key="controls_csv"
        )
        st.download_button(
            "Plantilla de control MICROS",
            data=(
                "location_ref,business_date,timecards,employees,worked_hours,adjusted_timecards\n"
                "BYC304,2026-07-20,42,18,126.50,3\n"
            ).encode("utf-8-sig"),
            file_name="micros_control_totals_template.csv",
            mime="text/csv",
            use_container_width=True,
        )
        snapshot_file = st.file_uploader(
            "Audit snapshot JSON", type=["json"], key="snapshot_json"
        )

    try:
        policy_records = policy_rows_to_records(load_employee_policy_csv(policy_file))
        workday_records = workday_rows_to_records(load_workday_config_csv(workday_file))
        rate_records = regular_rate_rows_to_records(load_regular_rate_csv(rate_file))
        control_totals = load_control_totals_csv(control_file)
        previous_snapshot = (
            load_snapshot_bytes(snapshot_file.getvalue()) if snapshot_file else None
        )
    except ValueError as error:
        st.sidebar.error(str(error))
        policy_records, workday_records, rate_records = {}, {}, {}
        control_totals, previous_snapshot = pd.DataFrame(), None

    return (
        policy_records,
        workday_records,
        rate_records,
        control_totals,
        default_start,
        previous_snapshot,
        default_classification,
    )


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------


def analyze_payloads(
    *,
    timecard_payloads: list[dict[str, Any]],
    employees_payloads: list[dict[str, Any]],
    jobs_payloads: list[dict[str, Any]],
    locations_payload: dict[str, Any],
    selected_locations: list[str],
    authorized_locations: list[str] | None,
    start_date: date,
    end_date: date,
    rules: CaliforniaMealRules,
    policy_records: dict[str, list[dict[str, Any]]],
    workday_records: dict[str, Any],
    rate_records: dict[str, list[dict[str, Any]]],
    control_totals: pd.DataFrame,
    default_workday_start: str,
    default_classification: str,
) -> tuple[AnalysisBundle, pd.DataFrame, pd.DataFrame]:
    normalized = normalize_timecards(
        timecard_payloads,
        employees=employee_dimension_map(employees_payloads),
        job_codes=job_code_dimension_map(jobs_payloads),
        locations=location_dimension_map(locations_payload),
    )
    legal = assign_legal_workdays(
        normalized,
        workday_configs=workday_records,
        default_workday_start=default_workday_start,
    )
    coverage = build_source_coverage(
        timecard_payloads,
        expected_locations=selected_locations,
        start_date=start_date,
        end_date=end_date,
    )
    selected_set = {str(value) for value in selected_locations}
    authorized_set = {str(value) for value in (authorized_locations or [])}
    scope_complete: bool | None
    if authorized_locations is None:
        scope_complete = None
    else:
        scope_complete = authorized_set.issubset(selected_set)
    missing_locations = sorted(authorized_set.difference(selected_set))
    validation = build_data_quality_report(
        legal,
        coverage=coverage,
        control_totals=control_totals,
        location_scope_complete=scope_complete,
        location_scope_detail=(
            "Missing authorized location refs: " + ", ".join(missing_locations)
            if missing_locations else ""
        ),
    )
    bundle = analyze_timecards(
        legal,
        rules=rules,
        policy_records=policy_records,
        regular_rate_records=rate_records,
        default_classification=default_classification,
        global_data_blocked=validation.blocking_global,
    )
    bundle.data_quality = validation.issues
    bundle.reconciliation = validation.reconciliation
    bundle.coverage = validation.coverage

    job_map = job_code_dimension_map(jobs_payloads)
    adjustment_audit = build_adjustment_audit(bundle.raw_timecards, job_codes=job_map)
    adjustment_history = build_adjustment_result_history(
        bundle.raw_timecards,
        rules=rules,
        policy_records=policy_records,
        regular_rate_records=rate_records,
        default_classification=default_classification,
    )
    bundle.change_history = adjustment_history
    return bundle, adjustment_audit, adjustment_history


def _payload_timecard_count(payload: dict[str, Any]) -> int:
    total = 0
    for business_day in payload.get("businessDates", []) or []:
        if isinstance(business_day, dict):
            cards = business_day.get("timeCardDetails", []) or []
            if isinstance(cards, list):
                total += len(cards)
    if not payload.get("businessDates") and isinstance(payload.get("timeCardDetails"), list):
        total += len(payload.get("timeCardDetails") or [])
    return total


def analyze_api_source(
    client: OracleBIClient,
    loc_refs: list[str],
    start_date: date,
    end_date: date,
    *,
    excel_fallback: dict[str, Any] | None = None,
    location_labels: dict[str, str] | None = None,
    **kwargs: Any,
) -> tuple[AnalysisBundle, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    employees_payloads: list[dict[str, Any]] = []
    jobs_payloads: list[dict[str, Any]] = []
    timecard_payloads: list[dict[str, Any]] = []
    locations_payload = st.session_state.get("locations_payload") or client.get_locations()
    for loc_ref in loc_refs:
        employees = client.get_employees(loc_ref)
        employees.setdefault("locRef", loc_ref)
        jobs = client.get_job_codes(loc_ref)
        jobs.setdefault("locRef", loc_ref)
        employees_payloads.append(employees)
        jobs_payloads.append(jobs)
        timecard_payloads.extend(
            client.get_timecards_range(
                loc_ref,
                start_date,
                end_date,
                include_adjustments=True,
                maximum_days=MAX_RANGE_DAYS,
            )
        )

    oracle_counts = {ref: 0 for ref in loc_refs}
    for payload in timecard_payloads:
        ref = str(payload.get("locRef") or "")
        if ref in oracle_counts:
            oracle_counts[ref] += _payload_timecard_count(payload)
    zero_oracle_refs = [ref for ref in loc_refs if oracle_counts.get(ref, 0) == 0]

    excel_diagnostics: dict[str, Any] | None = None
    excel_used_refs: list[str] = []
    if excel_fallback and zero_oracle_refs:
        converted = convert_excel_to_payloads(
            excel_fallback["frame"],
            mapping=excel_fallback["mapping"],
            location_labels=location_labels or {ref: ref for ref in loc_refs},
            fallback_refs=zero_oracle_refs,
            start_date=start_date,
            end_date=end_date,
            source_name=excel_fallback["filename"],
        )
        excel_diagnostics = converted.diagnostics
        excel_used_refs = list(excel_diagnostics.get("locations_with_rows") or [])
        # Oracle remains authoritative. Replace only locations that returned zero
        # timecards and for which the Excel produced valid rows in the selected range.
        if excel_used_refs:
            timecard_payloads = [
                payload for payload in timecard_payloads
                if str(payload.get("locRef") or "") not in excel_used_refs
            ] + converted.timecard_payloads
            employees_payloads = [
                payload for payload in employees_payloads
                if str(payload.get("locRef") or "") not in excel_used_refs
            ] + converted.employee_payloads
            jobs_payloads = [
                payload for payload in jobs_payloads
                if str(payload.get("locRef") or "") not in excel_used_refs
            ] + converted.job_payloads

    bundle, adjustment_audit, adjustment_history = analyze_payloads(
        timecard_payloads=timecard_payloads,
        employees_payloads=employees_payloads,
        jobs_payloads=jobs_payloads,
        locations_payload=locations_payload,
        selected_locations=loc_refs,
        authorized_locations=[
            str(item.get("locRef"))
            for item in locations_payload.get("locations", []) or []
            if isinstance(item, dict) and item.get("active", True)
        ],
        start_date=start_date,
        end_date=end_date,
        **kwargs,
    )
    metadata = {
        "employees_payloads": employees_payloads,
        "jobs_payloads": jobs_payloads,
        "locations_payload": locations_payload,
        "timecard_payloads": timecard_payloads,
        "oracle_timecard_counts": oracle_counts,
        "oracle_zero_data_locations": zero_oracle_refs,
        "excel_fallback_locations": excel_used_refs,
        "excel_required_locations": [
            ref for ref in zero_oracle_refs if ref not in excel_used_refs
        ],
        "excel_import_diagnostics": excel_diagnostics,
    }
    return bundle, metadata, adjustment_audit, adjustment_history


def save_analysis(
    bundle: AnalysisBundle,
    metadata: dict[str, Any],
    adjustment_audit: pd.DataFrame,
    adjustment_history: pd.DataFrame,
    context: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
) -> None:
    old_bundle = st.session_state.get("analysis_bundle")
    old_context = st.session_state.get("analysis_context") or {}
    if previous_snapshot is None and old_bundle is not None:
        previous_snapshot = load_snapshot_bytes(
            create_snapshot_bytes(old_bundle, app_version=APP_VERSION, context=old_context)
        )
    comparison = compare_snapshot_to_bundle(previous_snapshot, bundle) if previous_snapshot else pd.DataFrame()
    st.session_state.analysis_bundle = bundle
    st.session_state.dimension_payloads = metadata
    st.session_state.adjustment_audit = adjustment_audit
    st.session_state.adjustment_result_history = adjustment_history
    st.session_state.snapshot_comparison = comparison
    st.session_state.analysis_context = context


# ---------------------------------------------------------------------------
# Source UI
# ---------------------------------------------------------------------------


def _mapping_selectbox(
    *,
    label: str,
    field: str,
    columns: list[str],
    suggestions: dict[str, str | None],
    required: bool = False,
) -> str | None:
    options: list[str | None] = [None] + columns
    suggested = suggestions.get(field)
    index = options.index(suggested) if suggested in options else 0
    selected = st.selectbox(
        label + (" *" if required else ""),
        options,
        index=index,
        format_func=lambda value: "— No usar —" if value is None else str(value),
        key=f"excel_map_{field}",
    )
    return selected


def _active_excel_requirement(
    *,
    loc_refs: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    requirement = st.session_state.get("excel_fallback_required")
    if not isinstance(requirement, dict):
        return None
    if requirement.get("loc_refs") != list(loc_refs):
        return None
    if requirement.get("start_date") != start_date.isoformat():
        return None
    if requirement.get("end_date") != end_date.isoformat():
        return None
    return requirement


def render_excel_fallback_inputs(
    *,
    loc_refs: list[str],
    location_labels: dict[str, str],
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    st.markdown("#### Fallback para sucursales sin datos en Oracle")
    requirement = _active_excel_requirement(
        loc_refs=loc_refs,
        start_date=start_date,
        end_date=end_date,
    )
    required_refs = list(requirement.get("missing_refs") or []) if requirement else []
    required_labels = [location_labels.get(ref, ref) for ref in required_refs]

    if required_refs:
        st.error(
            "Auditoría incompleta: Oracle MICROS no encontró timecards para "
            + ", ".join(required_labels)
            + f" en el periodo {start_date:%m/%d/%Y}–{end_date:%m/%d/%Y}. "
            "Para incluir estas sucursales es necesario subir su reporte Time Card Detail y volver a ejecutar."
        )
    else:
        st.caption(
            "Oracle sigue siendo la fuente principal. El Excel se utilizará únicamente para "
            "las ubicaciones seleccionadas que devuelvan cero timecards en todo el periodo."
        )

    c1, c2 = st.columns([2, 1])
    uploader_label = (
        "Subir Time Card Detail requerido para: " + ", ".join(required_labels)
        if required_labels
        else "Time Card Detail para fallback (opcional)"
    )
    with c1:
        excel_files = st.file_uploader(
            uploader_label,
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="excel_fallback_files",
            help=(
                "Puedes subir uno o varios reportes Time Card Detail, normalmente uno por sucursal. "
                "La aplicación reconoce automáticamente la estructura exportada de MICROS. "
                "Oracle nunca se mezcla con Excel para la misma ubicación."
            ),
        )
    with c2:
        st.download_button(
            "Descargar plantilla alternativa",
            data=build_template_bytes(),
            file_name="meal_compliance_excel_fallback_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    if not excel_files:
        if required_refs:
            st.info(
                "Exporta en MICROS el reporte **Time Card Detail** de cada sucursal indicada, "
                "usando exactamente el mismo rango de fechas, súbelo aquí y ejecuta nuevamente."
            )
        else:
            st.info(
                "No es necesario cargar Excel mientras Oracle tenga información para todas las sucursales."
            )
        return None

    frames: list[pd.DataFrame] = []
    source_details: list[dict[str, Any]] = []
    errors: list[str] = []

    for file_index, excel_file in enumerate(excel_files):
        file_bytes = excel_file.getvalue()
        try:
            sheet_names = workbook_sheet_names(file_bytes, excel_file.name)
            sheet_name = "Reports" if "Reports" in sheet_names else sheet_names[0]
            frame = read_workbook_sheet(file_bytes, excel_file.name, sheet_name)
        except ExcelImportError as error:
            errors.append(f"{excel_file.name}: {error}")
            continue
        if frame.empty:
            errors.append(f"{excel_file.name}: la hoja seleccionada no contiene filas utilizables.")
            continue

        source_details.append(
            {
                "filename": excel_file.name,
                "sheet_name": sheet_name,
                "format": frame.attrs.get("source_format", "generic"),
                "location": frame.attrs.get("source_location", ""),
                "business_dates": frame.attrs.get("source_business_dates", ""),
                "rows": int(len(frame)),
            }
        )
        frame = frame.copy()
        frame["_Source File"] = excel_file.name
        frames.append(frame)

    for error in errors:
        st.error(error)
    if not frames:
        return None

    frame = pd.concat(frames, ignore_index=True, sort=False)
    columns = [str(column) for column in frame.columns if str(column) != "_Source File"]
    suggestions = suggest_mapping(columns)
    recognized = [item for item in source_details if item["format"] == "oracle_time_card_detail"]

    if len(recognized) == len(source_details):
        detected_locations = sorted(
            {str(item["location"]).strip() for item in source_details if str(item["location"]).strip()}
        )
        st.success(
            f"Formato Time Card Detail reconocido en {len(source_details)} archivo(s), "
            f"con {len(frame):,} segmentos. "
            + (
                "Sucursales detectadas: " + ", ".join(detected_locations) + "."
                if detected_locations
                else ""
            )
        )
    else:
        st.warning(
            "Al menos un archivo no coincide con el formato estándar Time Card Detail. "
            "Revisa el mapeo antes de ejecutar."
        )

    with st.expander(
        "Mapeo de columnas del Excel",
        expanded=len(recognized) != len(source_details),
    ):
        r1 = st.columns(3)
        with r1[0]:
            location = _mapping_selectbox(label="Location", field="location", columns=columns, suggestions=suggestions)
        with r1[1]:
            business_date = _mapping_selectbox(label="Business Date", field="business_date", columns=columns, suggestions=suggestions, required=True)
        with r1[2]:
            payroll_id = _mapping_selectbox(label="Payroll ID", field="payroll_id", columns=columns, suggestions=suggestions)

        r2 = st.columns(3)
        with r2[0]:
            employee_name = _mapping_selectbox(label="Employee Name", field="employee_name", columns=columns, suggestions=suggestions)
        with r2[1]:
            clock_in = _mapping_selectbox(label="Clock In", field="clock_in", columns=columns, suggestions=suggestions, required=True)
        with r2[2]:
            clock_out = _mapping_selectbox(label="Clock Out", field="clock_out", columns=columns, suggestions=suggestions, required=True)

        st.markdown("**Meals explícitos — opcionales**")
        r3 = st.columns(4)
        with r3[0]:
            meal_start = _mapping_selectbox(label="Meal Start", field="meal_start", columns=columns, suggestions=suggestions)
        with r3[1]:
            meal_end = _mapping_selectbox(label="Meal End", field="meal_end", columns=columns, suggestions=suggestions)
        with r3[2]:
            second_meal_start = _mapping_selectbox(label="Second Meal Start", field="second_meal_start", columns=columns, suggestions=suggestions)
        with r3[3]:
            second_meal_end = _mapping_selectbox(label="Second Meal End", field="second_meal_end", columns=columns, suggestions=suggestions)

        st.markdown("**Campos operativos — opcionales**")
        r4 = st.columns(4)
        with r4[0]:
            job_code = _mapping_selectbox(label="Job Code", field="job_code", columns=columns, suggestions=suggestions)
        with r4[1]:
            pay_rate = _mapping_selectbox(label="Pay Rate", field="pay_rate", columns=columns, suggestions=suggestions)
        with r4[2]:
            regular_hours = _mapping_selectbox(label="Regular Hours", field="regular_hours", columns=columns, suggestions=suggestions)
        with r4[3]:
            clock_out_status = _mapping_selectbox(label="Clock Out Status", field="clock_out_status", columns=columns, suggestions=suggestions)
        shift_type = _mapping_selectbox(label="Shift Type", field="shift_type", columns=columns, suggestions=suggestions)
        st.dataframe(
            frame.drop(columns=["_Source File"], errors="ignore").head(12),
            use_container_width=True,
            hide_index=True,
        )

    mapping = {
        "location": location,
        "business_date": business_date,
        "employee_name": employee_name,
        "payroll_id": payroll_id,
        "clock_in": clock_in,
        "clock_out": clock_out,
        "meal_start": meal_start,
        "meal_end": meal_end,
        "second_meal_start": second_meal_start,
        "second_meal_end": second_meal_end,
        "job_code": job_code,
        "pay_rate": pay_rate,
        "regular_hours": regular_hours,
        "clock_out_status": clock_out_status,
        "shift_type": shift_type,
    }
    return {
        "frame": frame,
        "mapping": mapping,
        "filename": ", ".join(item["filename"] for item in source_details),
        "files": source_details,
    }


def render_oracle_mode(
    *,
    rules: CaliforniaMealRules,
    policy_records: dict[str, list[dict[str, Any]]],
    workday_records: dict[str, Any],
    rate_records: dict[str, list[dict[str, Any]]],
    control_totals: pd.DataFrame,
    default_workday_start: str,
    default_classification: str,
    previous_snapshot: dict[str, Any] | None,
) -> None:
    try:
        config = config_from_secrets()
    except ValueError:
        st.error("Falta configurar los Secrets de Streamlit con la cuenta Business Intelligence API.")
        return
    client = get_or_create_client(config)
    with st.container(border=True):
        st.markdown("### Consulta a Oracle MICROS")
        c1, c2 = st.columns([1, 3])
        with c1:
            connect = st.button("Conectar a Oracle", type="primary", use_container_width=True)
        with c2:
            if st.session_state.get("locations_payload"):
                st.success("Oracle BI API conectado.")
            else:
                st.info("Las credenciales se leen de Streamlit Secrets.")
        if connect:
            try:
                with st.spinner("Autenticando y consultando ubicaciones..."):
                    client.authenticate()
                    st.session_state.locations_payload = client.get_locations()
                st.rerun()
            except OracleBIError as error:
                st.error(str(error))
                return

        payload = st.session_state.get("locations_payload")
        if not payload:
            return
        locations = [item for item in payload.get("locations", []) or [] if isinstance(item, dict) and item.get("active", True)]
        option_map = {
            f"{item.get('name') or item.get('locName') or item.get('locRef')} — {item.get('locRef')}": str(item.get("locRef"))
            for item in locations
        }
        if not option_map:
            st.warning("Oracle no devolvió ubicaciones activas.")
            return
        selected_labels = st.multiselect(
            "Ubicaciones",
            list(option_map),
            default=list(option_map),
            help=(
                "Para una auditoría final selecciona todas las ubicaciones autorizadas; "
                "así se consolidan las horas de empleados que trabajan en más de una sucursal."
            ),
        )
        loc_refs = [option_map[label] for label in selected_labels]
        if len(selected_labels) < len(option_map):
            st.warning(
                "Alcance parcial: las conclusiones automáticas se bloquearán porque podrían faltar "
                "horas trabajadas por el mismo empleado en otra ubicación."
            )
        default_end = date.today() - timedelta(days=1)
        default_start = default_end - timedelta(days=6)
        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input("Fecha inicial", value=default_start)
        with d2:
            end_date = st.date_input("Fecha final", value=default_end)
        call_count = len(loc_refs) * ((end_date - start_date).days + 1) if end_date >= start_date else 0
        st.caption(f"La consulta realizará aproximadamente {call_count} llamadas de timecards, más dimensiones.")

        location_labels = {
            option_map[label]: label.split(" — ")[0]
            for label in selected_labels
        }
        excel_fallback = render_excel_fallback_inputs(
            loc_refs=loc_refs,
            location_labels=location_labels,
            start_date=start_date,
            end_date=end_date,
        )

        if end_date < start_date:
            st.error("La fecha final no puede ser anterior a la inicial.")
            return
        if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
            st.error(f"El rango máximo por ejecución es de {MAX_RANGE_DAYS} días.")
            return
        if st.button("Consultar, reconciliar y analizar", type="primary", use_container_width=True):
            if not loc_refs:
                st.error("Selecciona al menos una ubicación.")
                return
            try:
                with st.spinner("Consultando ubicaciones, empleados, ajustes y timecards..."):
                    bundle, metadata, audit, history = analyze_api_source(
                        client,
                        loc_refs,
                        start_date,
                        end_date,
                        rules=rules,
                        policy_records=policy_records,
                        workday_records=workday_records,
                        rate_records=rate_records,
                        control_totals=control_totals,
                        default_workday_start=default_workday_start,
                        default_classification=default_classification,
                        excel_fallback=excel_fallback,
                        location_labels=location_labels,
                    )
                excel_used_refs = metadata.get("excel_fallback_locations") or []
                zero_oracle_refs = metadata.get("oracle_zero_data_locations") or []
                excel_required_refs = metadata.get("excel_required_locations") or []
                context = {
                    "location_label": ", ".join(selected_labels),
                    "date_label": f"{start_date:%m/%d/%Y}–{end_date:%m/%d/%Y}",
                    "location_refs": loc_refs,
                    "selected_locations": [
                        {"ref": option_map[label], "label": label.split(" — ")[0]}
                        for label in selected_labels
                    ],
                    "data_sources": (
                        ["Oracle BI API", "Excel fallback"] if excel_used_refs else ["Oracle BI API"]
                    ),
                    "oracle_zero_data_locations": zero_oracle_refs,
                    "excel_fallback_locations": excel_used_refs,
                    "excel_required_locations": excel_required_refs,
                    "excel_import_diagnostics": metadata.get("excel_import_diagnostics"),
                }
                save_analysis(bundle, metadata, audit, history, context, previous_snapshot)
                if excel_required_refs:
                    labels = [location_labels.get(ref, ref) for ref in excel_required_refs]
                    st.session_state["excel_fallback_required"] = {
                        "loc_refs": list(loc_refs),
                        "missing_refs": list(excel_required_refs),
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    }
                    st.error(
                        "Auditoría incompleta. Oracle MICROS no encontró información y todavía falta "
                        "un Time Card Detail válido para: "
                        + ", ".join(labels)
                        + ". Sube el Excel del mismo periodo y vuelve a ejecutar."
                    )
                else:
                    st.session_state.pop("excel_fallback_required", None)

                if excel_used_refs:
                    labels = [location_labels.get(ref, ref) for ref in excel_used_refs]
                    st.success(
                        "Fuente híbrida aplicada. Oracle se usó donde había datos y Excel únicamente en: "
                        + ", ".join(labels)
                    )
            except (OracleBIError, ExcelImportError, ValueError) as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"No fue posible completar el análisis: {type(error).__name__}: {error}")


def _load_json(file_obj: Any) -> Any:
    return None if file_obj is None else json.loads(file_obj.getvalue().decode("utf-8-sig"))


def render_json_mode(**kwargs: Any) -> None:
    previous_snapshot = kwargs.pop("previous_snapshot", None)
    with st.container(border=True):
        st.info("Modo de validación: carga una respuesta o lista de respuestas Oracle.")
        timecards_file = st.file_uploader("getTimeCardDetails JSON", type=["json"], key="json_timecards")
        employees_file = st.file_uploader("getEmployeeDimensions JSON", type=["json"], key="json_employees")
        jobs_file = st.file_uploader("getJobCodeDimensions JSON", type=["json"], key="json_jobs")
        locations_file = st.file_uploader("getLocationDimensions JSON", type=["json"], key="json_locations")
        if st.button("Analizar JSON", type="primary", use_container_width=True):
            if not timecards_file:
                st.error("Carga al menos getTimeCardDetails.")
                return
            try:
                raw = _load_json(timecards_file)
                timecard_payloads = raw if isinstance(raw, list) else [raw]
                employees_raw = _load_json(employees_file) or {}
                jobs_raw = _load_json(jobs_file) or {}
                employees_payloads = employees_raw if isinstance(employees_raw, list) else [employees_raw]
                jobs_payloads = jobs_raw if isinstance(jobs_raw, list) else [jobs_raw]
                locations_payload = _load_json(locations_file) or {}
                loc_refs = sorted({str(item.get("locRef") or "") for item in timecard_payloads if isinstance(item, dict)})
                dates = [pd.to_datetime(day.get("busDt"), errors="coerce").date() for p in timecard_payloads for day in p.get("businessDates", []) or [] if pd.notna(pd.to_datetime(day.get("busDt"), errors="coerce"))]
                start_date = min(dates) if dates else date.today()
                end_date = max(dates) if dates else start_date
                bundle, audit, history = analyze_payloads(
                    timecard_payloads=timecard_payloads,
                    employees_payloads=employees_payloads,
                    jobs_payloads=jobs_payloads,
                    locations_payload=locations_payload,
                    selected_locations=loc_refs,
                    authorized_locations=(
                        [str(item.get("locRef")) for item in locations_payload.get("locations", []) or [] if isinstance(item, dict) and item.get("active", True)]
                        if locations_payload else None
                    ),
                    start_date=start_date,
                    end_date=end_date,
                    **kwargs,
                )
                metadata = {
                    "employees_payloads": employees_payloads,
                    "jobs_payloads": jobs_payloads,
                    "locations_payload": locations_payload,
                    "timecard_payloads": timecard_payloads,
                }
                json_location_names = location_dimension_map(locations_payload)
                selected_location_context = [
                    {
                        "ref": ref,
                        "label": str(
                            json_location_names.get(ref, {}).get("name")
                            or json_location_names.get(ref, {}).get("locName")
                            or ref
                        ),
                    }
                    for ref in loc_refs
                ]
                save_analysis(
                    bundle,
                    metadata,
                    audit,
                    history,
                    {
                        "location_label": "Validación JSON",
                        "date_label": "",
                        "location_refs": loc_refs,
                        "selected_locations": selected_location_context,
                    },
                    previous_snapshot,
                )
            except (ValueError, json.JSONDecodeError) as error:
                st.error(str(error))


# ---------------------------------------------------------------------------
# Friendly result tables
# ---------------------------------------------------------------------------


def friendly_workdays(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df.iterrows():
        violations = _split_codes(row.get("Presumed Violations", row.get("Automatic Violations")))
        reviews = _split_codes(row.get("Reviews"))
        if violations:
            state = "🔴 Presunta violación"
            codes = violations
        elif "EXCLUDED_EXEMPT" in reviews:
            state = "⚪ Excluido"
            codes = reviews
        elif reviews:
            state = "🟠 Revisión requerida"
            codes = reviews
        else:
            state = "🟢 Cumplimiento por marcación"
            codes = ["COMPLIANT_BY_PUNCH"]
        rows.append(
            {
                "Estado": state,
                "Empleado": row.get("Employee", ""),
                "ID nómina": row.get("Payroll ID", ""),
                "Clasificación": row.get("Employee Classification", ""),
                "Fecha workday": _format_date(row.get("Legal Workday Date", row.get("Business Date"))),
                "Ubicación(es)": row.get("Location", ""),
                "Puesto(s)": row.get("Role(s)", ""),
                "Entrada": _format_time(row.get("First Clock In")),
                "Salida": _format_time(row.get("Last Clock Out")),
                "Horas": row.get("Worked Hours", 0),
                "Meals por marcación": row.get("Confirmed Meals", 0),
                "Meals probables": row.get("Probable Meals", 0),
                "Hallazgo": _labels(codes),
                "Acción": _actions(codes),
                "Premium estimado": row.get("Premium Estimate", row.get("Estimated Meal Premium", 0)),
                "Base premium": row.get("Premium Rate Basis", ""),
                "Ajustes": row.get("Adjustment Count", 0),
            }
        )
    return pd.DataFrame(rows)


def friendly_cases(df: pd.DataFrame, code_column: str, *, include_premium: bool) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df.iterrows():
        code = str(row.get(code_column) or "")
        rows.append(
            {
                "Empleado": row.get("Employee", ""),
                "ID nómina": row.get("Payroll ID", ""),
                "Clasificación": row.get("Employee Classification", ""),
                "Fecha workday": _format_date(row.get("Legal Workday Date", row.get("Business Date"))),
                "Ubicación(es)": row.get("Location", ""),
                "Horas": row.get("Worked Hours", 0),
                "Hallazgo": RESULT_LABELS.get(code, code),
                "Acción": RESULT_ACTIONS.get(code, "Revisar el caso."),
                "Premium estimado": row.get("Premium Estimate", row.get("Estimated Meal Premium", 0)) if include_premium else 0,
                "Base premium": row.get("Premium Rate Basis", "") if include_premium else "",
                "Detalle": row.get("Details", ""),
            }
        )
    return pd.DataFrame(rows)


def friendly_meals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Empleado": df.get("Employee", ""),
            "Fecha workday": df.get("Legal Workday Date", df.get("Business Date", pd.Series(dtype=object))).map(_format_date),
            "Meal #": df.get("Meal Sequence", ""),
            "Inicio": df.get("Meal Start", pd.Series(dtype=object)).map(_format_time),
            "Fin": df.get("Meal End", pd.Series(dtype=object)).map(_format_time),
            "Duración min": df.get("Duration Minutes", 0),
            "Horas trabajadas antes": df.get("Worked Hours Before", 0),
            "Confirmado por marcación": df.get("Confirmed by Punch", df.get("Confirmed Duty-Free Timestamp", False)),
            "Duty-free verificado": df.get("Duty-Free Verified", False),
            "Pagado": df.get("Paid", False),
            "Evidencia": df.get("Evidence", ""),
            "Ubicación(es)": df.get("Meal Location(s)", ""),
        }
    )


# ---------------------------------------------------------------------------
# Result dashboard
# ---------------------------------------------------------------------------


def _auditor_reason(code: Any) -> str:
    text = str(code or "").strip()
    return AUDITOR_REASON_LABELS.get(text, RESULT_LABELS.get(text, text))


def _auditor_breakdown(value: Any) -> str:
    if pd.isna(value) or not str(value).strip():
        return ""
    labels: list[str] = []
    for part in str(value).split("|"):
        item = part.strip()
        if not item:
            continue
        code, separator, count = item.partition(":")
        label = _auditor_reason(code.strip())
        labels.append(f"{count.strip()} × {label}" if separator else label)
    return " · ".join(labels)


def _ui_employee_group(row: pd.Series) -> str:
    key = str(row.get("Employee Key") or "").strip()
    if key and key.lower() not in {"nan", "<na>"}:
        return key
    payroll = str(row.get("Payroll ID") or "").strip()
    if payroll and payroll.lower() not in {"nan", "<na>"}:
        return payroll
    return "NAME::" + str(row.get("Employee") or "Empleado sin identificar").strip()


def auditor_finding_source(bundle: AnalysisBundle) -> pd.DataFrame:
    """Return all punch-pattern findings, including those pending validation."""
    if hasattr(bundle, "candidates") and not bundle.candidates.empty:
        return bundle.candidates.copy()
    return bundle.violations.copy()


def auditor_employee_table(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame(
            columns=[
                "Empleado",
                "ID nómina",
                "Meal Violations",
                "Razón principal",
                "Desglose",
                "Fechas afectadas",
                "Ubicación(es)",
            ]
        )
    result = summary.copy()
    return pd.DataFrame(
        {
            "Empleado": result["Employee"],
            "ID nómina": result["Payroll ID"],
            "Posibles Meal Violations": result["Violations"],
            "Razón principal": result["Principal Reason Code"].map(_auditor_reason),
            "Desglose": result["Reason Breakdown"].map(_auditor_breakdown),
            "Fechas afectadas": result["Affected Dates"],
            "Estado": result.get("Status", "Detectado por marcación"),
            "Ubicación(es)": result["Locations"],
        }
    )


def auditor_violation_details(
    bundle: AnalysisBundle,
    *,
    employee_group: str | None = None,
    reason_codes: list[str] | None = None,
) -> pd.DataFrame:
    columns = [
        "Case ID",
        "Fecha",
        "Empleado",
        "ID nómina",
        "Razón",
        "Estado",
        "Entrada",
        "Salida",
        "Horas",
        "Inicio meal",
        "Duración meal (min)",
        "Premium estimado",
        "Base de estimación",
        "Ubicación(es)",
        "Ajuste manual",
    ]
    source = auditor_finding_source(bundle)
    if source.empty:
        return pd.DataFrame(columns=columns)
    source["_Employee Group"] = source.apply(_ui_employee_group, axis=1)
    code_column = (
        "Candidate Violation"
        if "Candidate Violation" in source.columns
        else "Presumed Violation"
        if "Presumed Violation" in source.columns
        else "Violation"
    )
    if employee_group is not None:
        source = source[source["_Employee Group"] == employee_group]
    if reason_codes:
        source = source[source[code_column].astype(str).isin(reason_codes)]
    if source.empty:
        return pd.DataFrame(columns=columns)

    meal_lookup: dict[tuple[str, str, int], pd.Series] = {}
    if not bundle.meals.empty:
        meals = bundle.meals.copy()
        meals["_Employee Group"] = meals.apply(_ui_employee_group, axis=1)
        meal_date_column = (
            "Legal Workday Date"
            if "Legal Workday Date" in meals.columns
            else "Business Date"
        )
        for _, meal in meals.iterrows():
            date_key = _format_date(meal.get(meal_date_column))
            try:
                sequence = int(meal.get("Meal Sequence") or 0)
            except (TypeError, ValueError):
                sequence = 0
            meal_lookup[(str(meal["_Employee Group"]), date_key, sequence)] = meal

    workday_adjustments: dict[tuple[str, str], int] = {}
    if not bundle.workdays.empty:
        workdays = bundle.workdays.copy()
        workdays["_Employee Group"] = workdays.apply(_ui_employee_group, axis=1)
        workday_date_column = (
            "Legal Workday Date"
            if "Legal Workday Date" in workdays.columns
            else "Business Date"
        )
        for _, workday in workdays.iterrows():
            date_key = _format_date(workday.get(workday_date_column))
            adjustment_value = pd.to_numeric(
                workday.get("Adjustment Count", 0), errors="coerce"
            )
            workday_adjustments[(str(workday["_Employee Group"]), date_key)] = (
                0 if pd.isna(adjustment_value) else int(adjustment_value)
            )

    date_column = (
        "Legal Workday Date"
        if "Legal Workday Date" in source.columns
        else "Business Date"
    )
    rows: list[dict[str, Any]] = []
    for _, row in source.iterrows():
        code = str(row.get(code_column) or "")
        sequence = 1 if code.startswith("FIRST_") else 2 if code.startswith("SECOND_") else 0
        date_key = _format_date(row.get(date_column))
        group_key = str(row["_Employee Group"])
        meal = meal_lookup.get((group_key, date_key, sequence))
        rows.append(
            {
                "Case ID": row.get("Case ID", ""),
                "Fecha": date_key,
                "Empleado": row.get("Employee", ""),
                "ID nómina": row.get("Payroll ID", ""),
                "Razón": _auditor_reason(code),
                "Estado": (
                    "Pendiente de validación"
                    if bool(row.get("Pending Validation", False))
                    else "Detectado por marcación"
                ),
                "Entrada": _format_time(row.get("First Clock In")),
                "Salida": _format_time(row.get("Last Clock Out")),
                "Horas": round(float(row.get("Worked Hours", 0) or 0), 2),
                "Inicio meal": _format_time(meal.get("Meal Start")) if meal is not None else "—",
                "Duración meal (min)": (
                    round(float(meal.get("Duration Minutes", 0) or 0), 1)
                    if meal is not None
                    else "—"
                ),
                "Premium estimado": round(
                    float(row.get("Premium Estimate", row.get("Estimated Meal Premium", 0)) or 0),
                    2,
                ),
                "Base de estimación": row.get("Premium Rate Basis", ""),
                "Ubicación(es)": row.get("Location", ""),
                "Ajuste manual": (
                    "Sí"
                    if workday_adjustments.get((group_key, date_key), 0) > 0
                    else "No"
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["Fecha", "Empleado", "Razón"], ascending=[False, True, True]
    ).reset_index(drop=True)


def friendly_punch_errors(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Empleado",
        "ID nómina",
        "Fecha",
        "Ubicación(es)",
        "Categoría",
        "Detalle",
        "Acción",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    type_labels = {
        "OPEN_TIMECARD": "Timecard actualmente abierto",
        "NEGATIVE_DURATION": "Duración negativa",
        "ZERO_DURATION_REVIEW": "Marcación de duración cero",
        "CLOCK_OUT_STATUS_MISSING": "Clock Out status no disponible",
        "OVERLAPPING_TIMECARDS": "Timecards traslapados",
        "MANAGER_OR_AUTO_CLOCK_OUT": "Clock Out manual o automático",
        "PUNCH_REVIEW": "Revisión de marcación",
    }
    source = df.copy()
    issue_type = source.get(
        "Punch Review Type", pd.Series("PUNCH_REVIEW", index=source.index)
    ).fillna("PUNCH_REVIEW").astype(str)
    return pd.DataFrame(
        {
            "Empleado": source.get("Employee", ""),
            "ID nómina": source.get("Payroll ID", ""),
            "Fecha": source.get(
                "Legal Workday Date", source.get("Business Date", pd.Series(dtype=object))
            ).map(_format_date),
            "Ubicación(es)": source.get("Location", ""),
            "Categoría": issue_type.map(lambda value: type_labels.get(value, value)),
            "Detalle": source.get("Punch Error", ""),
            "Acción": issue_type.map(
                lambda value: (
                    "Esperar o corregir el Clock Out en MICROS."
                    if value == "OPEN_TIMECARD"
                    else "Confirmar el estado histórico y el ajuste asociado."
                    if value == "CLOCK_OUT_STATUS_MISSING"
                    else "Corregir o confirmar la marcación en MICROS."
                )
            ),
        },
        columns=columns,
    )


def friendly_adjustment_impact(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Fecha",
        "Empleado",
        "ID nómina",
        "Ajustado por",
        "Motivo",
        "Resultado antes",
        "Resultado después",
        "Impacto",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)
    source = df.copy()
    changed = source.get(
        "Compliance Result Changed", pd.Series(False, index=source.index)
    ).fillna(False)
    source = source[changed.astype(bool)]
    if source.empty:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        {
            "Fecha": source.get(
                "Legal Workday Date", pd.Series(dtype=object)
            ).map(_format_date),
            "Empleado": source.get("Employee", ""),
            "ID nómina": source.get("Payroll ID", ""),
            "Ajustado por": source.get("Manager", ""),
            "Motivo": source.get("Reason", ""),
            "Resultado antes": source.get("Presumed Violations Before", "").map(
                lambda value: _labels(_split_codes(value)) or "Sin violación"
            ),
            "Resultado después": source.get("Presumed Violations After", "").map(
                lambda value: _labels(_split_codes(value)) or "Sin violación"
            ),
            "Impacto": source.get("Impact Summary", ""),
        }
    )


def render_auditor_dashboard(
    bundle: AnalysisBundle,
    violation_summary: pd.DataFrame,
) -> None:
    findings = auditor_finding_source(bundle)
    total_findings = int(len(findings))
    pending_findings = int(
        findings.get("Pending Validation", pd.Series(False, index=findings.index))
        .fillna(False)
        .astype(bool)
        .sum()
    ) if not findings.empty else 0
    ready_findings = total_findings - pending_findings
    affected_employees = int(len(violation_summary))
    punch_errors = int(bundle.stats.get("punch_error_workdays", len(bundle.punch_errors)))
    candidate_exposure = float(bundle.stats.get("candidate_estimated_premium", 0.0) or 0.0)
    workdays = int(bundle.stats.get("workdays", len(bundle.workdays)))

    if total_findings:
        st.markdown(
            f'<div class="callout callout-orange"><b>{total_findings} patrones de Meal Violation detectados.</b> '
            f'{ready_findings} con controles completos y {pending_findings} pendientes de validación administrativa. '
            'Los hallazgos ya no se ocultan aunque falte clasificación, waiver o configuración del workday.</div>',
            unsafe_allow_html=True,
        )
    elif not bundle.data_quality.empty and bundle.data_quality["Blocking"].fillna(False).any():
        st.markdown(
            '<div class="callout callout-orange"><b>No se detectaron patrones de Meal Violation, pero hay controles pendientes.</b> '
            'Revise Punch Review y Controles pendientes antes de cerrar la auditoría.</div>',
            unsafe_allow_html=True,
        )

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Posibles Meal Violations", total_findings)
    k2.metric("Empleados señalados", affected_employees)
    k3.metric("Pendientes de validación", pending_findings)
    k4.metric("Exposición preliminar", f"${candidate_exposure:,.2f}")
    k5.metric("Jornadas con Punch Review", punch_errors)
    st.caption(
        f"{workdays:,} jornadas analizadas · {ready_findings} hallazgos con controles completos · "
        f"{int(bundle.stats.get('structural_break_markers', 0)):,} marcadores estructurales de break no contados como error"
    )

    if total_findings == 0:
        st.success("No se detectaron patrones de Meal Violation en las marcaciones consultadas.")
        return

    chart_left, chart_right = st.columns([3, 2])
    with chart_left:
        st.markdown("### Hallazgos por empleado")
        top = violation_summary.head(15).copy()
        chart = top.set_index("Employee")["Violations"]
        chart.index.name = "Empleado"
        st.bar_chart(chart, horizontal=True, height=max(300, min(600, 34 * len(chart))))
    with chart_right:
        st.markdown("### Razones")
        code_column = (
            "Candidate Violation"
            if "Candidate Violation" in findings.columns
            else "Presumed Violation"
            if "Presumed Violation" in findings.columns
            else "Violation"
        )
        reasons = findings[code_column].astype(str).map(_auditor_reason).value_counts()
        st.bar_chart(reasons, horizontal=True, height=300)


def render_meal_violations_tab(
    bundle: AnalysisBundle,
    violation_summary: pd.DataFrame,
) -> None:
    render_auditor_dashboard(bundle, violation_summary)
    if violation_summary.empty:
        return

    st.markdown("### Meal Violations por empleado")
    filter_1, filter_2 = st.columns([2, 3])
    with filter_1:
        search = st.text_input(
            "Buscar empleado",
            placeholder="Nombre o Payroll ID",
            key="auditor_employee_search",
        ).strip().casefold()
    with filter_2:
        available_codes = [
            code
            for code in AUDITOR_REASON_ORDER
            if code
            in set(
                auditor_finding_source(bundle).get(
                    "Candidate Violation",
                    auditor_finding_source(bundle).get(
                        "Presumed Violation",
                        auditor_finding_source(bundle).get("Violation", pd.Series(dtype=str)),
                    ),
                ).astype(str)
            )
        ]
        selected_labels = st.multiselect(
            "Filtrar por razón",
            [_auditor_reason(code) for code in available_codes],
            default=[_auditor_reason(code) for code in available_codes],
            key="auditor_reason_filter",
        )
        selected_codes = [
            code for code in available_codes if _auditor_reason(code) in selected_labels
        ]

    filtered_violations = auditor_finding_source(bundle)
    code_column = (
        "Candidate Violation"
        if "Candidate Violation" in filtered_violations.columns
        else "Presumed Violation"
        if "Presumed Violation" in filtered_violations.columns
        else "Violation"
    )
    if available_codes:
        filtered_violations = filtered_violations[
            filtered_violations[code_column].astype(str).isin(selected_codes)
        ]
    if search:
        names = filtered_violations.get("Employee", pd.Series("", index=filtered_violations.index)).astype(str).str.casefold()
        payroll = filtered_violations.get("Payroll ID", pd.Series("", index=filtered_violations.index)).astype(str).str.casefold()
        filtered_violations = filtered_violations[names.str.contains(search, regex=False) | payroll.str.contains(search, regex=False)]

    filtered_summary = build_violation_employee_summary(filtered_violations)
    st.dataframe(
        auditor_employee_table(filtered_summary),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Posibles Meal Violations": st.column_config.NumberColumn(format="%d"),
        },
    )

    if filtered_summary.empty:
        st.info("No hay resultados con los filtros seleccionados.")
        return

    options = {
        f"{row['Employee']} — {row['Payroll ID']} ({int(row['Violations'])})": str(row["Employee Group"])
        for _, row in filtered_summary.iterrows()
    }
    selected_label = st.selectbox(
        "Ver detalle de un empleado",
        list(options),
        key="auditor_employee_detail",
    )
    details = auditor_violation_details(
        bundle,
        employee_group=options[selected_label],
        reason_codes=selected_codes,
    )
    st.dataframe(details, use_container_width=True, hide_index=True)

    st.markdown("### Decisión del auditor")
    st.caption(
        "La decisión documenta la revisión operativa. No convierte por sí sola el caso "
        "en una conclusión legal y permanece en esta sesión hasta exportarla."
    )
    render_case_review_editor(
        details,
        editor_key="case_review_" + hashlib.sha256(
            options[selected_label].encode("utf-8")
        ).hexdigest()[:10],
    )


def render_location_coverage_panel(bundle: AnalysisBundle, context: dict[str, Any]) -> None:
    summary = build_location_coverage_summary(
        bundle.coverage,
        bundle.raw_timecards,
        selected_locations=context.get("selected_locations", []),
    )
    if summary.empty:
        return

    status_labels = {
        "Data returned": "Datos devueltos",
        "Valid responses — zero timecards": "Respuestas válidas sin timecards",
        "Partial API coverage": "Cobertura API parcial",
        "No API response captured": "Sin respuesta API capturada",
    }
    display = summary.copy()
    display["Status"] = display["Status"].map(
        lambda value: status_labels.get(str(value), str(value))
    )
    incomplete = display[display["Status"].isin(
        ["Cobertura API parcial", "Sin respuesta API capturada"]
    )]
    zero_data = display[display["Status"] == "Respuestas válidas sin timecards"]

    if not incomplete.empty:
        refs = ", ".join(incomplete["Location Ref"].astype(str))
        st.error(
            f"Cobertura incompleta para {len(incomplete)} ubicación(es): {refs}. "
            "La aplicación no debe interpretar esas ubicaciones como auditadas."
        )
    elif not zero_data.empty:
        refs = ", ".join(zero_data["Location Ref"].astype(str))
        st.info(
            f"Oracle respondió correctamente para {len(zero_data)} ubicación(es) sin timecards: {refs}."
        )

    with st.expander("Cobertura por ubicación", expanded=not incomplete.empty):
        st.dataframe(display, use_container_width=True, hide_index=True)


def render_results(bundle: AnalysisBundle, *, show_advanced: bool) -> None:
    context = st.session_state.get("analysis_context") or {}
    adjustment_audit = st.session_state.get("adjustment_audit", pd.DataFrame())
    result_history = st.session_state.get("adjustment_result_history", pd.DataFrame())
    comparison = st.session_state.get("snapshot_comparison", pd.DataFrame())

    finding_source = auditor_finding_source(bundle)
    violation_summary = build_violation_employee_summary(finding_source)
    employee_summary = build_employee_summary(
        workdays=bundle.workdays,
        violations=finding_source,
        reviews=bundle.reviews,
        punch_errors=bundle.punch_errors,
        raw_timecards=bundle.raw_timecards,
        adjustments=result_history if not result_history.empty else adjustment_audit,
    )

    st.markdown(
        f"<div class='callout callout-blue'><b>{context.get('location_label','Análisis')}</b> · "
        f"{context.get('date_label','')}</div>",
        unsafe_allow_html=True,
    )

    render_location_coverage_panel(bundle, context)

    readiness = build_readiness_table(bundle)
    core_ready = set(
        readiness.loc[
            readiness["Control"].isin(
                ["BI timecards", "Nombres y Payroll ID", "Ajustes solicitados a Oracle"]
            ),
            "Estado",
        ]
    ).issubset({"Confirmado"})
    if core_ready:
        st.success(
            "Fuente BI lista para auditoría: timecards, empleados y ajustes fueron recuperados. "
            "Los controles administrativos pendientes se muestran sin ocultar los hallazgos."
        )
    with st.expander("Cobertura de datos y controles administrativos", expanded=False):
        st.dataframe(readiness, use_container_width=True, hide_index=True)

    probable_queue = build_probable_meal_queue(bundle.workdays, bundle.meals)
    second_meal_queue = build_second_meal_review_queue(
        bundle.workdays,
        bundle.reviews,
        bundle.candidates,
    )
    review_summary = build_review_summary(bundle.reviews)

    tab_names = [
        "Meal Violations",
        "Meals probables",
        "Segundo meal",
        "Punch Review",
        "Controles pendientes",
        "Ajustes",
        "Más detalles",
    ]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_meal_violations_tab(bundle, violation_summary)

    with tabs[1]:
        st.markdown("### Meals probables por timestamps")
        st.caption(
            "Estos gaps cumplen la duración mínima, pero Oracle no aporta evidencia suficiente "
            "para confirmar que fueron meals duty-free. No se cuentan como cumplimiento ni como violación."
        )
        if probable_queue.empty:
            st.success("No hay meals probables pendientes de validación.")
        else:
            display = probable_queue.copy()
            display["Legal Workday Date"] = display["Legal Workday Date"].map(_format_date)
            st.metric("Jornadas con meal probable", len(display))
            st.dataframe(display, use_container_width=True, hide_index=True)

    with tabs[2]:
        st.markdown("### Revisión del segundo meal")
        st.caption(
            "Cola separada para jornadas mayores a 10 horas y hallazgos relacionados con el segundo meal."
        )
        if second_meal_queue.empty:
            st.success("No hay casos de segundo meal pendientes.")
        else:
            display = second_meal_queue.copy()
            display["Legal Workday Date"] = display["Legal Workday Date"].map(_format_date)
            display["Second Meal Status"] = display["Second Meal Status"].map(
                lambda value: _labels(_split_codes(value)) or value
            )
            st.metric("Jornadas en revisión", len(display))
            st.dataframe(display, use_container_width=True, hide_index=True)

    with tabs[3]:
        st.markdown("### Punch Review")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Jornadas con revisión", int(bundle.stats.get("punch_error_workdays", 0)))
        p2.metric("Timecards actualmente abiertos", int(bundle.stats.get("open_timecards", 0)))
        p3.metric(
            "Clock Out status histórico faltante",
            int(bundle.stats.get("historical_clock_out_status_missing", 0)),
        )
        p4.metric(
            "Marcadores de break estructurales",
            int(bundle.stats.get("structural_break_markers", 0)),
        )
        st.caption(
            "Los marcadores estructurales de break se conservan como evidencia, pero ya no se cuentan como Punch Errors."
        )
        punch_table = friendly_punch_errors(bundle.punch_errors)
        if punch_table.empty:
            st.success("No se detectaron marcaciones que requieran corrección o confirmación.")
        else:
            st.dataframe(punch_table, use_container_width=True, hide_index=True)

    with tabs[4]:
        st.markdown("### Controles y revisiones pendientes")
        st.caption(
            "El resumen cuenta jornadas únicas por categoría; evita presentar varios controles del mismo workday como incidentes separados."
        )
        if review_summary.empty:
            st.success("No hay controles pendientes.")
        else:
            st.dataframe(review_summary, use_container_width=True, hide_index=True)

        with st.expander("Ver detalle por empleado y workday", expanded=False):
            review_table = friendly_cases(bundle.reviews, "Review", include_premium=False)
            if review_table.empty:
                st.info("No hay detalle disponible.")
            else:
                compact_columns = [
                    column
                    for column in [
                        "Empleado",
                        "ID nómina",
                        "Fecha workday",
                        "Ubicación(es)",
                        "Hallazgo",
                        "Acción",
                    ]
                    if column in review_table.columns
                ]
                st.dataframe(
                    review_table[compact_columns],
                    use_container_width=True,
                    hide_index=True,
                )

    with tabs[5]:
        st.markdown("### Ajustes manuales que cambiaron el resultado")
        st.caption(
            "Solo se muestran ajustes que crearon, eliminaron o modificaron un hallazgo de meal compliance."
        )
        impact_table = friendly_adjustment_impact(result_history)
        if impact_table.empty:
            st.success("No se detectaron ajustes manuales con impacto en Meal Violations.")
        else:
            st.dataframe(impact_table, use_container_width=True, hide_index=True)
        with st.expander("Ver todos los ajustes técnicos", expanded=False):
            if adjustment_audit.empty:
                st.info("Oracle no devolvió ajustes para el alcance actual.")
            else:
                st.dataframe(adjustment_audit, use_container_width=True, hide_index=True)

    with tabs[6]:
        detail_tabs = ["Turnos", "Meals detectados", "Descargas"]
        if show_advanced:
            detail_tabs.extend(["Administración", "Cambios entre consultas"])
        nested = st.tabs(detail_tabs)
        with nested[0]:
            st.dataframe(
                friendly_workdays(bundle.workdays),
                use_container_width=True,
                hide_index=True,
            )
        with nested[1]:
            st.caption(
                "Un meal por marcación confirma timestamps/status; no prueba por sí solo que fue duty-free."
            )
            st.dataframe(
                friendly_meals(bundle.meals),
                use_container_width=True,
                hide_index=True,
            )
        with nested[2]:
            all_case_details = auditor_violation_details(bundle)
            location_summary = build_location_coverage_summary(
                bundle.coverage,
                bundle.raw_timecards,
                selected_locations=context.get("selected_locations", []),
            )
            downloads = [
                ("Posibles Meal Violations por empleado", violation_summary, "possible_meal_violations_by_employee.csv"),
                ("Detalle de posibles Meal Violations", all_case_details, "possible_meal_violations_detail.csv"),
                ("Bitácora de decisiones del auditor", _review_log(all_case_details), "auditor_review_log.csv"),
                ("Cobertura por ubicación", location_summary, "location_coverage.csv"),
                ("Meals probables", probable_queue, "probable_meal_review.csv"),
                ("Segundo meal", second_meal_queue, "second_meal_review.csv"),
                ("Punch Review", bundle.punch_errors, "punch_review.csv"),
                ("Resumen de controles", review_summary, "review_summary.csv"),
                ("Requiere revisión", bundle.reviews, "meal_review_cases.csv"),
                ("Ajustes con impacto", result_history, "adjustment_compliance_impact.csv"),
                ("Todos los turnos", bundle.workdays, "legal_workdays.csv"),
            ]
            for label, frame, filename in downloads:
                st.download_button(
                    label,
                    safe_csv_bytes(frame),
                    filename,
                    "text/csv",
                    use_container_width=True,
                )
            snapshot = create_snapshot_bytes(bundle, app_version=APP_VERSION, context=context)
            st.download_button(
                "Descargar audit snapshot completo JSON",
                snapshot,
                "meal_compliance_audit_snapshot.json",
                "application/json",
                use_container_width=True,
            )
            executive_snapshot = create_executive_snapshot_bytes(
                bundle,
                app_version=APP_VERSION,
                context=context,
            )
            st.download_button(
                "Descargar resumen ejecutivo anonimizado JSON",
                executive_snapshot,
                "meal_compliance_executive_summary.json",
                "application/json",
                use_container_width=True,
            )
        if show_advanced:
            with nested[3]:
                st.markdown("### Calidad de datos y reconciliación")
                if bundle.data_quality.empty:
                    st.success("No se detectaron issues en los controles disponibles.")
                else:
                    st.dataframe(bundle.data_quality, use_container_width=True, hide_index=True)
                if not bundle.reconciliation.empty:
                    st.markdown("### Reconciliación MICROS")
                    st.dataframe(bundle.reconciliation, use_container_width=True, hide_index=True)
                st.markdown("### Cobertura API")
                st.dataframe(bundle.coverage, use_container_width=True, hide_index=True)
                st.markdown("### Resumen técnico por empleado")
                st.dataframe(employee_summary, use_container_width=True, hide_index=True)
                with st.expander("Timecards normalizados", expanded=False):
                    raw = bundle.raw_timecards.drop(columns=["raw"], errors="ignore").copy()
                    if "adjustments" in raw.columns:
                        raw["adjustments"] = raw["adjustments"].map(
                            lambda value: json.dumps(value, ensure_ascii=False)
                            if isinstance(value, list)
                            else value
                        )
                    st.dataframe(raw, use_container_width=True, hide_index=True)
            with nested[4]:
                if comparison.empty:
                    st.info("No hay baseline anterior o no se detectaron cambios.")
                else:
                    st.dataframe(comparison, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Meal Violations Dashboard", page_icon="🍳", layout="wide", initial_sidebar_state="expanded")
    render_global_styles()
    render_header()
    if st.session_state.get("app_version") != APP_VERSION:
        reset_state()
        st.session_state.app_version = APP_VERSION

    st.sidebar.markdown("## Meal Compliance")
    st.sidebar.caption(f"The Broken Yolk · Versión {APP_VERSION}")
    show_advanced = st.sidebar.toggle("Mostrar administración", value=False)
    rules = render_rules()
    if st.sidebar.button("Cerrar conexión y borrar resultados", use_container_width=True):
        reset_state()
        st.rerun()

    (
        policy_records, workday_records, rate_records, control_totals,
        default_start, previous_snapshot, default_classification,
    ) = render_policy_inputs()

    source = (
        st.radio(
            "Fuente de datos",
            ["Oracle BI API", "JSON de Oracle para validación"],
            horizontal=True,
        )
        if show_advanced
        else "Oracle BI API"
    )
    common = dict(
        rules=rules,
        policy_records=policy_records,
        workday_records=workday_records,
        rate_records=rate_records,
        control_totals=control_totals,
        default_workday_start=default_start,
        default_classification=default_classification,
        previous_snapshot=previous_snapshot,
    )
    if source == "Oracle BI API":
        render_oracle_mode(**common)
    else:
        render_json_mode(**common)

    bundle = st.session_state.get("analysis_bundle")
    if bundle is not None:
        analysis_context = st.session_state.get("analysis_context") or {}
        fallback_refs = analysis_context.get("excel_fallback_locations") or []
        if fallback_refs:
            label_map = {
                str(item.get("ref")): str(item.get("label") or item.get("ref"))
                for item in analysis_context.get("selected_locations", [])
                if isinstance(item, dict)
            }
            fallback_labels = [label_map.get(str(ref), str(ref)) for ref in fallback_refs]
            st.info(
                "**Fuente híbrida:** Oracle MICROS es la fuente principal. Excel se utilizó "
                "únicamente para las ubicaciones sin timecards Oracle: "
                + ", ".join(fallback_labels)
                + "."
            )
        render_results(bundle, show_advanced=show_advanced)

    if show_advanced:
        with st.expander("Alcance y límites"):
            st.markdown(
                """
- El motor consolida por **empleado + workday legal**, incluso entre varias ubicaciones.
- Los empleados exentos se excluyen únicamente con clasificación activa verificada.
- Los empleados sin clasificación, workday verificado o cobertura completa pueden quedar bloqueados.
- “Cumplimiento por marcación” no demuestra que el meal fue duty-free.
- El premium usa un regular rate verificado cuando se carga; de lo contrario muestra un proxy de base rate claramente identificado.
- La auditoría reconstruye el antes/después de cada ajuste con los valores `prev*` de Oracle y reanaliza el workday.
- Para una cadena histórica permanente, descarga el snapshot en cada cierre o conecta posteriormente un almacenamiento durable.
                """
            )
    st.markdown('<div class="footer">Meal Violations Dashboard · Broken Yolk · By Jordan Memije</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
