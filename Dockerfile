FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set up entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Default environment variables (can be overridden in docker-compose)
ENV PLEX_URL="http://localhost:32400" \
    PLEX_TOKEN="" \
    PLEX_LIBRARY="TV Shows" \
    TMDB_API_KEY="" \
    APP_PORT=8080 \
    APP_DEBUG=false \
    APP_HOST="0.0.0.0" \
    APP_SCAN_RESULTS_PATH="scan_results.json"

# Entrypoint generates config.json and starts the app
ENTRYPOINT ["docker-entrypoint.sh"]

# Command to run the application
CMD ["python", "main.py"]
