FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY notify ./notify
ENV NOTIFY_DB_PATH=/data/notify.db
RUN mkdir -p /data
EXPOSE 8070
CMD ["uvicorn", "notify.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8070", "--proxy-headers", "--forwarded-allow-ips", "*"]
