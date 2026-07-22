# Meal Violations Dashboard 3.8.1

## Aviso automático y lectura del Time Card Detail de MICROS

Esta actualización conserva Oracle MICROS como fuente principal y mejora el fallback Excel.

### Comportamiento

1. La aplicación consulta Oracle para las sucursales seleccionadas.
2. Cuando Oracle devuelve cero timecards para una o más sucursales, la aplicación muestra:
   - que la auditoría está incompleta;
   - las sucursales que requieren Excel;
   - el periodo exacto que debe exportarse;
   - la instrucción de subir el reporte Time Card Detail y ejecutar nuevamente.
3. El aviso permanece visible mientras el alcance y las fechas no cambien.
4. El cargador acepta uno o varios archivos, normalmente uno por sucursal.
5. La estructura estándar `Time Card Detail` exportada de MICROS se reconoce automáticamente:
   - ubicación del encabezado;
   - sucursal;
   - periodo;
   - empleado y Payroll ID;
   - job code;
   - Clock In / Clock Out;
   - Clock Out Status;
   - horas y pago regular;
   - pay rate derivado para estimación.
6. Oracle y Excel nunca se mezclan para una misma sucursal.
7. Una sucursal solo se marca como cubierta por Excel cuando el archivo produjo filas válidas para esa sucursal y el periodo seleccionado.
8. Si todavía falta el Excel de alguna sucursal, la aplicación mantiene el aviso de auditoría incompleta.

## Archivos a reemplazar

- `app.py`
- `requirements.txt`
- `compliance/excel_import.py`
- `compliance/normalize.py`
- `compliance/validation.py`
- `tests/test_excel_fallback.py`

## Publicación

```powershell
git add app.py requirements.txt compliance/excel_import.py compliance/normalize.py compliance/validation.py tests/test_excel_fallback.py UPDATE_v3.8.1.md
git commit -m "Add required Excel alert and MICROS Time Card Detail autodetection"
git push origin main
```

## Validación

- 75 pruebas automatizadas aprobadas.
- Los tres formatos Time Card Detail proporcionados fueron reconocidos correctamente.
- Versión de la aplicación: `3.8.1`.
