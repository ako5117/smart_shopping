FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dispatch ./dispatch
ENV DISPATCH_DB_PATH=/data/dispatch.db
RUN mkdir -p /data
EXPOSE 8050
CMD ["uvicorn", "dispatch.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8050", "--proxy-headers", "--forwarded-allow-ips", "*"]
