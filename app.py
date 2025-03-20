import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
from collections import defaultdict

def extract_employee_data(pdf_path):
    """Extrae la información de los empleados del PDF, asegurando un cálculo preciso de las horas trabajadas y Meal Violations."""
    employee_data = defaultdict(lambda: defaultdict(list))  # Diccionario para almacenar registros de cada empleado por fecha
    violations = []
    
    with fitz.open(pdf_path) as doc:
        text = "\n".join([page.get_text("text") for page in doc])
        text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
        
        # Extraer Employee # y nombres
        matches = re.findall(r"(\b\d{3,10}\b)\s*-\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)", text)
        
        for emp_num, name in matches:
            if not re.search(r"\b(Job|Server|Cook|Cashier|Runner|Manager|Prep|Sanitation|Bussers|Food)\b", name, re.IGNORECASE):
                
                # Extraer registros de tiempo trabajados por día
                emp_section = re.findall(rf"{emp_num} - {name}(.*?)(?=\n\d{{3,10}} - |$)", text, re.DOTALL)
                
                if emp_section:
                    emp_text = emp_section[0]
                    work_matches = re.findall(r"(\w{3})\s+(IN|OUT)\s+.*?(\d{1,2}:\d{2}[ap]m)\s+(\d{1,2}/\d{1,2}/\d{4})", emp_text)
                    
                    daily_records = defaultdict(list)
                    for day, record_type, time, date in work_matches:
                        time = pd.to_datetime(time, format="%I:%M%p")  # Convertir a formato de 24 horas
                        daily_records[date].append((record_type, time))
                    
                    for date, records in daily_records.items():
                        records.sort(key=lambda x: x[1])  # Ordenar por hora
                        total_hours = 0
                        clock_in_time = None
                        took_break = False
                        break_before_fifth_hour = False
                        
                        cumulative_hours = 0
                        
                        for record_type, time in records:
                            if record_type == "IN":
                                clock_in_time = time
                            elif record_type == "OUT" and clock_in_time:
                                worked_hours = (time - clock_in_time).total_seconds() / 3600
                                total_hours += worked_hours
                                cumulative_hours += worked_hours
                                clock_in_time = None  # Reiniciar para la siguiente entrada y salida
                                
                                # Verificar si se tomó un descanso antes de la quinta hora
                                if "On Break" in emp_text and cumulative_hours < 5:
                                    took_break = True
                                    break_before_fifth_hour = True
                        
                        total_hours = round(total_hours, 2)
                        
                        if total_hours > 6 and not took_break:
                            violation_type = "Condición A: No tomó ningún descanso"
                        elif total_hours > 6 and not break_before_fifth_hour:
                            violation_type = "Condición B: No tomó descanso antes de la 5ª hora"
                        else:
                            violation_type = "Sin Violación"
                        
                        employee_data[(emp_num, name)][date] = (total_hours, violation_type)
    
    detailed_data = []
    for (emp_num, name), work_days in employee_data.items():
        for date, (total_hours, violation) in work_days.items():
            detailed_data.append([emp_num, name, date, total_hours, violation])
    
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
