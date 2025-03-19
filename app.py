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
                
                time_entry_match = re.match(r"(\w{3})IN (.+)(\d{1,2}:\d{2}[ap]m)(\d{1,2}/\d{1,2}/\d{4})", line)
                if time_entry_match and current_employee:
                    current_employee["time_cards"].append({
                        "date": time_entry_match.group(4),
                        "job": time_entry_match.group(2).strip(),
                        "entries": [{
                            "time": time_entry_match.group(3),
                            "status": "IN",
                            "reason": time_entry_match.group(2).strip()
                        }]
                    })
    
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
