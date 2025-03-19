import streamlit as st
import pandas as pd
import PyPDF2
from io import StringIO
from datetime import datetime, timedelta

def extract_data_from_pdf(pdf_file):
    """
    Extracts relevant data from the PDF file.
    """
    text = ""
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

def parse_time_card_data(text):
    """
    Parses the extracted text to create a structured DataFrame.
    """
    lines = text.split('\n')
    data =# Initialize data as an empty list
    current_employee = None
    employee_number = None

    for line in lines:
        if "Employee # And Name" in line:
            continue  # Skip header lines
        if line.strip() and line[0].isdigit():
            # Extract Employee Number and Name
            parts = line.split(" ", 1)
            employee_number = parts[0].strip()
            current_employee = parts[1].strip()
            continue

        if "IN" in line and "OUT" in line:
            # Found a clock in/out line
            parts = line.split()
            job_name = parts[0]
            in_date_str = parts[1]
            in_time_str = parts[2]
            out_date_str = parts[3]
            out_time_str = parts[4]

            try:
                # Handle cases where IN and OUT are on different days
                in_datetime_str = f"{in_date_str} {in_time_str}"
                out_datetime_str = f"{out_date_str} {out_time_str}"

                in_datetime = datetime.strptime(in_datetime_str, "%a %m/%d/%Y %I:%M%p")
                out_datetime = datetime.strptime(out_datetime_str, "%a %m/%d/%Y %I:%M%p")

                data.append({
                    "Employee #": employee_number,
                    "Employee Name": current_employee,
                    "Job Name": job_name,
                    "Clock In": in_datetime,
                    "Clock Out": out_datetime
                })
            except ValueError:
                # Handle cases where the date/time format might be slightly different
                print(f"Skipping line due to date/time format issue: {line}")
                continue

    return pd.DataFrame(data)

def calculate_meal_violations(df):
    """
    Calculates meal violations based on the provided rules.
    """
    violations =
    grouped_df = df.groupby(["Employee #", "Employee Name", df["Clock In"].dt.date])

    for (employee_num, employee_name, work_date), group in grouped_df:
        group = group.sort_values(by="Clock In")  # Ensure chronological order
        total_hours_worked = timedelta(0)
        first_clock_in = None
        last_clock_out = None

        for index, row in group.iterrows():
            if first_clock_in is None:
                first_clock_in = row["Clock In"]
            last_clock_out = row["Clock Out"]
            hours_worked = row["Clock Out"] - row["Clock In"]
            total_hours_worked += hours_worked

        total_hours_worked_hours = total_hours_worked.total_seconds() / 3600

        # Check for total work time exceeding 6 hours without a 30-minute break
        if total_hours_worked_hours > 6:
            has_30_min_break = False
            for index, row in group.iterrows():
                time_diff = row["Clock Out"] - row["Clock In"]
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
              time_worked_before_break += row["Clock Out"] - row["Clock In"]
              if time_worked_before_break >= timedelta(hours=5):
                break_duration = timedelta(0)
                next_index = group.index.get_loc(index) + 1
                if next_index < len(group):
                  next_row = group.iloc[next_index]
                  break_duration = next_row["Clock In"] - row["Clock Out"]
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
