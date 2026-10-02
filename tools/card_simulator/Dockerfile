FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim.py .
EXPOSE 8098
CMD ["uvicorn", "sim:app", "--host", "0.0.0.0", "--port", "8098", "--proxy-headers", "--forwarded-allow-ips", "*"]
