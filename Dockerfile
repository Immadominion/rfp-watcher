FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is the mount point for Railway's persistent volume
RUN mkdir -p /data

CMD ["python", "main.py"]
