import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
from collections import defaultdict

def extract_employee_data(pdf_path):
    """Extrae la información de los empleados del PDF, incluyendo horas trabajadas y descansos de manera detallada."""
    employee_data = defaultdict(lambda: defaultdict(list))  # Diccionario para almacenar registros de cada empleado por fecha
    violations = []
    
    with fitz.open(pdf_path) as doc:
        text = "\n".join([page.get_text("text") for page in doc])
        text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
        
        # Extraer Employee # y nombres
        matches = re.findall(r"(\b\d{3,10}\b)\s*-\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)", text)
        
        for emp_num, name in matches:
            if not re.search(r"\b(Job|Server|Cook|Cashier|Runner|Manager|Prep|Sanitation|Bussers|Food)\b", name, re.IGNORECASE):
                
                # Extraer las fechas y los registros de tiempo trabajados por día
                emp_section = re.findall(rf"{emp_num} - {name}(.*?)(?=\n\d{{3,10}} - |$)", text, re.DOTALL)
                
                if emp_section:
                    emp_text = emp_section[0]
                    work_matches = re.findall(r"(\d{1,2}/\d{1,2}/\d{4})\s.*?(\d+\.\d+)", emp_text)
                    
                    for date, hours in work_matches:
                        hours = float(hours)
                        took_break = bool(re.search(rf"{date}.*?On Break", emp_text))  # Verificar si hay un descanso ese día
                        
                        employee_data[(emp_num, name)][date].append((hours, took_break))  # Guardar horas y si tomó descanso
    
    # Evaluar Meal Violations
    detailed_data = []
    for (emp_num, name), work_days in employee_data.items():
        for date, records in work_days.items():
            total_hours = sum([h for h, _ in records])  # Sumar todas las horas trabajadas en el día
            took_break = any(break_flag for _, break_flag in records)  # Verificar si hubo algún descanso en el día
            
            # Verificar si el descanso ocurrió antes de la 5ta hora
            cumulative_hours = 0
            break_before_fifth_hour = False
            for h, break_flag in records:
                cumulative_hours += h
                if break_flag and cumulative_hours < 5:
                    break_before_fifth_hour = True
                    break
            
            if total_hours > 6 and not took_break:
                violation_type = "Condición A: No tomó ningún descanso"
            elif total_hours > 6 and not break_before_fifth_hour:
                violation_type = "Condición B: No tomó descanso antes de la 5ª hora"
            else:
                violation_type = "Sin Violación"
            
            detailed_data.append([emp_num, name, date, total_hours, violation_type])
    
    return detailed_data

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
            st.write("### Registros de Empleados con Meal Violations y Sin Violaciones:")
            st.dataframe(df)
        else:
            st.write("No se encontraron registros en el archivo.")
        
if __name__ == "__main__":
    main()
