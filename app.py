import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import time

# === Funciones auxiliares ===
def process_excel(file, progress_bar=None):
    time.sleep(0.5)  # Simula carga inicial
    df = pd.read_excel(file, sheet_name=0, header=9)

    steps = [
        ("Procesando nombres...", 0.2),
        ("Convirtiendo fechas y horas...", 0.4),
        ("Calculando horas totales...", 0.6),
        ("Agrupando datos...", 0.8),
        ("Finalizando...", 1.0)
    ]

    if progress_bar:
        for msg, pct in steps:
            progress_bar.progress(pct, text=msg)
            time.sleep(0.5)

    df["Nombre"] = df["Name"].where(df["Clock in Date and Time"] == "-", None)
    df["Nombre"] = df["Nombre"].ffill()

    df["Clock In"] = pd.to_datetime(df["Clock in Date and Time"], errors='coerce')
    df["Regular Hours"] = pd.to_numeric(df["Regular Hours"], errors='coerce')
    df["Overtime Hours"] = pd.to_numeric(df.get("Overtime Hours", 0), errors='coerce').fillna(0)

    df["Total Hours"] = df["Regular Hours"] + df["Overtime Hours"]
    df["Date"] = df["Clock In"].dt.date

    grouped = df.groupby(["Nombre", "Date"])
    violations = []

    for (name, date), group in grouped:
        total_hours = group["Total Hours"].sum()
        if total_hours <= 6:
            continue

        on_breaks = group.query('`Clock Out Status` == "On break"')
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
            if first_break["Total Hours"] > 5:
                overtime_value = group["Overtime Hours"].sum()
                violations.append({
                    "Nombre": name,
                    "Date": date,
                    "Regular Hours": round(first_break["Regular Hours"], 2),
                    "Overtime Hours": round(overtime_value, 2),
                    "Total Horas Día": round(total_hours, 2)
                })

    return pd.DataFrame(violations)

# === Configuración inicial Streamlit ===
st.set_page_config(page_title="Meal Violations Dashboard", page_icon="🍳", layout="wide")

# === Estilos CSS personalizados ===
st.markdown("""
    <style>
    body {
        background-color: #f9f9f9;
    }
    .main {
        background-color: #ffffff;
        padding: 2rem;
        border-radius: 10px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
    }
    .stMetric {
        background-color: #f0f0f5;
        padding: 1rem;
        border-radius: 10px;
        text-align: center;
    }
    .stButton > button {
        background-color: #FFC107;
        color: black;
        border: None;
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        font-weight: bold;
    }
    .stButton > button:hover {
        background-color: #ffb300;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🍳 Meal Violations Dashboard - Broken Yolk")
st.caption("By Jordan Memije - AI Solution Central")

file = st.file_uploader("📤 Sube tu archivo Excel de Time Card Detail", type=["xlsx"])

tab1, tab2 = st.tabs(["ℹ️ Instrucciones", "📊 Resultados"])

with tab1:
    st.markdown("""
    ### ¿Cómo se detectan las Meal Violations?
    - Se analizan solo los días con **más de 6 horas** trabajadas.
    - **No Break Taken**: No se registró ningún descanso.
    - **Break inválido**: El primer descanso fue **después de 5 horas**.
    - **Overtime** se suma a las horas regulares.
    """)

with tab2:
    if file:
        progress_bar = st.progress(0, text="Iniciando análisis...")
        violations_df = process_excel(file, progress_bar)
        progress_bar.empty()

        st.success('✅ Análisis completado.')

        total_violations = len(violations_df)
        unique_employees = violations_df['Nombre'].nunique()
        dates_analyzed = violations_df['Date'].nunique()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="🔎 Violaciones Detectadas", value=total_violations)
        with col2:
            st.metric(label="👤 Empleados Afectados", value=unique_employees)
        with col3:
            st.metric(label="📅 Días Analizados", value=dates_analyzed)

        st.divider()

        st.subheader("📋 Detalle de Violaciones Detectadas")
        st.dataframe(violations_df, use_container_width=True)

        violation_counts = violations_df["Nombre"].value_counts().reset_index()
        violation_counts.columns = ["Empleado", "Número de Violaciones"]

        st.divider()
        st.subheader("📊 Violaciones por Empleado")

        col_graph, col_table = st.columns([2, 1])

        with col_graph:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.barh(violation_counts["Empleado"], violation_counts["Número de Violaciones"], color="#FFB347")
            ax.set_xlabel("Número de Violaciones")
            ax.set_ylabel("Empleado")
            ax.set_title("Violaciones por Empleado", fontsize=14)
            st.pyplot(fig)

        with col_table:
            st.dataframe(violation_counts, use_container_width=True)

        st.divider()
        csv = violations_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Descargar resultados en CSV",
            data=csv,
            file_name="meal_violations.csv",
            mime="text/csv"
        )

    else:
        st.warning("🔔 Sube un archivo Excel para comenzar el análisis.")
