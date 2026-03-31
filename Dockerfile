FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 1 worker x 8 threads = 8 concurrent requests capacity (suficiente para el volumen actual)
# FIX D1: 1 solo worker garantiza que el in-memory lock _calls_in_progress sea compartido
# por todos los threads — previene duplicados de ARIA entre realtime webhook + polling.
# Con 4 workers, cada proceso tiene su propio set en memoria y el lock no funciona entre workers.
# gthread worker class handles I/O-bound workloads (GHL API calls) efficiently
# timeout 60s covers the longest GHL operations (get_contact + create_booking chains)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "60", "--worker-class", "gthread", "app:app"]
