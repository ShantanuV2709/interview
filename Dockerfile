FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure the startup script is executable
RUN chmod +x run.sh

# Port 3000: HTTP Proxy (start.py)
# Port 3002: WebSocket Logic Server (ws_server.py)
EXPOSE 3000 3002

CMD ["./run.sh"]
