from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from compliance.audit import build_adjustment_audit, build_adjustment_result_history
from compliance.engine import AnalysisBundle, analyze_timecards
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
from compliance.reporting import build_employee_summary
from compliance.snapshot import compare_snapshot_to_bundle, create_snapshot_bytes, load_snapshot_bytes
from compliance.validation import build_data_quality_report, build_source_coverage
from oracle_bi.client import OracleBIClient, OracleBIConfig, OracleBIError


APP_VERSION = "3.3.0"
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
        .hero { background:linear-gradient(135deg,#fff 0%,#eef8ff 100%); border:1px solid var(--border);
                border-radius:20px; padding:20px 24px; display:flex; align-items:center; gap:20px;
                box-shadow:0 8px 28px rgba(16,24,40,.06); margin-bottom:18px; }
        .hero img { width:88px; height:auto; }
        .hero-title { color:var(--ink); font-size:2rem; line-height:1.05; font-weight:850; }
        .hero-sub { color:var(--muted); font-size:1rem; margin-top:5px; }
        .hero-author { color:var(--blue-dark); font-weight:750; margin-top:7px; }
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
        @media(max-width:760px){ .block-container{padding-left:.75rem;padding-right:.75rem}.hero{padding:15px;align-items:flex-start}.hero img{width:62px}.hero-title{font-size:1.45rem} }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    logo = _logo_data_uri()
    image = f'<img src="{logo}" alt="The Broken Yolk Cafe">' if logo else ""
    st.markdown(
        f"""
        <div class="hero">{image}<div>
        <div class="hero-title">Meal Violations Dashboard</div>
        <div class="hero-sub">Oracle MICROS Simphony · California meal-period audit</div>
        <div class="hero-author">The Broken Yolk Cafe · By Jordan Memije</div>
        </div></div>
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
    st.sidebar.markdown("### Controles de cumplimiento")
    classification_mode = st.sidebar.selectbox(
        "Clasificación predeterminada",
        [
            "Estricto: desconocida (bloquea conclusión)",
            "Provisional: todos no exentos",
        ],
        help="La opción provisional debe usarse solo si HR confirma que todo el alcance es no exento.",
    )
    default_classification = "UNKNOWN" if classification_mode.startswith("Estricto") else "NON_EXEMPT"

    with st.sidebar.expander("1. Clasificación, waivers y acuerdos", expanded=False):
        policy_file = st.file_uploader("Employee policy CSV", type=["csv"], key="policy_csv")
        st.download_button(
            "Descargar plantilla",
            data=(
                "employee_key,classification,first_meal_waiver,second_meal_waiver,on_duty_meal_agreement,effective_date,expiration_date,document_reference,verified_by,notes\n"
                "12345,NON_EXEMPT,false,false,false,2026-01-01,,HRIS-123,HR Manager,\n"
            ).encode("utf-8-sig"),
            file_name="employee_compliance_policy_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with st.sidebar.expander("2. Workday legal por ubicación", expanded=False):
        workday_file = st.file_uploader("Workday configuration CSV", type=["csv"], key="workday_csv")
        default_start = st.text_input("Fallback temporal", value="00:00", help="Solo para análisis preliminar; no se considera verificado.")
        st.download_button(
            "Descargar plantilla",
            data=(
                "location_ref,workday_start,timezone,effective_date,expiration_date,verified_by,source\n"
                "BYC304,04:00,America/Los_Angeles,2026-01-01,,Payroll Manager,Payroll policy\n"
            ).encode("utf-8-sig"),
            file_name="workday_configuration_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with st.sidebar.expander("3. Regular rate verificado", expanded=False):
        rate_file = st.file_uploader("Regular rate CSV", type=["csv"], key="rate_csv")
        st.download_button(
            "Descargar plantilla",
            data=(
                "employee_key,regular_rate,effective_date,expiration_date,source,verified_by\n"
                "12345,24.75,2026-07-01,2026-07-15,Payroll calculation,Payroll Manager\n"
            ).encode("utf-8-sig"),
            file_name="verified_regular_rate_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with st.sidebar.expander("4. Reconciliación con MICROS", expanded=False):
        control_file = st.file_uploader("MICROS control totals CSV", type=["csv"], key="controls_csv")
        st.download_button(
            "Descargar plantilla",
            data=(
                "location_ref,business_date,timecards,employees,worked_hours,adjusted_timecards\n"
                "BYC304,2026-07-20,42,18,126.50,3\n"
            ).encode("utf-8-sig"),
            file_name="micros_control_totals_template.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with st.sidebar.expander("5. Snapshot anterior", expanded=False):
        snapshot_file = st.file_uploader("Audit snapshot JSON", type=["json"], key="snapshot_json")

    try:
        policy_records = policy_rows_to_records(load_employee_policy_csv(policy_file))
        workday_records = workday_rows_to_records(load_workday_config_csv(workday_file))
        rate_records = regular_rate_rows_to_records(load_regular_rate_csv(rate_file))
        control_totals = load_control_totals_csv(control_file)
        previous_snapshot = load_snapshot_bytes(snapshot_file.getvalue()) if snapshot_file else None
    except ValueError as error:
        st.sidebar.error(str(error))
        policy_records, workday_records, rate_records = {}, {}, {}
        control_totals, previous_snapshot = pd.DataFrame(), None

    return policy_records, workday_records, rate_records, control_totals, default_start, previous_snapshot, default_classification


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


def analyze_api_source(
    client: OracleBIClient,
    loc_refs: list[str],
    start_date: date,
    end_date: date,
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
    bundle, adjustment_audit, adjustment_history = analyze_payloads(
        timecard_payloads=timecard_payloads,
        employees_payloads=employees_payloads,
        jobs_payloads=jobs_payloads,
        locations_payload=locations_payload,
        selected_locations=loc_refs,
        authorized_locations=[str(item.get("locRef")) for item in locations_payload.get("locations", []) or [] if isinstance(item, dict) and item.get("active", True)],
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
                    )
                context = {
                    "location_label": ", ".join(selected_labels),
                    "date_label": f"{start_date:%m/%d/%Y}–{end_date:%m/%d/%Y}",
                    "location_refs": loc_refs,
                }
                save_analysis(bundle, metadata, audit, history, context, previous_snapshot)
            except (OracleBIError, ValueError) as error:
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
                save_analysis(bundle, metadata, audit, history, {"location_label": "Validación JSON", "date_label": ""}, previous_snapshot)
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


def render_dashboard(bundle: AnalysisBundle, employee_summary: pd.DataFrame, adjustment_audit: pd.DataFrame, result_history: pd.DataFrame) -> None:
    stats = bundle.stats
    missing = 0 if bundle.violations.empty else bundle.violations.get("Violation", pd.Series(dtype=str)).isin(["FIRST_MEAL_MISSING", "SECOND_MEAL_MISSING"]).sum()
    late = 0 if bundle.violations.empty else bundle.violations.get("Violation", pd.Series(dtype=str)).isin(["FIRST_MEAL_LATE", "SECOND_MEAL_LATE"]).sum()
    short = 0 if bundle.violations.empty else bundle.violations.get("Violation", pd.Series(dtype=str)).isin(["FIRST_MEAL_SHORT", "SECOND_MEAL_SHORT"]).sum()
    confirmed = int(pd.to_numeric(bundle.workdays.get("Confirmed Meals", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not bundle.workdays.empty else 0
    expected = int(employee_summary["Meals Expected by Hours"].sum()) if not employee_summary.empty else 0
    changed = int(result_history.get("Compliance Result Changed", pd.Series(dtype=bool)).fillna(False).sum()) if not result_history.empty else 0

    if not bundle.data_quality.empty and bundle.data_quality["Blocking"].fillna(False).any():
        st.markdown('<div class="callout callout-red"><b>Reporte bloqueado:</b> existen controles críticos de cobertura o reconciliación. Las presuntas violaciones se suprimieron hasta resolverlos.</div>', unsafe_allow_html=True)
    elif stats.get("classification_unverified_workdays", 0):
        st.markdown('<div class="callout callout-orange"><b>Resultados parciales:</b> hay empleados sin clasificación exento/no exento verificada.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="callout callout-green"><b>Controles básicos completos:</b> revisa de todos modos los casos duty-free, waivers y ajustes.</div>', unsafe_allow_html=True)

    cols = st.columns(6)
    metrics = [
        ("Presuntas violaciones", stats.get("presumed_violations", 0)),
        ("Empleados afectados", 0 if bundle.violations.empty else bundle.violations["Employee Key"].nunique()),
        ("Meals esperados", expected),
        ("Meals por marcación", confirmed),
        ("Ajustes", len(adjustment_audit)),
        ("Ajustes que cambiaron resultado", changed),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Meals faltantes", int(missing))
    c2.metric("Meals tardíos", int(late))
    c3.metric("Meals cortos", int(short))
    c4.metric("Workdays multi-location", stats.get("multi_location_workdays", 0))

    left, right = st.columns(2)
    with left:
        st.markdown("### Meals por empleado")
        if employee_summary.empty:
            st.info("No hay empleados.")
        else:
            chart = employee_summary.head(15).set_index("Employee")[["Confirmed Meals", "Missing Meals", "Late Meals", "Short Meals"]]
            chart.columns = ["Por marcación", "Faltantes", "Tardíos", "Cortos"]
            st.bar_chart(chart, horizontal=True, stack=True, height=max(320, min(650, 38 * len(chart))))
    with right:
        st.markdown("### Presuntas violaciones por día")
        if bundle.violations.empty:
            st.info("No hay presuntas violaciones automáticas.")
        else:
            daily = bundle.violations.copy()
            daily["Fecha"] = pd.to_datetime(daily.get("Legal Workday Date", daily.get("Business Date")), errors="coerce")
            st.line_chart(daily.groupby("Fecha").size().rename("Presuntas violaciones"), height=320)

    st.markdown("### Empleados que requieren atención")
    if employee_summary.empty:
        st.info("No hay datos.")
    else:
        attention = employee_summary[employee_summary["Status"] != "Cumplimiento por marcación"].copy()
        if attention.empty:
            st.success("No hay empleados pendientes en el alcance actual.")
        else:
            st.dataframe(
                attention[[
                    "Status", "Employee", "Payroll ID", "Classification", "Locations", "Meals Expected by Hours",
                    "Confirmed Meals", "Missing Meals", "Late Meals", "Short Meals", "Presumed Violations",
                    "Review Cases", "Adjustment Records", "Adjustments Changing Result", "Premium Workdays",
                ]].rename(columns={
                    "Status":"Estado", "Employee":"Empleado", "Payroll ID":"ID nómina", "Classification":"Clasificación",
                    "Locations":"Ubicaciones", "Meals Expected by Hours":"Meals esperados", "Confirmed Meals":"Por marcación",
                    "Missing Meals":"Faltantes", "Late Meals":"Tardíos", "Short Meals":"Cortos",
                    "Presumed Violations":"Presuntas violaciones", "Review Cases":"Revisiones", "Adjustment Records":"Ajustes",
                    "Adjustments Changing Result":"Ajustes con impacto", "Premium Workdays":"Workdays premium",
                }),
                use_container_width=True,
                hide_index=True,
            )


def render_results(bundle: AnalysisBundle) -> None:
    context = st.session_state.get("analysis_context") or {}
    adjustment_audit = st.session_state.get("adjustment_audit", pd.DataFrame())
    result_history = st.session_state.get("adjustment_result_history", pd.DataFrame())
    comparison = st.session_state.get("snapshot_comparison", pd.DataFrame())

    employee_summary = build_employee_summary(
        workdays=bundle.workdays,
        violations=bundle.violations,
        reviews=bundle.reviews,
        punch_errors=bundle.punch_errors,
        raw_timecards=bundle.raw_timecards,
        adjustments=result_history if not result_history.empty else adjustment_audit,
    )

    st.markdown(
        f"<div class='callout callout-blue'><b>{context.get('location_label','Análisis')}</b> · {context.get('date_label','')}</div>",
        unsafe_allow_html=True,
    )
    st.caption("'Meal por marcación' confirma timestamps/status de Oracle; no demuestra por sí solo que el periodo fue duty-free.")

    tabs = st.tabs([
        "Dashboard", "Empleados", "Presuntas violaciones", "Revisión manual", "Auditoría de ajustes",
        "Calidad de datos", "Jornadas y meals", "Cambios entre consultas", "Descargas",
    ])
    with tabs[0]:
        render_dashboard(bundle, employee_summary, adjustment_audit, result_history)
    with tabs[1]:
        st.markdown("### Meals por empleado")
        st.dataframe(employee_summary.rename(columns={
            "Employee":"Empleado", "Payroll ID":"ID nómina", "Classification":"Clasificación", "Locations":"Ubicaciones",
            "Workdays":"Jornadas", "Worked Hours":"Horas", "Meals Expected by Hours":"Meals esperados",
            "Confirmed Meals":"Meals por marcación", "Probable Meals":"Meals probables", "Missing Meals":"Faltantes",
            "Late Meals":"Tardíos", "Short Meals":"Cortos", "Presumed Violations":"Presuntas violaciones",
            "Review Cases":"Revisiones", "Punch Errors":"Punch errors", "Adjusted Timecards":"Timecards ajustados",
            "Adjustment Records":"Ajustes", "Adjustments Changing Result":"Ajustes con impacto", "Managers Involved":"Managers",
            "Premium Workdays":"Workdays premium", "Premium Estimate":"Premium estimado", "Verified Premium":"Premium verificado",
            "Meal Coverage %":"Cobertura por marcación %", "Status":"Estado",
        }), use_container_width=True, hide_index=True)
    with tabs[2]:
        if bundle.violations.empty:
            st.info("No hay presuntas violaciones automáticas con datos suficientes.")
        else:
            st.dataframe(friendly_cases(bundle.violations, "Violation", include_premium=True), use_container_width=True, hide_index=True)
    with tabs[3]:
        if bundle.reviews.empty:
            st.success("No hay casos pendientes de revisión.")
        else:
            st.dataframe(friendly_cases(bundle.reviews, "Review", include_premium=False), use_container_width=True, hide_index=True)
        if not bundle.punch_errors.empty:
            st.markdown("### Punch errors")
            st.dataframe(bundle.punch_errors, use_container_width=True, hide_index=True)
    with tabs[4]:
        st.markdown("### Cadena exacta de ajustes Oracle")
        st.caption("La reconstrucción recorre los ajustes del estado final al original usando los valores previos que Oracle devuelve.")
        if adjustment_audit.empty:
            st.info("Oracle no devolvió ajustes para el alcance actual.")
        else:
            st.dataframe(adjustment_audit, use_container_width=True, hide_index=True)
        st.markdown("### Impacto del ajuste en meal compliance")
        if result_history.empty:
            st.info("No hay ajustes con historial de reanálisis.")
        else:
            st.dataframe(result_history, use_container_width=True, hide_index=True)
    with tabs[5]:
        st.markdown("### Integridad y reconciliación")
        if bundle.data_quality.empty:
            st.success("No se detectaron issues de integridad en los controles disponibles.")
        else:
            st.dataframe(bundle.data_quality, use_container_width=True, hide_index=True)
        st.markdown("### Reconciliación contra MICROS")
        if bundle.reconciliation.empty:
            st.info("Carga MICROS control totals para comparar timecards, empleados y horas.")
        else:
            st.dataframe(bundle.reconciliation, use_container_width=True, hide_index=True)
        st.markdown("### Cobertura API")
        st.dataframe(bundle.coverage, use_container_width=True, hide_index=True)
    with tabs[6]:
        wtab, mtab, rtab = st.tabs(["Workdays consolidados", "Meals detectados", "Timecards"])
        with wtab:
            st.dataframe(friendly_workdays(bundle.workdays), use_container_width=True, hide_index=True)
        with mtab:
            st.dataframe(friendly_meals(bundle.meals), use_container_width=True, hide_index=True)
        with rtab:
            raw = bundle.raw_timecards.drop(columns=["raw"], errors="ignore").copy()
            if "adjustments" in raw.columns:
                raw["adjustments"] = raw["adjustments"].map(lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value)
            st.dataframe(raw, use_container_width=True, hide_index=True)
    with tabs[7]:
        st.markdown("### Cambios contra el snapshot anterior o la consulta previa")
        if comparison.empty:
            st.info("No hay baseline anterior o no se detectaron cambios.")
        else:
            st.dataframe(comparison, use_container_width=True, hide_index=True)
    with tabs[8]:
        downloads = [
            ("Resumen por empleado", employee_summary, "employee_meal_summary.csv"),
            ("Presuntas violaciones", bundle.violations, "presumed_meal_violations.csv"),
            ("Revisión manual", bundle.reviews, "meal_review_cases.csv"),
            ("Auditoría de ajustes", adjustment_audit, "timecard_adjustment_audit.csv"),
            ("Impacto de ajustes", result_history, "adjustment_compliance_impact.csv"),
            ("Calidad de datos", bundle.data_quality, "data_quality_issues.csv"),
            ("Reconciliación", bundle.reconciliation, "micros_reconciliation.csv"),
            ("Workdays", bundle.workdays, "legal_workdays.csv"),
            ("Meals", bundle.meals, "meal_candidates.csv"),
            ("Cambios", comparison, "snapshot_changes.csv"),
        ]
        for label, frame, filename in downloads:
            st.download_button(label, safe_csv_bytes(frame), filename, "text/csv", use_container_width=True)
        snapshot = create_snapshot_bytes(bundle, app_version=APP_VERSION, context=context)
        st.download_button("Descargar audit snapshot JSON", snapshot, "meal_compliance_audit_snapshot.json", "application/json", use_container_width=True)


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
    rules = render_rules()
    if st.sidebar.button("Cerrar conexión y borrar resultados", use_container_width=True):
        reset_state()
        st.rerun()

    (
        policy_records, workday_records, rate_records, control_totals,
        default_start, previous_snapshot, default_classification,
    ) = render_policy_inputs()

    source = st.radio("Fuente de datos", ["Oracle BI API", "JSON de Oracle para validación"], horizontal=True)
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
        render_results(bundle)

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
    st.markdown('<div class="footer">Meal Violations Dashboard · The Broken Yolk Cafe · Developed by Jordan Memije</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
