import streamlit as st
import PyPDF2
import re
from datetime import datetime

def extract_text_from_pdf(pdf_file):
    """
    Extracts text content from a PDF file.

    Args:
        pdf_file: The uploaded PDF file.

    Returns:
        The extracted text, or None if the file is not provided.
    """
    if pdf_file is not None:
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text()
        return text
    return None

def parse_time(time_str):
    """
    Parses a time string in the format "h:mma" (e.g., 7:51am, 2:57pm)
    and returns a datetime.time object.

    Args:
        time_str: The time string to parse.

    Returns:
        A datetime.time object, or None if parsing fails.
    """
    try:
        return datetime.strptime(time_str, "%I:%M%p").time()
    except ValueError:
        return None

def calculate_hours_worked(start_time, end_time):
    """
    Calculates the number of hours worked between two times.

    Args:
        start_time: The start time (datetime.time object).
        end_time: The end time (datetime.time object).

    Returns:
        The number of hours worked (float), or 0 if either time is None.
    """
    if start_time is None or end_time is None:
        return 0
    start_dt = datetime.combine(datetime.today(), start_time)
    end_dt = datetime.combine(datetime.today(), end_time)
    if end_dt < start_dt:
        end_dt = end_dt.replace(day=end_dt.day + 1)  # Handle cases where the time crosses midnight
    return (end_dt - start_dt).total_seconds() / 3600

def analyze_time_cards(text):
    """
    Analyzes the extracted text to identify Meal Violations.

    Args:
        text: The extracted text from the PDF.

    Returns:
        A list of dictionaries, where each dictionary represents a Meal Violation.
    """
    violations =
    employee_pattern = re.compile(r"Employee #\s*(\d+)\s*-\s*(.*):")
    date_pattern = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
    time_entry_pattern = re.compile(r"(IN|OUT)\s+(\d{1,2}:\d{2}[ap]m)")
    
    current_employee = None
    current_date = None
    time_entries =

    lines = text.split('\n')
    for line in lines:
        # Check for Employee and Date
        employee_match = employee_pattern.search(line)
        if employee_match:
            current_employee = {
                "id": employee_match.group(1),
                "name": employee_match.group(2).strip()
            }
            continue  # Move to the next line

        date_match = date_pattern.search(line)
        if date_match:
            current_date = date_match.group(1)
            time_entries =# Reset time entries for the new date
            continue  # Move to the next line

        # Check for Time Entries
        time_entry_matches = list(time_entry_pattern.finditer(line))
        for match in time_entry_matches:
            entry_type = match.group(1)
            entry_time_str = match.group(2)
            entry_time = parse_time(entry_time_str)
            if entry_time:
                time_entries.append((entry_type, entry_time))
        
        # Analyze for Violations after processing time entries
        if current_employee and current_date and time_entries:
            total_hours_worked = 0
            break_taken = False
            break_start_time = None
            time_at_5_hours = None
            
            # Calculate total hours and check for breaks
            in_time = None
            for entry_type, entry_time in time_entries:
                if entry_type == "IN":
                    in_time = entry_time
                elif entry_type == "OUT" and in_time:
                    hours_worked = calculate_hours_worked(in_time, entry_time)
                    total_hours_worked += hours_worked
                    in_time = None  # Reset in_time after processing the pair
                
                # Check for "On Break" in the line
                if "On Break" in line:
                    break_taken = True
                    # Attempt to capture break start time (Note: This might need refinement based on PDF structure)
                    break_start_time = entry_time
                
                # Calculate time at 5 hours (for Condition B)
                if time_at_5_hours is None and total_hours_worked < 5:
                    time_at_5_hours = entry_time

            # Check for Meal Violations
            if total_hours_worked > 6:
                if not break_taken:
                    violations.append({
                        "Employee #": current_employee["id"],
                        "Nombre": current_employee["name"],
                        "Fecha": current_date,
                        "Horas trabajadas por día": round(total_hours_worked,2),
                        "Violation": "Employee worked over 6 hours total and did not take a rest period."
                    })
                elif time_at_5_hours and break_start_time:
                    hours_to_break = calculate_hours_worked(time_at_5_hours, break_start_time)
                    if hours_to_break > 5:
                        violations.append({
                            "Employee #": current_employee["id"],
                            "Nombre": current_employee["name"],
                            "Fecha": current_date,
                            "Horas trabajadas por día": round(total_hours_worked,2),
                            "Violation": "Employee worked over 6 hours total and did not take a rest period before the start of their 5th hour of work."
                        })
    return violations

def main():
    """
    Main function to run the Streamlit application.
    """
    st.title("Meal Violation Detector")

    uploaded_file = st.file_uploader("Upload a PDF file", type="pdf")

    if uploaded_file is not None:
        pdf_text = extract_text_from_pdf(uploaded_file)
        if pdf_text:
            violations = analyze_time_cards(pdf_text)
            if violations:
                st.table(violations)
            else:
                st.write("No Meal Violations found.")
        else:
            st.warning("Could not extract text from PDF.")

if __name__ == "__main__":
    main()
