import streamlit as st
import fitz  # PyMuPDF para leer PDFs
import pandas as pd
import re
from datetime import datetime

def extract_lines_from_pdf(pdf_path):
    """Extrae todas las líneas del PDF."""
    doc = fitz.open(pdf_path)
    lines = []
    for page in doc:
        lines.extend(page.get_text("text").split("\n"))
    return lines

def extract_shifts(lines):
    """Extrae registros de entrada y salida con asignación del empleado y número de empleado."""
    records = []
    current_employee = None
    current_employee_id = None
    entry_time = None
    entry_date = None

    for i in range(len(lines) - 4):
        line = lines[i]

        # Detectar el número y nombre del empleado
        employee_match = re.match(r"(\d{6,}) - (.+)", line)
        if employee_match:
            current_employee_id = employee_match.group(1).strip()
            current_employee = employee_match.group(2).strip()

        # Detectar entradas ("IN")
        if line == "IN" and lines[i + 1] == "On Time":
            try:
                entry_time_str = lines[i + 3]  # Hora de entrada
                entry_date = lines[i + 4]  # Fecha
                entry_time = datetime.strptime(f"{entry_date} {entry_time_str}", "%m/%d/%Y %I:%M%p")
            except:
                continue

        # Detectar salidas ("OUT") asociadas a una entrada previa
        if line == "OUT" and entry_time and current_employee:
            try:
                exit_time_str = lines[i + 3]  # Hora de salida
                exit_time = datetime.strptime(f"{entry_date} {exit_time_str}", "%m/%d/%Y %I:%M%p")

                # Agregar registro
                records.append({
                    "Employee #": current_employee_id,
                    "Empleado": current_employee,
                    "Fecha": entry_date,
                    "Entrada": entry_time.strftime("%I:%M %p"),
                    "Salida": exit_time.strftime("%I:%M %p"),
                    "Horas Trabajadas": (exit_time - entry_time).total_seconds() / 3600
                })

                # Reiniciar valores después de agregar un turno
                entry_time = None
                entry_date = None

            ex
