FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure entrypoint is executable
RUN chmod +x docker-entrypoint.sh

# Set default env vars
ENV PLEX_URL="http://localhost:32400" \
    PLEX_TOKEN="" \
    PLEX_LIBRARY="TV Shows" \
    TMDB_API_KEY="" \
    APP_PORT=8080 \
    APP_DEBUG=false \
    APP_HOST="0.0.0.0" \
    APP_SCAN_RESULTS_PATH="scan_results.json"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "main.py"]
