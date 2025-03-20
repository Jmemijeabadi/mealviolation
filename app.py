import streamlit as st
import pdfplumber
import json
import pandas as pd
from io import StringIO

def extract_text_from_pdf(pdf_file):
    """Extrae texto de un archivo PDF."""
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def parse_employee_data(text):
    """Parsea el texto del PDF para extraer la información de empleados."""
    employees = []
    lines = text.split("\n")
    
    current_employee = None
    for line in lines:
        if " - " in line and any(char.isdigit() for char in line):
            # Detecta el inicio de un nuevo empleado
            parts = line.split(" - ", 1)
            employee_id = parts[0].strip()
            employee_name = parts[1].strip()
            if current_employee:
                employees.append(current_employee)
            current_employee = {
                "id": employee_id,
                "name": employee_name,
                "records": []
            }
        elif "IN" in line and "OUT" in line:
            # Extrae información de registros de entrada/salida
            parts = line.split()
            if len(parts) >= 5:
                try:
                    date = parts[-1]
                    job = parts[-3]
                    clock_in = parts[1]
                    clock_out = parts[3]
                    
                    # Manejar errores en horas trabajadas
                    try:
                        total_hours = float(parts[-2])
                    except ValueError:
                        total_hours = 0.0  # Si hay un error (ej. "FORGOT"), asignar 0.0

                    record = {
                        "date": date,
                        "job": job,
                        "clock_in": clock_in,
                        "clock_out": clock_out,
                        "total_hours": total_hours
                    }
                    if current_employee:
                        current_employee["records"].append(record)
                except Exception as e:
                    st.error(f"Error al procesar la línea: {line}\nDetalles: {str(e)}")
    
    if current_employee:
        employees.append(current_employee)
    
    return employees

# Interfaz de Streamlit
st.title("📊 Gestión de Registros de Empleados")

# Cargar archivo PDF
uploaded_file = st.file_uploader("📂 Sube un archivo PDF", type="pdf")

if uploaded_file is not None:
    st.write("⏳ Procesando archivo...")

    # Extraer texto del PDF
    text = extract_text_from_pdf(uploaded_file)
    
    # Procesar empleados
    employees = parse_employee_data(text)
    
    # Convertir a formato JSON
    json_data = json.dumps({"employees": employees}, indent=4)

    # Mostrar DataFrame con resumen de empleados
    st.write("### 📋 Resumen de empleados")
    df = pd.DataFrame([
        {
            "ID": emp["id"],
            "Nombre": emp["name"],
            "Total Registros": len(emp["records"])
        } for emp in employees
    ])
    st.dataframe(df)

    # Mostrar detalles individuales de cada empleado
    st.write("### 📂 Detalles de los empleados")
    for emp in employees:
        with st.expander(f"📌 {emp['name']} (ID: {emp['id']})"):
            emp_df = pd.DataFrame(emp["records"])
            st.dataframe(emp_df)

    # Mostrar el JSON generado en un cuadro de texto
    st.write("### 📝 JSON Generado")
    st.code(json_data, language="json")

    # Botón de descarga del JSON
    st.download_button("⬇️ Descargar JSON", json_data, "empleados.json", "application/json")
