from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st

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
from oracle_bi.client import OracleBIClient, OracleBIConfig, OracleBIError


APP_VERSION = "3.0.0"
MAX_RANGE_DAYS = 31


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
        "normalized_timecards",
        "source_payloads",
        "dimension_payloads",
    ):
        st.session_state.pop(key, None)


def load_json_upload(file_obj: Any) -> Any:
    if file_obj is None:
        return None
    raw = file_obj.getvalue()
    return json.loads(raw.decode("utf-8-sig"))


def render_header() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f5f7fa; }
        .block-container { padding-top: 1.5rem; max-width: 1500px; }
        div[data-testid="stMetric"] {
            background: white; border: 1px solid #e6e8ec; border-radius: 12px;
            padding: 16px; box-shadow: 0 2px 8px rgba(20, 30, 50, .05);
        }
        .status-card {
            background: white; border: 1px solid #e6e8ec; border-radius: 12px;
            padding: 14px 18px; margin-bottom: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Meal Compliance Dashboard")
    st.caption("Oracle MICROS Simphony BI API · California meal-period review")


def render_rules() -> CaliforniaMealRules:
    with st.sidebar.expander("Reglas aplicadas", expanded=False):
        st.markdown(
            """
- Primer meal: turno mayor de 5 horas.
- Waiver del primer meal: solo si el total no excede 6 horas.
- Segundo meal: turno mayor de 10 horas.
- Waiver del segundo meal: solo si el total no excede 12 horas y el primero no fue renunciado.
- Duración mínima: 30 minutos.
- Un gap sin status de break queda como revisión, no como cumplimiento confirmado.
            """
        )
    return CaliforniaMealRules()


def render_downloads(bundle: AnalysisBundle) -> None:
    tables = [
        ("Violaciones", bundle.violations, "meal_violations.csv"),
        ("Revisiones", bundle.reviews, "meal_reviews.csv"),
        ("Punch errors", bundle.punch_errors, "punch_errors.csv"),
        ("Workdays", bundle.workdays, "workdays.csv"),
        ("Meals", bundle.meals, "meal_candidates.csv"),
        ("Timecards normalizados", bundle.raw_timecards.drop(columns=["raw", "adjustments"], errors="ignore"), "normalized_timecards.csv"),
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
    stats = bundle.stats
    st.markdown("## Resumen")
    cols = st.columns(6)
    cols[0].metric("Violaciones", stats.get("automatic_violations", 0))
    cols[1].metric("Workdays con premium", stats.get("premium_workdays", 0))
    cols[2].metric("Revisiones", stats.get("reviews", 0))
    cols[3].metric("Punch errors", stats.get("punch_errors", 0))
    cols[4].metric("Turnos abiertos", stats.get("open_timecards", 0))
    cols[5].metric("Workdays", stats.get("workdays", 0))

    st.caption(
        "Premium estimado: ${:,.2f} · Premium registrado por Oracle: ${:,.2f}".format(
            stats.get("estimated_premium", 0.0), stats.get("oracle_premium_pay", 0.0)
        )
    )

    if stats.get("automatic_violations", 0):
        st.error(
            "Se detectaron incumplimientos automáticos con datos suficientemente confiables. "
            "El premium se consolida por empleado y business date."
        )
    else:
        st.success("No se detectaron violaciones automáticas con los datos confiables disponibles.")

    if stats.get("reviews", 0):
        st.warning(
            "Existen casos pendientes: waivers no verificados, meals probables, ajustes, "
            "paid breaks o timecards incompletos. No se incluyen automáticamente en el KPI."
        )

    tab_workdays, tab_violations, tab_reviews, tab_punches, tab_meals, tab_raw = st.tabs(
        ["Workdays", "Violaciones", "Revisiones", "Punch errors", "Meals", "Timecards"]
    )
    with tab_workdays:
        st.dataframe(bundle.workdays, use_container_width=True, hide_index=True)
    with tab_violations:
        if bundle.violations.empty:
            st.info("No hay violaciones automáticas.")
        else:
            st.dataframe(bundle.violations, use_container_width=True, hide_index=True)
    with tab_reviews:
        if bundle.reviews.empty:
            st.info("No hay casos pendientes de revisión.")
        else:
            st.dataframe(bundle.reviews, use_container_width=True, hide_index=True)
    with tab_punches:
        if bundle.punch_errors.empty:
            st.info("No hay Punch / Clock Errors.")
        else:
            st.dataframe(bundle.punch_errors, use_container_width=True, hide_index=True)
    with tab_meals:
        if bundle.meals.empty:
            st.info("No se identificaron candidatos de meal.")
        else:
            st.dataframe(bundle.meals, use_container_width=True, hide_index=True)
    with tab_raw:
        raw_display = bundle.raw_timecards.drop(columns=["raw"], errors="ignore").copy()
        if "adjustments" in raw_display.columns:
            raw_display["adjustments"] = raw_display["adjustments"].map(
                lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
            )
        st.dataframe(raw_display, use_container_width=True, hide_index=True)

    st.markdown("### Descargas")
    render_downloads(bundle)


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
        st.error("Falta configurar `.streamlit/secrets.toml` con la cuenta Business Intelligence API.")
        st.code(
            """[oracle]
auth_server = "https://YOUR-AUTH-SERVER"
application_server = "https://YOUR-APPLICATION-SERVER"
org_identifier = "YOUR_ORG_SHORT_NAME"
client_id = "YOUR_CLIENT_ID"
username = "YOUR_BI_API_ACCOUNT"
password = "YOUR_ROTATED_PASSWORD"
application_name = "Meal Compliance Dashboard"
""",
            language="toml",
        )
        return

    client = get_or_create_client(config)
    col_connect, col_status = st.columns([1, 3])
    with col_connect:
        connect = st.button("Conectar a Oracle", type="primary", use_container_width=True)
    with col_status:
        if st.session_state.get("locations_payload"):
            st.success("Oracle BI API conectado.")
        else:
            st.info("Las credenciales se leen desde Streamlit Secrets y no se muestran en pantalla.")

    if connect:
        try:
            with st.spinner("Autenticando y consultando ubicaciones..."):
                client.authenticate()
                st.session_state.locations_payload = client.get_locations()
            st.success("Conexión validada.")
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
            f"{item.get('name', item.get('locRef'))} — {item.get('locRef')}": str(item.get("locRef"))
            for item in locations
        }
        selected_label = st.selectbox("Location", list(option_map))
        loc_ref = option_map[selected_label]
    else:
        st.warning("Oracle no devolvió ubicaciones; captura el locRef manualmente.")
        loc_ref = st.text_input("Location Ref")

    default_end = date.today() - timedelta(days=1)
    default_start = default_end - timedelta(days=6)
    col_start, col_end = st.columns(2)
    with col_start:
        start_date = st.date_input("Business date inicial", value=default_start)
    with col_end:
        end_date = st.date_input("Business date final", value=default_end)

    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        st.error(f"El rango máximo por ejecución es de {MAX_RANGE_DAYS} días.")
        return

    if st.button("Consultar y analizar", type="primary", use_container_width=True):
        if not loc_ref:
            st.error("Selecciona una ubicación.")
            return
        try:
            with st.spinner("Consultando employees, job codes y timecards en Oracle..."):
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
        except (OracleBIError, ValueError) as error:
            st.error(str(error))
            return
        except Exception as error:
            st.error(f"No fue posible completar el análisis: {type(error).__name__}: {error}")
            return


def render_json_mode(rules: CaliforniaMealRules, waiver_records: dict[str, list[dict[str, Any]]]) -> None:
    st.info(
        "Modo de validación: carga respuestas JSON de Oracle sin exponer credenciales. "
        "Permite comparar el motor antes de habilitar producción."
    )
    timecards_file = st.file_uploader(
        "Respuesta de getTimeCardDetails (.json)", type=["json"], key="json_timecards"
    )
    employees_file = st.file_uploader(
        "Respuesta de getEmployeeDimensions (.json, opcional)", type=["json"], key="json_employees"
    )
    jobs_file = st.file_uploader(
        "Respuesta de getJobCodeDimensions (.json, opcional)", type=["json"], key="json_jobs"
    )
    locations_file = st.file_uploader(
        "Respuesta de getLocationDimensions (.json, opcional)", type=["json"], key="json_locations"
    )

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
        except (ValueError, json.JSONDecodeError) as error:
            st.error(str(error))


def main() -> None:
    st.set_page_config(
        page_title="Meal Compliance Dashboard",
        page_icon="🍳",
        layout="wide",
    )
    render_header()

    if st.session_state.get("app_version") != APP_VERSION:
        reset_state()
        st.session_state.app_version = APP_VERSION

    rules = render_rules()
    st.sidebar.caption(f"Versión {APP_VERSION}")
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
La aplicación utiliza `busDt` de Oracle como business date y consolida el premium potencial por empleado y business date. Los timecards abiertos, traslapados o materialmente inconsistentes no generan una conclusión automática. Los gaps de al menos 30 minutos sin evidencia de unpaid break se muestran como meal probable y requieren confirmación humana. Los paid breaks no se consideran automáticamente meals duty-free.

El resultado es una auditoría operativa y no sustituye la revisión de nómina, de waivers, de acuerdos on-duty ni la asesoría legal laboral.
            """
        )


if __name__ == "__main__":
    main()
