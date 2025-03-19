import streamlit as st
import pdfplumber
import pandas as pd
import re

def extract_text_from_pdf(pdf_file):
    """Extrae el texto del PDF usando pdfplumber."""
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

def parse_employee_data(text):
    """Extrae la información de entrada, salida y descansos de cada empleado."""
    records = []
    employee_id = None
    employee_name = None
    
    lines = text.split("\n")
    for line in lines:
        # Detectar el número y nombre del empleado
        emp_match = re.match(r"(\d{3,5}) - ([A-Z ]+)", line)
        if emp_match:
            employee_id, employee_name = emp_match.groups()
        
        # Detectar registros de entrada, salida y descansos
        work_match = re.search(r"(IN|OUT) (\w{3} \d{1,2}/\d{1,2}/\d{4}) (\d{1,2}:\d{2}[ap]m)(.*Break.*)?", line)
        if work_match and employee_id and employee_name:
            status, date, time, break_flag = work_match.groups()
            is_break = bool(break_flag)
            records.append({
                "Employee #": employee_id,
                "Employee Name": employee_name,
                "Date": date,
                "Status": status,
                "Time": time,
                "Is Break": is_break
            })
    return pd.DataFrame(records)

def detect_meal_violations(df):
    """Detecta empleados que trabajaron más de 6 horas sin descanso antes de la 5ta hora."""
    violations = []
    grouped = df.groupby(["Employee #", "Employee Name", "Date"])
    
    for (emp_id, emp_name, date), group in grouped:
        group = group.sort_values(by="Time")
        in_time = None
        out_time = None
        breaks = []
        total_hours = 0
        
        for _, row in group.iterrows():
            time = pd.to_datetime(row["Time"], format="%I:%M%p")
            if row["Status"] == "IN" and not in_time:
                in_time = time
            elif row["Status"] == "OUT":
                out_time = time
                total_hours = (out_time - in_time).total_seconds() / 3600 if in_time else 0
            if row["Is Break"]:
                breaks.append(time)
        
        if total_hours > 6:
            took_break_early = any(break_time < (in_time + pd.Timedelta(hours=5)) for break_time in breaks)
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
        st.text_area("Vista previa del texto extraído", text[:5000])  # Muestra los primeros 5000 caracteres
        
        employee_df = parse_employee_data(text)
        st.subheader("Registros Extraídos")
        st.dataframe(employee_df)
        
        violations_df = detect_meal_violations(employee_df)
        
        if not violations_df.empty:
            st.subheader("Meal Violations Detectadas")
            st.dataframe(violations_df)
        else:
            st.write("No se encontraron Meal Violations en el archivo proporcionado.")
            st.write("### Posibles causas del problema:")
            st.write("- Los datos de entrada/salida no están siendo extraídos correctamente.")
            st.write("- Los descansos no están siendo identificados adecuadamente.")
            st.write("- El formato del PDF es diferente y necesita ajustes en la extracción.")

if __name__ == "__main__":
    main()
