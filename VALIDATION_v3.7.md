# Validación de regresión 3.7.0

Fuente de validación: audit general de ocho ubicaciones, periodo 06/27/2026–07/10/2026.

## Resultados conservados

- 2,941 timecards.
- 1,651 legal workdays.
- 208 empleados.
- 109 candidatos de meal compliance.
- 45 empleados con candidatos.
- Candidate exposure: $2,148.90.
- 0 premiums validados o verificados, porque los controles administrativos siguen pendientes.

## Reclasificación de punches

- 102 marcadores de duración cero con `clock_out_status=66` se conservan como evidencia de break y no se cuentan como Punch Errors.
- 50 workdays mantienen Punch Review accionable.
- 62 categorías de punch review fueron generadas:
  - 39 Clock Out timestamps con status histórico ausente.
  - 23 marcaciones de duración cero que no califican como marcador estructural de break.
- 0 timecards estaban actualmente abiertos.

## Calidad

- 68 pruebas automatizadas aprobadas.
- Todos los módulos Python compilaron correctamente.
- No se incluyeron credenciales, tokens ni datos reales de empleados en el paquete.

## Limitación del entorno de build

El servidor Streamlit no se inició dentro del entorno de empaquetado porque el ejecutable `streamlit` no estaba instalado. El paquete conserva `streamlit` en `requirements.txt`; la validación de arranque debe realizarse al desplegar en Streamlit Community Cloud.
