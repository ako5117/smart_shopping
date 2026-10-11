FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY customers ./customers
ENV CUSTOMERS_DB_PATH=/data/customers.db
RUN mkdir -p /data
EXPOSE 8090
CMD ["uvicorn", "customers.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]
