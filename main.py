from flask import Flask, render_template, request, jsonify, redirect, url_for
from plexapi.server import PlexServer
import requests
import os
import json
from datetime import datetime
import time
import threading
import random
from urllib.parse import unquote
from database import init_db, DATABASE_PATH
import sqlite3
import logging

from qbittorrentapi import Client
import re

app = Flask(__name__, static_folder='static')

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Custom Jinja2 filter for strptime
def datetime_strptime(value, format):
    return datetime.strptime(value, format)

app.jinja_env.filters['strptime'] = datetime_strptime
app.jinja_env.filters['tojson'] = json.dumps

# Initialize the database
init_db()

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_PATH, 'r') as config_file:
    CONFIG = json.load(config_file)

# Set paths
# JSON_PATH = os.path.join(os.path.dirname(__file__), CONFIG['app']['scan_results_path'])

# Global variables for scan status
SCAN_STATUS = {
    'in_progress': False,
    'progress': 0,
    'current_show': '',
    'total_shows': 0,
    'processed_shows': 0,
    'status_message': '',
    'start_time': None,
    'stop_requested': False,
    'partial_results': {}
}

MOVIE_SCAN_STATUS = {
    'in_progress': False,
    'progress': 0,
    'current_collection': '',
    'total_collections': 0,
    'processed_collections': 0,
    'status_message': '',
    'start_time': None,
    'stop_requested': False,
}

# === DATABASE FUNCTIONS ===
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def insert_tv_show(title, tmdb_id, poster_url, overview, first_air_date, status, series_status, number_of_seasons, number_of_episodes, genres, vote_average, networks):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO tv_shows (title, tmdb_id, poster_url, overview, first_air_date, status, series_status, number_of_seasons, number_of_episodes, genres, vote_average, networks, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, tmdb_id, poster_url, overview, first_air_date, status, series_status, number_of_seasons, number_of_episodes, json.dumps(genres), vote_average, json.dumps(networks), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    tv_show_id = cursor.lastrowid
    conn.close()
    return tv_show_id

def get_tv_show_by_title(title):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tv_shows WHERE title = ?", (title,))
    show = cursor.fetchone()
    conn.close()
    return show

def get_tv_show_by_tmdb_id(tmdb_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tv_shows WHERE tmdb_id = ?", (tmdb_id,))
    show = cursor.fetchone()
    conn.close()
    return show

def insert_season(tv_show_id, season_number, name, overview, poster_path, air_date):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO seasons (tv_show_id, season_number, name, overview, poster_path, air_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (tv_show_id, season_number, name, overview, poster_path, air_date))
    conn.commit()
    season_id = cursor.lastrowid
    conn.close()
    return season_id

def get_season_by_tv_show_id_and_number(tv_show_id, season_number):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM seasons WHERE tv_show_id = ? AND season_number = ?", (tv_show_id, season_number))
    season = cursor.fetchone()
    conn.close()
    return season

def insert_episode(season_id, episode_number, name, air_date, overview, still_path, exists_in_plex, resolution, file_path):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO episodes (season_id, episode_number, name, air_date, overview, still_path, exists_in_plex, resolution, file_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (season_id, episode_number, name, air_date, overview, still_path, exists_in_plex, resolution, file_path))
    conn.commit()
    conn.close()

def get_episodes_by_season_id(season_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM episodes WHERE season_id = ?", (season_id,))
    episodes = cursor.fetchall()
    conn.close()
    return episodes

def insert_movie(title, tmdb_id, poster_url, overview, release_date, studio, collection_tmdb_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO movies (title, tmdb_id, poster_url, overview, release_date, studio, collection_tmdb_id, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, tmdb_id, poster_url, overview, release_date, studio, collection_tmdb_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    movie_id = cursor.lastrowid
    conn.close()
    return movie_id

def get_movie_by_title(title):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM movies WHERE title = ?", (title,))
    movie = cursor.fetchone()
    conn.close()
    return movie

def insert_collection(name, tmdb_id, poster_url):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO collections (name, tmdb_id, poster_url) VALUES (?, ?, ?)", (name, tmdb_id, poster_url))
    conn.commit()
    collection_id = cursor.lastrowid
    conn.close()
    return collection_id

def get_collection_by_tmdb_id(tmdb_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM collections WHERE tmdb_id = ?", (tmdb_id,))
    collection = cursor.fetchone()
    conn.close()
    return collection

def insert_missing_movie(collection_id, title, tmdb_id, poster_url, release_date):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO missing_movies (collection_id, title, tmdb_id, poster_url, release_date) VALUES (?, ?, ?, ?, ?)", (collection_id, title, tmdb_id, poster_url, release_date))
    conn.commit()
    conn.close()

def get_missing_movies_by_collection_id(collection_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM missing_movies WHERE collection_id = ?", (collection_id,))
    movies = cursor.fetchall()
    conn.close()
    return movies

def get_all_collections():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM collections")
    collections = cursor.fetchall()
    conn.close()
    return collections

# === UTILITIES ===
def get_api_url(source, endpoint):
    if source == 'tmdb':
        return f'https://api.themoviedb.org/3/{endpoint}?api_key={CONFIG["tmdb"]["api_key"]}'
    return None

def get_tmdb_id(plex_show):
    title = plex_show.title
    guid = plex_show.guid or ''
    if 'tmdb' in guid:
        return guid.split('//')[1].split('?')[0]
    for guid_obj in plex_show.guids:
        if 'tmdb' in guid_obj.id.lower():
            return guid_obj.id.split('//')[1].split('?')[0]
    search_url = get_api_url('tmdb', f'search/tv?query={requests.utils.quote(title)}')
    if not search_url: return None
    try:
        r = requests.get(search_url)
        r.raise_for_status()
        results = r.json()['results']
        if results:
            return str(results[0]['id'])
    except:
        pass
    return None

def get_show_details(tmdb_id):
    """Get detailed information about a show from TMDb"""
    if not tmdb_id:
        return None
    
    url = get_api_url('tmdb', f'tv/{tmdb_id}')
    if not url: return None
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        
        # Extract relevant information
        details = {
            'poster_path': f'https://image.tmdb.org/t/p/w500{data.get("poster_path")}' if data.get('poster_path') else None,
            'backdrop_path': f'https://image.tmdb.org/t/p/original{data.get("backdrop_path")}' if data.get('backdrop_path') else None,
            'overview': data.get('overview'),
            'first_air_date': data.get('first_air_date'),
            'status': data.get('status', 'Unknown'),
            'number_of_seasons': data.get('number_of_seasons', 0),
            'number_of_episodes': data.get('number_of_episodes', 0),
            'genres': [genre['name'] for genre in data.get('genres', [])],
            'vote_average': data.get('vote_average'),
            'networks': [network['name'] for network in data.get('networks', [])]
        }
        return details
    except Exception as e:
        print(f"Error fetching show details: {e}")
        return None

def get_show_poster(tmdb_id):
    if not tmdb_id:
        return None
    
    url = get_api_url('tmdb', f'tv/{tmdb_id}')
    if not url: return None
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        poster_path = data.get('poster_path')
        if poster_path:
            return f'https://image.tmdb.org/t/p/w500{poster_path}'
    except:
        pass
    return None

def get_series_status(tmdb_id):
    url = get_api_url('tmdb', f'tv/{tmdb_id}')
    if not url: return None
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        status = data.get('status', 'Unknown')
        if status in ["Ended", "Canceled"]:
            return "Ended"
        elif status == "Returning Series":
            return "Ongoing"
        else:
            last_air_date = data.get('last_air_date')
            if last_air_date:
                try:
                    last_date = datetime.strptime(last_air_date, '%Y-%m-%d')
                    # Set to start of day for today's date
                    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    if last_date >= today_start:
                        return "Ongoing"
                except:
                    pass
            return status
    except:
        return "Unknown"

def get_movie_tmdb_id(plex_movie):
    for guid in plex_movie.guids:
        if 'tmdb' in guid.id:
            return guid.id.split('//')[1]
    search_url = get_api_url('tmdb', f'search/movie?query={requests.utils.quote(plex_movie.title)}')
    if not search_url: return None
    try:
        r = requests.get(search_url)
        r.raise_for_status()
        results = r.json()['results']
        if results:
            return str(results[0]['id'])
    except:
        pass
    return None

def get_movie_details(tmdb_id):
    if not tmdb_id:
        return None
    url = get_api_url('tmdb', f'movie/{tmdb_id}')
    if not url: return None
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        studio = data['production_companies'][0]['name'] if data['production_companies'] else None
        return {
            'title': data.get('title'),
            'poster_path': f'https://image.tmdb.org/t/p/w500{data.get("poster_path")}' if data.get('poster_path') else None,
            'overview': data.get('overview'),
            'release_date': data.get('release_date'),
            'studio': studio,
            'belongs_to_collection': data.get('belongs_to_collection')
        }
    except:
        return None

def fetch_tmdb_episodes(tmdb_id):
    eps = set()
    try:
        show_info_url = get_api_url('tmdb', f'tv/{tmdb_id}')
        if not show_info_url: return eps
        show_info = requests.get(show_info_url).json()
        for season_num in range(1, show_info.get('number_of_seasons', 0) + 1):
            season_url = get_api_url('tmdb', f'tv/{tmdb_id}/season/{season_num}')
            if not season_url: continue
            season = requests.get(season_url).json()
            for ep in season.get('episodes', []):
                eps.add((season_num, ep['episode_number']))
    except:
        pass
    return eps

def get_existing_episodes(plex_show):
    existing = set()
    episode_details = {}
    
    for episode in plex_show.episodes():
        if episode.seasonNumber and episode.index:
            # Add to set of existing episodes
            existing.add((episode.seasonNumber, episode.index))
            
            # Get resolution
            resolution = "Unknown"
            if episode.media:
                for media in episode.media:
                    if media.videoResolution:
                        resolution = media.videoResolution
                        break
            
            # Store episode details
            if episode.seasonNumber not in episode_details:
                episode_details[episode.seasonNumber] = {}
            
            episode_details[episode.seasonNumber][episode.index] = {
                'title': episode.title,
                'resolution': resolution,
                'file': episode.locations[0] if episode.locations else None,
                'air_date': episode.originallyAvailableAt.strftime('%Y-%m-%d') if episode.originallyAvailableAt else None
            }
    
    return existing, episode_details

def get_season_details(tmdb_id, season_number):
    """Get details for a specific season including episodes"""
    if not tmdb_id or not season_number:
        return None
    
    url = get_api_url('tmdb', f'tv/{tmdb_id}/season/{season_number}')
    if not url: return None
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        
        # Process and return episodes
        episodes = []
        for ep in data.get('episodes', []):
            episodes.append({
                'episode_number': ep.get('episode_number'),
                'name': ep.get('name'),
                'air_date': ep.get('air_date'),
                'overview': ep.get('overview'),
                'still_path': f'https://image.tmdb.org/t/p/w300{ep.get("still_path")}' if ep.get('still_path') else None
            })
        
        return {
            'season_number': season_number,
            'name': data.get('name'),
            'overview': data.get('overview'),
            'poster_path': f'https://image.tmdb.org/t/p/w300{data.get("poster_path")}' if data.get('poster_path') else None,
            'air_date': data.get('air_date'),
            'episodes': episodes
        }
    except Exception as e:
        print(f"Error fetching season details: {e}")
        return None

def run_scan_thread():
    global SCAN_STATUS
    
    try:
        SCAN_STATUS['in_progress'] = True
        SCAN_STATUS['progress'] = 0
        SCAN_STATUS['status_message'] = 'Connecting to Plex server...'
        SCAN_STATUS['start_time'] = datetime.now()
        SCAN_STATUS['stop_requested'] = False
        
        app.logger.debug("Attempting to connect to Plex server...")
        plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
        app.logger.debug("Connected to Plex server.")
        
        SCAN_STATUS['status_message'] = 'Fetching TV Shows...'
        plex_shows = plex.library.section(CONFIG['plex']['library_section']).all()
        plex_show_titles = {s.title for s in plex_shows}

        # Get all show titles from the database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM tv_shows")
        db_show_titles = {row['title'] for row in cursor.fetchall()}
        conn.close()

        # Find and delete shows no longer in Plex
        shows_to_delete = db_show_titles - plex_show_titles
        if shows_to_delete:
            app.logger.debug(f"Deleting {len(shows_to_delete)} shows no longer in Plex: {shows_to_delete}")
            conn = get_db_connection()
            cursor = conn.cursor()
            for title in shows_to_delete:
                cursor.execute("SELECT id FROM tv_shows WHERE title = ?", (title,))
                show_id_row = cursor.fetchone()
                if show_id_row:
                    show_id = show_id_row['id']
                    # Delete episodes
                    cursor.execute("DELETE FROM episodes WHERE season_id IN (SELECT id FROM seasons WHERE tv_show_id = ?)", (show_id,))
                    # Delete seasons
                    cursor.execute("DELETE FROM seasons WHERE tv_show_id = ?", (show_id,))
                    # Delete show
                    cursor.execute("DELETE FROM tv_shows WHERE id = ?", (show_id,))
            conn.commit()
            conn.close()

        shows = plex_shows
        app.logger.debug(f"Fetched {len(shows)} TV shows from Plex.")
        
        # Load ignored shows
        ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
        ignored = []
        if os.path.exists(ignored_shows_path):
            with open(ignored_shows_path, 'r') as f:
                ignored = json.load(f)
        
        # Filter out ignored shows
        shows = [s for s in shows if s.title not in ignored]
        app.logger.debug(f"After filtering ignored shows, {len(shows)} remain.")

        SCAN_STATUS['total_shows'] = len(shows)
        SCAN_STATUS['processed_shows'] = 0
        
        app.logger.debug("Starting TV show processing loop.")
        for show in shows:
            app.logger.debug(f"Processing show: {show.title}")
            # Check if stop was requested
            if SCAN_STATUS['stop_requested']:
                SCAN_STATUS['status_message'] = 'Scan stopped by user.'
                app.logger.debug("Scan stop requested. Breaking loop.")
                break
                
            SCAN_STATUS['current_show'] = show.title
            SCAN_STATUS['status_message'] = f'Processing {show.title}...'
            
            
            show_id = None
            poster_url = None
            details = None
            series_status = 'Unknown'
            all_eps = set()

            SCAN_STATUS['status_message'] = f'Finding TMDb match for {show.title}...'
            show_id = get_tmdb_id(show)
            app.logger.debug(f"TMDb ID for {show.title}: {show_id}")
            if show_id:
                poster_url = get_show_poster(show_id)
                details = get_show_details(show_id)
                series_status = get_series_status(show_id)
                all_eps = fetch_tmdb_episodes(show_id)
                app.logger.debug(f"Fetched TMDB details for {show.title}.")

            if not show_id:
                app.logger.debug(f"No metadata ID found for {show.title}. Inserting with Unknown status.")
                # Insert into DB with unknown status
                insert_tv_show(show.title, None, None, None, None, 'Unknown', 'Unknown', 0, 0, [], 0.0, [])
                SCAN_STATUS['processed_shows'] += 1
                SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
                continue
            
            app.logger.debug(f"Processing {show.title} (ID: {show_id}) from tmdb")
            
            # Get series status
            SCAN_STATUS['status_message'] = f'Checking series status for {show.title}...'
            
            # Compare episodes
            SCAN_STATUS['status_message'] = f'Comparing episodes for {show.title}...'
            existing, existing_episode_details = get_existing_episodes(show)
            app.logger.debug(f"DEBUG: Compared episodes for {show.title}. Existing: {len(existing)}, All tmdb: {len(all_eps)}")
            
            # Filter out future episodes when calculating missing episodes
            aired_missing = []
            future_episodes = []
            
            # Process missing episodes
            for season_num, ep_num in sorted(list(all_eps - existing)):
                air_date = None
                has_aired = True
                
                season_details = get_season_details(show_id, season_num)

                if season_details and 'episodes' in season_details:
                    for ep in season_details.get('episodes', []):
                        if ep.get('episode_number') == ep_num:
                            air_date = ep.get('air_date')
                            if air_date:
                                try:
                                    air_date_obj = datetime.strptime(air_date, '%Y-%m-%d')
                                    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                                    has_aired = air_date_obj < today_start
                                except:
                                    has_aired = True
                            break
                
                if has_aired:
                    aired_missing.append((season_num, ep_num))
                else:
                    future_episodes.append((season_num, ep_num))
            
            missing = aired_missing
            
            # Determine overall status
            overall_status = 'Complete' if not missing else 'Incomplete'

            # Refine series status based on missing episodes and future episodes
            if future_episodes:
                display_series_status = f"{overall_status} - Upcoming"
            elif series_status == "Returning Series" or series_status == "Continuing": # TVDB uses "Continuing"
                display_series_status = f"{overall_status} - Ongoing"
            elif series_status in ["Ended", "Canceled"]:
                display_series_status = f"{overall_status} - {series_status}"
            else:
                display_series_status = "Unknown"

            app.logger.debug(f"DEBUG: Inserting/Updating TV Show {show.title} in DB.")
            tv_show_id = insert_tv_show(
                show.title,
                show_id,
                poster_url,
                details.get('overview') if details else None,
                details.get('first_air_date') if details else None,
                overall_status,
                display_series_status,
                details.get('number_of_seasons') if details else 0,
                details.get('number_of_episodes') if details else 0,
                details.get('genres') if details else [],
                details.get('vote_average') if details else 0.0,
                details.get('networks') if details else []
            )
            app.logger.debug(f"DEBUG: TV Show {show.title} (ID: {tv_show_id}) inserted/updated.")

            app.logger.debug(f"DEBUG: Inserting/Updating Seasons and Episodes for {show.title}.")
            # Insert/Update Seasons and Episodes
            unique_season_nums = sorted(list(set([s for s, e in all_eps])))
            for season_num in unique_season_nums:
                season_details = get_season_details(show_id, season_num)

                if season_details:
                    season_id = insert_season(
                        tv_show_id,
                        season_num,
                        season_details.get('name'),
                        season_details.get('overview'),
                        season_details.get('poster_path'),
                        season_details.get('air_date')
                    )
                    for ep in season_details.get('episodes', []):
                        episode_num = int(ep.get('episode_number', 0))
                        exists_in_plex = (season_num, episode_num) in existing
                        resolution = existing_episode_details.get(season_num, {}).get(episode_num, {}).get('resolution')
                        file_path = existing_episode_details.get(season_num, {}).get(episode_num, {}).get('file')
                        
                        insert_episode(
                            season_id,
                            episode_num,
                            ep.get('name'),
                            ep.get('air_date'),
                            ep.get('overview'),
                            ep.get('still_path'),
                            exists_in_plex,
                            resolution,
                            file_path
                        )
            app.logger.debug(f"DEBUG: Seasons and Episodes for {show.title} inserted/updated.")
            
            SCAN_STATUS['processed_shows'] += 1
            SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
            app.logger.debug(f"DEBUG: Progress for {show.title}: {SCAN_STATUS['progress']}%")
            
            time.sleep(random.uniform(0.2, 0.5))

        SCAN_STATUS['status_message'] = 'Scan complete.'
        app.logger.debug("TV show scan finished.")

    except Exception as e:
        SCAN_STATUS['status_message'] = f'Error during scan: {e}'
        app.logger.error(f"Exception in run_scan_thread: {e}", exc_info=True)
    finally:
        SCAN_STATUS['in_progress'] = False
        SCAN_STATUS['stop_requested'] = False
        app.logger.debug("SCAN_STATUS['in_progress'] set to False.")

def run_movie_scan_thread():
    global MOVIE_SCAN_STATUS
    try:
        MOVIE_SCAN_STATUS['in_progress'] = True
        MOVIE_SCAN_STATUS['progress'] = 0
        MOVIE_SCAN_STATUS['status_message'] = 'Connecting to Plex server...'
        MOVIE_SCAN_STATUS['start_time'] = datetime.now()
        MOVIE_SCAN_STATUS['stop_requested'] = False

        plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
        movie_section = plex.library.section(CONFIG['plex']['movie_library_section'])
        
        MOVIE_SCAN_STATUS['status_message'] = 'Fetching all movies from Plex...'
        all_plex_movies = movie_section.all()
        
        plex_movie_tmdb_ids = {get_movie_tmdb_id(m) for m in all_plex_movies if get_movie_tmdb_id(m)}

        # Clear old movie and collection data
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM movies")
        cursor.execute("DELETE FROM missing_movies")
        cursor.execute("DELETE FROM collections")
        conn.commit()
        conn.close()

        processed_collection_tmdb_ids = set()
        
        MOVIE_SCAN_STATUS['total_collections'] = len(all_plex_movies)
        MOVIE_SCAN_STATUS['processed_collections'] = 0

        for movie in all_plex_movies:
            if MOVIE_SCAN_STATUS['stop_requested']:
                MOVIE_SCAN_STATUS['status_message'] = 'Movie scan stopped by user.'
                break

            MOVIE_SCAN_STATUS['current_collection'] = movie.title
            
            tmdb_id = get_movie_tmdb_id(movie)
            if not tmdb_id:
                MOVIE_SCAN_STATUS['processed_collections'] += 1
                MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
                continue

            details = get_movie_details(tmdb_id)
            if not details:
                MOVIE_SCAN_STATUS['processed_collections'] += 1
                MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
                continue

            collection_info = details.get('belongs_to_collection')
            collection_tmdb_id = str(collection_info['id']) if collection_info else None
            
            insert_movie(
                details['title'],
                tmdb_id,
                details['poster_path'],
                details['overview'],
                details['release_date'],
                details['studio'],
                collection_tmdb_id
            )

            if not collection_info:
                # This movie is not in a collection. Create a single movie collection.
                insert_collection(details['title'], f"movie_{tmdb_id}", details['poster_path'])
                MOVIE_SCAN_STATUS['processed_collections'] += 1
                MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
                continue

            collection_tmdb_id = str(collection_info['id'])

            if collection_tmdb_id in processed_collection_tmdb_ids:
                MOVIE_SCAN_STATUS['processed_collections'] += 1
                MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
                continue

            # Fetch collection details from TMDB
            collection_url = get_api_url('tmdb', f'collection/{collection_tmdb_id}')
            if not collection_url: continue
            
            try:
                r = requests.get(collection_url)
                r.raise_for_status()
                collection_data = r.json()
                
                collection_id = insert_collection(collection_data['name'], collection_tmdb_id, f"https://image.tmdb.org/t/p/w500{collection_data.get('poster_path')}")

                # Get movies from TMDB collection and find missing ones
                for movie_part in collection_data.get('parts', []):
                    part_tmdb_id = str(movie_part['id'])
                    if part_tmdb_id not in plex_movie_tmdb_ids:
                        insert_missing_movie(collection_id, movie_part['title'], part_tmdb_id, f"https://image.tmdb.org/t/p/w500{movie_part.get('poster_path')}", movie_part.get('release_date'))
                
                processed_collection_tmdb_ids.add(collection_tmdb_id)

            except requests.exceptions.RequestException as e:
                app.logger.error(f"Error fetching TMDB collection details for {collection_data.get('name')}: {e}")

            MOVIE_SCAN_STATUS['processed_collections'] += 1
            MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
            time.sleep(random.uniform(0.2, 0.5))

        MOVIE_SCAN_STATUS['status_message'] = 'Movie scan complete.'

    except Exception as e:
        MOVIE_SCAN_STATUS['status_message'] = f'Error during movie scan: {e}'
        app.logger.error(f"Exception in run_movie_scan_thread: {e}", exc_info=True)
    finally:
        MOVIE_SCAN_STATUS['in_progress'] = False
        MOVIE_SCAN_STATUS['stop_requested'] = False

# === FLASK ROUTES ===
@app.route('/')
def index():
    conn = get_db_connection()
    shows = conn.execute('SELECT * FROM tv_shows ORDER BY title').fetchall()
    conn.close()

    results = {}
    total_shows = 0
    complete_shows = 0
    incomplete_shows = 0
    unknown_shows = 0
    
    ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
    ignored = []
    if os.path.exists(ignored_shows_path):
        with open(ignored_shows_path, 'r') as f:
            ignored = json.load(f)

    for show in shows:
        if show['title'] in ignored:
            continue
        
        total_shows += 1
        if show['status'] == 'Complete':
            complete_shows += 1
        elif show['status'] == 'Incomplete':
            incomplete_shows += 1
        else:
            unknown_shows += 1
            
        results[show['title']] = {
            'poster_url': show['poster_url'],
            'status': show['status'],
            'series_status': show['series_status']
        }

    return render_template('index.html', results=results, ignored=ignored, total_shows=total_shows, complete_shows=complete_shows, incomplete_shows=incomplete_shows, unknown_shows=unknown_shows)

@app.route('/scan')
def scan():
    global SCAN_STATUS
    if SCAN_STATUS['in_progress']:
        return jsonify({'error': 'A scan is already in progress.'}), 400
    
    scan_thread = threading.Thread(target=run_scan_thread)
    scan_thread.start()
    return jsonify({'success': True})

@app.route('/scan-status')
def scan_status():
    global SCAN_STATUS
    elapsed_time = None
    if SCAN_STATUS['start_time']:
        elapsed = datetime.now() - SCAN_STATUS['start_time']
        elapsed_time = str(elapsed).split('.')[0]
        
    return jsonify({
        'in_progress': SCAN_STATUS['in_progress'],
        'progress': SCAN_STATUS['progress'],
        'current_show': SCAN_STATUS['current_show'],
        'total_shows': SCAN_STATUS['total_shows'],
        'processed_shows': SCAN_STATUS['processed_shows'],
        'status_message': SCAN_STATUS['status_message'],
        'elapsed_time': elapsed_time,
        'stop_requested': SCAN_STATUS['stop_requested']
    })

@app.route('/stop-scan')
def stop_scan():
    global SCAN_STATUS
    if not SCAN_STATUS['in_progress']:
        return jsonify({'error': 'No scan is in progress.'}), 400
    
    SCAN_STATUS['stop_requested'] = True
    return jsonify({'success': True})

@app.route('/show/<title>')
def show_details(title):
    title = unquote(title)
    conn = get_db_connection()
    show_row = conn.execute('SELECT * FROM tv_shows WHERE title = ?', (title,)).fetchone()
    
    if show_row:
        show = dict(show_row)
        show['genres'] = json.loads(show['genres']) if show['genres'] else []
        show['networks'] = json.loads(show['networks']) if show['networks'] else []
    else:
        show = None

    seasons = {}
    if show:
        db_seasons = conn.execute('SELECT * FROM seasons WHERE tv_show_id = ? ORDER BY season_number', (show['id'],)).fetchall()
        for s in db_seasons:
            seasons[s['season_number']] = {
                'name': s['name'],
                'overview': s['overview'],
                'poster_path': s['poster_path'],
                'air_date': s['air_date'],
                'episodes': {}
            }
            db_episodes = conn.execute('SELECT * FROM episodes WHERE season_id = ? ORDER BY episode_number', (s['id'],)).fetchall()
            for ep in db_episodes:
                seasons[s['season_number']]['episodes'][ep['episode_number']] = dict(ep)

    conn.close()
    
    missing_episodes_count = 0
    future_episodes_count = 0
    now = datetime.now()

    if seasons:
        for season_num, season in seasons.items():
            for episode_num, episode in season['episodes'].items():
                if not episode['exists_in_plex']:
                    try:
                        air_date = datetime.strptime(episode['air_date'], '%Y-%m-%d')
                        if air_date < now:
                            missing_episodes_count += 1
                        else:
                            future_episodes_count += 1
                    except (ValueError, TypeError):
                        missing_episodes_count += 1


    return render_template('show_details.html', title=title, show=show, seasons=seasons, missing_episodes_count=missing_episodes_count, future_episodes_count=future_episodes_count, now=now)

@app.route('/ignore/<title>')
def ignore(title):
    title = unquote(title)
    ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
    ignored = []
    if os.path.exists(ignored_shows_path):
        with open(ignored_shows_path, 'r') as f:
            ignored = json.load(f)
    
    if title not in ignored:
        ignored.append(title)
        with open(ignored_shows_path, 'w') as f:
            json.dump(ignored, f)
            
    return jsonify({'success': True})

@app.route('/unignore/<title>')
def unignore(title):
    title = unquote(title)
    ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
    ignored = []
    if os.path.exists(ignored_shows_path):
        with open(ignored_shows_path, 'r') as f:
            ignored = json.load(f)
    
    if title in ignored:
        ignored.remove(title)
        with open(ignored_shows_path, 'w') as f:
            json.dump(ignored, f)
            
    return redirect(url_for('ignored_shows'))

@app.route('/ignored')
def ignored_shows():
    ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
    ignored = []
    if os.path.exists(ignored_shows_path):
        with open(ignored_shows_path, 'r') as f:
            ignored = json.load(f)
    return render_template('ignored.html', ignored=ignored)

@app.route('/movies')
def movies():
    conn = get_db_connection()
    collections_from_db = get_all_collections()
    
    collections_with_movies = []
    for coll in collections_from_db:
        collection_tmdb_id = coll['tmdb_id']
        
        # Get missing movies
        missing_movies_rows = get_missing_movies_by_collection_id(coll['id'])
        missing_movies = [dict(m) for m in missing_movies_rows]
        for m in missing_movies:
            m['owned'] = False

        # Get owned movies
        if collection_tmdb_id.startswith('movie_'):
            # Single movie collection
            movie_tmdb_id = collection_tmdb_id[6:]
            owned_movies_rows = conn.execute('SELECT * FROM movies WHERE tmdb_id = ?', (movie_tmdb_id,)).fetchall()
        else:
            owned_movies_rows = conn.execute('SELECT * FROM movies WHERE collection_tmdb_id = ?', (collection_tmdb_id,)).fetchall()
        
        owned_movies = [dict(m) for m in owned_movies_rows]
        for m in owned_movies:
            m['owned'] = True
            
        all_movies = sorted(owned_movies + missing_movies, key=lambda x: x.get('release_date') or '1900-01-01')

        if all_movies:
            collections_with_movies.append({
                'collection': coll,
                'movies': all_movies
            })
            
    conn.close()
    return render_template('movies.html', collections=collections_with_movies)

@app.route('/scan_movies')
def scan_movies():
    global MOVIE_SCAN_STATUS
    if MOVIE_SCAN_STATUS['in_progress']:
        return jsonify({'error': 'A movie scan is already in progress.'}), 400
    
    movie_scan_thread = threading.Thread(target=run_movie_scan_thread)
    movie_scan_thread.start()
    return jsonify({'success': True})

@app.route('/movie_scan_status')
def movie_scan_status():
    global MOVIE_SCAN_STATUS
    elapsed_time = None
    if MOVIE_SCAN_STATUS['start_time']:
        elapsed = datetime.now() - MOVIE_SCAN_STATUS['start_time']
        elapsed_time = str(elapsed).split('.')[0]
        
    return jsonify({
        'in_progress': MOVIE_SCAN_STATUS['in_progress'],
        'progress': MOVIE_SCAN_STATUS['progress'],
        'current_collection': MOVIE_SCAN_STATUS['current_collection'],
        'total_collections': MOVIE_SCAN_STATUS['total_collections'],
        'processed_collections': MOVIE_SCAN_STATUS['processed_collections'],
        'status_message': MOVIE_SCAN_STATUS['status_message'],
        'elapsed_time': elapsed_time,
        'stop_requested': MOVIE_SCAN_STATUS['stop_requested']
    })

@app.route('/stop_movie_scan')
def stop_movie_scan():
    global MOVIE_SCAN_STATUS
    if not MOVIE_SCAN_STATUS['in_progress']:
        return jsonify({'error': 'No movie scan is in progress.'}), 400
    
    MOVIE_SCAN_STATUS['stop_requested'] = True
    return jsonify({'success': True})

@app.route('/search')
def search():
    query = request.args.get('query', '')
    return render_template('search.html', query=query)

def search_prowlarr_api(query):
    prowlarr_url = CONFIG['prowlarr']['url']
    prowlarr_api_key = CONFIG['prowlarr']['api_key']
    
    url = f"{prowlarr_url}/api/v1/search?query={query}&apikey={prowlarr_api_key}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error searching Prowlarr: {e}")
        return None

def parse_title(title):
    resolution_match = re.search(r'(720p|1080p|2160p)', title, re.IGNORECASE)
    codec_match = re.search(r'(x264|x265|h264|h265)', title, re.IGNORECASE)
    
    resolution = resolution_match.group(1) if resolution_match else 'Unknown'
    codec = codec_match.group(1) if codec_match else 'Unknown'
    
    return resolution, codec

@app.route('/search_prowlarr')
def search_prowlarr():
    query = request.args.get('query')
    if not query:
        return jsonify({'error': 'Query parameter is required'}), 400

    results = search_prowlarr_api(query)
    if results is None:
        return jsonify({'error': 'Failed to fetch results from Prowlarr'}), 500

    # Check if item is already in Plex
    plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
    plex_movies = {m.title.lower() for m in plex.library.section(CONFIG['plex']['movie_library_section']).all()}
    plex_shows = {s.title.lower() for s in plex.library.section(CONFIG['plex']['library_section']).all()}

    prowlarr_cat_mappings = CONFIG.get('prowlarr', {}).get('category_mappings', {})

    processed_results = []
    for result in results:
        resolution, codec = parse_title(result['title'])
        
        # Category detection based on Prowlarr category ID
        prowlarr_cat_id = None
        if 'categories' in result and result['categories']:
            prowlarr_cat_id = result['categories'][0].get('id')

        category = 'Unknown'
        for cat_name, cat_ids in prowlarr_cat_mappings.items():
            if prowlarr_cat_id in cat_ids:
                category = cat_name
                break
        
        if category == 'Unknown':
            app.logger.warning(f"Unmapped Prowlarr categoryId: {prowlarr_cat_id} for result: {result.get('title')}")

        owned = False
        if category == 'movies' and result['title'].lower() in plex_movies:
            owned = True
        elif category == 'tv' and result['title'].lower() in plex_shows:
            owned = True

        processed_results.append({
            'title': result['title'],
            'seeders': result.get('seeders', 0),
            'size': result.get('size', 0),
            'resolution': resolution,
            'codec': codec,
            'category': category,
            'owned': owned,
            'magnetUrl': result.get('magnetUrl'),
            'downloadUrl': result.get('downloadUrl'),
            'link': result.get('link'),
            'guid': result.get('guid')
        })
    
    # Filter and sort results
    min_seeders = CONFIG['download_client']['min_seeders']
    filtered_results = [r for r in processed_results if r['seeders'] >= min_seeders]
    sorted_results = sorted(filtered_results, key=lambda x: x['seeders'], reverse=True)
    
    return jsonify(sorted_results)

@app.route('/downloads')
def downloads():
    return render_template('downloads.html')

@app.route('/downloads_status')
def downloads_status():
    try:
        qbt_client = Client(
            host=CONFIG['qbittorrent']['host'],
            port=CONFIG['qbittorrent']['port'],
            username=CONFIG['qbittorrent']['username'],
            password=CONFIG['qbittorrent']['password']
        )
        qbt_client.auth_log_in()
        torrents = qbt_client.torrents_info()
        qbt_client.auth_log_out()
        
        downloads = []
        for torrent in torrents:
            downloads.append({
                'name': torrent.name,
                'size': torrent.size,
                'progress': torrent.progress,
                'state': torrent.state,
                'dlspeed': torrent.dlspeed,
                'upspeed': torrent.upspeed,
                'eta': torrent.eta
            })
        return jsonify(downloads)
    except Exception as e:
        app.logger.error(f"Error fetching qBittorrent status: {e}")
        return jsonify({'error': 'Could not connect to qBittorrent.'}), 500

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    link = data.get('link')
    category = data.get('category')

    if not link:
        return jsonify({'error': 'Link is required'}), 400

    try:
        qbt_client = Client(
            host=CONFIG['qbittorrent']['host'],
            port=CONFIG['qbittorrent']['port'],
            username=CONFIG['qbittorrent']['username'],
            password=CONFIG['qbittorrent']['password']
        )
        qbt_client.auth_log_in()
        
        qbt_category = None
        if category == 'movies':
            qbt_category = CONFIG['qbittorrent']['category_mappings'].get('movies')
        elif category == 'tv':
            qbt_category = CONFIG['qbittorrent']['category_mappings'].get('tv')

        if link.startswith('magnet:'):
            qbt_client.torrents_add(urls=link, category=qbt_category)
        else:
            response = requests.get(link, allow_redirects=False)
            if response.status_code in [301, 302, 307, 308] and 'Location' in response.headers:
                redirect_url = response.headers['Location']
                if redirect_url.startswith('magnet:'):
                    qbt_client.torrents_add(urls=redirect_url, category=qbt_category)
                else:
                    final_response = requests.get(redirect_url)
                    final_response.raise_for_status()
                    torrent_content = final_response.content
                    qbt_client.torrents_add(torrent_files=torrent_content, category=qbt_category)
            else:
                response.raise_for_status()
                torrent_content = response.content
                qbt_client.torrents_add(torrent_files=torrent_content, category=qbt_category)
            
        qbt_client.auth_log_out()
        
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error adding download to qBittorrent: {e}")
        return jsonify({'error': 'Could not add download to qBittorrent.'}), 500

@app.route('/settings')
def settings():
    return render_template('settings.html', config=CONFIG)

@app.route('/save_settings', methods=['POST'])
def save_settings():
    global CONFIG
    
    # Update Plex settings
    CONFIG['plex']['url'] = request.form['plex_url']
    CONFIG['plex']['token'] = request.form['plex_token']
    CONFIG['plex']['library_section'] = request.form['plex_library_id']
    CONFIG['plex']['movie_library_section'] = request.form['plex_movie_library_id']
    
    # Update TMDb settings
    CONFIG['tmdb']['api_key'] = request.form['tmdb_api_key']
    
    
    
    # Update Download Client settings
    CONFIG['download_client']['min_quality'] = request.form['min_quality']
    CONFIG['download_client']['max_quality'] = request.form['max_quality']
    CONFIG['download_client']['codec'] = request.form['codec']
    CONFIG['download_client']['min_seeders'] = int(request.form['min_seeders'])
    
    # Update Prowlarr settings
    CONFIG['prowlarr']['url'] = request.form['prowlarr_url']
    CONFIG['prowlarr']['api_key'] = request.form['prowlarr_api_key']
    
    # Update qBittorrent settings
    CONFIG['qbittorrent']['host'] = request.form['qbittorrent_host']
    CONFIG['qbittorrent']['port'] = int(request.form['qbittorrent_port'])
    CONFIG['qbittorrent']['username'] = request.form['qbittorrent_username']
    CONFIG['qbittorrent']['password'] = request.form['qbittorrent_password']
    
    with open(CONFIG_PATH, 'w') as config_file:
        json.dump(CONFIG, config_file, indent=2)
        
    return redirect(url_for('settings'))

@app.route('/test_prowlarr_connection', methods=['POST'])
def test_prowlarr_connection():
    data = request.get_json()
    url = data.get('url')
    api_key = data.get('api_key')
    
    if not url or not api_key:
        return jsonify({'success': False, 'error': 'URL and API Key are required.'})
        
    try:
        response = requests.get(f"{url}/api/v1/health?apikey={api_key}")
        response.raise_for_status()
        return jsonify({'success': True})
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/test_qbittorrent_connection', methods=['POST'])
def test_qbittorrent_connection():
    data = request.get_json()
    host = data.get('host')
    port = data.get('port')
    username = data.get('username')
    password = data.get('password')

    if not host or not port:
        return jsonify({'success': False, 'error': 'Host and Port are required.'})

    try:
        client = Client(host=host, port=port, username=username, password=password)
        client.auth_log_in()
        client.auth_log_out()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(host=CONFIG['app']['host'], port=CONFIG['app']['port'], debug=CONFIG['app']['debug'])
