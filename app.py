import streamlit as st
import fitz  # PyMuPDF
import re

def extract_employee_numbers(pdf_path):
    """Extrae los números de empleados del PDF dado, asegurando la exclusión de códigos de trabajo."""
    employee_numbers = set()
    
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            text = re.sub(r"\s+", " ", text)  # Normalizar espacios en blanco
            
            # Expresión regular mejorada para capturar Employee # y nombres de empleados
            matches = re.findall(r"^(\d{3,10})\s*-\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)$", text, re.MULTILINE)
            
            for emp_num, name in matches:
                # Filtrar los que parecen Employee # válidos, evitando trabajos o códigos de sistema
                if not re.search(r"Job|Server|Cook|Cashier|Runner|Manager", name, re.IGNORECASE):
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
