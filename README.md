# Meal Violations Dashboard — Oracle MICROS Simphony

Versión **3.7.0** del dashboard interno de Broken Yolk para detectar y auditar Meal Violations desde Oracle MICROS Simphony Business Intelligence API.

La aplicación conserva los controles avanzados de workday, clasificación, waivers, reconciliación y ajustes manuales, pero la interfaz principal vuelve al objetivo operativo original:

> **Mostrar qué empleados tienen Meal Violations, cuántas tienen, en qué fechas y por qué.**

## Vista principal para auditores

La pantalla abre con cinco indicadores:

- posibles Meal Violations detectadas por marcación;
- empleados señalados;
- hallazgos pendientes de validación administrativa;
- exposición preliminar de los candidatos;
- jornadas con Punch Review.

Los patrones detectados ya no desaparecen cuando faltan clasificación, workday o controles administrativos. Se muestran con estado **Pendiente de validación** hasta completar esos controles.

La tabla principal presenta una fila por empleado:

- nombre;
- Payroll ID;
- número de posibles Meal Violations;
- razón principal;
- estado de validación;
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

La aplicación utiliza siete secciones:

1. **Meal Violations** — resumen por empleado, razones y detalle.
2. **Meals probables** — gaps de al menos 30 minutos sin evidencia suficiente de break.
3. **Segundo meal** — cola separada para jornadas mayores a 10 horas.
4. **Punch Review** — marcaciones accionables; los marcadores estructurales de break no se cuentan como error.
5. **Controles pendientes** — resumen por jornadas únicas, no por filas repetidas de control.
6. **Ajustes** — ajustes manuales que cambiaron el resultado y detalle técnico.
7. **Más detalles** — turnos, meals detectados, descargas y administración.

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

Los Secrets de Oracle deben permanecer en Streamlit Cloud, nunca en GitHub. La aplicación usa exclusivamente la cuenta **Business Intelligence API**:

```toml
[oracle_bi]
auth_server = "https://YOUR-AUTHENTICATION-SERVER"
application_server = "https://YOUR-APPLICATION-SERVER"
org_identifier = "YOUR_ENTERPRISE_SHORT_NAME"
client_id = "YOUR_BI_API_CLIENT_ID"
username = "YOUR_BI_API_ACCOUNT"
password = "YOUR_ROTATED_BI_PASSWORD"
application_name = "Meal Compliance Dashboard"
timeout_seconds = 45
verify_ssl = true
```

Las credenciales de **Labor Management** son distintas y no son necesarias para ejecutar el dashboard. No deben colocarse dentro de `[oracle_bi]`. El proyecto conserva un ejemplo separado en `.streamlit/labor_secrets.toml.example` únicamente para diagnósticos externos.

Para desarrollo local también se admite un archivo separado:

```text
.streamlit/bi_secrets.toml
```

La aplicación lo carga automáticamente cuando no existe `.streamlit/secrets.toml`. En Streamlit Community Cloud se debe usar el editor normal de Secrets con la sección `[oracle_bi]`.

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

La versión 3.7 incluye **68 pruebas automatizadas** para el motor, los IDs de caso y la separación de credenciales BI/Labor. Se agregaron pruebas para conservar hallazgos bajo controles estrictos y para distinguir una respuesta Oracle válida con cero timecards de una respuesta realmente faltante.

## Alcance

La aplicación identifica presuntas violaciones a partir de la información disponible en MICROS. Un meal compatible por timestamps no demuestra por sí solo que fue duty-free. Los waivers, clasificaciones y acuerdos requieren evidencia operativa o documental.


## Correcciones anteriores

- Conserva cada hallazgo por marcación en `bundle.candidates`.
- Mantiene `bundle.violations` como el subconjunto con controles completos.
- Corrige la cobertura API: una respuesta exitosa con cero timecards ya no se marca como ausencia de respuesta.
- El dashboard muestra posibles Meal Violations y su estado, en lugar de mostrar cero de forma engañosa.

## Cambios 3.7 — auditoría accionable

- Muestra todas las ubicaciones seleccionadas, incluso cuando Oracle responde con cero timecards.
- Distingue una respuesta válida sin actividad, cobertura parcial y ausencia de respuesta API.
- Calcula **Candidate exposure** sin confundirla con premium validado o pagadero.
- Los marcadores de break de duración cero con status `On Break` se conservan como evidencia y dejan de contarse como Punch Errors.
- Separa timecards actualmente abiertos de registros históricos con Clock Out timestamp pero status ausente.
- Resume controles por jornadas únicas para evitar inflar la métrica de revisiones.
- Agrega colas específicas para meals probables y segundo meal.
- El snapshot completo ahora incluye cobertura, data quality, meals, punch reviews y resúmenes por ubicación.
- Agrega un resumen ejecutivo JSON anonimizado sin nombres, Payroll IDs, pay rates individuales ni timecards crudos.
- La aplicación conserva `[oracle_bi]` como sección canónica y mantiene Labor como integración opcional separada.
