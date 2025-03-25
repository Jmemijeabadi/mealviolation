import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

def process_excel(file):
    df = pd.read_excel(file, sheet_name=0, header=9)

    # Extraer nombre del empleado
    df["Nombre"] = df["Name"].where(df["Clock in Date and Time"] == "-", None)
    df["Nombre"] = df["Nombre"].fillna(method="ffill")

    # Conversión de columnas
    df["Clock In"] = pd.to_datetime(df["Clock in Date and Time"], errors='coerce')
    df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
    df["Overtime Hours"] = pd.to_numeric(df.get("Overtime Hours", 0), errors='coerce').fillna(0)

    # Sumar Overtime si existe
    df["Total Hours"] = df["Regular Hours"]
    df.loc[df["Overtime Hours"] > 0, "Total Hours"] += df["Overtime Hours"]

    df["Date"] = df["Clock In"].dt.date

    grouped = df.groupby(["Nombre", "Date"])
    violations = []

    for (name, date), group in grouped:
        total_hours = group["Total Hours"].sum()
        if total_hours <= 6:
            continue

        # Solo considerar el primer "On break"
        on_breaks = group[group["Clock Out Status"] == "On break"]
        if on_breaks.empty:
            overtime_value = group["Overtime Hours"].sum()
            violations.append({
                "Nombre": name,
                "Date": date,
                "Regular Hours": "No Break Taken",
                "Overtime Hours": round(overtime_value, 2),
                "Total Horas Día": round(total_hours, 2)
            })
        else:
            first_break = on_breaks.iloc[0]
            hours_at_first_break = first_break["Total Hours"]
            if hours_at_first_break > 5:
                overtime_value = group["Overtime Hours"].sum()
                violations.append({
                    "Nombre": name,
                    "Date": date,
                    "Regular Hours": round(first_break["Regular Hours"], 2),
                    "Overtime Hours": round(overtime_value, 2),
                    "Total Horas Día": round(total_hours, 2)
                })

    return pd.DataFrame(violations)

# ==============================
# 🖥️ Streamlit UI
# ==============================
st.set_page_config(page_title="Meal Violations Detector", page_icon="🍳", layout="wide")
st.title("🤖🪄 Meal Violations Detector - Broken Yolk")
st.caption("By Jordan Memije - AI Solution Central")

with st.expander("ℹ️ ¿Cómo se detectan las Meal Violations?"):
    st.markdown("""
    ### Reglas de detección:
    - Se analizan solo los días donde se trabajaron **más de 6 horas**.
    - **No Break Taken**: El empleado **no tomó ningún descanso** ("On break").
    - **Break inválido**: El primer descanso fue **después de 5 horas** desde el inicio.
    - Si hay **Overtime**, este se **suma** a las horas regulares para el total diario.
    - Se muestra el total de horas extra (**Overtime Hours**) por día.
    """)

# Subida de archivo
file = st.file_uploader("📤 Sube un archivo Excel de Time Card Detail", type=["xlsx"])

if file:
    results = process_excel(file)
    st.success(f"✅ Análisis completado. Se encontraron {len(results)} violaciones.")
    st.subheader("📋 Detalle de Violaciones Detectadas")
    st.dataframe(results)

    # === Conteo por empleado
    violation_counts = results["Nombre"].value_counts().reset_index()
    violation_counts.columns = ["Empleado", "Número de Violaciones"]

    st.subheader("📊 Violaciones por Empleado")
    st.dataframe(violation_counts)

    # === Gráfico de barras
    fig, ax = plt.subplots()
    ax.bar(violation_counts["Empleado"], violation_counts["Número de Violaciones"])
    ax.set_xlabel("Empleado")
    ax.set_ylabel("Número de Violaciones")
    ax.set_title("📈 Total de Violaciones por Empleado")
    plt.xticks(rotation=45, ha="right")
    st.pyplot(fig)

    # === Botón de descarga
    csv = results.to_csv(index=False).encode('utf-8')
    st.download_button(
        "⬇️ Descargar resultados en CSV",
        data=csv,
        file_name="meal_violations.csv",
        mime="text/csv"
    )
