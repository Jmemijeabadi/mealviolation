# Actualización a v3.3.0

## Reemplazar o agregar

- `app.py`
- `requirements.txt`
- `README.md`
- `RELEASE_CHECKLIST.md`
- `oracle_bi/client.py`
- `compliance/models.py`
- `compliance/normalize.py`
- `compliance/engine.py`
- `compliance/audit.py`
- `compliance/reporting.py`
- `compliance/validation.py`
- `compliance/snapshot.py`
- `tests/`
- nuevos CSV de ejemplo en `demo/`

No cambies los Secrets de Streamlit. Ningún paquete contiene credenciales reales.

## Commit sugerido

```text
Upgrade to v3.3 with enterprise workday controls and audit safeguards
```

Después del push, Streamlit debe mostrar **Versión 3.3.0** en la barra lateral.

## Configuración inicial obligatoria

Para resultados finales, carga:

1. clasificación/waivers/acuerdos por empleado;
2. workday legal por ubicación;
3. regular rate por empleado y periodo;
4. totales de control exportados de MICROS;
5. todas las ubicaciones autorizadas.

Sin estos controles, la aplicación conserva resultados como revisión o bloquea conclusiones automáticas.
