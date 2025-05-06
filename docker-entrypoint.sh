#!/bin/sh
set -e

echo "Starting Plex Scanner..."
echo "PLEX_URL=$PLEX_URL"
echo "PLEX_LIBRARY=$PLEX_LIBRARY"
echo "TMDB_API_KEY set: ${TMDB_API_KEY:+yes}"

# Optionally generate config.json here if needed
# python generate_config.py or similar

exec "$@"
