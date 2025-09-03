# PlexiTrack

![PlexiTrack Logo](https://via.placeholder.com/300x150.png?text=PlexiTrack)

PlexiTrack is a web-based tool to scan your Plex media library, track missing episodes for your TV shows, and manage your movie collections. It integrates with Prowlarr and qBittorrent to help you find and download missing content.

## Key Features

-   **TV Show Tracking**: Scans your Plex TV show library and shows you which episodes are missing based on TMDb.
-   **Movie Collection Management**: Identifies movies in your library that are part of a collection and shows you which movies from that collection are missing.
-   **Prowlarr Integration**: Search for missing content on your Prowlarr indexers directly from the web interface.
-   **qBittorrent Integration**: Send downloads directly to qBittorrent with the correct category.
-   **Dockerized**: Easy to set up and run with Docker Compose.
-   **Web Interface**: Modern and easy-to-use web interface to view your library status and manage downloads.

## Getting Started

### Prerequisites

-   Docker and Docker Compose installed on your system.
-   A running Plex Media Server.
-   A running Prowlarr instance.
-   A running qBittorrent instance.

### Installation

1.  **Clone the repository (or download the source code):**
    ```sh
    git clone https://github.com/your-username/plexitrack.git
    cd plexitrack
    ```

2.  **Configure the application:**
    -   Rename `config.json.example` to `config.json` (or create a new `config.json` file).
    -   Edit `config.json` with your details for Plex, TMDb, Prowlarr, and qBittorrent.

3.  **Build and run the application with Docker Compose:**
    ```sh
    docker-compose up --build -d
    ```

4.  **Access the web interface:**
    -   Open your web browser and go to `http://localhost:5555` (or the port you configured).

## Configuration

The application is configured via the `config.json` file. Here is an overview of the configuration options:

-   **`plex`**: Your Plex server URL, token, and library names.
-   **`tmdb`**: Your TMDb API key.
-   **`prowlarr`**: Your Prowlarr URL, API key, and category mappings.
-   **`qbittorrent`**: Your qBittorrent host, port, username, password, and category mappings.
-   **`download_client`**: Settings for filtering search results (quality, codec, seeders).
-   **`app`**: Application settings (host, port, debug mode).

## Usage

-   **TV Shows Page**: Shows the status of your TV shows. Click the "Scan" button to scan your library.
-   **Movies Page**: Shows your movie collections and missing movies. Click the "Scan" button to scan your library.
-   **Search Page**: Search for torrents using Prowlarr.
-   **Downloads Page**: View the status of your downloads in qBittorrent.
-   **Settings Page**: Configure the application settings from the UI.

## Screenshots

*(Placeholder for screenshots of the application)*

| TV Shows Page | Movie Collections Page |
| :-----------: | :--------------------: |
| ![TV Shows](https://via.placeholder.com/400x250.png?text=TV+Shows+Page) | ![Movie Collections](https://via.placeholder.com/400x250.png?text=Movie+Collections) |

## Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.