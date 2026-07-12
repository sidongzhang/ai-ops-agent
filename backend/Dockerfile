FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/aiops:/opt/aiops/backend

WORKDIR /opt/aiops
COPY backend /opt/aiops/backend
COPY shared /opt/aiops/shared
RUN pip install --no-cache-dir /opt/aiops/backend

WORKDIR /opt/aiops/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
