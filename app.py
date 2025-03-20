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
    """Detecta violaciones de comida si 'Regular Hours' es mayor a 5 y 'Clock Out Status' es 'On Break'."""
    
    # Asegurar que Regular Hours sea numérico
    df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
    
    # Filtrar solo los registros donde 'Clock Out Status' es 'On Break' y 'Regular Hours' > 5
    df_violations = df[(df["Clock Out Status"].str.lower() == "on break") & (df["Regular Hours"] > 5)].copy()
    
    # Calcular las horas totales trabajadas por día
    df_total_hours = df.groupby(["Correct Employee Name", "Work Date"]).agg(
        Total_Hours_Worked=("Clock In", lambda x: (x.max() - x.min()).total_seconds() / 3600)
    ).reset_index()
    
    # Fusionar con las violaciones
    df_violations = df_violations.merge(df_total_hours, on=["Correct Employee Name", "Work Date"], how="left")
    
    # Agregar la columna de Violation
    df_violations["Violation"] = "Yes"
    
    # Seleccionar las columnas requeridas
    df_violations = df_violations.rename(columns={"Correct Employee Name": "Employee Name", "Work Date": "Date"})
    df_violations = df_violations[["Employee Name", "Date", "Regular Hours", "Total_Hours_Worked", "Violation"]]
    
    return df_violations

# Streamlit UI
st.title("Meal Violations Analyzer")

uploaded_file = st.file_uploader("Upload Excel File", type=["xls", "xlsx"])

if uploaded_file:
    df = load_excel(uploaded_file)
    results = detect_meal_violations(df)
    st.write("### Meal Violations Detected")
    st.dataframe(results)
