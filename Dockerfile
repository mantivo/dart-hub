FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 5000

# If you use DB_PATH env var in code, great; otherwise it will use default.
CMD ["python", "darts_hub.py"]
