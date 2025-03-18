import streamlit as st
import pandas as pd
import pdfplumber
from datetime import datetime, timedelta
import re

# Función para extraer datos del PDF
def extract_data_from_pdf(pdf_path):
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines = text.split('\n')
                employee_name = None
                for line in lines:
                    match = re.search(r"^([A-Z]+, [A-Z]+)$", line.strip())
                    if match:
                        employee_name = match.group(1)
                    match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z]+)\s+IN\s+([\d:apm]+).*?OUT.*?([\d:apm]+).*?(\d+\.\d+)", line)
                    if match and employee_name:
                        date = match.group(1)
                        in_time = match.group(3)
                        out_time = match.group(4)
                        hours_worked = float(match.group(5))
                        records.append([employee_name, date, in_time, out_time, hours_worked])
    return pd.DataFrame(records, columns=["Empleado", "Fecha", "Hora Entrada", "Hora Salida", "Horas Trabajadas"])

# Función para verificar infracciones de descanso
def check_lunch_break_violations(df):
    violations = []
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    df["Hora Entrada"] = pd.to_datetime(df["Hora Entrada"], format="%I:%M%p").dt.time
    df["Hora Salida"] = pd.to_datetime(df["Hora Salida"], format="%I:%M%p").dt.time
    
    grouped = df.groupby(["Empleado", "Fecha"])
    
    for (empleado, fecha), group in grouped:
        group = group.sort_values(by=["Hora Entrada"])  # Ordenar por hora de entrada
        total_hours = group["Horas Trabajadas"].sum()
        breaks = []
        
        for _, row in group.iterrows():
            in_time = datetime.combine(datetime.today(), row["Hora Entrada"])
            out_time = datetime.combine(datetime.today(), row["Hora Salida"])
            breaks.append((in_time, out_time))
        
        # Revisar total de descanso
        total_break_time = sum([(out - inp).seconds / 60 for inp, out in breaks])
        
        if total_hours > 6 and total_break_time < 30:
            violations.append([empleado, fecha, "Infracción: No descansó 30 minutos en total"])
        
        # Revisar descanso antes de la quinta hora
        first_shift_start = datetime.combine(datetime.today(), group.iloc[0]["Hora Entrada"])
        fifth_hour_limit = first_shift_start + timedelta(hours=5)
        
        had_break = any(inp <= fifth_hour_limit <= out for inp, out in breaks)
        if total_hours > 6 and not had_break:
            violations.append([empleado, fecha, "Infracción: No descansó 30 minutos antes de la quinta hora"])
    
    return pd.DataFrame(violations, columns=["Empleado", "Fecha", "Infracción"])

# Interfaz en Streamlit
st.title("Análisis de Infracciones de Descanso de Comida")

uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])

if uploaded_file:
    with st.spinner("Procesando el archivo..."):
        data = extract_data_from_pdf(uploaded_file)
        if not data.empty:
            violations_df = check_lunch_break_violations(data)
            if not violations_df.empty:
                st.write("### Infracciones encontradas:")
                st.dataframe(violations_df)
            else:
                st.success("No se encontraron infracciones de descanso de comida.")
        else:
            st.error("No se pudo extraer información del PDF.")
