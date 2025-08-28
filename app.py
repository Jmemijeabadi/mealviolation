import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import time

# =========================
# LÓGICA DE CÁLCULO ROBUSTA
# =========================
def detectar_meal_violations(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regresa un DataFrame con una fila por violación:
    - Nombre
    - Date
    - Reason ("No Break Taken" o "Break after 5h")
    - Overtime Hours (suma del día)
    - Total Horas Día (suma del día)
    """

    # Normaliza encabezados
    df.columns = df.columns.str.strip()

    # Construye "Nombre" como en el original (basado en la fila con "-")
    if "Name" in df.columns and "Clock in Date and Time" in df.columns:
        df["Nombre"] = df["Name"].where(df["Clock in Date and Time"] == "-", None)
        df["Nombre"] = df["Nombre"].ffill()
    else:
        st.error("No se encontraron columnas 'Name' y/o 'Clock in Date and Time'. Revisa el archivo.")
        st.stop()

    # Conversión de tipos
    df["Clock In"] = pd.to_datetime(df["Clock in Date and Time"], errors='coerce')

    # Algunas exportaciones no traen estas columnas; maneja nulos/ausentes
    df["Regular Hours"]  = pd.to_numeric(df.get("Regular Hours", 0), errors='coerce').fillna(0)
    df["Overtime Hours"] = pd.to_numeric(df.get("Overtime Hours", 0), errors='coerce').fillna(0)

    # Cálculos base
    df["Total Hours"] = df["Regular Hours"] + df["Overtime Hours"]
    df["Date"] = df["Clock In"].dt.date

    # 🔐 Quita filas-resumen (sin fecha/hora) que provocan falsos positivos
    df = df[df["Clock In"].notna()].copy()

    # Normaliza el status (por si trae espacios o mayúsculas)
    if "Clock Out Status" in df.columns:
        df["Clock Out Status"] = df["Clock Out Status"].astype(str).str.strip()
    else:
        # Si no existe, no se podrá detectar breaks → todas las jornadas >6h serían violación
        df["Clock Out Status"] = ""

    # Agrupa por persona y día
    violations = []
    for (name, date), group in df.groupby(["Nombre", "Date"], dropna=False):
        # Ordena por tiempo para identificar el PRIMER break
        group_sorted = group.sort_values("Clock In", kind="stable")

        total_hours = group_sorted["Total Hours"].sum()
        if total_hours <= 6:
            continue  # no aplica regla de comida

        on_breaks = group_sorted[group_sorted["Clock Out Status"].str.lower().eq("on break")]

        if on_breaks.empty:
            # No hubo ningún break en la jornada
            overtime_value = group_sorted["Overtime Hours"].sum()
            violations.append({
                "Nombre": name,
                "Date": date,
                "Reason": "No Break Taken",
                "Overtime Hours": round(float(overtime_value), 2),
                "Total Horas Día": round(float(total_hours), 2),
            })
        else:
            # Primer break registrado
            first_break = on_breaks.iloc[0]

            # IMPORTANTE:
            # Usamos 'Total Hours' de esa fila como indicador (igual que tu lógica).
            # Si quieres medir "horas reales hasta el break", tendrías que calcular
            # la diferencia entre el primer Clock In del día y la hora del primer break.
            horas_en_fila_break = pd.to_numeric(first_break.get("Total Hours", 0), errors='coerce')
            if pd.isna(horas_en_fila_break):
                horas_en_fila_break = 0

            if horas_en_fila_break > 5:
                overtime_value = group_sorted["Overtime Hours"].sum()
                violations.append({
                    "Nombre": name,
                    "Date": date,
                    "Reason": "Break after 5h",
                    "Overtime Hours": round(float(overtime_value), 2),
                    "Total Horas Día": round(float(total_hours), 2),
                })

    viol_df = pd.DataFrame(violations, columns=["Nombre", "Date", "Reason", "Overtime Hours", "Total Horas Día"])
    return viol_df


# =========================
# APP STREAMLIT
# =========================
st.set_page_config(page_title="Meal Violations Dashboard", page_icon="🍳", layout="wide")

# Sidebar
st.sidebar.title("Menú Principal")
menu = st.sidebar.radio("Navegación", ("Dashboard", "Configuración"))

# Estilos (look & feel)
st.markdown("""
    <style>
    body { background-color: #f4f6f9; }
    header, footer {visibility: hidden;}
    .block-container { padding-top: 2rem; }
    .metric-card {
        background: white; padding: 20px; border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1); text-align: center;
    }
    .card-title { font-size: 18px; color: #6c757d; margin-bottom: 0.5rem; }
    .card-value { font-size: 30px; font-weight: bold; color: #343a40; }
    .stButton > button {
        background-color: #009efb; color: white; padding: 0.75rem 1.5rem;
        border: none; border-radius: 8px; font-weight: bold; cursor: pointer;
    }
    .stButton > button:hover { background-color: #007acc; color: white; }
    </style>
""", unsafe_allow_html=True)

if menu == "Dashboard":
    st.markdown("""
        <h1 style='text-align: center; color: #343a40;'>🍳 Meal Violations Dashboard</h1>
        <p style='text-align: center; color: #6c757d;'>Broken Yolk - By Jordan Memije</p>
        <hr style='margin-top: 0px;'>
    """, unsafe_allow_html=True)

    file = st.file_uploader("📤 Sube tu archivo Excel de Time Card Detail", type=["xlsx"])

    if file:
        progress_bar = st.progress(0, text="Iniciando análisis...")
        time.sleep(0.3)
        df_raw = pd.read_excel(file, sheet_name=0, header=9)
        progress_bar.progress(0.3, text="Leyendo y limpiando datos...")

        viol_df = detectar_meal_violations(df_raw)
        progress_bar.progress(1.0, text="Listo ✅")
        progress_bar.empty()

        st.balloons()
        st.success('✅ Análisis completado.')

        # Métricas
        total_violations = len(viol_df)
        unique_employees = viol_df['Nombre'].nunique()
        dates_analyzed = viol_df['Date'].nunique()

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
                    <div class="card-title">Empleados con Violaciones</div>
                    <div class="card-value">{unique_employees}</div>
                </div>
            """, unsafe_allow_html=True)
        with col3:
            st.markdown(f"""
                <div class="metric-card">
                    <div class="card-title">Días con Violaciones</div>
                    <div class="card-value">{dates_analyzed}</div>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # Tabla general de violaciones
        st.markdown("## 📋 Detalle de Violaciones")
        if viol_df.empty:
            st.info("No se detectaron violaciones con las reglas actuales.")
        else:
            st.dataframe(viol_df.sort_values(["Nombre", "Date"]), use_container_width=True)

            # Conteo por empleado
            violation_counts = viol_df["Nombre"].value_counts().reset_index()
            violation_counts.columns = ["Empleado", "Número de Violaciones"]

            st.markdown("## 📊 Violaciones por Empleado")
            col_graph, col_table = st.columns([2, 1])
            with col_graph:
                fig, ax = plt.subplots(figsize=(10, 6))
                ax.barh(violation_counts["Empleado"], violation_counts["Número de Violaciones"])
                ax.set_xlabel("Número de Violaciones")
                ax.set_ylabel("Empleado")
                ax.set_title("Violaciones por Empleado", fontsize=14)
                st.pyplot(fig)
            with col_table:
                st.dataframe(violation_counts, use_container_width=True)

            # Alerta de muchos casos
            high_violators = violation_counts[violation_counts["Número de Violaciones"] > 10]
            if not high_violators.empty:
                st.error("🚨 Atención: Hay empleados con más de 10 violaciones detectadas!")
                st.dataframe(high_violators, use_container_width=True)

            st.markdown("---")

            # Exploración por empleado
            st.markdown("### 🔎 Explorar por empleado")
            empleados = ["(Todos)"] + sorted(viol_df["Nombre"].unique().tolist())
            elegido = st.selectbox("Empleado", empleados)
            if elegido != "(Todos)":
                st.dataframe(
                    viol_df[viol_df["Nombre"] == elegido].sort_values("Date"),
                    use_container_width=True
                )

            # Descargar CSV
            csv = viol_df.sort_values(["Nombre", "Date"]).to_csv(index=False).encode('utf-8')
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
