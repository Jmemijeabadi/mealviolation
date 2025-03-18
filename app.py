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
                if line == "IN" and "On Time" in lines[i + 1]:
                    try:
                        entry_time_str = lines[i + 2].strip()  # Hora de entrada
                        if not re.search(r"\d{1,2}:\d{2}[ap]m", entry_time_str):
                            entry_time_str = lines[i + 3].strip()  # Ajustar si la línea siguiente es incorrecta
                        entry_date_str = lines[i + 4].strip()  # Fecha de entrada
                        entry_time = datetime.strptime(f"{entry_date_str} {entry_time_str}", "%m/%d/%Y %I:%M%p")
                    except:
                        entry_time = None  # Reiniciar en caso de error

                # Detectar salidas ("OUT On Time") asociadas a una entrada previa
                if line == "OUT" and entry_time and current_employee_id:
                    try:
                        exit_time_str = lines[i + 2].strip()  # Hora de salida
                        if not re.search(r"\d{1,2}:\d{2}[ap]m", exit_time_str):
                            exit_time_str = lines[i + 3].strip()  # Ajustar si la línea siguiente es incorrecta
                        exit_time = datetime.strptime(f"{entry_date_str} {exit_time_str}", "%m/%d/%Y %I:%M%p")

                        # Calcular horas trabajadas
                        hours_worked = (exit_time - entry_time).total_seconds() / 3600

                        # Agregar registro con Employee # y Empleado correctos
                        records.append({
                            "Employee #": current_employee_id,
                            "Empleado": current_employee,
                            "Fecha": entry_date_str,
                            "Entrada": entry_time.strftime("%I:%M %p"),
                            "Salida": exit_time.strftime("%I:%M %p"),
                            "Horas Trabajadas": round(hours_worked, 2)
                        })

                        # Reiniciar valores después de registrar un turno
                        entry_time = None
                        entry_date_str = None
                    except:
                        continue

            return records

        # Función para detectar meal violations
        def check_meal_violations(shifts):
            """Identifica violaciones de meal break según la regla establecida."""
            violations = []

            for shift in shifts:
                total_hours = shift["Horas Trabajadas"]
                entry_time = datetime.strptime(shift["Entrada"], "%I:%M %p")
                exit_time = datetime.strptime(shift["Salida"], "%I:%M %p")

                # Aplicar regla de Meal Violation
                if total_hours > 6:
                    took_break = False

                    # Buscar si hay un break dentro de las primeras 5 horas
                    for check in shifts:
                        if check["Empleado"] == shift["Empleado"] and check["Fecha"] == shift["Fecha"]:
                            break_time = datetime.strptime(check["Salida"], "%I:%M %p")
                            break_duration = (break_time - entry_time).total_seconds() / 3600  # Horas desde entrada hasta el break

                            if 2 <= break_duration <= 5:  # Asegurarse de que sea un break real (mínimo 2 horas)
                                took_break = True
                                break

                    if not took_break:
                        violations.append({
                            "Employee #": shift["Employee #"],
                            "Empleado": shift["Empleado"],
                            "Fecha": shift["Fecha"],
                            "Entrada": shift["Entrada"],
                            "Salida": shift["Salida"],
                            "Horas Trabajadas": total_hours,
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
