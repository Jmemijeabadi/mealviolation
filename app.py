import streamlit as st
import pdfplumber
import json
import re

def extract_employees_from_pdf(pdf_file):
    employees = []
    with pdfplumber.open(pdf_file) as pdf:
        text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
        
        # Patrón para identificar empleados (ID - Nombre)
        employee_pattern = re.compile(r"(\d{4}) - ([A-Z ]+)")
        matches = employee_pattern.findall(text)
        
        for match in matches:
            employee_id, name = match
            employees.append({
                "employee_id": employee_id,
                "name": name.title(),
                "time_entries": [],
                "total_hours": 0,
                "overtime_hours": 0,
                "pay": 0
            })
        
    return employees

def main():
    st.title("PDF Employee Timecard Extractor")
    
    uploaded_file = st.file_uploader("Upload a PDF file", type=["pdf"])
    
    if uploaded_file:
        employees = extract_employees_from_pdf(uploaded_file)
        
        if employees:
            st.success(f"{len(employees)} employees extracted successfully!")
            st.json(employees)
            
            json_output = json.dumps(employees, indent=4)
            st.download_button("Download JSON", json_output, "employees.json", "application/json")
        else:
            st.error("No employees found in the document.")

if __name__ == "__main__":
    main()
