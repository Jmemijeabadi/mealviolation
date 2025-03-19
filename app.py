import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime, timedelta

# Función para extraer texto del PDF
def extract_text_from_pdf(uploaded_file):
    text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

# Función para procesar datos extraídos
def process_data(text):
    records = []
    lines = text.split("\n")
    current_employee = None
    
    for i, line in enumerate(lines):
        emp_match = re.match(r"(\d{4,5}) - ([A-Za-z ]+)", line)
        if emp_match:
            current_employee = emp_match.groups()

        if "IN" in line and current_employee:
            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
            time_in_match = re.search(r"(\d{1,2}:\d{2}[ap]m)", line)
            
            if date_match and time_in_match:
                date = date_match.group(1)
                time_in = time_in_match.group(1)

                for j in range(i + 1, min(i + 5, len(lines))):
                    if "OUT" in lines[j]:
                        time_out_match = re.search(r"(\d{1,2}:\d{2}[ap]m)", lines[j])
                        hours_match = re.search(r"OUT\s+\d{1,2}:\d{2}[ap]m\s+([\d\.]+)", lines[j])
                        
                        if time_out_match and hours_match:
                            time_out = time_out_match.group(1)
                            hours_worked = float(hours_match.group(1))

                            records.append({
                                "Employee #": current_employee[0],
                                "Nombre": current_employee[1],
                                "Fecha": date,
                                "Hora Entrada": time_in,
                                "Hora Salida": time_out,
                                "Horas trabajadas": hours_worked
                            })
                        break
    
    df = pd.DataFrame(records)
    return df

# Función para evaluar violaciones de comida
def check_meal_violation(df):
    df["Meal Violation"] = "No"
    fmt = "%I:%M%p"
    
    for index, row in df.iterrows():
        if row["Horas trabajadas"] > 6:
            entrada = datetime.strptime(row["Hora Entrada"], fmt)
            quinta_hora = entrada + timedelta(hours=5)
            
            descansos = df[(df["Employee #"] == row["Employee #"]) & (df["Fecha"] == row["Fecha"]) & (df["Horas trabajadas"] < 6)]
            
            descanso_tomado = any(
                (datetime.strptime(desc["Hora Entrada"], fmt) > entrada) and
                (datetime.strptime(desc["Hora Entrada"], fmt) < quinta_hora)
                for _, desc in descansos.iterrows()
            )
            
            if not descanso_tomado:
                df.at[index, "Meal Violation"] = "Sí"
    
    return df

# Streamlit UI
st.title("Análisis de Violaciones de Comida (Meal Violations)")

uploaded_file = st.file_uploader("Sube el archivo PDF de asistencia", type=["pdf"])

if uploaded_file is not None:
    text = extract_text_from_pdf(uploaded_file)
    df = process_data(text)
    df = check_meal_violation(df)
    
    # Filtrar solo las violaciones de comida
    df_violations = df[df["Meal Violation"] == "Sí"]
    
    st.write("### Resultados (Solo con Violaciones de Comida)")
    st.dataframe(df_violations)
