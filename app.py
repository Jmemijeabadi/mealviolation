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
            
            for line in lines:
                if "Payroll ID" in line:
                    parts = line.split()
                    emp_id = parts[2]
                    name = " ".join(parts[3:])
                
                if any(keyword in line for keyword in ["Clock In", "Clock Out"]):
                    parts = line.split()
                    date = parts[-1]
                    time = parts[-2]
                    status = "On Break" if "On Break" in line else "Work"
                    records.append([emp_id, name, date, time, status])
    
    return pd.DataFrame(records, columns=["Employee #", "Name", "Date", "Time", "Status"])

# Función para analizar violaciones de comida
def analyze_meal_violations(df):
    df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%Y')
    df['Time'] = pd.to_datetime(df['Time'], format='%I:%M%p').dt.time
    
    violations = []
    grouped = df.groupby(['Employee #', 'Name', 'Date'])
    
    for (emp_id, name, date), group in grouped:
        group = group.sort_values(by=['Time'])
        work_periods = []
        total_worked = timedelta()
        breaks = []
        first_punch = None
        
        for _, row in group.iterrows():
            if row['Status'] == 'On Break':
                breaks.append(row)
            else:
                if not first_punch:
                    first_punch = datetime.combine(date, row['Time'])
                start = datetime.combine(date, row['Time'])
                work_periods.append(start)
        
        if len(work_periods) > 1:
            total_worked = work_periods[-1] - work_periods[0]
            total_hours = total_worked.total_seconds() / 3600
            
            # Verificar Meal Violation 1
            if total_hours > 6 and not any((b['Time'].hour * 60 + b['Time'].minute) >= 30 for b in breaks):
                violations.append([emp_id, name, date.date(), total_hours, "No tomó un descanso de 30 minutos en total"])
            
            # Verificar Meal Violation 2
            fifth_hour_mark = first_punch + timedelta(hours=5)
            before_fifth_hour = [b for b in breaks if datetime.combine(date, b['Time']) <= fifth_hour_mark]
            if total_hours > 6 and not any((b['Time'].hour * 60 + b['Time'].minute) >= 30 for b in before_fifth_hour):
                violations.append([emp_id, name, date.date(), total_hours, "No tomó un descanso de 30 minutos antes de la quinta hora de trabajo"])
    
    return pd.DataFrame(violations, columns=["Employee #", "Name", "Date", "Hours Worked", "Meal Violation"])

# Interfaz de usuario con Streamlit
st.title("Análisis de Violaciones de Descanso Laboral")

uploaded_files = st.file_uploader("Sube los archivos PDF de registros de tiempo", accept_multiple_files=True, type=["pdf"])

if uploaded_files:
    all_data = pd.DataFrame()
    for uploaded_file in uploaded_files:
        df = extract_data_from_pdf(uploaded_file)
        all_data = pd.concat([all_data, df], ignore_index=True)
    
    if not all_data.empty:
        violations_df = analyze_meal_violations(all_data)
        
        if not violations_df.empty:
            st.subheader("Violaciones Detectadas")
            st.dataframe(violations_df)
        else:
            st.success("No se detectaron violaciones en los datos analizados.")
