import streamlit as st
import pandas as pd

def process_excel(file):
    df = pd.read_excel(file, sheet_name=0, header=9)

    # Extraer nombre real del empleado solo donde Clock in Date and Time es "-"
    df["Nombre"] = df["Name"].where(df["Clock in Date and Time"] == "-", None)
    df["Nombre"] = df["Nombre"].fillna(method="ffill")

    # Convertir fechas y horas
    df["Clock In"] = pd.to_datetime(df["Clock in Date and Time"], errors='coerce')
    df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
    df["Date"] = df["Clock In"].dt.date

    # Agrupar por empleado y fecha
    grouped = df.groupby(["Nombre", "Date"])
    violations = []

    for (name, date), group in grouped:
        total_hours = group["Regular Hours"].sum()
        if total_hours <= 6:
            continue

        took_break = group["Clock Out Status"].str.contains("On break", na=False).any()
        has_break_over_5 = (
            (group["Clock Out Status"] == "On break") & (group["Regular Hours"] > 5)
        ).any()

        if not took_break:
            violations.append({
                "Nombre": name,
                "Date": date.strftime('%d/%m/%Y'),
                "Regular Hours": None,
                "Total Horas Día": round(total_hours, 2),
                "Tipo de Violación": "No Break Taken"
            })
        elif has_break_over_5:
            rows = group[(group["Clock Out Status"] == "On break") & (group["Regular Hours"] > 5)]
            rh_value = rows["Regular Hours"].max() if not rows.empty else None
            violations.append({
                "Nombre": name,
                "Date": date.strftime('%d/%m/%Y'),
                "Regular Hours": round(rh_value, 2) if rh_value else None,
                "Total Horas Día": round(total_hours, 2),
                "Tipo de Violación": "Late Break"
            })

    return pd.DataFrame(violations)

# Streamlit UI
st.title("🤖🪄Meal Violations Detector Broken Yolk")
st.caption("By Jordan Memije AI Solution Central")

with st.expander("ℹ️ ¿Cómo se detectan las Meal Violations?"):
    st.markdown("""
    - Solo se evalúan días con **más de 6 horas trabajadas**.
    - **No Break Taken**: No se registró ningún descanso (\"On break\").
    - **Late Break**: El descanso ocurrió después de 5 horas de trabajo.
    """)

file = st.file_uploader("Sube un archivo Excel de Time Card Detail", type=["xlsx"])

if file:
    results = process_excel(file)
    st.success("Análisis completado. Resultado:")
    st.dataframe(results)

    # Botón de descarga
    csv = results.to_csv(index=False).encode('utf-8')
    st.download_button("Descargar resultados en CSV", data=csv, file_name="meal_violations.csv", mime="text/csv")
