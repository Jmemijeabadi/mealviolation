import streamlit as st
import json
import pdfplumber
import re

def extract_data_from_pdf(pdf_path):
    data = []
    current_employee = None
    time_entries = []
    
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
                        current_employee["time_cards"].extend(time_entries)
                        data.append(current_employee)
                    current_employee = {
                        "id": employee_match.group(1),
                        "name": employee_match.group(2),
                        "time_cards": []
                    }
                    time_entries = []
                    continue
                
                time_entry_match = re.match(r"(\w{3})IN\s+(.+?)\s+(\d{1,2}:\d{2}[ap]m)\s+(\d{1,2}/\d{1,2}/\d{4})", line)
                time_exit_match = re.match(r"(\w{3})OUT\s+(.+?)\s+(\d{1,2}:\d{2}[ap]m)\s+(\d{1,2}/\d{1,2}/\d{4})\s*(\d*\.\d*)?", line)
                
                if time_entry_match:
                    date = time_entry_match.group(4)
                    job = time_entry_match.group(2).strip()
                    time = time_entry_match.group(3)
                    
                    existing_entry = next((entry for entry in time_entries if entry["date"] == date and entry["job"] == job), None)
                    if existing_entry:
                        existing_entry["entries"].append({"time": time, "status": "IN", "reason": job})
                    else:
                        time_entries.append({
                            "date": date,
                            "job": job,
                            "entries": [{"time": time, "status": "IN", "reason": job}]
                        })
                    
                if time_exit_match:
                    date = time_exit_match.group(4)
                    job = time_exit_match.group(2).strip()
                    time = time_exit_match.group(3)
                    hours = float(time_exit_match.group(5)) if time_exit_match.group(5) else None
                    
                    existing_entry = next((entry for entry in time_entries if entry["date"] == date and entry["job"] == job), None)
                    if existing_entry:
                        existing_entry["entries"].append({"time": time, "status": "OUT", "reason": job, "hours": hours})
                    else:
                        time_entries.append({
                            "date": date,
                            "job": job,
                            "entries": [{"time": time, "status": "OUT", "reason": job, "hours": hours}]
                        })
    
    if current_employee:
        current_employee["time_cards"].extend(time_entries)
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
