import streamlit as st
import pandas as pd
import pdfplumber
import re
from io import StringIO

def extract_data_from_pdf(pdf_file):
    """Extrae los registros de tiempo de los empleados desde un archivo PDF."""
    records = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines = text.split('\n')
                for line in lines:
                    match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)', line)
                    if match:
                        records.append(line)
    return records

def parse_time_records(records):
    """Parses registros de tiempo para estructurar la información."""
    employee_data = {}
    current_employee = None
    
    for line in records:
        if re.search(r'Employee Time Card And Job Detail', line):
            current_employee = None
        
        match = re.search(r'(\d{4}) - ([A-Z ]+)', line)
        if match:
            current_employee = match.group(2).strip()
            employee_data[current_employee] = []
            continue
        
        time_match = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)', line)
        if current_employee and time_match:
            employee_data[current_employee].append(time_match)
    
    return employee_data

def detect_meal_violations(employee_data):
    """Detecta violaciones de descanso según las reglas establecidas."""
    violations = []
    for employee, records in employee_data.items():
        for record in records:
            if len(record) >= 2:
                clock_in = pd.to_datetime(record[0][0] + ' ' + record[0][1])
                clock_out = pd.to_datetime(record[-1][0] + ' ' + record[-1][1])
                work_duration = (clock_out - clock_in).total_seconds() / 3600
                
                if work_duration > 6:
                    took_break = False
                    for i in range(1, len(record)):
                        break_time = pd.to_datetime(record[i][0] + ' ' + record[i][1])
                        if (break_time - clock_in).total_seconds() / 3600 <= 5:
                            took_break = True
                            break
                    
                    if not took_break:
                        violations.append((employee, clock_in, clock_out, work_duration))
    
    return pd.DataFrame(violations, columns=['Empleado', 'Hora de Entrada', 'Hora de Salida', 'Horas Trabajadas'])

# Interfaz en Streamlit
st.title('Detección de Meal Violations')
st.write("Sube un archivo PDF con registros de empleados para detectar violaciones de descanso.")

uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])

if uploaded_file:
    records = extract_data_from_pdf(uploaded_file)
    employee_data = parse_time_records(records)
    violations_df = detect_meal_violations(employee_data)
    
    st.write("### Resultados de Violaciones de Descanso")
    if not violations_df.empty:
        st.dataframe(violations_df)
        
        csv = violations_df.to_csv(index=False).encode('utf-8')
        st.download_button("Descargar reporte en CSV", csv, "meal_violations.csv", "text/csv")
    else:
        st.write("No se detectaron violaciones de Meal Break.")
