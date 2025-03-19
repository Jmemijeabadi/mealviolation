import streamlit as st
import pandas as pd
import PyPDF2
import re

# Función para extraer datos del PDF
def extract_data_from_pdf(pdf_file):
    reader = PyPDF2.PdfReader(pdf_file)
    text = "".join([page.extract_text() for page in reader.pages if page.extract_text()])
    return text

# Función para analizar las violaciones de comida
def analyze_meal_violations(text):
    pattern = re.compile(r"(\d{4}) - ([A-Z ]+)\n.*?(\d{1,2}/\d{1,2}/\d{4}).*?IN On Time.*?(\d{1,2}:\d{2}[ap]m).*?OUT Not Scheduled\s(\d+\.\d+)", re.DOTALL)
    violations = []
    
    for match in pattern.finditer(text):
        emp_id, name, date, start_time, hours_worked = match.groups()
        hours_worked = float(hours_worked)
        
        if hours_worked > 6:
            violations.append([emp_id, name.strip(), date, hours_worked, "Meal Violation (No Rest)"])
        
        # Verificar si tomó el descanso de 30 minutos antes de la quinta hora
        break_pattern = re.compile(rf"{date}.*?OUT On Break (\d+\.\d+).*(\d{1,2}:\d{2}[ap]m)")
        breaks = break_pattern.findall(text)
        
        for break_duration, break_time in breaks:
            if float(break_duration) < 0.5 and "Meal Violation (No 30-min Break)" not in violations:
                violations.append([emp_id, name.strip(), date, hours_worked, "Meal Violation (No 30-min Break)"])
                break
    
    return pd.DataFrame(violations, columns=["Employee #", "Name", "Date", "Worked Hours", "Violation Type"])

# Streamlit UI
st.title("Meal Violation Analyzer")

uploaded_file = st.file_uploader("Sube el archivo PDF", type=["pdf"])
if uploaded_file is not None:
    text = extract_data_from_pdf(uploaded_file)
    violations_df = analyze_meal_violations(text)
    
    if not violations_df.empty:
        st.subheader("Meal Violations")
        st.dataframe(violations_df)
    else:
        st.write("No se encontraron violaciones de comida.")
