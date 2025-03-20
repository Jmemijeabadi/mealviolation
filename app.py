import streamlit as st
import pandas as pd

# Función para cargar y analizar el archivo
def load_and_analyze_data(uploaded_file):
    if uploaded_file is not None:
        df = pd.read_excel(uploaded_file, sheet_name='Reports', skiprows=8)
        
        # Renombrar columnas
        df.columns = ["Raw Name", "Payroll ID", "Clock In Date and Time", "Clock Out Date and Time",
                      "Clock Out Status", "Adjustment Count", "Regular Hours", "Regular Pay",
                      "Overtime Hours", "Overtime Pay", "Gross Sales", "Tips"]
        
        # Crear una columna para almacenar el último nombre de empleado encontrado
        df["Employee Name"] = df["Raw Name"].where(df["Payroll ID"].notna()).ffill()
        
        # Filtrar registros de tiempo válidos asegurando que no se pierdan nombres
        df = df[df["Clock In Date and Time"].notna()]
        
        # Excluir filas donde el nombre sea un título de trabajo en lugar de un empleado
        df = df[df["Payroll ID"].notna()]
        
        # Convertir tipos de datos
        df["Clock In Date and Time"] = pd.to_datetime(df["Clock In Date and Time"], errors='coerce')
        df["Clock Out Date and Time"] = pd.to_datetime(df["Clock Out Date and Time"], errors='coerce')
        df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
        
        # Normalizar la columna "Clock Out Status" para evitar errores de formato
        df["Clock Out Status"] = df["Clock Out Status"].astype(str).str.strip().str.lower()
        
        # Calcular total de horas trabajadas por día
        df["Date"] = df["Clock In Date and Time"].dt.date
        total_hours_per_day = df.groupby(["Employee Name", "Date"])["Regular Hours"].sum().reset_index()
        total_hours_per_day.rename(columns={"Regular Hours": "Total Worked Hours in Day"}, inplace=True)
        
        # Filtrar Meal Violations asegurando que se excluyen registros incorrectos
        meal_violations = df[
            (df["Clock Out Status"] == "on break") &
            (df["Regular Hours"] > 5) &
            (df["Employee Name"].notna())
        ]
        
        # Unir los datos de horas totales trabajadas por día
        meal_violations = meal_violations.merge(total_hours_per_day, on=["Employee Name", "Date"], how="left")
        
        # Seleccionar columnas deseadas
        meal_violations = meal_violations[["Employee Name", "Regular Hours", "Total Worked Hours in Day", "Clock In Date and Time", "Clock Out Date and Time"]]
        
        return meal_violations
    return None

# Configuración de la aplicación Streamlit
st.title("Meal Violations Tracker")
st.write("Sube un archivo de Excel para analizar las Meal Violations de los empleados.")

# Cargar archivo
uploaded_file = st.file_uploader("Sube un archivo de Excel", type=["xls", "xlsx"])

if uploaded_file:
    meal_violations_df = load_and_analyze_data(uploaded_file)
    
    if meal_violations_df is not None and not meal_violations_df.empty:
        st.success(f"Se encontraron {len(meal_violations_df)} Meal Violations.")
        st.dataframe(meal_violations_df)
        
        # Opción para descargar los resultados
        csv = meal_violations_df.to_csv(index=False).encode('utf-8')
        st.download_button("Descargar resultados en CSV", data=csv, file_name="meal_violations.csv", mime="text/csv")
    else:
        st.warning("No se encontraron Meal Violations en el archivo cargado.")
