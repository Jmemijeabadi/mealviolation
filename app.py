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
    """Detecta violaciones de comida si el tiempo de 'On Break' es mayor a 5 horas o si no hay descanso."""
    df["Break Duration"] = (df["Clock Out"] - df["Clock In"]).dt.total_seconds() / 3600
    
    # Detectar violaciones cuando el 'On Break' es mayor a 5 horas
    df_break_violations = df[(df["Clock Out Status"] == "On Break") & (df["Break Duration"] >= 5)].copy()
    df_break_violations["Violation Type"] = "Break Over 5 Hours"
    df_break_violations = df_break_violations[["Correct Employee Name", "Work Date", "Break Duration", "Violation Type"]]
    df_break_violations = df_break_violations.rename(columns={"Correct Employee Name": "Employee Name", "Work Date": "Date", "Break Duration": "Total_Hours_Worked"})
    
    # Detectar empleados que no tomaron ningún descanso
    df_no_breaks = df.groupby(["Correct Employee Name", "Work Date"]).agg(
        Total_Hours_Worked=("Clock In", lambda x: (x.max() - x.min()).total_seconds() / 3600),
        Break_Taken=("Clock Out Status", lambda x: (x == "On Break").any())
    ).reset_index()
    
    df_no_breaks = df_no_breaks[(df_no_breaks["Total_Hours_Worked"] > 6) & (df_no_breaks["Break_Taken"] == False)]
    df_no_breaks["Violation Type"] = "No Break Taken"
    df_no_breaks = df_no_breaks.rename(columns={"Correct Employee Name": "Employee Name", "Work Date": "Date"})
    df_no_breaks = df_no_breaks[["Employee Name", "Date", "Total_Hours_Worked", "Violation Type"]]
    
    # Unir ambas violaciones
    df_violations = pd.concat([df_break_violations, df_no_breaks], ignore_index=True)
    
    return df_violations

# Streamlit UI
st.title("Meal Violations Analyzer")

uploaded_file = st.file_uploader("Upload Excel File", type=["xls", "xlsx"])

if uploaded_file:
    df = load_excel(uploaded_file)
    results = detect_meal_violations(df)
    st.write("### Meal Violations Detected")
    st.dataframe(results)
