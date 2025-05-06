# Plex Episode Tracker

A Docker-based Flask application that tracks missing episodes in your Plex TV Show library by comparing with TMDB data.

## Features

- Scans your Plex TV Show library and identifies missing episodes
- Shows which series are complete or incomplete
- Provides details about missing episodes
- Displays upcoming episodes
- Tracks series status (ongoing, ended, etc.)

## Installation

### Prerequisites

- Docker and Docker Compose installed
- A Plex server with TV Shows library
- Plex token for API access

### Quick Start

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/plex-episode-tracker.git
   cd plex-episode-tracker
   ```

2. Edit the `docker-compose.yml` file to update your Plex server details:
   ```yaml
   environment:
     - PLEX_URL=http://your-plex-server:32400
     - PLEX_TOKEN=your-plex-token
     - PLEX_LIBRARY=TV Shows
   ```

3. Start the application with Docker Compose:
   ```
   docker-compose up -d
   ```

4. Access the application at `http://localhost:8080`

### Environment Variables

All configuration is handled through environment variables in the docker-compose.yml file:

| Variable | Description | Default |
|----------|-------------|---------|
| PLEX_URL | URL to your Plex server | http://localhost:32400 |
| PLEX_TOKEN | Your Plex authentication token | (required) |
| PLEX_LIBRARY | Name of your TV Shows library in Plex | TV Shows |
| TMDB_API_KEY | Your TMDB API key | (required) |
| APP_PORT | Port to run the web interface on | 8080 |
| APP_DEBUG | Enable/disable Flask debug mode | false |
| APP_HOST | Host to bind the web interface to | 0.0.0.0 |
| APP_SCAN_RESULTS_PATH | Path to store scan results | scan_results.json |

## Usage

1. Open the web interface at `http://localhost:8080`
2. Click "Scan Library" to begin scanning your Plex library
3. View missing episodes for each show
4. Click on a show to see detailed information about missing episodes

## Updating

To update the application:

```
cd plex-episode-tracker
git pull
docker-compose down
docker-compose up -d --build
```

## Troubleshooting

### Connection Issues

If you're having trouble connecting to your Plex server:

1. **Host Network Mode**: For local Plex servers, try using the host network mode by modifying docker-compose.yml:
   ```yaml
   services:
     plex-episode-tracker:
       # ... other settings
       network_mode: "host"
       # Remove the ports section when using host network
   ```

2. **IP Address**: Ensure you're using the correct IP address for your Plex server. For local installs, you might need to use the Docker host's IP instead of localhost.

### Volume Permissions

If you encounter permission issues with the scan_results.json file:

```bash
# Create the file with the correct permissions before starting the container
touch scan_results.json
chmod 666 scan_results.json
```

## Security Notes

- Your Plex token grants access to your Plex server. Keep your docker-compose.yml file secure.
- Consider using Docker secrets for sensitive information in production environments.
- Make sure your GitHub repository is private if you're pushing the docker-compose.yml with your credentials.

## License

MIT
