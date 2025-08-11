import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import time

# === Funciones auxiliares ===
def process_excel(file, progress_bar=None):
    time.sleep(0.5)
    # Lee el Excel (ajusta header si tu export cambia)
    df = pd.read_excel(file, sheet_name=0, header=9)

    # Normaliza encabezados (quita espacios al inicio/fin)
    df.columns = df.columns.str.strip()

    # Valida columnas mínimas necesarias
    cols_necesarias = ['Name', 'Clock in Date and Time', 'Regular Hours', 'Clock Out Status']
    faltantes = [c for c in cols_necesarias if c not in df.columns]
    if faltantes:
        st.error(f"Faltan columnas en el Excel: {faltantes}")
        st.stop()

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

    # Construcción de "Nombre" (propaga hacia abajo el último 'Name')
    df["Nombre"] = df["Name"].ffill()

    # Conversión de tipos
    df["Clock In"]       = pd.to_datetime(df["Clock in Date and Time"], errors='coerce')
    df["Regular Hours"]  = pd.to_numeric(df["Regular Hours"], errors='coerce').fillna(0)
    df["Overtime Hours"] = pd.to_numeric(df.get("Overtime Hours", 0), errors='coerce').fillna(0)

    # Cálculos base
    df["Total Hours"] = df["Regular Hours"] + df["Overtime Hours"]
    df["Date"] = df["Clock In"].dt.date

    # Agrupa por persona y día
    grouped = df.groupby(["Nombre", "Date"], dropna=False)

    violations = []
    for (name, date), group in grouped:
        # Ordena por hora para identificar correctamente el primer break
        group_sorted = group.sort_values(by="Clock In", kind="stable")

        total_hours = group_sorted["Total Hours"].sum()
        # Si el total del día es <= 6, no aplica regla de comida
        if total_hours <= 6:
            continue

        # Filtra "On break" sin usar .query() (evita errores por espacios)
        on_breaks = group_sorted[group_sorted['Clock Out Status'].eq('On break')]

        # Si no hay break en la jornada
        if on_breaks.empty:
            overtime_value = group_sorted["Overtime Hours"].sum()
            violations.append({
                "Nombre": name,
                "Date": date,
                "Regular Hours": "No Break Taken",
                "Overtime Hours": round(overtime_value, 2),
                "Total Horas Día": round(total_hours, 2)
            })
        else:
            # Marca si el primer descanso sucede después de 5 horas "reportadas"
            first_break = on_breaks.iloc[0]
            # Nota: Esto usa 'Total Hours' de esa fila, que suele ser acumulado por registro.
            # Si tu fuente tiene columnas de "horas hasta el break", úsala aquí.
            if pd.to_numeric(first_break.get("Total Hours", 0), errors='coerce') > 5:
                overtime_value = group_sorted["Overtime Hours"].sum()
                violations.append({
                    "Nombre": name,
                    "Date": date,
                    "Regular Hours": round(pd.to_numeric(first_break.get("Regular Hours", 0), errors='coerce'), 2),
                    "Overtime Hours": round(overtime_value, 2),
                    "Total Horas Día": round(total_hours, 2)
                })

    return pd.DataFrame(violations)

# === Configuración inicial Streamlit ===
st.set_page_config(page_title="Meal Violations Dashboard", page_icon="🍳", layout="wide")

# Sidebar
st.sidebar.title("Menú Principal")
menu = st.sidebar.radio("Navegación", ("Dashboard", "Configuración"))

# === Estilos CSS personalizados para Freedash Style ===
st.markdown("""
    <style>
    body {
        background-color: #f4f6f9;
    }
    header, footer {visibility: hidden;}
    .block-container {
        padding-top: 2rem;
    }
    .metric-card {
        background: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        text-align: center;
    }
    .card-title {
        font-size: 18px;
        color: #6c757d;
        margin-bottom: 0.5rem;
    }
    .card-value {
        font-size: 30px;
        font-weight: bold;
        color: #343a40;
    }
    .stButton > button {
        background-color: #009efb;
        color: white;
        padding: 0.75rem 1.5rem;
        border: none;
        border-radius: 8px;
        font-weight: bold;
        cursor: pointer;
    }
    .stButton > button:hover {
        background-color: #007acc;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

# === Encabezado personalizado ===
if menu == "Dashboard":
    st.markdown("""
        <h1 style='text-align: center; color: #343a40;'>🍳 Meal Violations Dashboard</h1>
        <p style='text-align: center; color: #6c757d;'>Broken Yolk - By Jordan Memije</p>
        <hr style='margin-top: 0px;'>
    """, unsafe_allow_html=True)

    file = st.file_uploader("📤 Sube tu archivo Excel de Time Card Detail", type=["xlsx"])

    if file:
        progress_bar = st.progress(0, text="Iniciando análisis...")
        violations_df = process_excel(file, progress_bar)
        progress_bar.empty()

        st.balloons()
        st.success('✅ Análisis completado.')

        # Si no se detectaron violaciones, muestra mensaje y evita errores posteriores
        if violations_df.empty:
            st.info("No se detectaron violaciones con las reglas actuales.")
        else:
            total_violations = len(violations_df)
            unique_employees = violations_df['Nombre'].nunique()
            dates_analyzed = violations_df['Date'].nunique()

            st.markdown("## 📈 Resumen General")
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(f"""
                    <div class="metric-card">
                        <div class="card-title">Violaciones Detectadas</div>
                        <div class="card-value">{total_violations}</div>
                    </div>
                """, unsafe_allow_html=True)

            with col2:
                st.markdown(f"""
                    <div class="metric-card">
                        <div class="card-title">Empleados Afectados</div>
                        <div class="card-value">{unique_employees}</div>
                    </div>
                """, unsafe_allow_html=True)

            with col3:
                st.markdown(f"""
                    <div class="metric-card">
                        <div class="card-title">Días Analizados</div>
                        <div class="card-value">{dates_analyzed}</div>
                    </div>
                """, unsafe_allow_html=True)

            st.markdown("---")

            st.markdown("## 📋 Detalle de Violaciones")
            st.dataframe(violations_df, use_container_width=True)

            # Conteo de violaciones por empleado
            violation_counts = violations_df["Nombre"].value_counts().reset_index()
            violation_counts.columns = ["Empleado", "Número de Violaciones"]

            st.markdown("## 📊 Violaciones por Empleado")
            col_graph, col_table = st.columns([2, 1])

            with col_graph:
                fig, ax = plt.subplots(figsize=(10, 6))
                ax.barh(violation_counts["Empleado"], violation_counts["Número de Violaciones"], color="#009efb")
                ax.set_xlabel("Número de Violaciones")
                ax.set_ylabel("Empleado")
                ax.set_title("Violaciones por Empleado", fontsize=14)
                st.pyplot(fig)

            with col_table:
                st.dataframe(violation_counts, use_container_width=True)

            st.markdown("---")

            high_violators = violation_counts[violation_counts["Número de Violaciones"] > 10]
            if not high_violators.empty:
                st.error("🚨 Atención: Hay empleados con más de 10 violaciones detectadas!")
                st.dataframe(high_violators, use_container_width=True)

            # Descarga CSV
            csv = violations_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="⬇️ Descargar resultados en CSV",
                data=csv,
                file_name="meal_violations.csv",
                mime="text/csv"
            )
    else:
        st.info("📤 Por favor sube un archivo Excel para comenzar.")

elif menu == "Configuración":
    st.markdown("# ⚙️ Configuración")
    st.info("Opciones de configuración próximamente disponibles.")
