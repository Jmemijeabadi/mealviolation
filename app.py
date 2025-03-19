import streamlit as st
import json
import pdfplumber
import re

def extract_data_from_pdf(pdf_path):
    data = []
    current_employee = None
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            lines = text.split("\n")
            for line in lines:
                # Detectar empleados
                employee_match = re.match(r"(\d{4,}) - (.+)", line)
                if employee_match:
                    if current_employee:
                        data.append(current_employee)
                    current_employee = {
                        "id": employee_match.group(1),
                        "name": employee_match.group(2),
                        "time_cards": []
                    }
                    continue
                
                # Detectar registros de entrada y salida
                time_match = re.match(r"(\w{3})\s+(IN|OUT)\s+(.+?)\s+(\d{1,2}:\d{2}[ap]m)\s+(\d{1,2}/\d{1,2}/\d{4})\s*(\d*\.\d*)?", line)
                if time_match and current_employee:
                    date = time_match.group(5)
                    status = time_match.group(2)
                    job = time_match.group(3).strip()
                    time = time_match.group(4)
                    hours = float(time_match.group(6)) if time_match.group(6) else None
                    
                    # Buscar si ya hay una entrada para ese día y ese trabajo
                    existing_entry = next((entry for entry in current_employee["time_cards"] if entry["date"] == date and entry["job"] == job), None)
                    if not existing_entry:
                        existing_entry = {"date": date, "job": job, "entries": []}
                        current_employee["time_cards"].append(existing_entry)
                    
                    existing_entry["entries"].append({"time": time, "status": status, "hours": hours})
    
    if current_employee:
        data.append(current_employee)
    
    return {"employees": data}

def main():
    st.title("Employee Time Card Analyzer")
    uploaded_file = st.file_uploader("Upload a PDF file", type=["pdf"])
    
    if uploaded_file:
        with open("temp.pdf", "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        extracted_data = extract_data_from_pdf("temp.pdf")
        st.json(extracted_data)

if __name__ == "__main__":
    main()
