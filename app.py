import streamlit as st
import pdfplumber
import json
import pandas as pd
from collections import defaultdict
import re

def extract_text_from_pdf(pdf_file):
    """Extrae texto de un archivo PDF."""
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def parse_employee_data(text):
    """Parsea el texto del PDF para extraer información de empleados con formato correcto."""
    employees = []
    lines = text.split("\n")
    
    current_employee = None
    employee_period = None
    records_by_date = defaultdict(list)

    for line in lines:
        # Detecta el inicio de un nuevo empleado
        match = re.match(r"(\d{3,}) - (.+)", line)
        if match:
            if current_employee:
                current_employee["records"] = [
                    {
                        "date": date,
                        "job": records[0]["job"],
                        "clock_in": records[0]["clock_in"],
                        "clock_out": records[-1]["clock_out"],
                        "breaks": [
                            {"start": r["clock_in"], "duration": r["duration"]}
                            for r in records if r["status"] == "On Break"
                        ],
                        "total_hours": sum(r["total_hours"] for r in records if r["status"] != "On Break"),
                    }
                    for date, records in records_by_date.items()
                ]
                current_employee["total_hours_worked"] = sum(r["total_hours"] for r in current_employee["records"])
                current_employee["overtime_hours"] = 0.0  # Suponiendo sin horas extras
                employees.append(current_employee)
            
            employee_id = match.group(1).strip()
            employee_name = match.group(2).strip()
            current_employee = {
                "id": int(employee_id),
                "name": employee_name,
                "period": employee_period,
                "records": []
            }
            records_by_date.clear()

        # Detecta el periodo de pago
        if "Period From" in line:
            date_match = re.findall(r"(\d{2}/\d{2}/\d{4})", line)
            if date_match and len(date_match) == 2:
                employee_period = {"from": date_match[0], "to": date_match[1]}

        # Extrae información de entrada/salida y breaks
        parts = line.split()
        if len(parts) >= 6 and "IN" in parts[0] and "OUT" in parts[3]:
            try:
                date = parts[-1]
                job = parts[-3]
                clock_in = parts[1]
                clock_out = parts[3]
                status = parts[2]
                
                try:
                    total_hours = float(parts[-2])
                except ValueError:
                    total_hours = 0.0  # Si hay un error (ej. "FORGOT"), asignar 0.0
                
                record = {
                    "date": date,
                    "job": job,
                    "clock_in": clock_in,
                    "clock_out": clock_out,
                    "status": status,
                    "total_hours": total_hours,
                    "duration": total_hours if status == "On Break" else 0.0
                }
                
                records_by_date[date].append(record)
            except Exception as e:
                st.error(f"Error al procesar la línea: {line}\nDetalles: {str(e)}")

    if current_employee:
        current_employee["records"] = [
            {
                "date": date,
                "job": records[0]["job"],
                "clock_in": records[0]["clock_in"],
                "clock_out": records[-1]["clock_out"],
                "breaks": [
                    {"start": r["clock_in"], "duration": r["duration"]}
                    for r in records if r["status"] == "On Break"
                ],
                "total_hours": sum(r["total_hours"] for r in records if r["status"] != "On Break"),
            }
            for date, records in records_by_date.items()
        ]
        current_employee["total_hours_worked"] = sum(r["total_hours"] for r in current_employee["records"])
        current_employee["overtime_hours"] = 0.0
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
            "Total Registros": len(emp["records"]),
            "Horas Totales": emp["total_hours_worked"],
            "Horas Extra": emp["overtime_hours"]
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
