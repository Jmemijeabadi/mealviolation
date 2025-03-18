import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime

def extract_data_from_pdf(pdf_file):
    """Extrae los registros de tiempo de los empleados desde un archivo PDF."""
    records = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines = text.split('\n')
                records.extend(lines)
    return records

def parse_time_records(records):
    """Parses registros de tiempo para estructurar la información."""
    employee_data = {}
    current_employee = None
    current_date = None
    
    for line in records:
        line = line.strip()
        
        employee_match = re.match(r'(\d{3,4}) - ([A-Za-z ]+)', line)
        if employee_match:
            current_employee = employee_match.group(2).strip()
            employee_data[current_employee] = []
            continue
        
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
        if date_match:
            current_date = date_match.group(1)
        
        time_match = re.findall(r'(\d{1,2}:\d{2}[ap]m)', line)
        if current_employee and current_date and time_match:
            for time in time_match:
                employee_data[current_employee].append((current_date, time))
    
    return employee_data

def detect_meal_violations(employee_data):
    """Detecta violaciones de Meal Break."""
    violations = []
    
    for employee, records in employee_data.items():
        if len(records) >= 2:
            clock_in = datetime.strptime(records[0][0] + ' ' + records[0][1], "%m/%d/%Y %I:%M%p")
            clock_out = datetime.strptime(records[-1][0] + ' ' + records[-1][1], "%m/%d/%Y %I:%M%p")
            work_duration = (clock_out - clock_in).total_seconds() / 3600
            
            if work_duration > 6:
                took_break = False
                valid_break = False
                for i in range(1, len(records) - 1):
                    break_time = datetime.strptime(records[i][0] + ' ' + records[i][1], "%m/%d/%Y %I:%M%p")
                    break_duration = (break_time - clock_in).total_seconds() / 3600
                    
                    if break_duration <= 5:
                        took_break = True
                    if break_duration <= 5 and (clock_out - break_time).total_seconds() / 3600 >= 0.5:
                        valid_break = True
                        break
                
                if not valid_break:
                    violations.append((employee, clock_in.strftime("%Y-%m-%d %I:%M %p"), clock_out.strftime("%Y-%m-%d %I:%M %p"), round(work_duration, 2)))
    
    return pd.DataFrame(violations, columns=['Empleado', 'Hora de Entrada', 'Hora de Salida', 'Horas Trabajadas'])

# Interfaz en Streamlit
st.title('Detección de Meal Violations')
st.write("Sube un archivo PDF con registros de empleados para detectar violaciones de Meal Break.")

uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])

if uploaded_file:
    records = extract_data_from_pdf(uploaded_file)
    employee_data = parse_time_records(records)
    violations_df = detect_meal_violations(employee_data)
    
    st.write("### Resultados de Violaciones de Meal Break")
    if not violations_df.empty:
        st.dataframe(violations_df)
        
        csv = violations_df.to_csv(index=False).encode('utf-8')
        st.download_button("Descargar reporte en CSV", csv, "meal_violations.csv", "text/csv")
    else:
        st.write("No se detectaron violaciones de Meal Break.")
