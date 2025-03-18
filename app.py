import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# Función para extraer datos de empleados y tiempos
def extract_time_records(pdf_text):
    employee_pattern = re.compile(r"(?P<employee_id>\d{4,7}) - (?P<employee_name>[A-Za-z\s-']+)")
    time_pattern = re.compile(
        r"(\d+ - [A-Za-z\s-']+)\s+IN\s+(\w{3})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)\s+(On Time|Early|Late)"
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

# Aplicación en Streamlit
st.title("Meal Violation Detector")

uploaded_file = st.file_uploader("Sube un archivo PDF de registros de tiempo", type=["pdf"])

if uploaded_file:
    with pdfplumber.open(uploaded_file) as pdf:
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        df_records = extract_time_records(pdf_text)

    if df_records.empty:
        st.warning("No se encontraron registros en el PDF. Verifica el formato del archivo.")
    else:
        st.subheader("Registros de Tiempo Extraídos")
        st.dataframe(df_records)

        # Convertir horas a formato datetime para mejor análisis
        df_records["clock_in_time"] = pd.to_datetime(df_records["clock_in"], format="%I:%M%p")

        # Identificar Meal Violations (ejemplo: empleados que marcaron entrada después de las 5 PM)
        violations = df_records[df_records["clock_in_time"].dt.hour >= 17]
        violations["violation"] = True

        if violations.empty:
            st.success("No se detectaron Meal Violations.")
        else:
            st.subheader("Meal Violations Detectadas")
            st.dataframe(violations)
