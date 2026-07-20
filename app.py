from __future__ import annotations

import hashlib
import re
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from numbers import Real
from typing import Any

import pandas as pd
import streamlit as st

try:
    import matplotlib.pyplot as plt
except ImportError:  # La app sigue funcionando si el entorno aún no lo instaló.
    plt = None


APP_VERSION = "2.5.0"
MAX_FILE_SIZE_MB = 25
UNKNOWN_LOCATION = "No especificada"

# Estos umbrales identifican calidad de marcacion; no cambian las reglas de meal.
INSTANT_PUNCH_MAX_MINUTES = 1.0
RAPID_PUNCH_MAX_MINUTES = 5.0
EARLY_FRAGMENT_MAX_MINUTES = 60.0
SUSPICIOUS_GAP_MIN_MINUTES = 20.0
SUSPICIOUS_GAP_MAX_MINUTES = 60.0
LONG_FOLLOWING_SEGMENT_MIN_HOURS = 4.0
EXPLICIT_BREAK_STATUS_KEYS = {"on break", "on paid break", "paid break"}

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
    "Location",
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
    "Location",
    "Nombre",
    "Payroll ID",
    "Rol(es)",
    "Date",
    "Turno",
    "Revisión",
    "Detalle",
]

PUNCH_ERROR_COLUMNS = [
    "Location",
    "Nombre",
    "Payroll ID",
    "Rol(es)",
    "Date",
    "Turno",
    "Tipo de Punch Error",
    "Confianza",
    "Clock In sospechoso",
    "Clock Out sospechoso",
    "Duración punch (min)",
    "Siguiente Clock In",
    "Gap posterior (min)",
    "Horas trabajadas antes del gap",
    "Estado MICROS",
    "Adjustment Count",
    "Impacto en Meal",
    "Detalle",
    "Acción recomendada",
]

SHIFT_COLUMNS = [
    "Location",
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
    "Meals confirmados",
    "Meals probables",
    "Punch errors",
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
    punch_errors: pd.DataFrame
    shifts: pd.DataFrame
    warnings: list[str]
    stats: dict[str, Any]
    header_row_excel: int
    location: str


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


def extract_location(preview: pd.DataFrame, header_row: int) -> str:
    location_labels = {"location", "locations", "location(s)"}
    locations: list[str] = []
    seen: set[str] = set()

    for _, row in preview.iloc[:header_row].iterrows():
        values = row.tolist()
        for position, value in enumerate(values):
            label = canonical_header(value).rstrip(":").strip()
            if label not in location_labels:
                continue

            for candidate in values[position + 1 :]:
                cleaned = clean_text(candidate)
                if pd.isna(cleaned):
                    continue
                text = str(cleaned)
                key = text.casefold()
                if key not in seen:
                    locations.append(text)
                    seen.add(key)
            break

    return " | ".join(locations) if locations else UNKNOWN_LOCATION


def inspect_report(file_bytes: bytes) -> tuple[int, str]:
    preview = pd.read_excel(
        BytesIO(file_bytes),
        sheet_name=0,
        header=None,
        nrows=100,
        dtype=object,
    )
    anchors = {canonical_header(name) for name in HEADER_ANCHORS}

    for index, row in preview.iterrows():
        values = {canonical_header(value) for value in row.tolist()}
        if anchors.issubset(values):
            header_row = int(index)
            return header_row, extract_location(preview, header_row)

    raise DataValidationError(
        "No encontré el encabezado del reporte. Debe incluir Name, Clock in Date and "
        "Time, Clock Out Date and Time y Regular Hours."
    )


def read_time_card(file_bytes: bytes) -> tuple[pd.DataFrame, int, str]:
    header_row, location = inspect_report(file_bytes)
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

    return df, header_row + 1, location


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
        # En Streamlit Cloud, string[pyarrow].eq() produce bool[pyarrow].
        # PyArrow no implementa cumsum para booleanos, así que acumulamos en NumPy.
        marker_values = marker_mask.to_numpy(dtype="int8", na_value=0)
        block = pd.Series(marker_values.cumsum(), index=data.index, dtype="int64")
        data["_Employee Block"] = block
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
        data["_Employee Block"] = pd.NA

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
            f"{int(missing_ids.sum())} marcaciones no tienen Payroll ID; usé el bloque "
            "del empleado y su nombre como identificador alternativo."
        )

    punches["_Employee Key"] = punches["_Employee ID"].astype("string")
    fallback_keys = "NAME::" + punches["_Employee Name"].astype(str)
    has_block = punches["_Employee Block"].notna()
    fallback_keys.loc[has_block] = (
        "BLOCK::"
        + punches.loc[has_block, "_Employee Block"].astype("Int64").astype("string")
        + "::"
        + punches.loc[has_block, "_Employee Name"].astype(str)
    )
    punches.loc[missing_ids, "_Employee Key"] = fallback_keys.loc[missing_ids]
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
        "Location": first["_Location"],
        "Nombre": first["_Employee Name"],
        "Payroll ID": first["_Employee ID"],
        "Rol(es)": ", ".join(roles),
        "Date": start.date(),
        "Turno": period_number,
        "Inicio Turno": start,
        "Fin Turno": end,
    }


def classify_punch_errors(
    group: pd.DataFrame,
    gaps: list[dict[str, Any]],
    base: dict[str, Any],
    rules: Rules,
    reported_hours: float,
) -> tuple[list[dict[str, Any]], set[int], set[int], set[int]]:
    """Clasifica errores de reloj sin convertirlos en meal violations.

    Devuelve los errores visibles y tres grupos de gaps:
    - bloqueados: no pueden usarse como meal por una anomalía clara/probable;
    - ambiguos: podrían ser meal, pero necesitan corregir o validar el punch;
    - probables: el tiempo parece meal aunque MICROS no tenga status de break.
    """

    errors: list[dict[str, Any]] = []
    blocked_gap_rows: set[int] = set()
    ambiguous_gap_rows: set[int] = set()
    probable_gap_rows: set[int] = set()
    classified_rows: set[int] = set()

    def append_error(
        gap: dict[str, Any] | None,
        row_index: int,
        error_type: str,
        confidence: str,
        meal_impact: str,
        detail: str,
        action: str,
    ) -> None:
        current = group.iloc[row_index]
        following = (
            group.iloc[row_index + 1] if row_index + 1 < len(group) else None
        )
        gap_minutes = float(gap["minutes"]) if gap is not None else pd.NA
        worked_before = (
            round(float(gap["worked_before"]), 2) if gap is not None else pd.NA
        )
        following_adjustments = (
            float(following["_Adjustments"]) if following is not None else 0.0
        )
        errors.append(
            {
                **base,
                "Tipo de Punch Error": error_type,
                "Confianza": confidence,
                "Clock In sospechoso": current["_Clock In"],
                "Clock Out sospechoso": current["_Clock Out"],
                "Duración punch (min)": round(
                    float(current["_Clock Hours"]) * 60, 2
                ),
                "Siguiente Clock In": (
                    following["_Clock In"] if following is not None else pd.NaT
                ),
                "Gap posterior (min)": (
                    round(gap_minutes, 2) if not pd.isna(gap_minutes) else pd.NA
                ),
                "Horas trabajadas antes del gap": worked_before,
                "Estado MICROS": current["_Status"],
                "Adjustment Count": int(
                    float(current["_Adjustments"]) + following_adjustments
                ),
                "Impacto en Meal": meal_impact,
                "Detalle": detail,
                "Acción recomendada": action,
            }
        )
        classified_rows.add(row_index)

    for gap in gaps:
        row_index = int(gap["row_index"])
        current = group.iloc[row_index]
        following = group.iloc[row_index + 1]
        current_minutes = float(current["_Clock Hours"]) * 60
        current_reported = float(current["_Reported Hours"])
        following_hours = float(following["_Clock Hours"])
        gap_minutes = float(gap["minutes"])
        status_key = str(current["_Status Key"])
        explicit_break = status_key in EXPLICIT_BREAK_STATUS_KEYS
        first_segment = row_index == 0

        if (
            first_segment
            and not explicit_break
            and current_minutes <= INSTANT_PUNCH_MAX_MINUTES
            and current_reported <= 0.02
        ):
            blocked_gap_rows.add(row_index)
            ambiguous_gap_rows.add(row_index)
            remaining_hours = max(0.0, reported_hours - current_reported)
            impact = (
                "Posible meal faltante tras corregir el punch; el gap no se contó "
                "como meal."
                if remaining_hours > rules.meal_required_over_hours
                else "El gap no se contó como meal; se debe recalcular tras corregir."
            )
            append_error(
                gap,
                row_index,
                "Clock In/Out instantáneo al inicio",
                "Alta",
                impact,
                (
                    f"El primer registro duró {current_minutes:.1f} min y fue seguido "
                    f"por otro Clock In {gap_minutes:.1f} min después. Esto parece un "
                    "doble punch accidental, no un meal confirmado."
                ),
                "Corregir el timecard en MICROS y volver a ejecutar el análisis.",
            )
            continue

        if (
            first_segment
            and not explicit_break
            and INSTANT_PUNCH_MAX_MINUTES < current_minutes <= RAPID_PUNCH_MAX_MINUTES
            and gap_minutes <= SUSPICIOUS_GAP_MAX_MINUTES
        ):
            blocked_gap_rows.add(row_index)
            ambiguous_gap_rows.add(row_index)
            append_error(
                gap,
                row_index,
                "Doble punch rápido al inicio",
                "Alta",
                "El gap no se contó como meal; el cumplimiento quedó en revisión.",
                (
                    f"El primer registro duró solo {current_minutes:.1f} min y el "
                    f"siguiente Clock In ocurrió {gap_minutes:.1f} min después."
                ),
                "Confirmar el punch con el empleado o revisar Time Card Adjustments.",
            )
            continue

        if (
            first_segment
            and not explicit_break
            and RAPID_PUNCH_MAX_MINUTES < current_minutes <= EARLY_FRAGMENT_MAX_MINUTES
            and SUSPICIOUS_GAP_MIN_MINUTES
            <= gap_minutes
            <= SUSPICIOUS_GAP_MAX_MINUTES
            and following_hours >= LONG_FOLLOWING_SEGMENT_MIN_HOURS
        ):
            if gap_minutes >= rules.minimum_meal_minutes:
                blocked_gap_rows.add(row_index)
                ambiguous_gap_rows.add(row_index)
                impact = (
                    "El gap podría ser un meal, pero no se validó ni se convirtió en "
                    "violación hasta revisar el punch."
                )
            else:
                impact = (
                    f"El gap fue menor de {rules.minimum_meal_minutes:g} min y no "
                    "cuenta como meal; las reglas de meal siguen aplicándose."
                )
            append_error(
                gap,
                row_index,
                "Fragmento temprano antes de otro Clock In",
                "Media",
                impact,
                (
                    f"El turno muestra {current_minutes:.1f} min iniciales, un gap de "
                    f"{gap_minutes:.1f} min y luego {following_hours:.2f} h continuas. "
                    "Puede ser un meal temprano o un Clock Out/In equivocado."
                ),
                "Validar con el empleado; corregir MICROS si no fue un meal real.",
            )
            continue

        if (
            not explicit_break
            and gap_minutes >= rules.minimum_meal_minutes
        ):
            probable_gap_rows.add(row_index)
            append_error(
                gap,
                row_index,
                "Clock Out/In sin status de break",
                "Media",
                "Se contó como meal probable por timestamps, sujeto a revisión.",
                (
                    f"Existe un gap real de {gap_minutes:.1f} min después de "
                    f"{gap['worked_before']:.2f} h trabajadas, pero MICROS muestra "
                    f"status '{current['_Status'] or 'vacío'}'."
                ),
                "Confirmar que fue duty-free y usar la función Break en MICROS.",
            )
            continue

        if (
            not explicit_break
            and SUSPICIOUS_GAP_MIN_MINUTES <= gap_minutes < rules.minimum_meal_minutes
        ):
            append_error(
                gap,
                row_index,
                "Clock Out/In corto sin status de break",
                "Media",
                f"El gap no alcanza {rules.minimum_meal_minutes:g} min; no cuenta como meal.",
                (
                    f"MICROS muestra un gap de {gap_minutes:.1f} min con status "
                    f"'{current['_Status'] or 'vacío'}'. Puede ser un break corto o "
                    "una marcación incorrecta."
                ),
                "Revisar la marcación; las reglas de meal se evaluaron por separado.",
            )

    # Un registro instantáneo final no tiene gap y por eso no aparece en la lista anterior.
    last_index = len(group) - 1
    if last_index >= 0 and last_index not in classified_rows:
        last = group.iloc[last_index]
        last_minutes = float(last["_Clock Hours"]) * 60
        last_reported = float(last["_Reported Hours"])
        if (
            last_minutes <= INSTANT_PUNCH_MAX_MINUTES
            and last_reported <= 0.02
            and str(last["_Status Key"]) not in EXPLICIT_BREAK_STATUS_KEYS
        ):
            append_error(
                None,
                last_index,
                "Clock In/Out instantáneo sin continuación",
                "Alta",
                "No se utilizó como meal; la marcación quedó en revisión.",
                "El registro comienza y termina prácticamente a la misma hora.",
                "Corregir el timecard en MICROS antes de cerrar el periodo.",
            )

    return errors, blocked_gap_rows, ambiguous_gap_rows, probable_gap_rows


def analyze_work_periods(
    punches: pd.DataFrame,
    rules: Rules,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    violations: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    punch_errors: list[dict[str, Any]] = []
    shifts: list[dict[str, Any]] = []

    tolerance_minutes = rules.timestamp_tolerance_seconds / 60
    tolerance_hours = rules.timestamp_tolerance_seconds / 3600
    eligible_count = 0
    inferred_meals = 0
    zero_break_reviews = 0
    zero_gap_reviews = 0
    confirmed_meal_count = 0
    probable_meal_count = 0
    review_period_keys: set[tuple[str, int]] = set()
    punch_error_period_keys: set[tuple[str, int]] = set()

    grouped = punches.groupby(["_Employee Key", "_Work Period"], sort=False)
    for (employee_key, period_number), rows in grouped:
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

        (
            local_punch_errors,
            blocked_gap_rows,
            _ambiguous_gap_rows,
            _probable_gap_rows,
        ) = classify_punch_errors(group, gaps, base, rules, reported_hours)
        punch_errors.extend(local_punch_errors)
        if local_punch_errors:
            punch_error_period_keys.add((str(employee_key), int(period_number)))

        temporal_meals = [
            gap
            for gap in gaps
            if gap["minutes"] + tolerance_minutes >= rules.minimum_meal_minutes
        ]
        zero_duration_break_meals = [
            gap
            for gap in temporal_meals
            if gap["status_key"] in EXPLICIT_BREAK_STATUS_KEYS
            and gap["current_clock_hours"] <= tolerance_hours
        ]
        ambiguous_punch_meals = [
            gap
            for gap in temporal_meals
            if gap["row_index"] in blocked_gap_rows
            or gap in zero_duration_break_meals
        ]
        confirmed_meals = [
            gap
            for gap in temporal_meals
            if gap["row_index"] not in blocked_gap_rows
            and gap["status_key"] == "on break"
            and gap not in zero_duration_break_meals
        ]
        probable_meals = [
            gap
            for gap in temporal_meals
            if gap["row_index"] not in blocked_gap_rows
            and gap["status_key"] != "on break"
            and gap not in zero_duration_break_meals
        ]
        valid_meals = sorted(
            confirmed_meals + probable_meals,
            key=lambda gap: gap["start"],
        )
        explicit_short_meals = [
            gap
            for gap in gaps
            if gap["row_index"] not in blocked_gap_rows
            and gap["status_key"] in EXPLICIT_BREAK_STATUS_KEYS
            and gap["minutes"] > tolerance_minutes
            and gap["minutes"] + tolerance_minutes < rules.minimum_meal_minutes
        ]
        zero_gap_breaks = [
            gap
            for gap in gaps
            if gap["status_key"] in EXPLICIT_BREAK_STATUS_KEYS
            and gap["minutes"] <= tolerance_minutes
        ]

        if eligible:
            confirmed_meal_count += len(confirmed_meals)
            probable_meal_count += len(probable_meals)

            for gap in probable_meals:
                inferred_meals += 1
                status = gap["status"] or "vacío"
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Meal probable por timestamps",
                        "Detalle": (
                            f"Gap de {gap['minutes']:.1f} min con status '{status}'. "
                            "Cumple tiempo y duración, pero el status de MICROS debe "
                            "confirmarse."
                        ),
                    }
                )

            if blocked_gap_rows:
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Meal no concluyente por Punch Error",
                        "Detalle": (
                            f"Se detectaron {len(local_punch_errors)} anomalías de reloj. "
                            "Los gaps afectados no se usaron como meals y el turno no se "
                            "convirtió automáticamente en violación; corrige MICROS y "
                            "vuelve a analizar."
                        ),
                    }
                )

            zero_breaks = group[
                (group["_Status Key"].isin(EXPLICIT_BREAK_STATUS_KEYS))
                & (group["_Clock Hours"] <= tolerance_hours)
            ]
            if not zero_breaks.empty:
                zero_break_reviews += 1
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Evento de break de cero horas",
                        "Detalle": (
                            "El evento no contiene trabajo previo en su propia fila. "
                            "Se conservó el gap real, pero conviene revisar la marcación."
                        ),
                    }
                )

            if zero_gap_breaks:
                zero_gap_reviews += 1
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Evento de break sin duración",
                        "Detalle": (
                            "El siguiente Clock In ocurre al mismo tiempo que el Clock Out "
                            "marcado como break; no se confirmó ni se descartó el meal."
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
        reason: str | None = None
        violation_meal: dict[str, Any] | None = None

        if eligible:
            incomplete_final_break = (
                str(group.iloc[-1]["_Status Key"]) in EXPLICIT_BREAK_STATUS_KEYS
            )
            if valid_meals:
                first_meal = valid_meals[0]
                if (
                    first_meal["elapsed_before"]
                    > rules.latest_meal_start_hours + tolerance_hours
                ):
                    if blocked_gap_rows:
                        local_reviews.append(
                            {
                                **base,
                                "Revisión": "Hora del meal no concluyente por Punch Error",
                                "Detalle": (
                                    "Un punch sospechoso puede cambiar la hora real de inicio "
                                    "del turno. El meal posterior no se marcó automáticamente "
                                    "como tardío; corrige MICROS y vuelve a analizar."
                                ),
                            }
                        )
                    else:
                        reason = f"Break after {rules.latest_meal_start_hours:g}h"
                        violation_meal = first_meal
            elif incomplete_final_break:
                local_reviews.append(
                    {
                        **base,
                        "Revisión": "Break sin regreso registrado",
                        "Detalle": (
                            "El último registro termina con status de break y no existe un Clock In "
                            "posterior para medir su duración."
                        ),
                    }
                )
            elif explicit_short_meals:
                reason = f"Break under {rules.minimum_meal_minutes:g} min"
                violation_meal = explicit_short_meals[0]
                meal_for_output = violation_meal
            elif blocked_gap_rows:
                # Un error de punch puede cambiar el inicio real del turno o la naturaleza
                # del gap. Se conserva fuera del KPI hasta corregir el timecard.
                pass
            elif zero_duration_break_meals:
                # El status indica break, pero la fila no contiene trabajo previo.
                # Se mantiene como revisión y no como conclusión automática.
                pass
            elif zero_gap_breaks:
                # El status indica un intento de break, pero los timestamps no permiten
                # confirmar su duración. Se conserva como revisión manual.
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
        if local_reviews:
            review_period_keys.add((str(employee_key), int(period_number)))
        if reason and local_punch_errors:
            result_label = "Posible violación + Punch error"
        elif reason:
            result_label = "Posible violación"
        elif local_punch_errors:
            result_label = "Punch error / revisión"
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
                "Meals confirmados": len(confirmed_meals),
                "Meals probables": len(probable_meals),
                "Punch errors": len(local_punch_errors),
                "Resultado": result_label,
                "Inicio Meal": meal_for_output["start"] if meal_for_output else pd.NaT,
            }
        )

    violation_df = pd.DataFrame(violations, columns=VIOLATION_COLUMNS)
    review_df = pd.DataFrame(reviews, columns=REVIEW_COLUMNS)
    punch_error_df = pd.DataFrame(punch_errors, columns=PUNCH_ERROR_COLUMNS)
    shift_df = pd.DataFrame(shifts)
    if not shift_df.empty:
        shift_df = shift_df[SHIFT_COLUMNS + ["Inicio Meal"]]

    stats = {
        "work_periods": int(len(shift_df)),
        "eligible_periods": int(eligible_count),
        "inferred_meals": int(inferred_meals),
        "confirmed_meals": int(confirmed_meal_count),
        "probable_meals": int(probable_meal_count),
        "zero_break_reviews": int(zero_break_reviews),
        "zero_gap_reviews": int(zero_gap_reviews),
        "review_periods": int(len(review_period_keys)),
        "punch_errors": int(len(punch_error_df)),
        "punch_error_periods": int(len(punch_error_period_keys)),
        "instant_punch_errors": int(
            punch_error_df["Tipo de Punch Error"]
            .astype("string")
            .str.contains("instantáneo", case=False, na=False)
            .sum()
        ),
        "rapid_punch_errors": int(
            punch_error_df["Tipo de Punch Error"]
            .astype("string")
            .str.contains("Doble punch rápido", case=False, na=False)
            .sum()
        ),
        "early_fragment_errors": int(
            punch_error_df["Tipo de Punch Error"]
            .astype("string")
            .str.contains("Fragmento temprano", case=False, na=False)
            .sum()
        ),
        "unlabeled_break_errors": int(
            punch_error_df["Tipo de Punch Error"]
            .astype("string")
            .str.contains("sin status de break", case=False, na=False)
            .sum()
        ),
    }
    return violation_df, review_df, punch_error_df, shift_df, stats


def analyze_time_card(file_bytes: bytes, rules: Rules) -> AnalysisResult:
    raw, header_row_excel, location = read_time_card(file_bytes)
    punches, warnings, source_stats = prepare_punches(raw)
    punches["_Location"] = location
    if location == UNKNOWN_LOCATION:
        warnings.append(
            "No encontré la Location en el preámbulo; los resultados se etiquetaron "
            f"como '{UNKNOWN_LOCATION}'."
        )
    punches = assign_work_periods(punches, rules)
    violations, reviews, punch_errors, shifts, analysis_stats = analyze_work_periods(
        punches, rules
    )

    if not violations.empty:
        violations.sort_values(["Nombre", "Date", "Turno"], inplace=True)
        violations.reset_index(drop=True, inplace=True)
    if not reviews.empty:
        reviews.sort_values(["Nombre", "Date", "Turno", "Revisión"], inplace=True)
        reviews.reset_index(drop=True, inplace=True)
    if not punch_errors.empty:
        punch_errors.sort_values(
            ["Nombre", "Date", "Turno", "Clock In sospechoso"], inplace=True
        )
        punch_errors.reset_index(drop=True, inplace=True)
    if not shifts.empty:
        shifts.sort_values(["Nombre", "Inicio Turno", "Turno"], inplace=True)
        shifts.reset_index(drop=True, inplace=True)

    return AnalysisResult(
        violations=violations,
        reviews=reviews,
        punch_errors=punch_errors,
        shifts=shifts,
        warnings=warnings,
        stats={**source_stats, **analysis_stats},
        header_row_excel=header_row_excel,
        location=location,
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


def filename_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug[:60] or "location_unknown"


def render_downloads(result: AnalysisResult) -> None:
    location_slug = filename_slug(result.location)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.download_button(
            "Descargar todas las violaciones",
            safe_csv_bytes(result.violations),
            f"meal_violations_{location_slug}.csv",
            "text/csv",
            key="download_violations",
        )
    with col2:
        st.download_button(
            "Descargar Punch / Clock Errors",
            safe_csv_bytes(result.punch_errors),
            f"punch_errors_{location_slug}.csv",
            "text/csv",
            key="download_punch_errors",
        )
    with col3:
        st.download_button(
            "Descargar todas las revisiones",
            safe_csv_bytes(result.reviews),
            f"meal_reviews_{location_slug}.csv",
            "text/csv",
            key="download_reviews",
        )
    with col4:
        st.download_button(
            "Descargar todos los turnos",
            safe_csv_bytes(result.shifts),
            f"processed_shifts_{location_slug}.csv",
            "text/csv",
            key="download_shifts",
        )


def invalidate_analysis() -> None:
    for key in (
        "analysis_key",
        "analysis_payload",
        "analysis_result",
        "analysis_rules",
    ):
        st.session_state.pop(key, None)


def result_to_payload(result: AnalysisResult) -> dict[str, Any]:
    """Guarda sólo tipos simples/serializables en Session State."""
    return {
        "violations": result.violations,
        "reviews": result.reviews,
        "punch_errors": result.punch_errors,
        "shifts": result.shifts,
        "warnings": result.warnings,
        "stats": result.stats,
        "header_row_excel": result.header_row_excel,
        "location": result.location,
    }


def result_from_payload(payload: dict[str, Any]) -> AnalysisResult:
    return AnalysisResult(**payload)


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


def render_punch_errors_table(result: AnalysisResult, key_prefix: str) -> None:
    if result.punch_errors.empty:
        st.success("No se detectaron Punch / Clock Errors con los criterios actuales.")
        return

    st.caption(
        "Estos errores de reloj se muestran por separado. No aumentan por sí solos "
        "el total de Meal Violations; revisa 'Impacto en Meal' para saber si el turno "
        "también podría requerir corrección o análisis legal."
    )
    filtered = result.punch_errors.copy()
    filtered["_Empleado"] = filtered.apply(employee_option, axis=1)
    employees = ["(Todos)"] + sorted(
        filtered["_Empleado"].dropna().unique().tolist()
    )
    error_types = sorted(
        filtered["Tipo de Punch Error"].dropna().astype(str).unique().tolist()
    )
    confidence_values = sorted(
        filtered["Confianza"].dropna().astype(str).unique().tolist()
    )

    col_employee, col_type, col_confidence = st.columns(3)
    with col_employee:
        selected_employee = st.selectbox(
            "Empleado",
            employees,
            key=f"{key_prefix}_punch_employee",
        )
    with col_type:
        selected_types = st.multiselect(
            "Tipo de error",
            error_types,
            default=error_types,
            key=f"{key_prefix}_punch_types",
        )
    with col_confidence:
        selected_confidence = st.multiselect(
            "Confianza",
            confidence_values,
            default=confidence_values,
            key=f"{key_prefix}_punch_confidence",
        )

    if selected_employee != "(Todos)":
        filtered = filtered[filtered["_Empleado"] == selected_employee]
    filtered = filtered[
        filtered["Tipo de Punch Error"].isin(selected_types)
        & filtered["Confianza"].isin(selected_confidence)
    ]
    st.dataframe(
        filtered.drop(columns="_Empleado"),
        use_container_width=True,
        hide_index=True,
    )


def render_detection_rules() -> None:
    rules = Rules()
    st.markdown(
        f"""
Esta herramienta aplica reglas operativas de auditoría. Los resultados deben
revisarse antes de considerarse una determinación definitiva.

### Cálculos utilizados

```text
Horas del turno = Σ (Regular Hours + Overtime Hours)

Duración del meal = siguiente Clock In − Clock Out anterior

Inicio del meal = Clock Out anterior

Horas hasta el meal = Inicio del meal − primer Clock In del turno
```

Un intervalo mayor de **{rules.maximum_same_shift_gap_minutes:g} minutos** inicia
otro turno y no se utiliza como meal del turno anterior.

### Reglas actuales

| Resultado | Condición |
|---|---|
| No aplica | Turno de {rules.meal_required_over_hours:g} horas o menos |
| Meal confirmado | Gap de al menos {rules.minimum_meal_minutes:g} minutos con status `On break` |
| Meal probable | Gap de al menos {rules.minimum_meal_minutes:g} minutos con otro status; requiere revisión |
| Break after {rules.latest_meal_start_hours:g}h | Meal que inicia después de {rules.latest_meal_start_hours:g} horas |
| Break under {rules.minimum_meal_minutes:g} min | Status explícito de break, pero el gap no completa {rules.minimum_meal_minutes:g} minutos |
| No Break Taken | Turno mayor de {rules.meal_required_over_hours:g} horas sin meal válido |

Los límites son estrictos: exactamente **{rules.meal_required_over_hours:g} horas**
no aplica; exactamente **{rules.latest_meal_start_hours:g} horas** no es tardío; y
exactamente **{rules.minimum_meal_minutes:g} minutos** es un meal válido.

### Cómo se evitan falsos positivos

El status `On break` no se usa como única evidencia. Un gap real de
{rules.minimum_meal_minutes:g} minutos o más con status `Undefined`, `On time` o
similar se reconoce como **meal probable**, se muestra también como inconsistencia
de punch y requiere validación. Los status `On paid break` y `Paid break` también
quedan como meal probable sujeto a revisión, pero no se etiquetan por sí solos
como Punch Error. Un evento de break de cero horas nunca confirma un meal.

### Punch / Clock Errors

Estos casos se analizan por separado y no aumentan el KPI de Meal Violations:

- Clock In y Clock Out instantáneos al inicio del turno.
- Doble punch rápido seguido por otro Clock In.
- Fragmento inicial de hasta {EARLY_FRAGMENT_MAX_MINUTES:g} min, gap de
  {SUSPICIOUS_GAP_MIN_MINUTES:g}–{SUSPICIOUS_GAP_MAX_MINUTES:g} min y después un
  bloque largo de trabajo.
- Clock Out/In que parece meal por tiempo, pero no tiene status de break.

Un gap posterior a un punch instantáneo o claramente sospechoso **no se cuenta
como meal**. Si puede cambiar el resultado, el turno queda como Punch Error /
Revisión hasta corregir MICROS. Una posible violación puede coexistir con un Punch
Error cuando las reglas objetivas de duración o tiempo siguen fallando.

### Casos que requieren revisión

- Meal visible en timestamps con un status diferente de `On break`.
- Punch inicial instantáneo, doble punch o fragmento temprano ambiguo.
- Evento con status de break de cero horas.
- Clock Out y siguiente Clock In a la misma hora.
- Último registro con status de break sin Clock In de regreso.
- Diferencia mayor de 0.05 horas entre timestamps y horas reportadas.

Los casos de revisión se muestran por separado y **no aumentan el total de
violaciones**.
        """
    )


if hasattr(st, "dialog"):

    @st.dialog("Cómo se detectan los Meal Violations", width="large")
    def show_detection_rules() -> None:
        render_detection_rules()

else:

    def show_detection_rules() -> None:
        with st.expander(
            "Cómo se detectan los Meal Violations",
            expanded=True,
        ):
            render_detection_rules()


def main() -> None:
    st.set_page_config(
        page_title="Meal Violations Dashboard",
        page_icon="🍳",
        layout="wide",
    )

    st.markdown(
        """
        <style>
        .stApp { background-color: #f4f6f9; }
        .block-container { padding-top: 2rem; }
        .brand-header {
            display: flex; align-items: center; justify-content: center;
            gap: 14px; margin-bottom: 0.25rem;
        }
        .brand-header h1 { color: #343a40; margin: 0; }
        .brand-subtitle { text-align: center; color: #6c757d; }
        div[data-testid="stMetric"] {
            background: white; padding: 20px; border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .stButton > button, .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] button {
            background-color: #009efb; color: white;
            border: none; border-radius: 8px; font-weight: bold;
        }
        .stButton > button:hover, .stDownloadButton > button:hover,
        [data-testid="stFormSubmitButton"] button:hover {
            background-color: #007acc; color: white;
        }
        </style>
        <div class="brand-header">
            <img src="https://images.getbento.com/accounts/84a0d88fde80e86c78e3c3b842c4ecf8/media/images/19880THE-BY-logonew-FIXED.png"
                 width="80" alt="The Broken Yolk Cafe">
            <h1>Meal Violations Dashboard</h1>
        </div>
        <p class="brand-subtitle">Broken Yolk - By Jordan Memije</p>
        <hr>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Los meals se detectan por el intervalo real entre Clock Out y el siguiente "
        "Clock In. El status se usa como evidencia secundaria."
    )

    if "uploader_version" not in st.session_state:
        st.session_state.uploader_version = 0

    if st.session_state.get("app_version") != APP_VERSION:
        invalidate_analysis()
        st.session_state.app_version = APP_VERSION

    st.sidebar.title("Menú Principal")
    st.sidebar.caption("Broken Yolk · Meal Compliance")

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
            except Exception as error:
                technical_details = traceback.format_exc()
                invalidate_analysis()
                st.error(
                    "No pude analizar el archivo. "
                    f"{type(error).__name__}: {error}"
                )
                with st.expander("Detalles técnicos del error"):
                    st.code(technical_details, language="text")

    result = st.session_state.get("analysis_result")
    if result is not None and not hasattr(result, "location"):
        invalidate_analysis()
        st.info("La aplicación fue actualizada. Presiona Analizar para regenerar resultados.")
        return
    if result is None:
        if uploaded is None:
            st.info("Sube un archivo y presiona Analizar.")
        else:
            st.info("Archivo listo. Presiona Analizar en el panel izquierdo.")
        return

    st.subheader(f"📍 Location: {result.location}")

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

    st.markdown("## 📈 Resumen General")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Violaciones", total_violations)
    col2.metric("Empleados afectados", affected_employees)
    col3.metric("Punch / Clock Errors", result.stats["punch_errors"])
    col4.metric("Turnos por revisar", result.stats["review_periods"])
    col5.metric("Turnos analizados", result.stats["work_periods"])

    if total_violations:
        st.error(f"Se detectaron {total_violations} posibles violaciones.")
    else:
        st.success("No se detectaron violaciones automáticas con las reglas seleccionadas.")

    if result.stats["punch_errors"]:
        st.warning(
            f"Se detectaron {result.stats['punch_errors']} Punch / Clock Errors. "
            "Se muestran por separado y no se cuentan automáticamente como Meal "
            "Violations; deben corregirse o confirmarse en MICROS."
        )

    if result.stats["review_periods"]:
        st.warning(
            "Los casos de revisión no se cuentan como violaciones hasta que una persona "
            "confirme la marcación."
        )

    tab_violations, tab_punch_errors, tab_reviews, tab_shifts = st.tabs(
        [
            "Violaciones",
            "Punch / Clock Errors",
            "Requiere revisión",
            "Turnos procesados",
        ]
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

    with tab_punch_errors:
        render_punch_errors_table(result, "main")

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
                "Versión de la app": APP_VERSION,
                "Location": result.location,
                "Fila de encabezado detectada": result.header_row_excel,
                "Marcaciones válidas": result.stats["punch_rows"],
                "Empleados": result.stats["employees"],
                "Turnos sujetos a regla": result.stats["eligible_periods"],
                "Meals confirmados": result.stats["confirmed_meals"],
                "Meals probables por timestamps": result.stats["probable_meals"],
                "Punch / Clock Errors": result.stats["punch_errors"],
                "Turnos con Punch Errors": result.stats["punch_error_periods"],
                "Punches instantáneos": result.stats["instant_punch_errors"],
                "Fragmentos tempranos": result.stats["early_fragment_errors"],
                "Eventos On break de cero horas": result.stats["zero_break_reviews"],
                "Eventos On break con gap de cero minutos": result.stats[
                    "zero_gap_reviews"
                ],
                "Filas con ajustes": result.stats["adjusted_rows"],
            }
        )


def classic_main() -> None:
    """Interfaz original de Broken Yolk con el motor corregido."""
    st.set_page_config(
        page_title="Meal Violations Dashboard",
        page_icon="🍳",
        layout="wide",
    )

    if st.session_state.get("app_version") != APP_VERSION:
        invalidate_analysis()
        st.session_state["app_version"] = APP_VERSION

    st.sidebar.title("Menú Principal")
    menu = st.sidebar.radio("Navegación", ("Dashboard", "Configuración"))
    if st.sidebar.button(
        "📘 Cómo se detectan las violaciones",
        key="open_detection_rules",
        use_container_width=True,
    ):
        show_detection_rules()

    st.markdown(
        """
        <style>
        body { background-color: #f4f6f9; }
        header, footer { visibility: hidden; }
        .block-container { padding-top: 2rem; }
        .metric-card {
            background: white; padding: 20px; border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1); text-align: center;
        }
        .punch-error-card { border-top: 4px solid #f59e0b; }
        .card-title { font-size: 18px; color: #6c757d; margin-bottom: 0.5rem; }
        .card-value { font-size: 30px; font-weight: bold; color: #343a40; }
        .stButton > button, .stDownloadButton > button {
            background-color: #009efb; color: white; padding: 0.75rem 1.5rem;
            border: none; border-radius: 8px; font-weight: bold; cursor: pointer;
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            background-color: #007acc; color: white;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if menu == "Configuración":
        st.markdown("# ⚙️ Configuración")
        st.info("Opciones de configuración próximamente disponibles.")
        return

    st.markdown(
        """
        <h1 style='text-align: center; color: #343a40;'>
            <img src='https://images.getbento.com/accounts/84a0d88fde80e86c78e3c3b842c4ecf8/media/images/19880THE-BY-logonew-FIXED.png'
                 width='80' alt='The Broken Yolk Cafe'>
            Meal Violations Dashboard
        </h1>
        <p style='text-align: center; color: #6c757d;'>Broken Yolk - By Jordan Memije</p>
        <hr style='margin-top: 0px;'>
        """,
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "📤 Sube tu archivo Excel de Time Card Detail",
        type=["xlsx"],
        key="timecard_upload_classic",
        on_change=invalidate_analysis,
    )
    if uploaded is None:
        st.info("📤 Por favor sube un archivo Excel para comenzar.")
        return

    try:
        file_bytes = uploaded.getvalue()
    except Exception as error:
        st.error(f"No pude leer el archivo cargado. {type(error).__name__}: {error}")
        return

    if len(file_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
        st.error(f"El archivo supera el límite de {MAX_FILE_SIZE_MB} MB.")
        return

    rules = Rules()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    analysis_key = (APP_VERSION, file_hash, rules.cache_key())
    progress_bar = None
    fresh_analysis = False

    try:
        if (
            st.session_state.get("analysis_key") != analysis_key
            or st.session_state.get("analysis_payload") is None
        ):
            progress_bar = st.progress(0, text="Iniciando análisis...")
            time.sleep(0.2)
            progress_bar.progress(0.3, text="Leyendo y limpiando datos...")
            analyzed = analyze_time_card(file_bytes, rules)
            st.session_state["analysis_payload"] = result_to_payload(analyzed)
            st.session_state["analysis_key"] = analysis_key
            fresh_analysis = True
        result = result_from_payload(st.session_state["analysis_payload"])
    except DataValidationError as error:
        if progress_bar is not None:
            progress_bar.empty()
        invalidate_analysis()
        st.error(str(error))
        return
    except Exception as error:
        technical_details = traceback.format_exc()
        if progress_bar is not None:
            progress_bar.empty()
        invalidate_analysis()
        st.error(f"No pude analizar el archivo. {type(error).__name__}: {error}")
        with st.expander("Detalles técnicos del error"):
            st.code(technical_details, language="text")
        return

    if progress_bar is not None:
        progress_bar.progress(1.0, text="Listo ✅")
        progress_bar.empty()
    if fresh_analysis:
        st.balloons()
    st.success("✅ Análisis completado.")
    st.info(f"📍 Location del reporte: {result.location}")

    for warning in result.warnings:
        st.warning(warning)

    total_violations = len(result.violations)
    unique_employees = count_affected_employees(result.violations)
    dates_analyzed = result.violations["Date"].nunique() if total_violations else 0

    st.markdown("## 📈 Resumen General")
    metric_values = (
        ("Violaciones Detectadas", total_violations, ""),
        ("Empleados con Violaciones", unique_employees, ""),
        ("Días con Violaciones", dates_analyzed, ""),
        ("Punch / Clock Errors", result.stats["punch_errors"], "punch-error-card"),
    )
    for column, (title, value, card_class) in zip(st.columns(4), metric_values):
        with column:
            st.markdown(
                f"""
                <div class="metric-card {card_class}">
                    <div class="card-title">{title}</div>
                    <div class="card-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if result.stats["punch_errors"]:
        st.warning(
            f"Se detectaron {result.stats['punch_errors']} Punch / Clock Errors. "
            "Son anomalías separadas de las Meal Violations automáticas y requieren "
            "confirmación o corrección en MICROS."
        )

    st.markdown("---")
    st.markdown("## 🕒 Punch / Clock Errors")
    render_punch_errors_table(result, "classic")
    if not result.punch_errors.empty:
        st.download_button(
            label="⬇️ Descargar Punch / Clock Errors",
            data=safe_csv_bytes(result.punch_errors),
            file_name=f"punch_errors_{filename_slug(result.location)}.csv",
            mime="text/csv",
            key="classic_download_punch_errors",
        )

    st.markdown("---")
    st.markdown("## 📋 Detalle de Violaciones")
    if result.violations.empty:
        st.info(
            "No se detectaron violaciones automáticas con las reglas actuales. "
            "Revisa Punch / Clock Errors y los casos pendientes antes de cerrar el periodo."
        )
    else:
        violations = result.violations.sort_values(["Nombre", "Date", "Turno"])
        st.dataframe(violations, use_container_width=True, hide_index=True)

        violation_counts = violations["Nombre"].value_counts().reset_index()
        violation_counts.columns = ["Empleado", "Número de Violaciones"]

        st.markdown("## 📊 Violaciones por Empleado")
        col_graph, col_table = st.columns([2, 1])
        with col_graph:
            if plt is not None:
                figure_height = max(6, min(18, len(violation_counts) * 0.4))
                fig, ax = plt.subplots(figsize=(10, figure_height))
                ax.barh(
                    violation_counts["Empleado"],
                    violation_counts["Número de Violaciones"],
                )
                ax.set_xlabel("Número de Violaciones")
                ax.set_ylabel("Empleado")
                ax.set_title("Violaciones por Empleado", fontsize=14)
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.bar_chart(
                    violation_counts.set_index("Empleado")["Número de Violaciones"]
                )
        with col_table:
            st.dataframe(violation_counts, use_container_width=True, hide_index=True)

        high_violators = violation_counts[
            violation_counts["Número de Violaciones"] > 10
        ]
        if not high_violators.empty:
            st.error("🚨 Atención: Hay empleados con más de 10 violaciones detectadas!")
            st.dataframe(high_violators, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### 🔎 Explorar por empleado")
        employees = ["(Todos)"] + sorted(
            violations["Nombre"].dropna().astype(str).unique().tolist()
        )
        selected = st.selectbox(
            "Empleado",
            employees,
            key=f"classic_employee_{file_hash[:12]}",
        )
        if selected != "(Todos)":
            st.dataframe(
                violations[violations["Nombre"].astype(str) == selected].sort_values(
                    "Date"
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            label="⬇️ Descargar resultados en CSV",
            data=safe_csv_bytes(violations),
            file_name=f"meal_violations_{filename_slug(result.location)}.csv",
            mime="text/csv",
            key="classic_download_violations",
        )

    if not result.reviews.empty:
        st.markdown("---")
        st.markdown("## ⚠️ Casos que requieren revisión")
        st.caption(
            "Estos casos no se cuentan como violaciones hasta confirmar la marcación."
        )
        st.dataframe(result.reviews, use_container_width=True, hide_index=True)
        st.download_button(
            label="⬇️ Descargar casos para revisión",
            data=safe_csv_bytes(result.reviews),
            file_name=f"meal_reviews_{filename_slug(result.location)}.csv",
            mime="text/csv",
            key="classic_download_reviews",
        )

    with st.expander("Ver turnos procesados y detalles técnicos"):
        st.dataframe(result.shifts, use_container_width=True, hide_index=True)
        st.download_button(
            label="⬇️ Descargar turnos procesados",
            data=safe_csv_bytes(result.shifts),
            file_name=f"processed_shifts_{filename_slug(result.location)}.csv",
            mime="text/csv",
            key="classic_download_shifts",
        )
        st.write(
            {
                "Versión de la app": APP_VERSION,
                "Location": result.location,
                "Fila de encabezado": result.header_row_excel,
                "Marcaciones válidas": result.stats["punch_rows"],
                "Turnos analizados": result.stats["work_periods"],
                "Punch / Clock Errors": result.stats["punch_errors"],
                "Turnos con Punch Errors": result.stats["punch_error_periods"],
                "Meals confirmados": result.stats["confirmed_meals"],
                "Meals probables": result.stats["probable_meals"],
            }
        )


if __name__ == "__main__":
    classic_main()
