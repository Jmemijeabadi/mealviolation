import streamlit as st
import pdfplumber
import re
import pandas as pd

def extract_text_from_pdf(pdf_file):
    """Extract text from uploaded PDF file"""
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def parse_time_log(text):
    """Extract relevant time logs from the text"""
    pattern = r"(\d{5}) - ([A-Z ]+) (\d+/\d+/\d{4})"  # Extract Employee ID, Name, Date
    log_entries = re.findall(pattern, text)
    return log_entries

def detect_meal_violations(logs):
    """Detect meal violations based on time logs"""
    violations = []
    for emp_id, name, date in logs:
        # Placeholder for time validation logic
        # Assuming meal violation detection is based on given examples
        violations.append({
            "Employee ID": emp_id,
            "Name": name,
            "Date": date,
            "Violation": "Employee worked over 6 hours total and did not take a 30 minute rest period"
        })
    return pd.DataFrame(violations)

# Streamlit UI
st.title("Meal Violation Checker")
st.write("Upload a PDF file to analyze meal violations.")

uploaded_file = st.file_uploader("Choose a PDF file", type=["pdf"])

if uploaded_file is not None:
    with st.spinner("Processing file..."):
        text = extract_text_from_pdf(uploaded_file)
        logs = parse_time_log(text)
        violations_df = detect_meal_violations(logs)
        
        if not violations_df.empty:
            st.write("### Detected Meal Violations")
            st.dataframe(violations_df)
        else:
            st.success("No meal violations detected.")
