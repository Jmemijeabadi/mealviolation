import streamlit as st

st.title("🚀 Meal Violation Analyzer")
st.write("Si ves este mensaje, la app está funcionando correctamente.")

uploaded_file = st.file_uploader("Sube un PDF de prueba", type=["pdf"])

if uploaded_file is not None:
    st.success("📁 Archivo subido exitosamente")
    st.write("Nombre del archivo:", uploaded_file.name)
