from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from numbers import Real
from typing import Any

import pandas as pd
import streamlit as st


APP_VERSION = "2.0.0"
MAX_FILE_SIZE_MB = 25

COL_NAME = "Name"
COL_PAYROLL_ID = "Payroll ID"
COL_CLOCK_IN = "Clock in Date and Time"
COL_CLOCK_OUT = "Clock Out Date and Time"
COL_STATUS = "Clock Out Status"
COL_REGULAR = "Regular Hours"
COL_OVERTIME = "Overtime Hours"
COL_ADJUSTMENTS = "Adjustment Count"

HEADER_ANCHORS = {
    COL_NAME,
    COL_CLOCK_IN,
    COL_CLOCK_OUT,
    COL_REGULAR,
}

VIOLATION_COLUMNS = [
    "Nombre",
    "Payroll ID",
    "Rol(es)",
    "Date",
    "Turno",
    "Reason",
    "Inicio Turno",
    "Fin Turno",
    "Inicio Meal",
    "Duración Meal (min)",
    "Horas antes del Meal",
    "Overtime Hours",
    "Total Horas Turno",
    "Estado original",
]

REVIEW_COLUMNS = [
    "Nombre",
    "Payroll ID",
    "Rol(es)",
    "Date",
    "Turno",
    "Revisión",
    "Detalle",
]

SHIFT_COLUMNS = [
    "Nombre",
    "Payroll ID",
    "Rol(es)",
    "Date",
    "Turno",
    "Inicio Turno",
    "Fin Turno",
    "Duración Turno (h)",
    "Horas Trabajadas por Reloj",
    "Total Horas Reportadas",
    "Overtime Hours",
    "Meals válidos",
    "Resultado",
]


class DataValidationError(ValueError):
    """Error de formato o calidad que impide emitir resultados confiables."""


@dataclass(frozen=True)
class Rules:
    meal_required_over_hours: float = 6.0
    latest_meal_start_hours: float = 5.0
    minimum_meal_minutes: float = 30.0
    maximum_same_shift_gap_minutes: float = 120.0
    timestamp_tolerance_seconds: float = 0.01

    def __post_init__(self) -> None:
        if self.meal_required_over_hours < 0:
            raise ValueError("El umbral de horas no puede ser negativo.")
        if self.latest_meal_start_hours < 0:
            raise ValueError("La hora límite del meal no puede ser negativa.")
        if self.minimum_meal_minutes <= 0:
            raise ValueError("La duración mínima del meal debe ser mayor a cero.")
        if self.maximum_same_shift_gap_minutes <= self.minimum_meal_minutes:
            raise ValueError(
                "El gap que separa turnos debe ser mayor a la duración mínima del meal."
            )
        if self.timestamp_tolerance_seconds < 0:
            raise ValueError("La tolerancia de timestamps no puede ser negativa.")

    def cache_key(self) -> tuple[float, ...]:
        return (
            self.meal_required_over_hours,
            self.latest_meal_start_hours,
            self.minimum_meal_minutes,
            self.maximum_same_shift_gap_minutes,
            self.timestamp_tolerance_seconds,
        )


@dataclass
class AnalysisResult:
    violations: pd.DataFrame
    reviews: pd.DataFrame
    shifts: pd.DataFrame
    warnings: list[str]
    stats: dict[str, Any]
    header_row_excel: int


def clean_column_name(value: Any) -> str:
    return " ".join(str(value).replace("\ufeff", "").split())


def canonical_header(value: Any) -> str:
    if pd.isna(value):
        return ""
    return clean_column_name(value).casefold()


def clean_text(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    text = " ".join(str(value).split())
    return text if text else pd.NA


def clean_identifier(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    if isinstance(value, Real) and not isinstance(value, bool):
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
    text = str(value).strip()
    return text if text else pd.NA


def display_employee_name(value: Any) -> Any:
    """Convierte 'Apellido, Nombre' a 'Apellido Nombre' sin adivinar otros formatos."""
    text = clean_text(value)
    if pd.isna(text):
        return pd.NA
    if "," in text:
        family, given = text.split(",", 1)
        return f"{family.strip()} {given.strip()}".strip()
    return text


def parse_excel_datetime(value: Any) -> pd.Timestamp:
    """Acepta datetime de Excel, texto o serial numérico de Excel."""
    if pd.isna(value):
        return pd.NaT

    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, (datetime, date)):
        return pd.Timestamp(value)

    if isinstance(value, Real) and not isinstance(value, bool):
        serial = float(value)
        if 20_000 <= serial <= 80_000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D")
        return pd.NaT

    text = str(value).strip()
    if not text or text == "-":
        return pd.NaT

    try:
        serial = float(text)
    except ValueError:
        serial = None

    if serial is not None and 20_000 <= serial <= 80_000:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D")

    parsed = pd.to_datetime(text, errors="coerce")
    return parsed if not pd.isna(parsed) else pd.NaT


def find_header_row(file_bytes: bytes) -> int:
    preview = pd.read_excel(
        BytesIO(file_bytes),
        sheet_name=0,
        header=None,
        nrows=50,
        dtype=object,
    )
    anchors = {canonical_header(name) for name in HEADER_ANCHORS}

    for index, row in preview.iterrows():
        values = {canonical_header(value) for value in row.tolist()}
        if anchors.issubset(values):
            return int(index)

    raise DataValidationError(
        "No encontré el encabezado del reporte. Debe incluir Name, Clock in Date and "
        "Time, Clock Out Date and Time y Regular Hours."
    )


def read_time_card(file_bytes: bytes) -> tuple[pd.DataFrame, int]:
    header_row = find_header_row(file_bytes)
    df = pd.read_excel(
        BytesIO(file_bytes),
        sheet_name=0,
        header=header_row,
        dtype=object,
    )

    df.columns = [clean_column_name(column) for column in df.columns]
    expected_names = {
        canonical_header(name): name
        for name in (
            COL_NAME,
            COL_PAYROLL_ID,
            COL_CLOCK_IN,
            COL_CLOCK_OUT,
            COL_STATUS,
            COL_ADJUSTMENTS,
            COL_REGULAR,
            COL_OVERTIME,
        )
    }
    df.rename(
        columns={
            column: expected_names[canonical_header(column)]
            for column in df.columns
            if canonical_header(column) in expected_names
        },
        inplace=True,
    )

    duplicated = pd.Index(df.columns)[pd.Index(df.columns).duplicated()].unique().tolist()
    if duplicated:
        raise DataValidationError(
            "El archivo contiene encabezados duplicados: " + ", ".join(map(str, duplicated))
        )

    missing = sorted(HEADER_ANCHORS.difference(df.columns))
    if missing:
        raise DataValidationError("Faltan columnas obligatorias: " + ", ".join(missing))

    return df, header_row + 1


def numeric_column(
    series: pd.Series,
    punch_mask: pd.Series,
    column_name: str,
    *,
    blank_is_zero: bool,
) -> pd.Series:
    text = series.astype("string").str.strip()
    blank = series.isna() | text.eq("") | text.eq("-")
    parsed = pd.to_numeric(series.where(~blank), errors="coerce")
    invalid = punch_mask & ~blank & parsed.isna()

    if invalid.any():
        raise DataValidationError(
            f"Hay {int(invalid.sum())} valores no numéricos en '{column_name}'."
        )

    if not blank_is_zero and (punch_mask & blank).any():
        raise DataValidationError(
            f"Hay {int((punch_mask & blank).sum())} marcaciones sin '{column_name}'."
        )

    return parsed.fillna(0.0).astype(float)


def prepare_punches(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], dict[str, int]]:
    data = df.copy()
    warnings: list[str] = []

    for optional in (COL_PAYROLL_ID, COL_STATUS, COL_OVERTIME, COL_ADJUSTMENTS):
        if optional not in data.columns:
            data[optional] = pd.NA

    clock_in_text = data[COL_CLOCK_IN].astype("string").str.strip()
    clock_out_text = data[COL_CLOCK_OUT].astype("string").str.strip()
    marker_mask = clock_in_text.eq("-").fillna(False) & clock_out_text.eq("-").fillna(
        False
    )

    if marker_mask.any():
        block = marker_mask.cumsum()
        data["_Employee Name"] = (
            data[COL_NAME].where(marker_mask).groupby(block).transform("first")
        )
        data["_Employee ID"] = (
            data[COL_PAYROLL_ID].where(marker_mask).groupby(block).transform("first")
        )
        data["_Role"] = data[COL_NAME].where(~marker_mask)
    else:
        warnings.append(
            "No encontré filas marcador con '-'; usé Name y Payroll ID de cada marcación."
        )
        data["_Employee Name"] = data[COL_NAME]
        data["_Employee ID"] = data[COL_PAYROLL_ID]
        data["_Role"] = pd.NA

    data["_Employee Name"] = data["_Employee Name"].map(display_employee_name)
    data["_Employee ID"] = data["_Employee ID"].map(clean_identifier)
    data["_Role"] = data["_Role"].map(clean_text)

    data["_Clock In"] = data[COL_CLOCK_IN].map(parse_excel_datetime)
    data["_Clock Out"] = data[COL_CLOCK_OUT].map(parse_excel_datetime)
    punch_mask = data["_Clock In"].notna()

    nonblank_clock_in = ~data[COL_CLOCK_IN].isna() & ~clock_in_text.eq("") & ~marker_mask
    bad_clock_in = nonblank_clock_in & data["_Clock In"].isna()
    if bad_clock_in.any():
        raise DataValidationError(
            f"No pude interpretar {int(bad_clock_in.sum())} valores de Clock In."
        )

    if not punch_mask.any():
        raise DataValidationError("El archivo no contiene marcaciones con fecha y hora válidas.")

    missing_clock_out = punch_mask & data["_Clock Out"].isna()
    if missing_clock_out.any():
        raise DataValidationError(
            f"Hay {int(missing_clock_out.sum())} marcaciones sin Clock Out válido."
        )

    data["_Regular"] = numeric_column(
        data[COL_REGULAR], punch_mask, COL_REGULAR, blank_is_zero=False
    )
    data["_Overtime"] = numeric_column(
        data[COL_OVERTIME], punch_mask, COL_OVERTIME, blank_is_zero=True
    )
    data["_Adjustments"] = numeric_column(
        data[COL_ADJUSTMENTS], punch_mask, COL_ADJUSTMENTS, blank_is_zero=True
    )

    punches = data.loc[punch_mask].copy()
    missing_names = punches["_Employee Name"].isna()
    if missing_names.any():
        raise DataValidationError(
            f"No pude asignar empleado a {int(missing_names.sum())} marcaciones."
        )

    missing_ids = punches["_Employee ID"].isna()
    if missing_ids.any():
        warnings.append(
            f"{int(missing_ids.sum())} marcaciones no tienen Payroll ID; usé el nombre "
            "como identificador alternativo."
        )

    punches["_Employee Key"] = punches["_Employee ID"].astype("string")
    punches.loc[missing_ids, "_Employee Key"] = (
        "NAME::" + punches.loc[missing_ids, "_Employee Name"].astype(str)
    )
    punches["_Status"] = punches[COL_STATUS].astype("string").fillna("").str.strip()
    punches["_Status Key"] = punches["_Status"].str.casefold()
    punches["_Reported Hours"] = punches["_Regular"] + punches["_Overtime"]
    punches["_Clock Hours"] = (
        punches["_Clock Out"] - punches["_Clock In"]
    ).dt.total_seconds() / 3600

    negative_time = punches["_Clock Hours"] < 0
    if negative_time.any():
        raise DataValidationError(
            f"Hay {int(negative_time.sum())} marcaciones cuyo Clock Out es anterior al Clock In."
        )

    negative_hours = punches["_Reported Hours"] < 0
    if negative_hours.any():
        raise DataValidationError(
            f"Hay {int(negative_hours.sum())} marcaciones con horas negativas."
        )

    duplicate_columns = [
        "_Employee Key",
        "_Role",
        "_Clock In",
        "_Clock Out",
        "_Status",
        "_Regular",
        "_Overtime",
    ]
    duplicates = punches.duplicated(duplicate_columns, keep="first")
    if duplicates.any():
        count = int(duplicates.sum())
        warnings.append(f"Eliminé {count} marcaciones exactamente duplicadas.")
        punches = punches.loc[~duplicates].copy()

    punches.sort_values(["_Employee Key", "_Clock In", "_Clock Out"], inplace=True)
    punches.reset_index(drop=True, inplace=True)

    stats = {
        "source_rows": int(len(df)),
        "punch_rows": int(len(punches)),
        "employees": int(punches["_Employee Key"].nunique()),
        "adjusted_rows": int((punches["_Adjustments"] > 0).sum()),
        "zero_duration_rows": int((punches["_Clock Hours"] == 0).sum()),
    }
    return punches, warnings, stats


def assign_work_periods(punches: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    result: list[pd.DataFrame] = []
    tolerance_minutes = rules.timestamp_tolerance_seconds / 60

    for _, employee_rows in punches.groupby("_Employee Key", sort=False):
        group = employee_rows.sort_values(["_Clock In", "_Clock Out"]).copy()
        periods: list[int] = []
        period_number = 0
        previous_out: pd.Timestamp | None = None

        for _, row in group.iterrows():
            clock_in = row["_Clock In"]
            clock_out = row["_Clock Out"]

            if previous_out is None:
                period_number += 1
            else:
                gap_minutes = (clock_in - previous_out).total_seconds() / 60
                if gap_minutes < -tolerance_minutes:
                    raise DataValidationError(
                        "Encontré marcaciones traslapadas para un empleado. Corrige el "
                        "reporte antes de analizarlo."
                    )
                if gap_minutes > rules.maximum_same_shift_gap_minutes:
                    period_number += 1

            periods.append(period_number)
            if previous_out is None or clock_out > previous_out:
                previous_out = clock_out

        group["_Work Period"] = periods
        result.append(group)

    return pd.concat(result, ignore_index=True)


def build_base_row(group: pd.DataFrame, period_number: int) -> dict[str, Any]:
    first = group.iloc[0]
    start = group["_Clock In"].min()
    end = group["_Clock Out"].max()
    roles = sorted(group["_Role"].dropna().astype(str).unique().tolist())
    return {
        "Nombre": first["_Employee Name"],
        "Payroll ID": first["_Employee ID"],
        "Rol(es)": ", ".join(roles),
        "Date": start.date(),
        "Turno": period_number,
        "Inicio Turno": start,
        "Fin Turno": end,
    }


def analyze_work_periods(
    punches: pd.DataFrame,
    rules: Rules,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    violations: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    shifts: list[dict[str, Any]] = []

    tolerance_minutes = rules.timestamp_tolerance_seconds / 60
    tolerance_hours = rules.timestamp_tolerance_seconds / 3600
    eligible_count = 0
    inferred_meals = 0
    zero_break_reviews = 0

    grouped = punches.groupby(["_Employee Key", "_Work Period"], sort=False)
    for (_, period_number), rows in grouped:
        group = rows.sort_values(["_Clock In", "_Clock Out"]).reset_index(drop=True)
        base = build_base_row(group, int(period_number))
        shift_start = base["Inicio Turno"]
        shift_end = base["Fin Turno"]
        reported_hours = float(group["_Reported Hours"].sum())
        overtime_hours = float(group["_Overtime"].sum())
        clock_hours = float(group["_Clock Hours"].sum())
        span_hours = (shift_end - shift_start).total_seconds() / 3600
        eligible = reported_hours > rules.meal_required_over_hours + tolerance_hours

        if eligible:
            eligible_count += 1

        local_reviews: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for index in range(len(group) - 1):
            current = group.iloc[index]
            following = group.iloc[index + 1]
            minutes = (
                following["_Clock In"] - current["_Clock Out"]
            ).total_seconds() / 60
            if minutes < -tolerance_minutes:
                raise DataValidationError("Encontré intervalos traslapados dentro de un turno.")

            worked_before = float(group.iloc[: index + 1]["_Clock Hours"].sum())
            elapsed_before = (
                current["_Clock Out"] - shift_start
            ).total_seconds() / 3600
            gaps.append(
                {
                    "start": current["_Clock Out"],
                    "end": following["_Clock In"],
                    "minutes": max(0.0, minutes),
                    "worked_before": worked_before,
                    "elapsed_before": elapsed_before,
                    "current_clock_hours": float(current["_Clock Hours"]),
                    "status": current["_Status"],
                    "status_key": current["_Status Key"],
                    "row_index": index,
                }
            )

        temporal_meals = [
            gap
            for gap in gaps
            if gap["minutes"] + tolerance_minutes >= rules.minimum_meal_minutes
        ]
        ambiguous_zero_meals = [
            gap
            for gap in temporal_meals
            if gap["status_key"] == "on break"
            and gap["current_clock_hours"] <= tolerance_hours
        ]
        valid_meals = [gap for gap in temporal_meals if gap not in ambiguous_zero_meals]
        explicit_short_meals = [
            gap
            for gap in gaps
            if gap["status_key"] == "on break"
            and gap["minutes"] > tolerance_minutes
            and gap["minutes"] + tolerance_minutes < rules.minimum_meal_minutes
        ]

        if eligible:
            inferred = [gap for gap in valid_meals if gap["status_key"] != "on break"]
            for gap in inferred:
                inferred_meals += 1
                status = gap["status"] or "vacío"
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Meal inferido por timestamps",
                        "Detalle": (
                            f"Gap de {gap['minutes']:.1f} min con status '{status}'. "
                            "Se contó como meal para evitar un falso positivo."
                        ),
                    }
                )

            zero_breaks = group[
                (group["_Status Key"] == "on break")
                & (group["_Clock Hours"] <= tolerance_hours)
            ]
            if not zero_breaks.empty:
                zero_break_reviews += 1
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Evento On break de cero horas",
                        "Detalle": (
                            "El evento no contiene trabajo previo en su propia fila. "
                            "Se conservó el gap real, pero conviene revisar la marcación."
                        ),
                    }
                )

            hour_difference = abs(reported_hours - clock_hours)
            if hour_difference > 0.05:
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Horas reportadas distintas a timestamps",
                        "Detalle": (
                            f"Diferencia de {hour_difference:.2f} h entre horas reportadas "
                            "y la suma de intervalos trabajados."
                        ),
                    }
                )

        meal_for_output: dict[str, Any] | None = valid_meals[0] if valid_meals else None
        if meal_for_output is None and ambiguous_zero_meals:
            meal_for_output = ambiguous_zero_meals[0]
        reason: str | None = None
        violation_meal: dict[str, Any] | None = None

        if eligible:
            incomplete_final_break = str(group.iloc[-1]["_Status Key"]) == "on break"
            if valid_meals:
                first_meal = valid_meals[0]
                if (
                    first_meal["elapsed_before"]
                    > rules.latest_meal_start_hours + tolerance_hours
                ):
                    reason = f"Break after {rules.latest_meal_start_hours:g}h"
                    violation_meal = first_meal
            elif incomplete_final_break:
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Break sin regreso registrado",
                        "Detalle": (
                            "El último registro termina On break y no existe un Clock In "
                            "posterior para medir su duración."
                        ),
                    }
                )
            elif explicit_short_meals:
                reason = f"Break under {rules.minimum_meal_minutes:g} min"
                violation_meal = explicit_short_meals[0]
                meal_for_output = violation_meal
            elif ambiguous_zero_meals:
                # Existe un gap suficiente, pero el evento On break no contiene trabajo
                # previo. No se confirma cumplimiento ni se inventa una violación.
                pass
            else:
                reason = "No Break Taken"

        if reason is not None:
            violations.append(
                {
                    **base,
                    "Reason": reason,
                    "Inicio Meal": violation_meal["start"] if violation_meal else pd.NaT,
                    "Duración Meal (min)": (
                        round(float(violation_meal["minutes"]), 2)
                        if violation_meal
                        else pd.NA
                    ),
                    "Horas antes del Meal": (
                        round(float(violation_meal["elapsed_before"]), 2)
                        if violation_meal
                        else pd.NA
                    ),
                    "Overtime Hours": round(overtime_hours, 2),
                    "Total Horas Turno": round(reported_hours, 2),
                    "Estado original": (
                        violation_meal["status"] if violation_meal else ""
                    ),
                }
            )

        reviews.extend(local_reviews)
        if reason:
            result_label = "Violación"
        elif local_reviews:
            result_label = "Requiere revisión"
        elif eligible:
            result_label = "Sin violación"
        else:
            result_label = "No aplica"

        shifts.append(
            {
                **base,
                "Duración Turno (h)": round(span_hours, 2),
                "Horas Trabajadas por Reloj": round(clock_hours, 2),
                "Total Horas Reportadas": round(reported_hours, 2),
                "Overtime Hours": round(overtime_hours, 2),
                "Meals válidos": len(valid_meals),
                "Resultado": result_label,
                "Inicio Meal": meal_for_output["start"] if meal_for_output else pd.NaT,
            }
        )

    violation_df = pd.DataFrame(violations, columns=VIOLATION_COLUMNS)
    review_df = pd.DataFrame(reviews, columns=REVIEW_COLUMNS)
    shift_df = pd.DataFrame(shifts)
    if not shift_df.empty:
        shift_df = shift_df[SHIFT_COLUMNS + ["Inicio Meal"]]

    stats = {
        "work_periods": int(len(shift_df)),
        "eligible_periods": int(eligible_count),
        "inferred_meals": int(inferred_meals),
        "zero_break_reviews": int(zero_break_reviews),
        "review_periods": int(
            review_df[["Payroll ID", "Nombre", "Date", "Turno"]]
            .drop_duplicates()
            .shape[0]
        )
        if not review_df.empty
        else 0,
    }
    return violation_df, review_df, shift_df, stats


def analyze_time_card(file_bytes: bytes, rules: Rules) -> AnalysisResult:
    raw, header_row_excel = read_time_card(file_bytes)
    punches, warnings, source_stats = prepare_punches(raw)
    punches = assign_work_periods(punches, rules)
    violations, reviews, shifts, analysis_stats = analyze_work_periods(punches, rules)

    if not violations.empty:
        violations.sort_values(["Nombre", "Date", "Turno"], inplace=True)
        violations.reset_index(drop=True, inplace=True)
    if not reviews.empty:
        reviews.sort_values(["Nombre", "Date", "Turno", "Revisión"], inplace=True)
        reviews.reset_index(drop=True, inplace=True)
    if not shifts.empty:
        shifts.sort_values(["Nombre", "Inicio Turno", "Turno"], inplace=True)
        shifts.reset_index(drop=True, inplace=True)

    return AnalysisResult(
        violations=violations,
        reviews=reviews,
        shifts=shifts,
        warnings=warnings,
        stats={**source_stats, **analysis_stats},
        header_row_excel=header_row_excel,
    )


def safe_csv_bytes(df: pd.DataFrame) -> bytes:
    safe = df.copy()
    dangerous_prefixes = ("=", "+", "-", "@")

    for column in safe.columns:
        if pd.api.types.is_object_dtype(safe[column]) or pd.api.types.is_string_dtype(
            safe[column]
        ):
            safe[column] = safe[column].map(
                lambda value: (
                    "'" + str(value)
                    if not pd.isna(value) and str(value).startswith(dangerous_prefixes)
                    else value
                )
            )

    return safe.to_csv(index=False).encode("utf-8-sig")


def render_downloads(result: AnalysisResult) -> None:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "Descargar todas las violaciones",
            safe_csv_bytes(result.violations),
            "meal_violations.csv",
            "text/csv",
            key="download_violations",
        )
    with col2:
        st.download_button(
            "Descargar todas las revisiones",
            safe_csv_bytes(result.reviews),
            "meal_reviews.csv",
            "text/csv",
            key="download_reviews",
        )
    with col3:
        st.download_button(
            "Descargar todos los turnos",
            safe_csv_bytes(result.shifts),
            "processed_shifts.csv",
            "text/csv",
            key="download_shifts",
        )


def invalidate_analysis() -> None:
    for key in (
        "analysis_key",
        "analysis_result",
        "analysis_rules",
        "violation_employee",
        "violation_reasons",
    ):
        st.session_state.pop(key, None)


def count_affected_employees(violations: pd.DataFrame) -> int:
    if violations.empty:
        return 0
    ids = violations["Payroll ID"].astype("string").str.strip()
    fallback = "NAME::" + violations["Nombre"].astype("string")
    keys = ids.where(ids.notna() & ids.ne(""), fallback)
    return int(keys.nunique())


def employee_option(row: pd.Series) -> str:
    payroll_id = clean_identifier(row["Payroll ID"])
    if pd.isna(payroll_id):
        return str(row["Nombre"])
    return f"{row['Nombre']} — {payroll_id}"


def main() -> None:
    st.set_page_config(
        page_title="Meal Violations Dashboard",
        page_icon="🍳",
        layout="wide",
    )

    st.title("🍳 Meal Violations Dashboard")
    st.caption(
        "Los meals se detectan por el intervalo real entre Clock Out y el siguiente "
        "Clock In. El status se usa como evidencia secundaria."
    )

    if "uploader_version" not in st.session_state:
        st.session_state.uploader_version = 0

    if st.sidebar.button("Borrar archivo y resultados"):
        current_upload_key = f"timecard_upload_{st.session_state.uploader_version}"
        invalidate_analysis()
        st.session_state.pop(current_upload_key, None)
        st.session_state.uploader_version += 1
        st.rerun()

    st.sidebar.header("Reglas de análisis")
    with st.sidebar.form("analysis_rules_form"):
        required_over = st.number_input(
            "Meal requerido cuando el turno supera (horas)",
            min_value=0.0,
            max_value=24.0,
            value=6.0,
            step=0.25,
        )
        latest_start = st.number_input(
            "Inicio máximo del meal (horas)",
            min_value=0.0,
            max_value=24.0,
            value=5.0,
            step=0.25,
        )
        minimum_minutes = st.number_input(
            "Duración mínima del meal (minutos)",
            min_value=1.0,
            max_value=180.0,
            value=30.0,
            step=1.0,
        )
        split_gap = st.number_input(
            "Gap que inicia otro turno (minutos)",
            min_value=31.0,
            max_value=720.0,
            value=120.0,
            step=15.0,
            help="Un intervalo mayor a este valor separa dos turnos y no cuenta como meal.",
        )
        analyze_clicked = st.form_submit_button("Analizar", use_container_width=True)

    uploaded = st.file_uploader(
        "Sube el archivo Time Card Detail (.xlsx)",
        type=["xlsx"],
        key=f"timecard_upload_{st.session_state.uploader_version}",
        on_change=invalidate_analysis,
    )

    if analyze_clicked:
        if uploaded is None:
            st.error("Primero sube un archivo Excel.")
        elif uploaded.size > MAX_FILE_SIZE_MB * 1024 * 1024:
            st.error(f"El archivo supera el límite de {MAX_FILE_SIZE_MB} MB.")
        else:
            try:
                rules = Rules(
                    meal_required_over_hours=float(required_over),
                    latest_meal_start_hours=float(latest_start),
                    minimum_meal_minutes=float(minimum_minutes),
                    maximum_same_shift_gap_minutes=float(split_gap),
                )
                file_bytes = uploaded.getvalue()
                file_hash = hashlib.sha256(file_bytes).hexdigest()
                analysis_key = (APP_VERSION, file_hash, rules.cache_key())

                if (
                    st.session_state.get("analysis_key") != analysis_key
                    or st.session_state.get("analysis_result") is None
                ):
                    invalidate_analysis()
                    with st.spinner("Leyendo el archivo y reconstruyendo turnos..."):
                        result = analyze_time_card(file_bytes, rules)
                    st.session_state.analysis_result = result
                    st.session_state.analysis_key = analysis_key
                    st.session_state.analysis_rules = rules
            except DataValidationError as error:
                invalidate_analysis()
                st.error(str(error))
            except ValueError as error:
                invalidate_analysis()
                st.error(str(error))
            except (ImportError, OSError) as error:
                invalidate_analysis()
                st.error(f"No pude leer el Excel: {error}")
            except Exception:
                invalidate_analysis()
                st.error(
                    "Ocurrió un error inesperado al analizar el archivo. Verifica que sea "
                    "un reporte Time Card Detail válido."
                )

    result = st.session_state.get("analysis_result")
    if result is None:
        if uploaded is None:
            st.info("Sube un archivo y presiona Analizar.")
        else:
            st.info("Archivo listo. Presiona Analizar en el panel izquierdo.")
        return

    applied_rules: Rules | None = st.session_state.get("analysis_rules")
    if applied_rules is not None:
        st.caption(
            "Resultado aplicado: turno > "
            f"{applied_rules.meal_required_over_hours:g} h, meal ≥ "
            f"{applied_rules.minimum_meal_minutes:g} min, inicio ≤ "
            f"{applied_rules.latest_meal_start_hours:g} h."
        )

    for warning in result.warnings:
        st.warning(warning)

    total_violations = len(result.violations)
    affected_employees = count_affected_employees(result.violations)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Violaciones", total_violations)
    col2.metric("Empleados afectados", affected_employees)
    col3.metric("Turnos por revisar", result.stats["review_periods"])
    col4.metric("Turnos analizados", result.stats["work_periods"])

    if total_violations:
        st.error(f"Se detectaron {total_violations} posibles violaciones.")
    else:
        st.success("No se detectaron violaciones con las reglas seleccionadas.")

    if result.stats["review_periods"]:
        st.warning(
            "Los casos de revisión no se cuentan como violaciones hasta que una persona "
            "confirme la marcación."
        )

    tab_violations, tab_reviews, tab_shifts = st.tabs(
        ["Violaciones", "Requiere revisión", "Turnos procesados"]
    )

    with tab_violations:
        if result.violations.empty:
            st.info("No hay violaciones para mostrar.")
        else:
            filtered = result.violations.copy()
            filtered["_Empleado"] = filtered.apply(employee_option, axis=1)
            employees = ["(Todos)"] + sorted(
                filtered["_Empleado"].dropna().unique().tolist()
            )
            selected = st.selectbox("Empleado", employees, key="violation_employee")
            reasons = sorted(filtered["Reason"].dropna().unique().tolist())
            selected_reasons = st.multiselect(
                "Motivo",
                reasons,
                default=reasons,
                key="violation_reasons",
            )
            if selected != "(Todos)":
                filtered = filtered[filtered["_Empleado"] == selected]
            filtered = filtered[filtered["Reason"].isin(selected_reasons)]
            displayed = filtered.drop(columns="_Empleado")
            st.dataframe(displayed, use_container_width=True, hide_index=True)

            counts = (
                result.violations["Nombre"]
                .value_counts()
                .rename_axis("Empleado")
                .rename("Violaciones")
            )
            st.subheader("Violaciones por empleado")
            st.bar_chart(counts)

    with tab_reviews:
        if result.reviews.empty:
            st.info("No hay marcaciones ambiguas para revisar.")
        else:
            st.dataframe(result.reviews, use_container_width=True, hide_index=True)

    with tab_shifts:
        st.dataframe(result.shifts, use_container_width=True, hide_index=True)

    st.subheader("Descargas completas")
    render_downloads(result)

    with st.expander("Detalles técnicos del archivo"):
        st.write(
            {
                "Fila de encabezado detectada": result.header_row_excel,
                "Marcaciones válidas": result.stats["punch_rows"],
                "Empleados": result.stats["employees"],
                "Turnos sujetos a regla": result.stats["eligible_periods"],
                "Meals inferidos por timestamps": result.stats["inferred_meals"],
                "Eventos On break de cero horas": result.stats["zero_break_reviews"],
                "Filas con ajustes": result.stats["adjusted_rows"],
            }
        )


if __name__ == "__main__":
    main()
