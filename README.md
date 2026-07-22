# Meal Violations Dashboard — Oracle MICROS Simphony

Versión **3.2.0** del dashboard interno de The Broken Yolk Cafe para auditar meal periods de California a partir de Oracle MICROS Simphony Business Intelligence API.

## Cambios principales de la versión 3.2

- Se restauró el logo original de The Broken Yolk Cafe.
- Se restauró el encabezado **“The Broken Yolk Cafe · By Jordan Memije”**.
- Rediseño responsive con mejor jerarquía visual.
- Dashboard ejecutivo con:
  - meals esperados por horas;
  - meals confirmados;
  - meals faltantes;
  - meals tardíos;
  - meals cortos;
  - empleados afectados;
  - workdays con premium potencial;
  - premium estimado.
- Nueva tabla **Meals por empleado** con cobertura, violaciones, revisiones y ajustes.
- Nueva pestaña **Auditoría de ajustes**.
- Identificación del manager que hizo cada ajuste, fecha, motivo y valores anteriores.
- Clasificación del posible impacto de cada ajuste sobre meals y horas trabajadas.
- Descargas nuevas:
  - `employee_meal_summary.csv`
  - `timecard_adjustment_audit.csv`

## Estructura del proyecto

```text
app.py
oracle_probe.py
requirements.txt
README.md
assets/
  broken_yolk_logo.png
oracle_bi/
  client.py
compliance/
  engine.py
  models.py
  normalize.py
  audit.py
  reporting.py
tests/
demo/
.streamlit/
  secrets.toml.example
```

## Despliegue en Streamlit Community Cloud

Configura:

```text
Repository: Jmemijeabadi/mealviolation
Branch: main
Main file path: app.py
```

En **App settings → Secrets** agrega la configuración Oracle. No subas el archivo real `.streamlit/secrets.toml` a GitHub.

```toml
[oracle]
auth_server = "https://..."
application_server = "https://..."
org_identifier = "BYC"
client_id = "..."
username = "BLK8BIAPI"
password = "..."
application_name = "Meal Compliance Dashboard"
timeout_seconds = 45
verify_ssl = true
```

## Auditoría de ajustes

La consulta se ejecuta con `includeAdjustments=true`. La pestaña de auditoría muestra:

- manager que realizó el ajuste;
- fecha y hora UTC;
- motivo;
- Clock In anterior y actual;
- Clock Out anterior y actual;
- diferencia estimada en minutos;
- puesto y revenue center anteriores;
- nivel de riesgo;
- posible impacto sobre meal compliance.

Oracle devuelve los valores anteriores de los campos modificados y el timecard contiene su estado final. Cuando un timecard tiene varios ajustes secuenciales, la comparación de duración contra el valor actual se presenta como una estimación y no como una reconstrucción exacta de todos los estados intermedios.

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
python -m pytest -q
```

La versión 3.2 incluye pruebas para el motor de meal compliance, normalización de empleados, resumen por empleado y auditoría de ajustes.

## Alcance

La aplicación es una herramienta operativa de auditoría. Los resultados deben revisarse contra timecards, waivers, acuerdos on-duty y políticas aplicables antes de tomar una determinación definitiva de nómina o cumplimiento laboral.
