FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shelf ./shelf
COPY config.example.json ./config.example.json
# The real service (python -m shelf.app) needs an MQTT broker and shelf nodes; this image's default
# is the live simulator, used by the demo profile in docker-compose.yml.
CMD ["python", "-m", "shelf.simulator", "--config", "config.example.json", "--live", "/data/shelf_events.jsonl"]
