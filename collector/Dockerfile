FROM python:3.12-slim

WORKDIR /app

# Build from the repository root:
# docker build -f collector/Dockerfile -t aiops-collector:local .
COPY collector/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy collector and shared modules
COPY collector/ ./collector/
COPY shared/ ./shared/

ENV PLATFORM_URL=""
ENV COLLECTOR_KEY=""
ENV COLLECTOR_INTERVAL="30"
ENV COLLECTOR_WS="true"

CMD python collector/run.py
