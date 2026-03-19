FROM python:3.11-slim

WORKDIR /app

# Installing only what's needed, no cache to keep image small
RUN pip install --no-cache-dir flask prometheus_client gunicorn

COPY sensor_service.py .

# gunicorn instead of Flask dev server: stable threading under concurrent scrapes
CMD ["gunicorn", "--workers=1", "--threads=2", "--bind=0.0.0.0:8000", "sensor_service:app"]
