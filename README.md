# Meal Violations Dashboard — Oracle MICROS Simphony

Versión **3.3.0** del dashboard interno de The Broken Yolk Cafe para auditar marcaciones de meal periods en California desde Oracle MICROS Simphony Business Intelligence API.

> La aplicación identifica **presuntas violaciones** y casos de revisión. No sustituye la validación de Payroll, Recursos Humanos o asesoría laboral. “Cumplimiento por marcación” significa que los timestamps y status visibles son compatibles con un meal; no prueba por sí solo que el periodo fue duty-free.

## Qué cambia en la versión 3.3

### Alcance empresarial y workday legal

- Selecciona todas las ubicaciones autorizadas de forma predeterminada.
- Consolida cada empleado por **empleado + workday legal**, aunque trabaje en varias sucursales.
- Bloquea conclusiones automáticas cuando se analiza solo una parte del alcance autorizado.
- Permite cargar la hora fija de inicio del workday por ubicación, con vigencia, fuente y responsable de validación.
- Divide de forma controlada un timecard que cruza el límite de un workday.
- Compara el `business date` de Oracle contra el workday legal calculado.

### Clasificación, waivers y acuerdos

- Clasificación verificada `NON_EXEMPT`, `EXEMPT` o `UNKNOWN` por empleado y vigencia.
- Exclusión de empleados exentos únicamente cuando existe un registro activo verificado.
- Waiver del primer meal y del segundo meal por fecha.
- Registro de acuerdos on-duty y referencia documental.
- Modo estricto predeterminado: una clasificación desconocida bloquea una conclusión automática.

### Primer y segundo meal

- Primer meal después de más de 5 horas trabajadas.
- Waiver del primer meal únicamente dentro del límite configurado de 6 horas.
- Segundo meal después de más de 10 horas trabajadas.
- Waiver del segundo meal únicamente dentro del límite configurado de 12 horas y cuando el primero no fue renunciado.
- Detección de meal faltante, tardío o corto.
- Separación entre:
  - meal confirmado por marcación;
  - meal probable por timestamps;
  - paid/on-duty break;
  - punch error;
  - resultado inconcluso.

### Auditoría exacta de ajustes

- La consulta usa `includeAdjustments=true`.
- Reconstruye el estado **antes y después** de cada ajuste usando los valores `prev*` de Oracle.
- Ordena cadenas con varios ajustes desde el estado original hasta el estado final.
- Muestra manager, fecha UTC, razón, campos modificados y diferencias de Clock In/Out.
- Reanaliza el workday antes y después de cada ajuste.
- Señala si el ajuste cambió una presunta violación o un caso de revisión.
- No considera un arreglo `adjustments` ausente como error cuando la solicitud de ajustes está verificada; Oracle puede omitirlo cuando el timecard no tiene ajustes.

### Integridad y reconciliación

- Matriz de cobertura por ubicación y business date.
- Bloqueo cuando falta una respuesta del alcance solicitado.
- Detección de IDs duplicados con estados contradictorios.
- Validación de nombres, Payroll ID, códigos de Oracle y timecards abiertos.
- Reconciliación opcional contra totales de MICROS:
  - timecards;
  - empleados;
  - horas;
  - timecards ajustados.
- Snapshot JSON para comparar una consulta posterior contra una ejecución anterior.
- Detección de timecards agregados, eliminados o modificados y cambios de resultado.

### Premium

- Cuenta un workday potencialmente sujeto a premium una sola vez, aunque existan varios incidentes de meal.
- Usa un `regular rate` verificado cuando se carga desde Payroll.
- Cuando no existe, muestra explícitamente un **proxy de base pay rate — no final**.
- Mantiene separados el premium estimado por el motor y `premPay` informado por Oracle.

## Estructura

```text
app.py
oracle_probe.py
requirements.txt
README.md
UPDATE_v3.3.md
RELEASE_CHECKLIST.md
assets/
  broken_yolk_logo.png
oracle_bi/
  client.py
compliance/
  models.py
  normalize.py
  engine.py
  audit.py
  reporting.py
  validation.py
  snapshot.py
tests/
demo/
.streamlit/
  config.toml
  secrets.toml.example
```

## Despliegue en Streamlit Community Cloud

```text
Repository: Jmemijeabadi/mealviolation
Branch: main
Main file path: app.py
```

En **App settings → Secrets**, agrega la configuración real de Oracle. No subas `.streamlit/secrets.toml` a GitHub.

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

## Archivos de control

La barra lateral permite descargar plantillas. También hay ejemplos en `demo/`.

### `employee_compliance_policy.csv`

```csv
employee_key,classification,first_meal_waiver,second_meal_waiver,on_duty_meal_agreement,effective_date,expiration_date,document_reference,verified_by,notes
12345,NON_EXEMPT,false,false,false,2026-01-01,,HRIS-123,HR Manager,
```

El `employee_key` debe corresponder preferentemente al Payroll ID normalizado.

### `workday_configuration.csv`

```csv
location_ref,workday_start,timezone,effective_date,expiration_date,verified_by,source
BYC304,04:00,America/Los_Angeles,2026-01-01,,Payroll Manager,Payroll policy
```

### `verified_regular_rate.csv`

```csv
employee_key,regular_rate,effective_date,expiration_date,source,verified_by
12345,24.75,2026-07-01,2026-07-15,Payroll calculation,Payroll Manager
```

### `micros_control_totals.csv`

```csv
location_ref,business_date,timecards,employees,worked_hours,adjusted_timecards
BYC304,2026-07-20,42,18,126.50,3
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
python -m pytest -q
```

La versión 3.3 incluye **48 pruebas automatizadas** para autenticación/cliente, normalización, empleados, primer y segundo meal, waivers, clasificación, multi-location, workday, cobertura, reconciliación, regular rate, ajustes secuenciales, reanálisis y snapshots.

## Flujo recomendado para producción

1. Seleccionar todas las ubicaciones autorizadas.
2. Cargar clasificación, waivers y acuerdos vigentes.
3. Cargar el workday legal verificado por ubicación.
4. Cargar regular rates verificados para el periodo.
5. Consultar Oracle con ajustes incluidos.
6. Revisar **Calidad de datos** y resolver controles críticos.
7. Reconciliar contra MICROS.
8. Revisar presuntas violaciones y casos manuales.
9. Revisar ajustes que cambiaron el resultado.
10. Descargar el snapshot del cierre y conservarlo fuera de Streamlit.

## Límites que siguen requiriendo evidencia humana

- MICROS no demuestra por sí solo que un meal fue duty-free.
- Un waiver debe estar vigente, ser aplicable y estar respaldado documentalmente.
- Un acuerdo on-duty requiere validación adicional de sus condiciones.
- La cifra de premium solo es final cuando Payroll suministra el regular rate correcto.
- Streamlit Community Cloud no debe considerarse almacenamiento histórico durable; conserva los snapshots en un repositorio seguro autorizado o en otro sistema corporativo.
