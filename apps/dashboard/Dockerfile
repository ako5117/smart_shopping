# Build context is the repo root, so the shelf catalogue can be copied in.
FROM python:3.11-slim
WORKDIR /app
COPY apps/dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/dashboard/dashboard ./dashboard
COPY services/shelf/config.example.json ./shelf-catalogue.json
ENV SHELF_CONFIG_PATH=/app/shelf-catalogue.json \
    SHELF_EVENTS_PATH=/data/shelf_events.jsonl
RUN mkdir -p /data
EXPOSE 8020
CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8020", "--proxy-headers", "--forwarded-allow-ips", "*"]
