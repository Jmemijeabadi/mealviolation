import streamlit as st
import pandas as pd
import PyPDF2
import re

# Función para extraer datos del PDF
def extract_data_from_pdf(pdf_path):
    with open(pdf_path, "rb") as file:
        reader = PyPDF2.PdfReader(file)
        text = "".join([page.extract_text() for page in reader.pages if page.extract_text()])
    return text

# Función para analizar los datos y encontrar violaciones
def analyze_meal_violations(text):
    pattern = re.compile(r"(\d{4}) - ([A-Z ]+)\n.*?(\d{1,2}/\d{1,2}/\d{4}).*?OUT Not Scheduled\s(\d+\.\d+)", re.DOTALL)
    violations = []
    
    for match in pattern.finditer(text):
        emp_id, name, date, hours = match.groups()
        hours = float(hours)
        if hours > 6:
            violations.append([emp_id, name.strip(), date, hours, "Meal Violation"])
    
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
