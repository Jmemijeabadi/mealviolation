import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

def load_excel(file):
    """Carga el archivo Excel y extrae la información relevante."""
    df = pd.read_excel(file, skiprows=8)
    df.columns = [
        "Employee Name", "Employee ID", "Clock In", "Clock Out", "Clock Out Status",
        "Adjustment Count", "Regular Hours", "Regular Pay", "Overtime Hours", 
        "Overtime Pay", "Gross Sales", "Tips"
    ]
    
    df = df.dropna(subset=["Clock In", "Clock Out"])
    df["Clock In"] = pd.to_datetime(df["Clock In"], errors='coerce')
    df["Clock Out"] = pd.to_datetime(df["Clock Out"], errors='coerce')
    df["Work Date"] = df["Clock In"].dt.date
    
    df_raw_name_column = pd.read_excel(file, sheet_name="Reports", usecols=[0], skiprows=8)
    df_raw_name_column.columns = ["Name"]
    df_raw_name_column["Is Employee Name"] = df_raw_name_column["Name"].str.contains(",", na=False)
    df_raw_name_column["Correct Employee Name"] = df_raw_name_column["Name"].where(df_raw_name_column["Is Employee Name"]).ffill()
    
    df["Correct Employee Name"] = df_raw_name_column["Correct Employee Name"]
    
    return df

def detect_meal_violations(df):
    """Detecta violaciones de comida si el empleado no registró 'On Break' o si 'Regular Hours' es mayor a 5."""
    
    # Convertir Regular Hours a valores numéricos
    df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
    
    # Agrupar por empleado y fecha
    df_summary = df.groupby(["Correct Employee Name", "Work Date"]).agg(
        Total_Hours_Worked=("Clock In", lambda x: (x.max() - x.min()).total_seconds() / 3600),
        Break_Taken=("Clock Out Status", lambda x: (x == "On Break").any()),
        Max_Regular_Hours=("Regular Hours", "max")
    ).reset_index()
    
    # Aplicar las reglas de Meal Violation
    df_summary["Violation Type"] = None
    
    # Regla 1: Trabajó más de 6 horas y no registró 'On Break'
    df_summary.loc[
        (df_summary["Total_Hours_Worked"] > 6) & (df_summary["Break_Taken"] == False), 
        "Violation Type"
    ] = "No Break Taken"
    
    # Regla 2: Trabajó más de 6 horas y el descanso fue mayor a 5 horas
    df_summary.loc[
        (df_summary["Total_Hours_Worked"] > 6) & (df_summary["Max_Regular_Hours"] > 5), 
        "Violation Type"
    ] = "Break Over 5 Hours"
    
    # Filtrar solo las violaciones detectadas
    df_violations = df_summary.dropna(subset=["Violation Type"])
    
    # Seleccionar las columnas requeridas para la salida final
    df_violations = df_violations.rename(columns={"Correct Employee Name": "Employee Name", "Work Date": "Date"})
    df_violations = df_violations[["Employee Name", "Date", "Total_Hours_Worked", "Violation Type"]]
    
    return df_violations

# Streamlit UI
st.title("Meal Violations Analyzer")

uploaded_file = st.file_uploader("Upload Excel File", type=["xls", "xlsx"])

if uploaded_file:
    df = load_excel(uploaded_file)
    results = detect_meal_violations(df)
    st.write("### Meal Violations Detected")
    st.dataframe(results)
