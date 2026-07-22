# Checklist de liberación y validación

## Seguridad

- [ ] Repositorio privado.
- [ ] `.streamlit/secrets.toml` no aparece en GitHub.
- [ ] Contraseña/token expuestos anteriormente fueron rotados.
- [ ] Solo usuarios autorizados tienen acceso a la app.
- [ ] Snapshots y CSV con datos laborales se almacenan en un repositorio corporativo autorizado.

## Oracle

- [ ] Cuenta Business Intelligence API activa.
- [ ] Permiso `Employee Time Card Details and Pay Rates` activo.
- [ ] Consulta usa `includeAdjustments=true`.
- [ ] Todas las ubicaciones autorizadas están seleccionadas.
- [ ] Un día de API fue comparado registro por registro contra MICROS.

## Políticas y nómina

- [ ] Clasificación exento/no exento validada por HR.
- [ ] Waivers vigentes vinculados por Payroll ID.
- [ ] Acuerdos on-duty documentados.
- [ ] Workday fijo de 24 horas confirmado por ubicación.
- [ ] Regular rate del periodo suministrado por Payroll.

## Integridad

- [ ] No hay controles críticos abiertos.
- [ ] Nombres y Payroll IDs están resueltos.
- [ ] No hay códigos Oracle desconocidos.
- [ ] No hay respuestas faltantes por location/date.
- [ ] Totales API coinciden con los controles de MICROS.
- [ ] Timecards abiertos se excluyeron del cierre o se reconsultaron.

## Validación paralela

- [ ] Dos a cuatro periodos de nómina ejecutados en modo sombra.
- [ ] Muestra manual de casos límite revisada por HR/Payroll.
- [ ] Falsos positivos y falsos negativos documentados.
- [ ] Reglas aceptadas por el responsable de cumplimiento.
- [ ] Snapshot descargado y archivado en cada cierre.
