FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rider ./rider
EXPOSE 8060
CMD ["uvicorn", "rider.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8060", "--proxy-headers", "--forwarded-allow-ips", "*"]
