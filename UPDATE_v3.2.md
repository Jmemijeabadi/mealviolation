# Actualización a v3.2.0

Reemplaza o agrega estos archivos en la raíz del repositorio:

```text
app.py
README.md
UPDATE_v3.2.md
assets/broken_yolk_logo.png
compliance/audit.py
compliance/reporting.py
compliance/normalize.py
.streamlit/config.toml
tests/test_audit.py
tests/test_reporting.py
tests/test_normalize.py
```

No reemplaces ni subas `.streamlit/secrets.toml`. Los Secrets actuales de Streamlit permanecen igual.

Después del commit y push a `main`, Streamlit hará el redeploy automáticamente. La barra lateral debe mostrar **Versión 3.2.0**.
