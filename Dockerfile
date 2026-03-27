FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 4 workers x 4 threads = 16 concurrent requests capacity
# gthread worker class handles I/O-bound workloads (GHL API calls) efficiently
# timeout 60s covers the longest GHL operations (get_contact + create_booking chains)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--threads", "4", "--timeout", "60", "--worker-class", "gthread", "app:app"]
