import sqlite3
import os

DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'plexscanner.db')

def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Create TV Shows table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tv_shows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            tmdb_id TEXT,
            poster_url TEXT,
            overview TEXT,
            first_air_date TEXT,
            status TEXT,
            series_status TEXT,
            number_of_seasons INTEGER,
            number_of_episodes INTEGER,
            genres TEXT,
            vote_average REAL,
            networks TEXT,
            last_updated TEXT
        )
    ''')

    # Create Movies table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            tmdb_id TEXT,
            poster_url TEXT,
            overview TEXT,
            release_date TEXT,
            studio TEXT,
            last_updated TEXT
        )
    ''')

    # Create Seasons table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tv_show_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            name TEXT,
            overview TEXT,
            poster_path TEXT,
            air_date TEXT,
            FOREIGN KEY (tv_show_id) REFERENCES tv_shows(id),
            UNIQUE(tv_show_id, season_number)
        )
    ''')

    # Create Episodes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            name TEXT,
            air_date TEXT,
            overview TEXT,
            still_path TEXT,
            exists_in_plex BOOLEAN,
            resolution TEXT,
            file_path TEXT,
            FOREIGN KEY (season_id) REFERENCES seasons(id),
            UNIQUE(season_id, episode_number)
        )
    ''')

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully!")
