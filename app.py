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

# Función para convertir horas a formato 24h
def convert_to_24h(time_str):
    try:
        time_match = re.search(r"(\d{1,2}):(\d{2})([ap]m)", time_str)
        if time_match:
            hour, minute, period = int(time_match.group(1)), int(time_match.group(2)), time_match.group(3)
            if period == "pm" and hour != 12:
                hour += 12  # Convertir PM a formato 24h
            elif period == "am" and hour == 12:
                hour = 0  # Convertir 12 AM a 0 horas
            return hour + minute / 60  # Convertir a decimal para cálculos
    except:
        return None  # Retornar None en caso de error

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

        # Convertir las horas a formato 24h para análisis
        df_records["clock_in_24h"] = df_records["clock_in"].apply(convert_to_24h)

        # Agrupar por empleado para analizar tiempos de descanso
        violations = []
        for employee_id, group in df_records.groupby("employee_id"):
            group = group.sort_values(by="clock_in_24h")  # Ordenar por hora de entrada
            
            first_entry = group.iloc[0]  # Primera entrada del día
            on_break_records = group[group["status"] == "On Break"]  # Filtrar solo los breaks
            
            # Revisar si algún break ocurrió después de la quinta hora de trabajo
            for _, break_record in on_break_records.iterrows():
                if break_record["clock_in_24h"] - first_entry["clock_in_24h"] > 5:
                    violations.append(break_record)

        violations_df = pd.DataFrame(violations)

        st.subheader("Meal Violations Detectadas")
        if violations_df.empty:
            st.success("No se encontraron Meal Violations.")
        else:
            st.dataframe(violations_df)
