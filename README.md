# Meal Violations Dashboard — Oracle MICROS Simphony

Versión **3.4.0** del dashboard interno de Broken Yolk para detectar y auditar Meal Violations desde Oracle MICROS Simphony Business Intelligence API.

La aplicación conserva los controles avanzados de workday, clasificación, waivers, reconciliación y ajustes manuales, pero la interfaz principal vuelve al objetivo operativo original:

> **Mostrar qué empleados tienen Meal Violations, cuántas tienen, en qué fechas y por qué.**

## Vista principal para auditores

La pantalla abre con cuatro indicadores:

- Meal Violations.
- Empleados afectados.
- Punch Errors.
- Jornadas analizadas.

La tabla principal presenta una fila por empleado:

- nombre;
- Payroll ID;
- número de Meal Violations;
- razón principal;
- desglose por razón;
- fechas afectadas;
- ubicación.

El auditor puede buscar por nombre o Payroll ID, filtrar por razón y abrir el detalle de un empleado con:

- fecha;
- entrada y salida;
- horas trabajadas;
- razón de la violación;
- inicio y duración del meal cuando existe;
- indicador de ajuste manual.

## Razones mostradas

La interfaz traduce los códigos internos a seis razones claras:

- No tomó el primer meal.
- Primer meal después de la 5.ª hora.
- Primer meal menor de 30 minutos.
- No tomó el segundo meal.
- Segundo meal después de la 10.ª hora.
- Segundo meal menor de 30 minutos.

## Navegación simplificada

La aplicación utiliza cinco secciones:

1. **Meal Violations** — resumen por empleado, razones y detalle.
2. **Punch Errors** — marcaciones que deben corregirse o confirmarse.
3. **Requiere revisión** — casos ambiguos que no se cuentan automáticamente.
4. **Ajustes con impacto** — solo ajustes manuales que cambiaron el resultado.
5. **Más detalles** — turnos, meals detectados y descargas.

La administración técnica queda oculta detrás de **Mostrar administración** en la barra lateral. Ahí permanecen:

- clasificación exento/no exento;
- waivers y acuerdos on-duty;
- workday legal;
- regular rate;
- reconciliación con MICROS;
- cobertura API;
- timecards normalizados;
- snapshots y cambios entre consultas.

## Auditoría de ajustes manuales

La consulta usa `includeAdjustments=true`. La vista principal de auditoría muestra únicamente ajustes que:

- crearon una Meal Violation;
- eliminaron una Meal Violation;
- cambiaron el tipo de hallazgo;
- cambiaron un caso de revisión.

Se muestra:

- empleado;
- fecha;
- manager;
- motivo;
- resultado antes;
- resultado después;
- impacto.

El detalle técnico completo continúa disponible en un expander.

## Branding

La interfaz conserva:

- logo de The Broken Yolk Cafe;
- encabezado **Meal Violations Dashboard**;
- autoría **Broken Yolk - By Jordan Memije**.

## Despliegue en Streamlit Community Cloud

```text
Repository: Jmemijeabadi/mealviolation
Branch: main
Main file path: app.py
```

Los Secrets de Oracle deben permanecer en Streamlit Cloud, nunca en GitHub:

```toml
[oracle]
auth_server = "https://YOUR-AUTHENTICATION-SERVER"
application_server = "https://YOUR-APPLICATION-SERVER"
org_identifier = "YOUR_ENTERPRISE_SHORT_NAME"
client_id = "YOUR_CLIENT_ID"
username = "YOUR_BI_API_ACCOUNT"
password = "YOUR_ROTATED_PASSWORD"
application_name = "Meal Compliance Dashboard"
timeout_seconds = 45
verify_ssl = true
```

## Desarrollo local

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Pruebas

```bash
PYTHONPATH=. pytest -q
```

La versión 3.4 incluye **50 pruebas automatizadas**. Además de las pruebas del motor, incluye validación del nuevo resumen de Meal Violations por empleado, razones, fechas y separación de empleados que comparten el mismo nombre.

## Alcance

La aplicación identifica presuntas violaciones a partir de la información disponible en MICROS. Un meal compatible por timestamps no demuestra por sí solo que fue duty-free. Los waivers, clasificaciones y acuerdos requieren evidencia operativa o documental.
