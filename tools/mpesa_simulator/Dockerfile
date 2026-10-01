FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim.py .
EXPOSE 8099
CMD ["uvicorn", "sim:app", "--host", "0.0.0.0", "--port", "8099"]
