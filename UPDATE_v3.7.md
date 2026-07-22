# Actualización 3.7.0 — auditoría accionable y cobertura completa

## Correcciones principales

- Todas las ubicaciones seleccionadas permanecen visibles aunque tengan cero timecards.
- Se distingue entre respuesta API válida sin actividad, cobertura parcial y ausencia de respuesta.
- Se agrega `candidate_estimated_premium` como exposición preliminar independiente de `estimated_premium` y `verified_premium`.
- Los timecards de duración cero con `clock_out_status=66` se clasifican como marcadores estructurales de break y no como Punch Errors.
- Los registros con Clock Out timestamp y status ausente se clasifican como revisión histórica, no como timecards abiertos.
- Los Punch Reviews incluyen tipo y detalle.
- Las revisiones se resumen por jornadas únicas y categorías accionables.
- Se agregan colas para meals probables y segundo meal.
- El snapshot sube a schema 1.1 e incluye cobertura, calidad de datos, reconciliación, meals y punch reviews.
- Se agrega un resumen ejecutivo anonimizado.

## Validación con el audit general de 06/27/2026–07/10/2026

- 2,941 timecards y 1,651 workdays conservados.
- 109 candidatos conservados.
- Candidate exposure: $2,148.90.
- 102 marcadores estructurales de break reclasificados fuera de Punch Errors.
- 50 workdays con Punch Review accionable.
- 39 timecards completados con Clock Out status no disponible.
- 0 timecards actualmente abiertos.
- 68 pruebas automatizadas aprobadas.
