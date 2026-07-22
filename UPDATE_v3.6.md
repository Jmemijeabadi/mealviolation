# Actualización 3.6.0 — BI-first production

## Objetivo

Consolidar la aplicación alrededor de la fuente que ya fue validada: Oracle MICROS Simphony Business Intelligence API. Labor Management deja de bloquear el funcionamiento del auditor.

## Separación de credenciales

- Streamlit usa la sección `[oracle_bi]`.
- En desarrollo local puede usar `.streamlit/bi_secrets.toml` separado.
- `[oracle]` se acepta temporalmente para despliegues existentes.
- `[oracle_labor]` nunca se interpreta como una cuenta BI.
- Las credenciales de Labor se mantienen en un archivo separado solo para diagnósticos.

## Flujo del auditor

- Cada hallazgo recibe un `Case ID` determinista.
- El Case ID no contiene directamente Payroll ID ni employee key.
- El auditor puede marcar cada caso como:
  - Pendiente de revisión.
  - Sustentado por los registros.
  - No sustentado por los registros.
  - Requiere evidencia adicional.
- Se pueden agregar notas y exportar la bitácora completa.

## Cobertura y controles

La interfaz distingue datos recuperados directamente de BI de controles administrativos:

- Timecards, empleados y ajustes.
- Cobertura de nombres y Payroll ID.
- Disponibilidad de `payRt` como base de estimación.
- Workday legal verificado.
- Clasificación exento/no exento.
- Waivers y acuerdos on-duty.

`payRt` se presenta como base de estimación, no como regular rate legal definitivo.

## Alcance

La aplicación identifica patrones y candidatos de meal compliance. Una decisión del auditor documenta la revisión operativa, pero no sustituye la validación de HR, Payroll o Legal.
