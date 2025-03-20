import streamlit as st
import pandas as pd

# Función para cargar y analizar el archivo
def load_and_analyze_data(uploaded_file):
    if uploaded_file is not None:
        df = pd.read_excel(uploaded_file, sheet_name='Reports', skiprows=8)
        
        # Renombrar columnas
        df.columns = ["Name", "Payroll ID", "Clock In Date and Time", "Clock Out Date and Time",
                      "Clock Out Status", "Adjustment Count", "Regular Hours", "Regular Pay",
                      "Overtime Hours", "Overtime Pay", "Gross Sales", "Tips"]
        
        # Eliminar filas innecesarias
        df = df.dropna(subset=["Name"])
        df = df[df["Name"] != "Total"]
        
        # Convertir tipos de datos
        df["Clock In Date and Time"] = pd.to_datetime(df["Clock In Date and Time"], errors='coerce')
        df["Clock Out Date and Time"] = pd.to_datetime(df["Clock Out Date and Time"], errors='coerce')
        df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
        
        # Filtrar Meal Violations
        meal_violations = df[(df["Clock Out Status"] == "On break") & (df["Regular Hours"] > 5)]
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
