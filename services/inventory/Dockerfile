FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY inventory ./inventory
ENV INVENTORY_DB_PATH=/data/inventory.db
RUN mkdir -p /data
EXPOSE 8010
CMD ["uvicorn", "inventory.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips", "*"]
