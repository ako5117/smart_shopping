FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scan_and_go ./scan_and_go
EXPOSE 8030
CMD ["uvicorn", "scan_and_go.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8030", "--proxy-headers", "--forwarded-allow-ips", "*"]
