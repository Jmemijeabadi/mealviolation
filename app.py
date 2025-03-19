import streamlit as st
import pdfplumber
import re
import pandas as pd
from datetime import datetime
from collections import defaultdict

def main():
    st.title("Análisis de Asistencia (Meal Breaks) - Detallado")

    pdf_file = st.file_uploader("Cargar archivo PDF", type=["pdf"])
    
    if pdf_file is not None:
        with pdfplumber.open(pdf_file) as pdf:
            texto_completo = ""
            for page in pdf.pages:
                texto_completo += page.extract_text() + "\n"
        
        # Procesar texto para extraer marcajes (IN/OUT) de manera detallada
        registros_eventos = extraer_eventos(texto_completo)
        
        if not registros_eventos:
            st.warning("No se detectaron eventos IN/OUT en el PDF.")
            return
        
        # Convertimos la lista de eventos a un DataFrame
        df_eventos = pd.DataFrame(registros_eventos)
        
        # Analizamos día por día para cada empleado
        df_resultado = analizar_asistencia_detallada(df_eventos)
        
        st.subheader("Resultado:")
        st.dataframe(df_resultado)

def extraer_eventos(texto):
    """
    Devuelve una lista de diccionarios, donde cada dict representa un evento:
    {
      'employee_id': '1054',
      'nombre': 'Cristal Cervantes',
      'evento': 'IN Not Scheduled' o 'OUT On Break' etc.,
      'datetime': datetime(2025, 2, 22, 7, 51),
      'fecha_str': '2/22/2025',
      'hora_str': '7:51am'
    }
    """
    
    # Patrón para encontrar la línea de "Employee # y Nombre":  <numero> - <nombre>
    patron_empleado = re.compile(r"^(\d+)\s*-\s*(.+)$")
    
    # Patrón para eventos (IN/OUT), intentando capturar algo como:
    #   "SatIN Not Scheduled200 - CASHIER 7:51am2/22/2025"
    #   "OUT On Break 0.79 8:38am"
    # Observamos que la hora en 12h viene en el form "7:51am" o "12:30pm".
    # Y la fecha en "2/22/2025".
    # También notamos que "IN Not Scheduled" / "OUT On Break" / "OUT Not Scheduled" etc.
    # es la parte que define el tipo de evento.
    
    # Haremos 2 búsquedas en la misma línea: 
    # 1) Para ver si es una línea de "IN" o "OUT"
    # 2) Para extraer la hora y fecha
    
    # Expresión para detectar un tag "IN algo" o "OUT algo":
    patron_in_out = re.compile(r"(IN\s+[A-Za-z\s]*)|(OUT\s+[A-Za-z\s]*)")
    # Expresión para hora: (\d{1,2}:\d{2}(am|pm))
    patron_hora = re.compile(r"(\d{1,2}:\d{2}(?:am|pm))")
    # Expresión para fecha: (\d+/\d+/\d+)
    patron_fecha = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
    
    eventos = []
    
    empleado_actual = None
    nombre_actual = None
    
    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea:
            continue
        
        # 1) Checar si la línea define un nuevo empleado
        m_emp = patron_empleado.search(linea)
        if m_emp:
            empleado_actual = m_emp.group(1).strip()
            nombre_actual = m_emp.group(2).strip()
            continue
        
        if empleado_actual is None:
            # Todavía no hemos visto un "ID - Nombre", ignoramos
            continue
        
        # 2) Buscar si la línea contiene un evento IN/OUT
        m_in_out = patron_in_out.search(linea)
        if not m_in_out:
            # No hay "IN" ni "OUT", pasamos
            continue
        
        # Determinar el string exacto: "IN Not Scheduled", "OUT On Break", etc.
        evento_str = m_in_out.group(0)  # Ej: "OUT On Break", "IN Not Scheduled", etc.
        evento_str = evento_str.strip()
        
        # 3) Buscar la hora
        m_hora = patron_hora.findall(linea)
        if not m_hora:
            # No hay hora en la línea, no podemos registrar un evento
            continue
        # Tomamos la última (o la primera) si hubiera más de una
        hora_str = m_hora[-1][0]  # m_hora es una lista de tuplas capturadas con groups, tomamos el primer group
        # hora_str algo como "7:51am"
        
        # 4) Buscar la fecha
        m_fecha = patron_fecha.findall(linea)
        if not m_fecha:
            # No hay fecha, no podemos registrar
            continue
        fecha_str = m_fecha[-1]  # Tomamos la última coincidencia
        
        # 5) Convertir hora_str + fecha_str a datetime
        #    Formato: "m/d/yyyy" + "hh:mm(am|pm)"
        try:
            # Normalizamos "7:51am" en datetime
            dt = datetime.strptime(fecha_str + " " + hora_str, "%m/%d/%Y %I:%M%p")
        except ValueError:
            # Si falla, continuamos
            continue
        
        # 6) Guardamos el evento
        evento = {
            "employee_id": empleado_actual,
            "nombre": nombre_actual,
            "evento": evento_str,             # p.ej. "OUT On Break"
            "datetime": dt,
            "fecha_str": fecha_str,
            "hora_str": hora_str
        }
        eventos.append(evento)
    
    return eventos

def analizar_asistencia_detallada(df_eventos):
    """
    Toma el DataFrame de eventos con columnas:
      - employee_id
      - nombre
      - evento (ej. 'IN Not Scheduled', 'OUT On Break', etc.)
      - datetime
      - fecha_str
      - hora_str
    
    Retorna un DataFrame con:
      - Employee #
      - Nombre
      - Fecha
      - Horas trabajadas (total del día)
      - Meal Violation (Sí / No)
    
    La lógica:
      - Se agrupa por (employee_id, fecha_str)
      - Se ordena por datetime
      - Se calculan las horas totales (sumando IN -> OUT)
      - Detectamos si hubo un break antes de la 5.ª hora
        (Al primer "OUT On Break", vemos cuántas horas se llevaban).
      - Si total > 6 y no break o break tardío => "Sí"
    """
    # Ordenar primero
    df_eventos = df_eventos.sort_values(by=["employee_id", "fecha_str", "datetime"])
    
    resultados = []
    
    for (emp_id, fecha), grupo in df_eventos.groupby(["employee_id", "fecha_str"]):
        grupo = grupo.sort_values(by="datetime")
        
        nombre = grupo["nombre"].iloc[0]
        
        # Para calcular horas reales:
        # Recorremos cada evento y formamos pares IN->OUT. 
        # Ejemplo simple: 
        #   [ (datetime_in1, 'IN ...'), (datetime_out1, 'OUT ...'), (datetime_in2, ...), ... ]
        
        # 'tiempo_total_trabajado' en horas
        tiempo_total_trabajado = 0.0
        
        # ¿Hubo break antes de la 5.ª hora? 
        #   Guardaremos la marca en una var booleana
        hubo_break_antes_5 = False
        
        ultimo_in = None  # datetime de la última entrada
        horas_acumuladas_en_turno = 0.0  # cuántas horas llevaba el empleado en el día, para ver si un break fue < 5
        
        for idx, row in grupo.iterrows():
            evento = row["evento"]
            dt = row["datetime"]
            
            if evento.startswith("IN"):
                # Marca de entrada
                # Cerramos un posible IN anterior? (Depende de si se repite)
                # Para simplificar, cuando vemos un IN, guardamos ese dt para medir un OUT posterior
                ultimo_in = dt
            
            elif evento.startswith("OUT"):
                # Marca de salida
                if ultimo_in is not None:
                    # Calculamos la diferencia
                    delta = dt - ultimo_in
                    horas = delta.total_seconds() / 3600.0
                    # Sumamos al "tiempo_total_trabajado"
                    tiempo_total_trabajado += horas
                    # También al "horas_acumuladas_en_turno"
                    horas_acumuladas_en_turno += horas
                    
                    # Si es un OUT On Break, revisamos si horas_acumuladas_en_turno < 5
                    if "On Break" in evento:
                        if horas_acumuladas_en_turno < 5:
                            hubo_break_antes_5 = True
                    
                    # Cerramos ese IN
                    ultimo_in = None
                else:
                    # OUT sin IN previo... en la práctica pasa con "FORGOT CLOCK-IN"
                    # lo podrías ignorar o manejar distinto
                    pass
        
        # Al final del día, 'tiempo_total_trabajado' es la suma de los intervalos IN->OUT
        # Revisamos violación:
        #   - Criterio: Más de 6 horas y (no hubo break o break fue despues de 5 horas)
        if tiempo_total_trabajado > 6 and not hubo_break_antes_5:
            meal_violation = "Sí"
        else:
            meal_violation = "No"
        
        resultados.append({
            "Employee #": emp_id,
            "Nombre": nombre,
            "Fecha": fecha,
            "Horas trabajadas": round(tiempo_total_trabajado, 2),
            "Meal Violation": meal_violation
        })
    
    df_resultado = pd.DataFrame(resultados)
    # Orden final
    df_resultado = df_resultado.sort_values(["Employee #", "Fecha"]).reset_index(drop=True)
    return df_resultado

if __name__ == "__main__":
    main()
