import streamlit as st
import pdfplumber
import pandas as pd
import re
from collections import defaultdict

def extract_text_from_pdf(pdf_file):
    """Extrae el texto del PDF usando pdfplumber."""
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def process_work_sessions(text):
    """Estructura la información de entrada, salida y descansos de cada empleado."""
    employees = defaultdict(lambda: defaultdict(list))
    current_employee = None

    lines = text.split("\n")
    for line in lines:
        # Identificar empleados
        emp_match = re.match(r"(\d{3,5}) - ([A-Z ]+)", line)
        if emp_match:
            emp_id, emp_name = emp_match.groups()
            current_employee = (emp_id, emp_name)

        # Identificar registros de entrada, salida y descanso
        work_match = re.search(r"(IN|OUT) (\w{3} \d{1,2}/\d{1,2}/\d{4}) (\d{1,2}:\d{2}[ap]m)", line)
        if work_match and current_employee:
            status, date, time = work_match.groups()
            employees[current_employee][date].append((status, time, "Break" in line))

    return employees

def detect_meal_violations_from_sessions(employees):
    """Detecta Meal Violations en los registros de entrada y salida."""
    violations = []

    for (emp_id, emp_name), work_data in employees.items():
        for date, records in work_data.items():
            records.sort(key=lambda x: pd.to_datetime(x[1], format="%I:%M%p"))  # Ordenar por hora

            in_time = None
            out_time = None
            break_times = []

            for status, time, is_break in records:
                if status == "IN":
                    in_time = pd.to_datetime(time, format="%I:%M%p")
                elif status == "OUT":
                    out_time = pd.to_datetime(time, format="%I:%M%p")
                if is_break:
                    break_times.append(pd.to_datetime(time, format="%I:%M%p"))

            if in_time and out_time:
                total_hours = (out_time - in_time).total_seconds() / 3600

                # Detectar Meal Violation si trabajó más de 6 horas y no tomó descanso antes de la 5ta hora
                if total_hours > 6:
                    took_break_early = any(break_time < (in_time + pd.Timedelta(hours=5)) for break_time in break_times)

                    if not took_break_early:
                        violations.append({
                            "Employee #": emp_id,
                            "Employee Name": emp_name,
                            "Date": date,
                            "Worked Hours": round(total_hours, 2),
                            "Violation Type": "Meal Violation (No Break before 5th hour)"
                        })

    return pd.DataFrame(violations)

def main():
    st.title("Meal Violation Checker")
    st.write("Sube un archivo PDF para analizar las violaciones de descanso.")
    
    uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])
    
    if uploaded_file is not None:
        st.write("Analizando el documento...")
        
        text = extract_text_from_pdf(uploaded_file)
        structured_sessions = process_work_sessions(text)
        violations_df = detect_meal_violations_from_sessions(structured_sessions)
        
        if not violations_df.empty:
            st.subheader("Meal Violations Detectadas")
            st.dataframe(violations_df)
        else:
            st.write("No se encontraron Meal Violations en el archivo proporcionado.")

if __name__ == "__main__":
    main()
