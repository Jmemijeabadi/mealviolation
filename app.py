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
    
    for line in records:
        employee_match = re.match(r'(\d{3,4}) - ([A-Z ]+)', line)
        if employee_match:
            current_employee = employee_match.group(2).strip()
            employee_data[current_employee] = []
            continue
        
        time_match = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)', line)
        if current_employee and time_match:
            employee_data[current_employee].append([(date, time) for date, time in time_match])
    
    return employee_data

def detect_meal_violations(employee_data):
    """Detecta violaciones de descanso según las reglas establecidas."""
    violations = []
    
    for employee, records in employee_data.items():
        for shifts in records:
            if len(shifts) >= 2:
                clock_in = datetime.strptime(shifts[0][0] + ' ' + shifts[0][1], "%m/%d/%Y %I:%M%p")
                clock_out = datetime.strptime(shifts[-1][0] + ' ' + shifts[-1][1], "%m/%d/%Y %I:%M%p")
                work_duration = (clock_out - clock_in).total_seconds() / 3600
                
                if work_duration > 6:
                    took_break = False
                    for i in range(1, len(shifts)):
                        break_time = datetime.strptime(shifts[i][0] + ' ' + shifts[i][1], "%m/%d/%Y %I:%M%p")
                        break_duration = (break_time - clock_in).total_seconds() / 3600
                        if 0.5 <= (clock_out - break_time).total_seconds() / 3600 and break_duration <= 5:
                            took_break = True
                            break
                    
                    if not took_break:
                        violations.append((employee, clock_in.strftime("%Y-%m-%d %I:%M %p"), clock_out.strftime("%Y-%m-%d %I:%M %p"), round(work_duration, 2)))
    
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
