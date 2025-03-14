import streamlit as st
import fitz  # PyMuPDF

st.title("📄 PDF Uploader - Prueba de Extracción de Texto")
st.write("Sube un archivo PDF y mostraremos su contenido.")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    st.success("📁 Archivo subido correctamente")
    
    # Guardar el archivo en disco
    with open("uploaded_file.pdf", "wb") as f:
        f.write(uploaded_file.getbuffer())

    # Intentar leer el PDF
    try:
        doc = fitz.open("uploaded_file.pdf")
        text = "\n".join([page.get_text("text") for page in doc])

        st.write("📄 **Vista previa del contenido extraído:**")
        st.text_area("Texto extraído", text[:1000])  # Mostrar solo los primeros 1000 caracteres
        
    except Exception as e:
        st.error(f"❌ Error al leer el PDF: {e}")
