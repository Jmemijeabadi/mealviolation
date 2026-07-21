from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
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


APP_VERSION = "3.1.0"
MAX_RANGE_DAYS = 31

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
    "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED": "Confirmar acuerdo on-duty y si el empleado fue relevado de funciones.",
    "MEAL_PROBABLE_TIMESTAMP_ONLY": "Confirmar con MICROS o el supervisor que el gap fue un meal duty-free.",
    "PUNCH_ERROR": "Corregir o confirmar la marcación en MICROS antes de cerrar el caso.",
    "INCOMPLETE_TIMECARD": "Esperar el Clock Out o corregir el timecard.",
    "ADJUSTED_TIMECARD_REVIEW": "Revisar el historial y motivo del ajuste.",
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
        "analysis_context",
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
        .block-container { padding-top: 1.3rem; max-width: 1480px; }
        div[data-testid="stMetric"] {
            background: white; border: 1px solid #e5e7eb; border-radius: 14px;
            padding: 14px 16px; box-shadow: 0 3px 12px rgba(20, 30, 50, .05);
        }
        .hero-card {
            background: white; border: 1px solid #e5e7eb; border-radius: 16px;
            padding: 18px 22px; margin: 4px 0 18px 0;
        }
        .hero-title { font-size: 1.55rem; font-weight: 750; color: #172033; }
        .hero-subtitle { color: #667085; margin-top: 4px; }
        .section-note {
            background: #ffffff; border-left: 4px solid #009efb; padding: 12px 16px;
            border-radius: 8px; margin-bottom: 12px;
        }
        .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .stTabs [data-baseweb="tab"] { padding-left: 14px; padding-right: 14px; }
        </style>
        <div class="hero-card">
            <div class="hero-title">Meal Violations Dashboard</div>
            <div class="hero-subtitle">Oracle MICROS Simphony · Auditoría de meals en California</div>
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


def _employee_filter_options(bundle: AnalysisBundle) -> list[str]:
    if bundle.workdays.empty or "Employee" not in bundle.workdays:
        return ["Todos los empleados"]
    employees = sorted(
        value for value in bundle.workdays["Employee"].dropna().astype(str).unique().tolist() if value
    )
    return ["Todos los empleados", *employees]


def _filter_employee(df: pd.DataFrame, selected_employee: str) -> pd.DataFrame:
    if df.empty or selected_employee == "Todos los empleados" or "Employee" not in df.columns:
        return df.copy()
    return df[df["Employee"].astype(str) == selected_employee].copy()


def _friendly_workdays(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        automatic = _split_codes(row.get("Automatic Violations"))
        reviews = _split_codes(row.get("Reviews"))
        if automatic:
            status = "Violación confirmada"
            priority = "Alta"
            codes = automatic
        elif reviews:
            status = "Revisión requerida"
            priority = "Media"
            codes = reviews
        else:
            status = "Cumple"
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
                "Horas trabajadas": row.get("Worked Hours", 0),
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
                "Horas trabajadas": row.get("Worked Hours", 0),
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
            "Horas trabajadas": df.get("Worked Hours", 0),
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
        result["Error de marcación"] = result["Error de marcación"].astype(str).str.replace(
            old, new, regex=False
        )
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
            "Horas trabajadas antes": df.get("Worked Hours Before", 0),
            "Evidencia": df.get("Evidence", "").map(lambda value: evidence_map.get(str(value), value)),
            "Confirmado": df.get("Confirmed Duty-Free Timestamp", False).map(
                lambda value: "Sí" if bool(value) else "No"
            ),
            "Pagado": df.get("Paid", False).map(lambda value: "Sí" if bool(value) else "No"),
        }
    )


def _friendly_timecards(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    result = pd.DataFrame(
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
            "Status de salida": df.get("clock_out_status_label", "").map(
                lambda value: CLOCK_OUT_LABELS.get(str(value), value)
            ),
            "Ajustes": df.get("adjustment_count", 0),
            "Nombre vinculado": df.get("employee_name_resolved", False).map(
                lambda value: "Sí" if bool(value) else "No"
            ),
        }
    )
    return result


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
        f"La app mostrará el ID de nómina como respaldo. Oracle devolvió "
        f"{employee_catalog_count:,} empleados en getEmployeeDimensions."
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
        st.caption(
            "La nueva versión intenta vincular por Employee Num, Employee ID, Payroll ID y External Payroll ID. "
            "Si siguen apareciendo registros aquí, el catálogo de Oracle no está devolviendo el nombre correspondiente."
        )


def render_downloads(bundle: AnalysisBundle) -> None:
    tables = [
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
    stats = bundle.stats
    context = st.session_state.get("analysis_context", {})
    location_label = context.get("location_label", "")
    date_label = context.get("date_label", "")
    if location_label or date_label:
        st.markdown(
            f'<div class="section-note"><strong>{location_label}</strong>'
            f'{" · " if location_label and date_label else ""}{date_label}</div>',
            unsafe_allow_html=True,
        )

    render_name_resolution(bundle)

    employee_options = _employee_filter_options(bundle)
    selected_employee = st.selectbox(
        "Filtrar por empleado",
        employee_options,
        key=f"employee_filter_{APP_VERSION}",
    )

    workdays = _filter_employee(bundle.workdays, selected_employee)
    violations = _filter_employee(bundle.violations, selected_employee)
    reviews = _filter_employee(bundle.reviews, selected_employee)
    punches = _filter_employee(bundle.punch_errors, selected_employee)
    meals = _filter_employee(bundle.meals, selected_employee)
    raw_timecards = bundle.raw_timecards.copy()
    if selected_employee != "Todos los empleados" and "employee_name" in raw_timecards:
        raw_timecards = raw_timecards[raw_timecards["employee_name"].astype(str) == selected_employee]

    affected = set(violations.get("Employee", pd.Series(dtype=str)).dropna().astype(str))
    premium_days = (
        violations[["Employee", "Business Date"]].drop_duplicates().shape[0]
        if not violations.empty and {"Employee", "Business Date"}.issubset(violations.columns)
        else 0
    )
    estimated_premium = (
        workdays.loc[
            workdays.get("Potential Premium Workday", pd.Series(False, index=workdays.index)).fillna(False).astype(bool),
            "Estimated Meal Premium",
        ].sum()
        if not workdays.empty and "Estimated Meal Premium" in workdays
        else 0.0
    )

    st.markdown("## Resumen ejecutivo")
    cols = st.columns(6)
    cols[0].metric("Jornadas con violación", premium_days)
    cols[1].metric("Empleados afectados", len(affected))
    cols[2].metric("Casos por revisar", len(reviews))
    cols[3].metric("Errores de marcación", len(punches))
    cols[4].metric("Jornadas analizadas", len(workdays))
    cols[5].metric("Premium estimado", f"${estimated_premium:,.2f}")

    if premium_days:
        st.error(
            f"Hay {premium_days} jornada(s) con una violación automática. "
            "Revisa primero la tabla de Atención requerida."
        )
    elif len(reviews):
        st.warning(
            "No hay violaciones automáticas en el filtro actual, pero existen casos que requieren validación humana."
        )
    else:
        st.success("No se detectaron casos que requieran acción en el filtro actual.")

    friendly_workdays = _friendly_workdays(workdays)
    attention = friendly_workdays[
        friendly_workdays["Estado"].isin(["Violación confirmada", "Revisión requerida"])
    ] if not friendly_workdays.empty else friendly_workdays

    tabs = st.tabs(
        [
            "Atención requerida",
            "Violaciones",
            "Revisión manual",
            "Todas las jornadas",
            "Meals detectados",
            "Marcaciones",
            "Descargas",
        ]
    )

    with tabs[0]:
        st.markdown("### Qué debe revisarse primero")
        st.caption(
            "Alta = violación automática. Media = falta confirmar waiver, meal, ajuste o marcación."
        )
        if attention.empty:
            st.info("No hay casos pendientes para el filtro seleccionado.")
        else:
            priority_order = pd.Categorical(attention["Prioridad"], ["Alta", "Media", "Sin acción"], ordered=True)
            attention = attention.assign(_orden=priority_order).sort_values(["_orden", "Fecha", "Empleado"])
            st.dataframe(
                attention.drop(columns="_orden"),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Horas trabajadas": st.column_config.NumberColumn(format="%.2f"),
                    "Premium estimado": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

        if not attention.empty:
            counts = attention.groupby("Empleado", as_index=False).size().rename(columns={"size": "Casos"})
            counts = counts.sort_values("Casos", ascending=False).head(15)
            st.markdown("### Empleados con más casos pendientes")
            st.bar_chart(counts.set_index("Empleado")["Casos"])

    with tabs[1]:
        friendly = _friendly_cases(violations, "Violation", violation=True)
        if friendly.empty:
            st.info("No hay violaciones automáticas.")
        else:
            st.dataframe(
                friendly,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Horas trabajadas": st.column_config.NumberColumn(format="%.2f"),
                    "Premium estimado": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

    with tabs[2]:
        friendly = _friendly_cases(reviews, "Review", violation=False)
        if friendly.empty:
            st.info("No hay casos pendientes de revisión manual.")
        else:
            st.dataframe(friendly, use_container_width=True, hide_index=True)
        if not punches.empty:
            st.markdown("### Errores de marcación")
            st.dataframe(_friendly_punches(punches), use_container_width=True, hide_index=True)

    with tabs[3]:
        if friendly_workdays.empty:
            st.info("No hay jornadas para mostrar.")
        else:
            st.dataframe(
                friendly_workdays,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Horas trabajadas": st.column_config.NumberColumn(format="%.2f"),
                    "Premium estimado": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

    with tabs[4]:
        friendly = _friendly_meals(meals)
        if friendly.empty:
            st.info("No se identificaron candidatos de meal.")
        else:
            st.dataframe(friendly, use_container_width=True, hide_index=True)
            st.caption(
                "Confirmado = Oracle registró un unpaid break o un status On Break con duración suficiente. "
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
        with st.expander("Ver detalle técnico de timecards"):
            technical = raw_timecards.drop(columns=["raw"], errors="ignore").copy()
            if "adjustments" in technical.columns:
                technical["adjustments"] = technical["adjustments"].map(
                    lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                )
            st.dataframe(technical, use_container_width=True, hide_index=True)

    with tabs[6]:
        render_downloads(bundle)

    with st.expander("Indicadores técnicos del análisis"):
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
                "Timecards abiertos": stats.get("open_timecards", 0),
            }
        )


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
    col_connect, col_status = st.columns([1, 3])
    with col_connect:
        connect = st.button("Conectar a Oracle", type="primary", use_container_width=True)
    with col_status:
        if st.session_state.get("locations_payload"):
            st.success("Oracle BI API conectado.")
        else:
            st.info("Las credenciales se leen desde Streamlit Secrets.")

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
            with st.spinner("Consultando empleados, puestos y timecards en Oracle..."):
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
            return
        except Exception as error:
            st.error(f"No fue posible completar el análisis: {type(error).__name__}: {error}")
            return


def render_json_mode(rules: CaliforniaMealRules, waiver_records: dict[str, list[dict[str, Any]]]) -> None:
    st.info(
        "Modo de validación: carga respuestas JSON de Oracle sin exponer credenciales."
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
            st.session_state.dimension_payloads = {
                "employees_payload": employees_payload,
                "jobs_payload": jobs_payload,
                "locations_payload": locations_payload,
                "timecard_payloads": payloads,
            }
            st.session_state.analysis_context = {"location_label": "Validación JSON", "date_label": ""}
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
La aplicación consolida resultados por empleado y business date de Oracle. Los timecards abiertos, traslapados o materialmente inconsistentes no generan una conclusión automática. Los gaps de al menos 30 minutos sin evidencia de unpaid break se muestran como meal probable y requieren confirmación humana. Los paid breaks no se consideran automáticamente meals duty-free.

El resultado es una auditoría operativa y no sustituye la revisión de nómina, waivers, acuerdos on-duty ni asesoría legal laboral.
            """
        )


if __name__ == "__main__":
    main()
