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

def structure_employee_shifts(records):
    """Reestructura los registros de tiempo agrupando entrada, salida y descansos correctamente."""
    employee_shifts = {}
    current_employee = None
    current_date = None
    current_shift = []

    for line in records:
        line = line.strip()

        # Identificar nombre del empleado
        employee_match = re.match(r'(\d{3,4}) - ([A-Za-z ]+)', line)
        if employee_match:
            if current_employee and current_shift:
                employee_shifts[current_employee].append(current_shift)
            current_employee = employee_match.group(2).strip()
            employee_shifts.setdefault(current_employee, [])
            current_shift = []
            continue

        # Identificar fecha
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
        if date_match:
            current_date = date_match.group(1)

        # Identificar horarios de entrada y salida
        time_match = re.findall(r'(
\d{1,2}:\d{2}[ap]m)', line)
        if current_employee and current_date and time_match:
            for time in time_match:
                current_shift.append((current_date, time))

        # Si se encuentra "OUT" en la línea, finalizar el turno actual
        if 'OUT' in line and current_shift:
            employee_shifts[current_employee].append(current_shift)
            current_shift = []

    return employee_shifts

def detect_meal_violations(employee_shifts):
    """Detecta violaciones de Meal Break."""
    violations = []

    for employee, shifts in employee_shifts.items():
        for shift in shifts:
            if len(shift) >= 2:
                clock_in = datetime.strptime(shift[0][0] + ' ' + shift[0][1], "%m/%d/%Y %I:%M%p")
                clock_out = datetime.strptime(shift[-1][0] + ' ' + shift[-1][1], "%m/%d/%Y %I:%M%p")
                work_duration = (clock_out - clock_in).total_seconds() / 3600

                if work_duration > 6:
                    took_valid_break = False
                    for i in range(1, len(shift) - 1):
                        break_time = datetime.strptime(shift[i][0] + ' ' + shift[i][1], "%m/%d/%Y %I:%M%p")
                        break_duration = (break_time - clock_in).total_seconds() / 3600
                        rest_period = (clock_out - break_time).total_seconds() / 3600

                        if break_duration <= 5 and rest_period >= 0.5:
                            took_valid_break = True
                            break

                    if not took_valid_break:
                        violations.append((employee, clock_in.strftime("%Y-%m-%d %I:%M %p"), clock_out.strftime("%Y-%m-%d %I:%M %p"), round(work_duration, 2)))

    return pd.DataFrame(violations, columns=['Empleado', 'Hora de Entrada', 'Hora de Salida', 'Horas Trabajadas'])

# Interfaz en Streamlit
st.title('Detección de Meal Violations')
st.write("Sube un archivo PDF con registros de empleados para detectar violaciones de Meal Break.")

uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])

if uploaded_file:
    records = extract_data_from_pdf(uploaded_file)
    employee_shifts = structure_employee_shifts(records)
    violations_df = detect_meal_violations(employee_shifts)

    st.write("### Resultados de Violaciones de Meal Break")
    if not violations_df.empty:
        st.dataframe(violations_df)

        csv = violations_df.to_csv(index=False).encode('utf-8')
        st.download_button("Descargar reporte en CSV", csv, "meal_violations.csv", "text/csv")
    else:
        st.write("No se detectaron violaciones de Meal Break.")
