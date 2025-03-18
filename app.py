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

    try:
        doc = fitz.open(stream=BytesIO(uploaded_file.read()), filetype="pdf")
        lines = [line for page in doc for line in page.get_text("text").split("\n")]

        st.write("✅ **Texto extraído correctamente. Ahora procesando horarios...**")

        def extract_shifts(lines):
            """Extrae horarios de entrada y salida de empleados basándose en la estructura detectada."""
            records = []
            current_employee_id, current_employee, current_date = None, None, None
            time_pattern = re.compile(r"\b\d{1,2}:\d{2}[ap]m\b")  # Patrón de hora

            for i, line in enumerate(lines):
                line = line.strip()

                # Detectar el número de empleado y el nombre
                employee_match = re.match(r"(\d{6,}) - (.+)", line)
                if employee_match:
                    current_employee_id, current_employee = employee_match.groups()
                    continue

                # Detectar fechas en formato MM/DD/YYYY
                if re.match(r"\d{1,2}/\d{1,2}/\d{4}", line):
                    current_date = line

                # Buscar horarios
                if time_pattern.search(line) and current_employee_id and current_date:
                    try:
                        entry_time_str = line
                        entry_dt = datetime.strptime(f"{current_date} {entry_time_str}", "%m/%d/%Y %I:%M%p")

                        # Buscar la salida más cercana en las siguientes líneas
                        for j in range(i + 1, len(lines)):
                            if time_pattern.search(lines[j]):
                                exit_time_str = lines[j].strip()
                                exit_dt = datetime.strptime(f"{current_date} {exit_time_str}", "%m/%d/%Y %I:%M%p")
                                hours_worked = (exit_dt - entry_dt).total_seconds() / 3600

                                records.append({
                                    "Employee #": current_employee_id,
                                    "Empleado": current_employee,
                                    "Fecha": current_date,
                                    "Entrada": entry_dt.strftime("%I:%M %p"),
                                    "Salida": exit_dt.strftime("%I:%M %p"),
                                    "Horas Trabajadas": round(hours_worked, 2),
                                    "On Break": "On Break" in exit_time_str,  # Identificar si el registro es un break
                                    "Break Time": exit_dt if "On Break" in exit_time_str else None  # Guardar tiempo de break
                                })
                                break
                    except ValueError:
                        continue

            return records

        def check_meal_violations(shifts_df):
            """Identifica violaciones de meal break: más de 6 horas trabajadas sin 'On Break' antes de la 5ta hora."""
            violations = []

            shifts_df["Entrada"] = pd.to_datetime(shifts_df["Fecha"] + " " + shifts_df["Entrada"], format="%m/%d/%Y %I:%M %p")
            shifts_df["Salida"] = pd.to_datetime(shifts_df["Fecha"] + " " + shifts_df["Salida"], format="%m/%d/%Y %I:%M %p")

            for (employee_id, fecha), group in shifts_df.groupby(["Employee #", "Fecha"]):
                group = group.sort_values(by="Entrada")

                first_entry = group.iloc[0]["Entrada"]
                last_exit = group.iloc[-1]["Salida"]
                total_hours = (last_exit - first_entry).total_seconds() / 3600

                took_break = False

                for _, row in group.iterrows():
                    if row["On Break"]:
                        break_time = row["Break Time"]
                        break_duration = (break_time - first_entry).total_seconds() / 3600 if break_time else None
                        if break_duration is not None and break_duration <= 5:
                            took_break = True
                            break

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

            csv = shifts_df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Descargar CSV con horarios", data=csv, file_name="registros_horarios.csv", mime="text/csv")

            # Detectar meal violations
            meal_violations = check_meal_violations(shifts_df)
            meal_violations_df = pd.DataFrame(meal_violations)

            if meal_violations_df.empty:
                st.success("✅ No se encontraron violaciones de meal break.")
            else:
                st.error("⚠ Se detectaron Meal Violations.")
                st.dataframe(meal_violations_df)

                csv_violations = meal_violations_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Descargar Reporte de Meal Violations", data=csv_violations, file_name="meal_violations_report.csv", mime="text/csv")

    except Exception as e:
        st.error(f"❌ Error al procesar el PDF: {e}")
