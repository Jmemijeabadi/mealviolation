import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime, timedelta

# Función para extraer texto del PDF
def extract_text_from_pdf(pdf_file):
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

# Función para procesar los datos
def process_timecard_data(text):
    # Expresión regular para extraer los datos de entrada y salida
    pattern = re.compile(r"(\w{3})IN On Time\d+ - (\w+) -\$ (\d{1,2}:\d{2}[ap]m)(\d{1,2}/\d{1,2}/\d{4})\nOUT (\w+ \d*\.\d*)?(\d{1,2}:\d{2}[ap]m)?")

    records = []
    current_employee = None

    lines = text.split("\n")
    for line in lines:
        if re.match(r"\d{9} -", line):  # Detectar nuevo empleado
            current_employee = line.split("-")[1].strip()
        else:
            match = pattern.search(line)
            if match:
                day, job, clock_in, date, break_status, clock_out = match.groups()
                clock_in_time = datetime.strptime(f"{date} {clock_in}", "%m/%d/%Y %I:%M%p")
                if clock_out:
                    clock_out_time = datetime.strptime(f"{date} {clock_out}", "%m/%d/%Y %I:%M%p")
                    hours_worked = (clock_out_time - clock_in_time).total_seconds() / 3600
                else:
                    hours_worked = 0
                
                records.append({
                    "Employee": current_employee,
                    "Job": job,
                    "Date": date,
                    "Clock In": clock_in,
                    "Clock Out": clock_out if clock_out else "N/A",
                    "Hours Worked": round(hours_worked, 2),
                    "Break Taken": "Yes" if break_status and "On Break" in break_status else "No",
                })

    return pd.DataFrame(records)

# Función para detectar Meal Violations
def detect_meal_violations(df):
    violations = []
    for _, row in df.iterrows():
        if row["Hours Worked"] > 6 and row["Break Taken"] == "No":
            violations.append(row)

    return pd.DataFrame(violations)

# Interfaz en Streamlit
st.title("Meal Violation Detector")
st.write("Sube un archivo PDF con registros de tiempo para detectar violaciones de descanso.")

uploaded_file = st.file_uploader("Subir archivo PDF", type="pdf")

if uploaded_file:
    with st.spinner("Procesando..."):
        text = extract_text_from_pdf(uploaded_file)
        df = process_timecard_data(text)
        violations_df = detect_meal_violations(df)
    
    st.write("### Datos procesados")
    st.dataframe(df)

    if not violations_df.empty:
        st.write("### Meal Violations Detectadas")
        st.dataframe(violations_df)
    else:
        st.success("No se detectaron Meal Violations.")

