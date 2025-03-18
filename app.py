import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime, timedelta

# Función para extraer datos del PDF
def extract_data_from_pdf(pdf_path):
    data = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            lines = text.split('\n')
            
            employee_name = None
            for line in lines:
                # Detectar nombres de empleados
                if re.match(r'\d{4} -', line):
                    employee_name = line.split('-')[-1].strip()
                
                match = re.match(r'(\w{3})IN\s+(\w+).*?(\d{1,2}:\d{2}[ap]m)', line)
                if match and employee_name:
                    day, status, time = match.groups()
                    data.append({
                        'Employee': employee_name,
                        'Day': day,
                        'Status': status,
                        'Time': time
                    })
    return pd.DataFrame(data)

# Función para detectar Meal Violations
def detect_meal_violations(df):
    violations = []
    employee_groups = df.groupby('Employee')
    
    for employee, group in employee_groups:
        group = group.sort_values(by='Time')
        work_sessions = []
        
        in_time = None
        for index, row in group.iterrows():
            if 'IN' in row['Status']:
                in_time = datetime.strptime(row['Time'], "%I:%M%p")
            elif 'OUT' in row['Status'] and in_time:
                out_time = datetime.strptime(row['Time'], "%I:%M%p")
                work_duration = (out_time - in_time).seconds / 3600  # Convertir a horas
                
                if work_duration > 6:
                    # Verificar si hubo un break antes de la 5ta hora
                    break_taken = any(
                        in_time + timedelta(hours=5) >= datetime.strptime(break_row['Time'], "%I:%M%p")
                        for _, break_row in group.iterrows() if 'On Break' in break_row['Status']
                    )
                    if not break_taken:
                        violations.append({'Employee': employee, 'Violation': 'Meal Violation'})
                
                in_time = None  # Reiniciar el tiempo de entrada
    
    return pd.DataFrame(violations)

# Configuración de la aplicación Streamlit
st.title("Meal Violation Detector")

uploaded_file = st.file_uploader("Sube un archivo PDF con registros de tiempo", type=["pdf"])

if uploaded_file:
    st.write("Procesando el archivo...")
    df = extract_data_from_pdf(uploaded_file)
    violations_df = detect_meal_violations(df)
    
    if not violations_df.empty:
        st.write("## Empleados con violaciones de descanso")
        st.dataframe(violations_df)
    else:
        st.write("No se encontraron violaciones de descanso en los registros.")
