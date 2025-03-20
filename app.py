import streamlit as st
import pdfplumber
import json
import pandas as pd
from io import StringIO

def extract_text_from_pdf(pdf_file):
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def parse_employee_data(text):
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
                date = parts[-1]
                job = parts[-3]
                clock_in = parts[1]
                clock_out = parts[3]
                total_hours = float(parts[-2])
                record = {
                    "date": date,
                    "job": job,
                    "clock_in": clock_in,
                    "clock_out": clock_out,
                    "total_hours": total_hours
                }
                if current_employee:
                    current_employee["records"].append(record)
    
    if current_employee:
        employees.append(current_employee)
    
    return employees

st.title("Gestión de Registros de Empleados")

uploaded_file = st.file_uploader("Sube un archivo PDF", type="pdf")

if uploaded_file is not None:
    text = extract_text_from_pdf(uploaded_file)
    employees = parse_employee_data(text)
    
    st.write("### Registros Extraídos")
    df = pd.DataFrame([
        {
            "ID": emp["id"],
            "Nombre": emp["name"],
            "Total Registros": len(emp["records"])
        } for emp in employees
    ])
    st.dataframe(df)

    st.write("### Detalles de los empleados")
    for emp in employees:
        st.write(f"**{emp['name']} (ID: {emp['id']})**")
        emp_df = pd.DataFrame(emp["records"])
        st.dataframe(emp_df)
    
    json_data = json.dumps({"employees": employees}, indent=4)
    st.download_button("Descargar JSON", json_data, "empleados.json", "application/json")
