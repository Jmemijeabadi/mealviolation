import streamlit as st
import pdfplumber
import pandas as pd
import re

# Función para extraer datos de empleados y tiempos

def extract_time_records(pdf_text):
    employee_pattern = re.compile(r"(?P<employee_id>\d{4,7}) - (?P<employee_name>[A-Z\s-]+)")
    time_pattern = re.compile(
        r"(\d+ - [A-Z\s-]+)\s+IN\s+(\w{3})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)\s+(On Time|Early)"
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
        pdf_text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
        df_records = extract_time_records(pdf_text)
    
    st.subheader("Registros de Tiempo Extraídos")
    st.dataframe(df_records)

    # Identificar Meal Violations
    violations = df_records.groupby("employee_id").filter(
        lambda x: (x["clock_in"].apply(lambda t: int(t.split(":")[0]) >= 5).any())
    )
    violations["violation"] = True

    st.subheader("Meal Violations Detectadas")
    st.dataframe(violations)
