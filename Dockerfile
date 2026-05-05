FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .
COPY database/ ./database/
COPY .env .env

EXPOSE 8000

CMD ["python", "main.py"]
