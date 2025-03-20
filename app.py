import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
import tempfile
from datetime import datetime

def extract_employee_data(pdf_path):
    """Extrae los registros de tiempo de los empleados desde el PDF."""
    employee_data = []
    
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
            
            # Expresión regular para extraer registros de tiempo (Ejemplo: 12345 - John Doe - 03/18/2024 08:00 AM In)
            matches = re.findall(r"(\b\d{3,10}\b)\s*-\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)\s*-\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}\s*[APM]+)\s*(In|Out|On Break)", text)
            
            for emp_num, name, date, time, status in matches:
                timestamp = datetime.strptime(f"{date} {time}", "%m/%d/%Y %I:%M %p")
                employee_data.append({
                    "Employee #": emp_num.strip(),
                    "Name": name.strip(),
                    "Date": date,
                    "Timestamp": timestamp,
                    "Status": status.strip()
                })
    
    return pd.DataFrame(employee_data)

def analyze_meal_violations(df):
    """Analiza los registros y determina las Meal Violations."""
    violations = []
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    
    grouped = df.groupby(["Employee #", "Name", "Date"])
    
    for (emp_num, name, date), group in grouped:
        group = group.sort_values(by="Timestamp")
        total_worked = (group[group["Status"] == "Out"]["Timestamp"].max() - 
                        group[group["Status"] == "In"]["Timestamp"].min()).total_seconds() / 3600.0
        
        took_break = any(group["Status"] == "On Break")
        first_break_time = group[group["Status"] == "On Break"]["Timestamp"].min() if took_break else None
        first_in_time = group[group["Status"] == "In"]["Timestamp"].min()
        
        violation = None
        if total_worked > 6 and not took_break:
            violation = "No break taken"
        elif total_worked > 6 and first_break_time and (first_break_time - first_in_time).total_seconds() / 3600.0 > 5:
            violation = "Break after 5th hour"
        
        if violation:
            violations.append({
                "Employee #": emp_num,
                "Name": name,
                "Date": date,
                "Total Hours Worked": round(total_worked, 2),
                "Violation": violation
            })
    
    return pd.DataFrame(violations)

def main():
    st.title("Meal Violation Analyzer")
    st.write("Sube un archivo PDF con los registros de tiempo de los empleados.")
    
    uploaded_file = st.file_uploader("Sube un archivo PDF", type=["pdf"])
    
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(uploaded_file.getbuffer())
            pdf_path = temp_file.name
        
        st.write("Procesando el archivo...")
        employee_df = extract_employee_data(pdf_path)
        violations_df = analyze_meal_violations(employee_df)
        
        if not violations_df.empty:
            st.write("### Meal Violations Detectadas:")
            st.dataframe(violations_df)
        else:
            st.write("No se encontraron Meal Violations en el archivo.")
        
if __name__ == "__main__":
    main()
