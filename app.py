import streamlit as st
import fitz  # PyMuPDF
import pandas as pd
import re
from datetime import datetime
from io import BytesIO

st.title("📊 Meal Violation Analyzer")
st.write("Sube un archivo PDF con los registros de tiempo para analizar violaciones de meal break.")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    st.success("📁 Archivo subido correctamente")
    
    # Leer PDF directamente desde memoria
    try:
        doc = fitz.open(stream=BytesIO(uploaded_file.read()), filetype="pdf")
        lines = []
        for page in doc:
            lines.extend(page.get_text("text").split("\n"))

        st.write("✅ **Texto extraído correctamente. Ahora procesando horarios...**")

        # Función para extraer horarios de los empleados
        def extract_shifts(lines):
            records = []
            current_employee_id = None
            current_employee = None
            entry_time = None
            entry_date_str = None

            for i in range(len(lines) - 4):
                line = lines[i].strip()

                # Detectar el número de empleado y el nombre
                employee_match = re.match(r"(\d{6,}) - (.+)", line)
                if employee_match:
                    current_employee_id = employee_match.group(1).strip()
                    current_employee = employee_match.group(2).strip()
                    continue

                # Detectar entradas ("IN On Time")
                if line == "IN" and "On Time" in lines[i + 1] and current_employee_id is not None:
                    try:
                        entry_time_str = lines[i + 2].strip()
                        if not re.search(r"\d{1,2}:\d{2}[ap]m", entry_time_str):
                            entry_time_str = lines[i + 3].strip()
                        entry_date_str = lines[i + 4].strip()
                        entry_time = datetime.strptime(f"{entry_date_str} {entry_time_str}", "%m/%d/%Y %I:%M%p")
                    except:
                        entry_time = None  

                # Detectar salidas ("OUT On Time" o "OUT On Break") asociadas a una entrada previa
                if "OUT" in line and entry_time and current_employee_id is not None:
                    try:
                        exit_time_str = lines[i + 2].strip()
                        if not re.search(r"\d{1,2}:\d{2}[ap]m", exit_time_str):
                            exit_time_str = lines[i + 3].strip()
                        exit_time = datetime.strptime(f"{entry_date_str} {exit_time_str}", "%m/%d/%Y %I:%M%p")

                        # Calcular horas trabajadas
                        hours_worked = (exit_time - entry_time).total_seconds() / 3600

                        # Agregar registro con Employee # y Empleado correctos
                        records.append({
                            "Employee #": current_employee_id,
                            "Empleado": current_employee,
                            "Fecha": entry_date_str,
                            "Entrada": entry_time.strftime("%I:%M %p"),
                            "Salida": exit_time.strftime("%I:%M %p") + (" (Break)" if "Break" in line else ""),
                            "Horas Trabajadas": round(hours_worked, 2)
                        })

                        # Reiniciar valores después de registrar un turno
                        entry_time = None
                        entry_date_str = None
                    except:
                        continue

            return records

        # Función mejorada para detectar meal violations considerando los breaks dentro del mismo día
        def check_meal_violations(shifts):
            """Identifica violaciones de meal break asegurando que el break fue antes de las 5 horas."""
            violations = []

            # Convertimos los datos en un DataFrame para un análisis más fácil
            shifts_df = pd.DataFrame(shifts)
            
            # Convertimos los tiempos de entrada y salida en datetime para cálculos
            shifts_df["Entrada"] = pd.to_datetime(shifts_df["Fecha"] + " " + shifts_df["Entrada"], format="%m/%d/%Y %I:%M %p")
            shifts_df["Salida"] = pd.to_datetime(shifts_df["Fecha"] + " " + shifts_df["Salida"].str.replace(" (Break)", "", regex=True), format="%m/%d/%Y %I:%M %p")

            # Iteramos por cada empleado y fecha
            for (employee_id, fecha), group in shifts_df.groupby(["Employee #", "Fecha"]):
                group = group.sort_values(by="Entrada")  # Ordenamos por entrada
                
                # Identificamos la primera entrada y la última salida del turno
                first_entry = group.iloc[0]["Entrada"]
                last_exit = group.iloc[-1]["Salida"]
                total_hours = (last_exit - first_entry).total_seconds() / 3600  # Total de horas trabajadas
                
                took_break = False

                # Revisamos si hubo un "OUT On Break" antes de las 5 horas
                for _, row in group.iterrows():
                    salida_str = str(row["Salida"])  # Convertimos Salida a string para evitar el error
                    
                    if "(Break)" in row["Salida"].strftime("%I:%M %p"):  # Buscamos "(Break)" en la hora
                        break_time = row["Salida"]
                        break_duration = (break_time - first_entry).total_seconds() / 3600
                        
                        if 0 < break_duration <= 5:  # Se tomó el break antes de 5 horas
                            took_break = True
                            break  # No hay necesidad de seguir buscando

                # Si trabajó más de 6 horas sin un break antes de 5 horas, es una violación
                if total_hours > 6 and not took_break:
                    violations.append({
                        "Employee #": employee_id,
                        "Empleado": group.iloc[0]["Empleado"],
                        "Fecha": fecha,
                        "Entrada": first_entry.strftime("%I:%M %p"),
                        "Salida": last_exit.strftime("%I:%M %p"),
                        "Horas Trabajadas": round(total_hours, 2),
                        "Violación": "Meal Violation"
                    })

            return violations

        # Extraer los turnos de los empleados
        shifts = extract_shifts(lines)
        shifts_df = pd.DataFrame(shifts)

        if shifts_df.empty:
            st.warning("⚠ No se encontraron registros de horarios en el PDF.")
        else:
            st.write("✅ **Registros de horarios extraídos:**")
            st.dataframe(shifts_df)

            # Guardar CSV con los registros
            csv = shifts_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descargar CSV con horarios",
                data=csv,
                file_name="registros_horarios.csv",
                mime="text/csv"
            )

            # Detectar meal violations
            meal_violations = check_meal_violations(shifts)
            meal_violations_df = pd.DataFrame(meal_violations)

            if meal_violations_df.empty:
                st.success("✅ No se encontraron violaciones de meal break.")
            else:
                st.error("⚠ Se detectaron Meal Violations.")
                st.dataframe(meal_violations_df)

                # Descargar reporte de Meal Violations
                csv_violations = meal_violations_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar Reporte de Meal Violations",
                    data=csv_violations,
                    file_name="meal_violations_report.csv",
                    mime="text/csv"
                )

    except Exception as e:
        st.error(f"❌ Error al procesar el PDF: {e}")
