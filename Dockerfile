FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Environment variables
ENV PORT=5600
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 5600

CMD ["python", "server.py"]
