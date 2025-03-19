import streamlit as st
import pandas as pd
import pdfplumber
from datetime import datetime, timedelta

# Función para extraer datos de los PDFs
def extract_data_from_pdf(uploaded_file):
    records = []
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            lines = text.split("\n")
            emp_id, name = None, None
            # Buscar la línea que contenga la información del empleado
            for line in lines:
                if "Payroll ID" in line:
                    parts = line.split()
                    # Se asume que el formato es: "Payroll ID Employee # And Name"
                    try:
                        emp_id = parts[2]
                        name = " ".join(parts[3:])
                    except Exception:
                        pass
                # Extraer registros de “Clock In” o “Clock Out” (incluyendo "On Break")
                if ("Clock In" in line or "Clock Out" in line) and emp_id is not None:
                    parts = line.split()
                    # Se asume que la última parte es la fecha y la penúltima es la hora
                    date = parts[-1]
                    time = parts[-2]
                    status = "On Break" if "On Break" in line else "Work"
                    records.append([emp_id, name, date, time, status])
    return pd.DataFrame(records, columns=["Employee #", "Name", "Date", "Time", "Status"])

# Función para analizar las violaciones de descanso
def analyze_meal_violations(df):
    # Convertir la columna de fecha y tiempo a formatos adecuados
    df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    df['Time'] = pd.to_datetime(df['Time'], format='%I:%M%p', errors='coerce').dt.time
    
    violations = []
    # Agrupar por empleado y por día
    grouped = df.groupby(['Employee #', 'Name', 'Date'])
    for (emp_id, name, date), group in grouped:
        # Ordenar por hora
        group = group.sort_values(by=['Time'])
        if group.empty or pd.isnull(date):
            continue
        # Usamos la primera y la última marca para calcular el lapso de trabajo
        first_time = datetime.combine(date, group.iloc[0]['Time'])
        last_time = datetime.combine(date, group.iloc[-1]['Time'])
        total_worked = last_time - first_time
        total_hours = total_worked.total_seconds() / 3600
        
        # Filtrar los registros de descanso
        breaks = group[group['Status'] == "On Break"]
        
        # Criterio 1: Si trabajó más de 6 horas y NO hay registro de descanso en el día
        if total_hours > 6 and breaks.empty:
            violations.append([
                emp_id,
                name,
                date.date(),
                round(total_hours, 2),
                "No tomó un descanso de 30 minutos en total"
            ])
        
        # Criterio 2: Si trabajó más de 6 horas y no hay registro de descanso antes de que se cumplan 5 horas
        fifth_hour = first_time + timedelta(hours=5)
        breaks_before_fifth = breaks[breaks['Time'].apply(lambda t: datetime.combine(date, t) <= fifth_hour)]
        if total_hours > 6 and breaks_before_fifth.empty:
            violations.append([
                emp_id,
                name,
                date.date(),
                round(total_hours, 2),
                "No tomó un descanso de 30 minutos antes de la quinta hora de trabajo"
            ])
    
    return pd.DataFrame(violations, columns=["Employee #", "Name", "Date", "Hours Worked", "Meal Violation"])

# Interfaz de usuario con Streamlit
st.title("Análisis de Violaciones de Descanso Laboral")

uploaded_files = st.file_uploader("Sube los archivos PDF de registros de tiempo", accept_multiple_files=True, type=["pdf"])

if uploaded_files:
    all_data = pd.DataFrame()
    for uploaded_file in uploaded_files:
        df = extract_data_from_pdf(uploaded_file)
        all_data = pd.concat([all_data, df], ignore_index=True)
    
    st.subheader("Datos Extraídos")
    st.dataframe(all_data)
    
    if not all_data.empty:
        violations_df = analyze_meal_violations(all_data)
        st.subheader("Violaciones Detectadas")
        if not violations_df.empty:
            st.dataframe(violations_df)
        else:
            st.success("No se detectaron violaciones en los datos analizados.")
