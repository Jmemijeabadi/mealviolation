import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
from collections import defaultdict

def extract_employee_data(pdf_path):
    """Extrae la información de los empleados del PDF, incluyendo horas trabajadas y descansos."""
    employee_data = defaultdict(lambda: defaultdict(float))  # Diccionario anidado para acumular horas trabajadas por día
    violations = []
    
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
            
            # Extraer Employee # y nombres
            matches = re.findall(r"(\b\d{3,10}\b)\s*-\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)", text)
            
            for emp_num, name in matches:
                if not re.search(r"\b(Job|Server|Cook|Cashier|Runner|Manager|Prep|Sanitation|Bussers|Food)\b", name, re.IGNORECASE):
                    
                    # Extraer las fechas y los registros de tiempo trabajados por día
                    work_matches = re.findall(r"(\d{1,2}/\d{1,2}/\d{4}).*?(\d+\.\d+)", text)
                    
                    for date, hours in work_matches:
                        hours = float(hours)
                        employee_data[(emp_num, name)][date] += hours  # Sumar horas trabajadas en la misma fecha

    # Evaluar Meal Violations
    for (emp_num, name), work_days in employee_data.items():
        for date, total_hours in work_days.items():
            took_break = "Break" in text  # Verificar si hay algún descanso en el texto
            break_before_fifth_hour = "Break" in text[:text.find(date)]  # Solo hasta la fecha detectada
            
            if total_hours > 6 and not took_break:
                violation_type = "Condición A: No tomó ningún descanso"
            elif total_hours > 6 and not break_before_fifth_hour:
                violation_type = "Condición B: No tomó descanso antes de la 5ª hora"
            else:
                violation_type = "Sin Violación"
            
            violations.append([emp_num, name, date, total_hours, violation_type])
    
    return violations

def main():
    st.title("PDF Employee Meal Violation Checker")
    st.write("Sube un archivo PDF para verificar Meal Violations.")
    
    uploaded_file = st.file_uploader("Sube un archivo PDF", type=["pdf"])
    
    if uploaded_file is not None:
        st.write("Procesando el archivo...")
        pdf_path = f"temp_{uploaded_file.name}"  # Guardamos temporalmente
        
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        employee_data = extract_employee_data(pdf_path)
        
        if employee_data:
            df = pd.DataFrame(employee_data, columns=["Employee #", "Nombre", "Fecha", "Horas Trabajadas", "Violación"])
            st.write("### Meal Violations Detectadas:")
            st.dataframe(df)
        else:
            st.write("No se encontraron violaciones de comida en el archivo.")
        
if __name__ == "__main__":
    main()
