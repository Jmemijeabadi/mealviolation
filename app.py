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
    lines = text.split("\n")
    records = []       # Para guardar registros IN/OUT
    break_records = [] # Para guardar registros "On Break"
    current_employee = None

    for i, line in enumerate(lines):
        # Detectar empleado (e.g., "1234 - Nombre")
        emp_match = re.match(r"(\d{4,5}) - ([A-Za-z ]+)", line)
        if emp_match:
            current_employee = emp_match.groups()  # (empleado, nombre)

        # Si hay un empleado en contexto y la línea tiene "IN"
        if current_employee and "IN" in line:
            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
            time_in_match = re.search(r"(\d{1,2}:\d{2}[ap]m)", line)
            if date_match and time_in_match:
                date = date_match.group(1)
                time_in = time_in_match.group(1)

                # Buscar la salida (OUT) en las siguientes líneas
                # Ajusta el rango si tu PDF requiere más o menos líneas
                for j in range(i + 1, min(i + 8, len(lines))):
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

        # Si hay un empleado en contexto y la línea contiene "On Break"
        if current_employee and "On Break" in line:
            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
            time_break_match = re.search(r"(\d{1,2}:\d{2}[ap]m)", line)
            if date_match and time_break_match:
                date_break = date_match.group(1)
                time_break = time_break_match.group(1)

                break_records.append({
                    "Employee #": current_employee[0],
                    "Nombre": current_employee[1],
                    "Fecha": date_break,
                    "Hora Break": time_break
                })

    # DataFrame con las jornadas (IN/OUT)
    df_main = pd.DataFrame(records)
    # DataFrame con los breaks
    df_breaks = pd.DataFrame(break_records)
    return df_main, df_breaks

# Función para evaluar violaciones de comida
def check_meal_violation(df_main, df_breaks):
    df_main["Meal Violation"] = "No"
    fmt = "%I:%M%p"

    for index, row in df_main.iterrows():
        if row["Horas trabajadas"] > 6:
            entrada = datetime.strptime(row["Hora Entrada"], fmt)
            quinta_hora = entrada + timedelta(hours=5)

            # Filtrar los breaks del mismo empleado y fecha
            same_day_breaks = df_breaks[
                (df_breaks["Employee #"] == row["Employee #"]) &
                (df_breaks["Fecha"] == row["Fecha"])
            ]

            # Verificar si existe al menos un break antes de la quinta hora
            descanso_en_quinta = False
            for _, br in same_day_breaks.iterrows():
                br_time = datetime.strptime(br["Hora Break"], fmt)
                if entrada < br_time < quinta_hora:
                    descanso_en_quinta = True
                    break

            # Si no hay break en ese intervalo, es violación
            if not descanso_en_quinta:
                df_main.at[index, "Meal Violation"] = "Sí"
    
    return df_main

# Interfaz de Streamlit
st.title("Análisis de Violaciones de Comida (Meal Violations)")

uploaded_file = st.file_uploader("Sube el archivo PDF de asistencia", type=["pdf"])

if uploaded_file is not None:
    text = extract_text_from_pdf(uploaded_file)
    # Obtenemos los DataFrames de jornadas (df_main) y breaks (df_breaks)
    df_main, df_breaks = process_data(text)
    
    # Calculamos violaciones de comida
    df_result = check_meal_violation(df_main, df_breaks)

    # Filtramos únicamente las violaciones
    df_violations = df_result[df_result["Meal Violation"] == "Sí"]

    st.write("### Resultados (Solo con Violaciones de Comida)")
    st.dataframe(df_violations)
