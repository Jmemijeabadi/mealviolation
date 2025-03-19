import streamlit as st
import pandas as pd
import PyPDF2
import re
from datetime import datetime, timedelta

def extract_data_from_pdf(pdf_file):
    """
    Extracts text from the PDF file.
    """
    text = ""
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

def parse_time_card_data(text):
    """
    Parses the extracted text using regular expressions.
    """
    data =
    employee_pattern = re.compile(r"(\d+)\s+([\w\s,]+)")
    time_entry_pattern = re.compile(
        r"(IN|OUT)\s+([A-Za-z]*\s*\d+/\d+/\d+)\s+(\d+:\d+[ap]m)"
    )

    current_employee_num = None
    current_employee_name = None

    for match in employee_pattern.finditer(text):
        current_employee_num = match.group(1)
        current_employee_name = match.group(2).strip()

    for match in time_entry_pattern.finditer(text):
        entry_type = match.group(1)
        date_str = match.group(2)
        time_str = match.group(3)

        try:
            # Attempt to parse date and time
            dt_str = f"{date_str} {time_str}"
            date_object = datetime.strptime(dt_str, "%a %m/%d/%Y %I:%M%p")

            if current_employee_num and current_employee_name:
                data.append({
                    "Employee #": current_employee_num,
                    "Employee Name": current_employee_name,
                    "Entry Type": entry_type,
                    "Datetime": date_object,
                })
        except ValueError:
            print(f"Skipping: Could not parse datetime from '{date_str} {time_str}'")
            continue

    return pd.DataFrame(data)

def calculate_meal_violations(df):
    """
    Calculates meal violations based on the provided rules.
    """
    violations =
    if df.empty:
        return pd.DataFrame(violations)  # Return empty DataFrame if input is empty

    grouped_df = df.groupby(["Employee #", "Employee Name", df["Datetime"].dt.date])

    for (employee_num, employee_name, work_date), group in grouped_df:
        group = group.sort_values(by="Datetime")
        total_hours_worked = timedelta(0)
        first_clock_in = None
        last_clock_out = None

        # Calculate total hours worked
        for index, row in group.iterrows():
            if first_clock_in is None:
                first_clock_in = row["Datetime"]
            last_clock_out = row["Datetime"]
        total_hours_worked = last_clock_out - first_clock_in
        total_hours_worked_hours = total_hours_worked.total_seconds() / 3600

        # Check for total work time exceeding 6 hours without a 30-minute break
        if total_hours_worked_hours > 6:
            has_30_min_break = False
            for i in range(len(group) - 1):
                time_diff = group.iloc[i + 1]["Datetime"] - group.iloc[i]["Datetime"]
                if time_diff >= timedelta(minutes=30):
                    has_30_min_break = True
                    break
            if not has_30_min_break:
                violations.append({
                    "Employee #": employee_num,
                    "Employee Name": employee_name,
                    "Date": work_date,
                    "Violation Type": "Over 6 hours, no 30-min break",
                    "Total Hours Worked": total_hours_worked_hours
                })

        # Check for not taking a 30-minute break before the 5th hour
        if total_hours_worked_hours > 6:
            has_break_before_5 = False
            time_worked_before_break = timedelta(0)
            for index, row in group.iterrows():
                time_worked_before_break += row["Datetime"] - first_clock_in
                if time_worked_before_break >= timedelta(hours=5):
                    break_duration = timedelta(0)
                    next_index = group.index.get_loc(index) + 1
                    if next_index < len(group):
                        next_row = group.iloc[next_index]
                        break_duration = next_row["Datetime"] - row["Datetime"]
                    if break_duration >= timedelta(minutes=30):
                        has_break_before_5 = True
                        break
            if not has_break_before_5:
                violations.append({
                    "Employee #": employee_num,
                    "Employee Name": employee_name,
                    "Date": work_date,
                    "Violation Type": "No 30-min break before 5th hour",
                    "Total Hours Worked": total_hours_worked_hours
                })

    return pd.DataFrame(violations)

def main():
    st.title("Meal Violation Analyzer")

    uploaded_file = st.file_uploader("Upload Employee Time Card PDF", type="pdf")

    if uploaded_file is not None:
        text = extract_data_from_pdf(uploaded_file)
        df = parse_time_card_data(text)

        if not df.empty:
            violations_df = calculate_meal_violations(df)

            if not violations_df.empty:
                st.header("Meal Violations")
                st.dataframe(violations_df)
            else:
                st.success("No meal violations found.")
        else:
            st.warning("Could not parse time card data from the PDF.")

if __name__ == "__main__":
    main()
