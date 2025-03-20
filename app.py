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
    """Detecta violaciones de comida basadas en las reglas especificadas."""
    df_grouped = df.groupby(["Correct Employee Name", "Work Date"]).agg(
        Total_Hours_Worked=("Clock In", lambda x: (x.max() - x.min()).total_seconds() / 3600),
        First_Clock_In=("Clock In", "min"),
        Break_Taken=("Clock Out Status", lambda x: (x == "On Break").any()),
        Break_Before_Fifth_Hour=("Clock In", lambda x: any(
            ((row - x.min()).total_seconds() / 3600 < 5) for row in x if row in df[df["Clock Out Status"] == "On Break"]["Clock In"].values
        ))
    ).reset_index()
    
    df_grouped["Violation Type"] = None
    df_grouped.loc[
        (df_grouped["Total_Hours_Worked"] > 6) & (df_grouped["Break_Before_Fifth_Hour"] == False), 
        "Violation Type"
    ] = "Break After 5th Hour"
    
    df_grouped.loc[
        (df_grouped["Total_Hours_Worked"] > 6) & (df_grouped["Break_Taken"] == False), 
        "Violation Type"
    ] = "No Break Taken"
    
    df_violations = df_grouped.dropna(subset=["Violation Type"])
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
