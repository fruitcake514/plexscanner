#!/bin/sh
set -e

# Generate config.json from environment variables
cat > /app/config.json << EOF
{
  "plex": {
    "url": "${PLEX_URL}",
    "token": "${PLEX_TOKEN}",
    "library_section": "${PLEX_LIBRARY}"
  },
  "tmdb": {
    "api_key": "${TMDB_API_KEY}"
  },
  "app": {
    "port": ${APP_PORT},
    "debug": ${APP_DEBUG},
    "host": "${APP_HOST}",
    "scan_results_path": "${APP_SCAN_RESULTS_PATH}"
  }
}
EOF

# Initialize scan_results.json if it doesn't exist
if [ ! -s "${APP_SCAN_RESULTS_PATH}" ]; then
    echo "{}" > "${APP_SCAN_RESULTS_PATH}"
    echo "Created empty scan_results.json file"
fi

# Execute CMD
exec "$@"
