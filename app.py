import streamlit as st
import pdfplumber
import pandas as pd
import re

# Función para extraer datos de empleados y tiempos
def extract_time_records(pdf_text):
    employee_pattern = re.compile(r"(?P<employee_id>\d{4,7}) - (?P<employee_name>[A-Z\s-]+)")
    time_pattern = re.compile(
        r"(\d+ - [A-Z\s-]+)\s+IN\s+(\w{3})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)\s+(On Time|Early|On Break)"
    )

    employees = {}
    time_records = []
    current_employee_id = None
    current_employee_name = None

    for line in pdf_text.split("\n"):
        emp_match = employee_pattern.search(line)
        if emp_match:
            current_employee_id = emp_match.group("employee_id")
            current_employee_name = emp_match.group("employee_name")
        elif "IN " in line and current_employee_id:
            time_match = time_pattern.search(line)
            if time_match:
                time_records.append({
                    "employee_id": current_employee_id,
                    "employee_name": current_employee_name,
                    "job": time_match.group(1),
                    "day": time_match.group(2),
                    "date": time_match.group(3),
                    "clock_in": time_match.group(4),
                    "status": time_match.group(5)
                })
    return pd.DataFrame(time_records)

# Función para convertir horas a formato 24h y verificar Meal Violations
def hour_exceeds_limit(time_str, limit=5):
    try:
        time_match = re.search(r"(\d{1,2}):(\d{2})([ap]m)", time_str)
        if time_match:
            hour, minute, period = int(time_match.group(1)), int(time_match.group(2)), time_match.group(3)
            if period == "pm" and hour != 12:
                hour += 12  # Convertir PM a formato 24h
            elif period == "am" and hour == 12:
                hour = 0  # Convertir 12 AM a 0 horas
            return hour >= limit
    except:
        return False  # Evitar errores si el formato no es el esperado

# Aplicación en Streamlit
st.title("Meal Violation Detector")

uploaded_file = st.file_uploader("Sube un archivo PDF de registros de tiempo", type=["pdf"])

if uploaded_file:
    with pdfplumber.open(uploaded_file) as pdf:
        pdf_text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text() is not None])
        df_records = extract_time_records(pdf_text)
    
    if df_records.empty:
        st.warning("No se encontraron registros en el archivo PDF.")
    else:
        st.subheader("Registros de Tiempo Extraídos")
        st.dataframe(df_records)

        # Filtrar solo registros con status válido (excluir "On Break")
        valid_records = df_records[df_records["status"].isin(["On Time", "Early"])]

        # Identificar Meal Violations
        violations = valid_records[valid_records["clock_in"].apply(hour_exceeds_limit)]
        violations["violation"] = True

        st.subheader("Meal Violations Detectadas")
        if violations.empty:
            st.success("No se encontraron Meal Violations.")
        else:
            st.dataframe(violations)
