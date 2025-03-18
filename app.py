import streamlit as st
import pdfplumber
import pandas as pd
import re

def extract_text_from_pdf(uploaded_file):
    with pdfplumber.open(uploaded_file) as pdf:
        text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
    return text

def parse_time_logs_with_fixed_employee_names(text):
    employee_logs = []
    current_employee = None

    lines = text.split("\n")
    for line in lines:
        # Detectar cambio de empleado incluyendo IDs largos
        match_employee = re.match(r"(\d{4,5} - [A-Z ]+)", line)
        if match_employee:
            current_employee = match_employee.group(1).split(" - ", 1)[-1].strip()  # Extraer solo el nombre

        # Extraer registros de entrada y salida
        match_in = re.search(r"IN (\w{3}) (\d{1,2}/\d{1,2}/\d{4}) (\d{1,2}:\d{2}[ap]m) (.+)", line)
        match_out = re.search(r"OUT (\d{1,2}:\d{2}[ap]m) ([\d.]+) (.+)", line)

        if match_in and current_employee:
            day, date, time, status = match_in.groups()
            employee_logs.append([current_employee, date, time, "IN", status])

        if match_out and current_employee:
            time, hours, status = match_out.groups()
            employee_logs.append([current_employee, date, time, "OUT", status])

    return pd.DataFrame(employee_logs, columns=["Employee", "Date", "Time", "Direction", "Status"])

def detect_meal_violations(df):
    violations = []

    for employee, group in df.groupby("Employee"):
        group = group.sort_values(by="Datetime").reset_index(drop=True)
        shift_start = None
        shift_end = None
        breaks = []

        for i, row in group.iterrows():
            if row["Direction"] == "IN":
                if shift_start is None:
                    shift_start = row["Datetime"]  # Registrar inicio del turno
                shift_end = row["Datetime"]  # Actualizar fin de turno en cada entrada

            # Identificar descansos
            if "On Break" in row["Status"] or "Break" in row["Status"]:
                breaks.append(row["Datetime"])

        # Evaluar si el turno supera las 6 horas y si hubo un descanso adecuado
        if shift_start and shift_end:
            total_hours = (shift_end - shift_start).total_seconds() / 3600

            if total_hours > 6:
                # Verificar si tomó un descanso de al menos 30 minutos antes de la quinta hora
                took_break = any(
                    (b - shift_start).total_seconds() / 3600 < 5 and (b - shift_start).total_seconds() / 60 >= 30
                    for b in breaks
                )

                # Si no tomó un descanso válido o no tiene breaks registrados, marcar como violación
                if not took_break or not breaks:
                    violations.append(
                        [employee, shift_start.date(), shift_start.time(), shift_end.time(), "Meal Violation"]
                    )

    return pd.DataFrame(violations, columns=["Employee", "Date", "Shift Start", "Shift End", "Violation"])

st.title("Meal Violation Checker")

uploaded_file = st.file_uploader("Upload Employee Time Card PDF", type=["pdf"])

if uploaded_file is not None:
    text = extract_text_from_pdf(uploaded_file)
    df_logs = parse_time_logs_with_fixed_employee_names(text)
    df_logs["Datetime"] = pd.to_datetime(df_logs["Date"] + " " + df_logs["Time"])
    df_logs = df_logs.sort_values(by=["Employee", "Datetime"])
    df_violations = detect_meal_violations(df_logs)
    
    st.subheader("Detected Meal Violations")
    st.dataframe(df_violations)
