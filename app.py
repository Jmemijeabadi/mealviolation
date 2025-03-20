import streamlit as st
import fitz  # PyMuPDF
import re

def extract_employee_numbers(pdf_path):
    """Extrae los números de empleados del PDF dado."""
    employee_numbers = set()
    
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
            
            # Expresión regular mejorada para detectar Employee # con más variabilidad
            matches = re.findall(r"(\b\d{3,10}\b)\s*-\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)", text)
            for emp_num, name in matches:
                employee_numbers.add((emp_num.strip(), name.strip()))
    
    return sorted(employee_numbers, key=lambda x: x[0])

def main():
    st.title("PDF Employee Number Extractor")
    st.write("Sube un archivo PDF para extraer los Employee #.")
    
    uploaded_file = st.file_uploader("Sube un archivo PDF", type=["pdf"])
    
    if uploaded_file is not None:
        st.write("Procesando el archivo...")
        pdf_path = f"temp_{uploaded_file.name}"  # Guardamos temporalmente
        
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        employee_numbers = extract_employee_numbers(pdf_path)
        
        if employee_numbers:
            st.write("### Números de Empleados Extraídos:")
            for emp_num, name in employee_numbers:
                st.write(f"- {emp_num}: {name}")
        else:
            st.write("No se encontraron números de empleados en el archivo.")
        
if __name__ == "__main__":
    main()
