import streamlit as st
import pdfplumber
import re
import json

def extract_data_from_pdf(pdf_path):
    employees = []
    current_employee = None
    employee_pattern = re.compile(r"(\d{4,}) - (.+)")
    entry_pattern = re.compile(r"(\d{3} - [A-Z\s-]+)\s+(IN|OUT)\s+(\w{3})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}[ap]m)\s*(\d*\.\d*)?\s*(.+)?")
    total_hours_pattern = re.compile(r"Total Hours Worked This Week:\s*(\d+\.\d+)")
    pay_pattern = re.compile(r"(\d{3} - [A-Z\s-]+)\s*(\d+\.\d+)\s*(\d+\.\d+)\s*(\d+\.\d+)\s*(\d+\.\d+)")
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            lines = text.split("\n")
            for line in lines:
                employee_match = employee_pattern.match(line)
                if employee_match:
                    if current_employee:
                        employees.append(current_employee)
                    current_employee = {
                        "employee_id": employee_match.group(1),
                        "name": employee_match.group(2),
                        "time_cards": [],
                        "total_hours": 0.0,
                        "jobs": []
                    }
                    continue
                
                time_match = entry_pattern.match(line)
                if time_match and current_employee:
                    job = time_match.group(1).strip()
                    status = time_match.group(2)
                    day = time_match.group(3)
                    date = time_match.group(4)
                    time = time_match.group(5)
                    hours = float(time_match.group(6)) if time_match.group(6) else None
                    reason = time_match.group(7).strip() if time_match.group(7) else ""
                    
                    existing_entry = next((entry for entry in current_employee["time_cards"] if entry["date"] == date and entry["job"] == job), None)
                    if not existing_entry:
                        existing_entry = {"date": date, "job": job, "entries": []}
                        current_employee["time_cards"].append(existing_entry)
                    
                    existing_entry["entries"].append({
                        "day": day,
                        "time": time,
                        "status": status,
                        "hours": hours,
                        "reason": reason
                    })
                
                total_hours_match = total_hours_pattern.search(line)
                if total_hours_match and current_employee:
                    current_employee["total_hours"] = float(total_hours_match.group(1))
                
                pay_match = pay_pattern.match(line)
                if pay_match and current_employee:
                    job = pay_match.group(1).strip()
                    regular_hours = float(pay_match.group(2))
                    overtime_hours = float(pay_match.group(3))
                    regular_pay = float(pay_match.group(4))
                    total_pay = float(pay_match.group(5))
                    current_employee["jobs"].append({
                        "job": job,
                        "regular_hours": regular_hours,
                        "overtime_hours": overtime_hours,
                        "regular_pay": regular_pay,
                        "total_pay": total_pay
                    })
    
    if current_employee:
        employees.append(current_employee)
    
    return {"employees": employees}

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
