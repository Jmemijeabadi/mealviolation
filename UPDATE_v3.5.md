# Actualización 3.5.0

## Problema corregido

La versión estricta eliminaba de la vista los patrones de Meal Violation cuando faltaban controles administrativos. Esto hacía que el dashboard mostrara `0 Meal Violations` aunque los timestamps sí contuvieran hallazgos.

También se corrigió la cobertura de Oracle: las respuestas válidas con cero timecards ahora se reconocen como respuestas presentes mediante `_requestedBusDt`.

## Nuevo comportamiento

- Todos los patrones detectados se muestran como **Posibles Meal Violations**.
- Cada hallazgo indica si está **Detectado por marcación** o **Pendiente de validación**.
- Los hallazgos pendientes no se cuentan como conclusiones finales, pero tampoco se ocultan.
- La tabla principal conserva empleado, cantidad, razón, fechas y ubicación.

## Validación con el snapshot recibido

- 2,941 timecards.
- 1,651 jornadas.
- 109 posibles Meal Violations.
- 45 empleados señalados.
- 54 primeros meals faltantes.
- 49 primeros meals tardíos.
- 6 primeros meals menores de 30 minutos.
- 125 Punch Errors.

## Archivos modificados

- `app.py`
- `compliance/engine.py`
- `compliance/models.py`
- `compliance/reporting.py`
- `compliance/snapshot.py`
- `compliance/validation.py`
- `tests/test_engine.py`
- `tests/test_workday_and_validation.py`
- `README.md`
