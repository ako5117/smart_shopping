FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storefront ./storefront
EXPOSE 8040
CMD ["uvicorn", "storefront.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8040", "--proxy-headers", "--forwarded-allow-ips", "*"]
