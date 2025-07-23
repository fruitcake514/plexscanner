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

def insert_movie(title, tmdb_id, poster_url, overview, release_date, studio):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO movies (title, tmdb_id, poster_url, overview, release_date, studio, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, tmdb_id, poster_url, overview, release_date, studio, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
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

# === UTILITIES ===
def determine_content_tag(categories):
    """Map Prowlarr categories to qBittorrent tags using category mappings"""
    if not categories:
        return None  # No tag if no categories
    
    # Get category mappings from config
    mappings = CONFIG.get('qbittorrent', {}).get('category_mappings', {})
    
    # Check categories for TV or Movie indicators
    for category in categories:
        category_name = category.get('name', '').lower()
        if 'tv' in category_name:
            return mappings.get('tv', CONFIG.get('qbittorrent', {}).get('tv_tag', 'tv'))
        elif 'movies' in category_name or 'movie' in category_name:
            return mappings.get('movies', CONFIG.get('qbittorrent', {}).get('movie_tag', 'movies'))
    
    # If no clear indicators found, leave untagged
    return None

def determine_qb_category(categories):
    """Determine qBittorrent category: 'tv', 'movies', or 'prowlarr' as fallback"""
    if not categories:
        return 'prowlarr'  # Fallback category
    
    # Check categories for TV or Movie indicators
    for category in categories:
        category_name = category.get('name', '').lower()
        if 'tv' in category_name:
            return 'tv'
        elif 'movies' in category_name or 'movie' in category_name:
            return 'movies'
    
    # If no clear match found, use fallback
    return 'prowlarr'
def get_tmdb_id(plex_show):
    title = plex_show.title
    guid = plex_show.guid or ''
    if 'tmdb' in guid:
        return guid.split('//')[1].split('?')[0]
    for guid in plex_show.guids:
        if 'tmdb' in guid.id.lower():
            return guid.id.split('//')[1].split('?')[0]
    search_url = f'https://api.themoviedb.org/3/search/tv?api_key={CONFIG["tmdb"]["api_key"]}&query={requests.utils.quote(title)}'
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
    
    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={CONFIG["tmdb"]["api_key"]}'
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
    
    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={CONFIG["tmdb"]["api_key"]}'
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
    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={CONFIG["tmdb"]["api_key"]}'
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
    search_url = f'https://api.themoviedb.org/3/search/movie?api_key={CONFIG["tmdb"]["api_key"]}&query={requests.utils.quote(plex_movie.title)}'
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
    url = f'https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={CONFIG["tmdb"]["api_key"]}'
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
            'studio': studio
        }
    except:
        return None

def fetch_tmdb_episodes(tmdb_id):
    eps = set()
    try:
        show_info = requests.get(f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={CONFIG["tmdb"]["api_key"]}').json()
        for season_num in range(1, show_info.get('number_of_seasons', 0) + 1):
            season = requests.get(f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_num}?api_key={CONFIG["tmdb"]["api_key"]}').json()
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
    
    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}?api_key={CONFIG["tmdb"]["api_key"]}'
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
            
            # Get TMDB ID
            SCAN_STATUS['status_message'] = f'Finding TMDb match for {show.title}...'
            tmdb_id = get_tmdb_id(show)
            app.logger.debug(f"TMDb ID for {show.title}: {tmdb_id}")
            
            poster_url = None
            details = None
            if tmdb_id:
                poster_url = get_show_poster(tmdb_id)
                details = get_show_details(tmdb_id)
                app.logger.debug(f"Fetched TMDB details for {show.title}.")
            
            if not tmdb_id:
                app.logger.debug(f"No TMDb ID found for {show.title}. Inserting with Unknown status.")
                # Insert into DB with unknown status
                insert_tv_show(show.title, None, None, None, None, 'Unknown', 'Unknown', 0, 0, [], 0.0, [])
                SCAN_STATUS['processed_shows'] += 1
                SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
                continue
            
            app.logger.debug(f"Processing {show.title} (TMDb ID: {tmdb_id})")
            
            # Get series status
            SCAN_STATUS['status_message'] = f'Checking series status for {show.title}...'
            series_status = get_series_status(tmdb_id)
            app.logger.debug(f"DEBUG: Series status for {show.title}: {series_status}")
            
            # Compare episodes
            SCAN_STATUS['status_message'] = f'Comparing episodes for {show.title}...'
            existing, existing_episode_details = get_existing_episodes(show)
            all_eps = fetch_tmdb_episodes(tmdb_id)
            app.logger.debug(f"DEBUG: Compared episodes for {show.title}. Existing: {len(existing)}, All TMDB: {len(all_eps)}")
            
            # Filter out future episodes when calculating missing episodes
            aired_missing = []
            future_episodes = []
            
            # Process missing episodes
            for season_num, ep_num in sorted(list(all_eps - existing)):
                air_date = None
                has_aired = True
                
                season_details = get_season_details(tmdb_id, season_num)
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
            elif series_status == "Returning Series":
                display_series_status = f"{overall_status} - Ongoing"
            elif series_status in ["Ended", "Canceled"]:
                display_series_status = f"{overall_status} - {series_status}"
            else:
                display_series_status = "Unknown"

            app.logger.debug(f"DEBUG: Inserting/Updating TV Show {show.title} in DB.")
            tv_show_id = insert_tv_show(
                show.title,
                tmdb_id,
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
                season_details = get_season_details(tmdb_id, season_num)
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
        
        # Update scan status message
        if SCAN_STATUS['stop_requested']:
            SCAN_STATUS['status_message'] = f'Scan stopped by user. Processed {SCAN_STATUS["processed_shows"]} of {SCAN_STATUS["total_shows"]} shows.'
        else:
            SCAN_STATUS['status_message'] = 'Scan complete.'
            SCAN_STATUS['progress'] = 100
        
        time.sleep(1)
    except Exception as e:
        SCAN_STATUS['status_message'] = f'Error: {str(e)}'
        app.logger.error(f"ERROR: Scan failed with exception: {e}")
    finally:
        SCAN_STATUS['in_progress'] = False
        SCAN_STATUS['stop_requested'] = False

@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tv_shows")
    shows_data = cursor.fetchall()
    conn.close()

    results = {}
    total_shows = len(shows_data)
    incomplete_shows = 0
    complete_shows = 0
    unknown_shows = 0

    for show in shows_data:
        show_dict = dict(show)
        if show_dict['status'] == 'Incomplete':
            incomplete_shows += 1
        elif show_dict['status'] == 'Complete':
            complete_shows += 1
        elif show_dict['status'] == 'Unknown':
            unknown_shows += 1
        
        # Convert genres and networks back to list from JSON string
        if show_dict['genres']:
            show_dict['genres'] = json.loads(show_dict['genres'])
        if show_dict['networks']:
            show_dict['networks'] = json.loads(show_dict['networks'])
        results[show_dict['title']] = show_dict

    ignored_shows_path = os.path.join(os.path.dirname(__file__), 'ignore.json')
    ignored = []
    if os.path.exists(ignored_shows_path):
        with open(ignored_shows_path, 'r') as f:
            ignored = json.load(f)

    return render_template('index.html', 
                           results=results, 
                           ignored=ignored,
                           total_shows=total_shows,
                           incomplete_shows=incomplete_shows,
                           complete_shows=complete_shows,
                           unknown_shows=unknown_shows)

@app.route('/show/<path:title>')
def show_details(title):
    title = unquote(title)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tv_shows WHERE title = ?", (title,))
    show_data = cursor.fetchone()

    if show_data:
        show_data = dict(show_data)
        # Convert genres and networks back to list from JSON string
        if show_data['genres']:
            show_data['genres'] = json.loads(show_data['genres'])
        if show_data['networks']:
            show_data['networks'] = json.loads(show_data['networks'])

        # Fetch seasons and episodes
        cursor.execute("SELECT * FROM seasons WHERE tv_show_id = ?", (show_data['id'],))
        seasons_raw = cursor.fetchall()
        seasons_data = {}
        for season_row in seasons_raw:
            season_dict = dict(season_row)
            cursor.execute("SELECT * FROM episodes WHERE season_id = ?", (season_dict['id'],))
            episodes_raw = cursor.fetchall()
            season_episodes = {}
            for episode_row in episodes_raw:
                episode_dict = dict(episode_row)
                season_episodes[episode_dict['episode_number']] = episode_dict
            season_dict['episodes'] = season_episodes
            seasons_data[season_dict['season_number']] = season_dict
        
        # Calculate missing and future episodes for display
        missing_episodes_count = 0
        future_episodes_count = 0
        for season_num, season_data in seasons_data.items():
            for episode_num, episode_data in season_data['episodes'].items():
                if not episode_data['exists_in_plex']:
                    air_date_obj = None
                    if episode_data['air_date']:
                        try:
                            air_date_obj = datetime.strptime(episode_data['air_date'], '%Y-%m-%d')
                        except ValueError:
                            pass

                    if air_date_obj and air_date_obj < datetime.now():
                        missing_episodes_count += 1
                    elif air_date_obj and air_date_obj >= datetime.now():
                        future_episodes_count += 1

        conn.close()
        return render_template('show_details.html', 
                               title=title, 
                               show=show_data, 
                               seasons=seasons_data,
                               missing_episodes_count=missing_episodes_count,
                               future_episodes_count=future_episodes_count,
                               now=datetime.now())
    
    conn.close()
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET'])
def settings():
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    return render_template('settings.html', config=config)

@app.route('/settings', methods=['POST'])
def save_settings():
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    
    config['plex']['url'] = request.form['plex_url']
    config['plex']['token'] = request.form['plex_token']
    config['plex']['library_section'] = request.form['plex_library_id']
    config['plex']['movie_library_section'] = request.form['plex_movie_library_id']
    config['tmdb']['api_key'] = request.form['tmdb_api_key']
    config['prowlarr']['url'] = request.form['prowlarr_url']
    config['prowlarr']['api_key'] = request.form['prowlarr_api_key']
    config['qbittorrent']['host'] = request.form['qbittorrent_host']
    config['qbittorrent']['port'] = int(request.form['qbittorrent_port'])
    config['qbittorrent']['username'] = request.form['qbittorrent_username']
    config['qbittorrent']['password'] = request.form['qbittorrent_password']
    # Set default tags based on category mappings for backward compatibility
    config['qbittorrent']['movie_tag'] = request.form.get('category_mapping_movies', 'movies')
    config['qbittorrent']['tv_tag'] = request.form.get('category_mapping_tv', 'tv')
    
    # Category mappings
    if 'category_mappings' not in config['qbittorrent']:
        config['qbittorrent']['category_mappings'] = {}
    config['qbittorrent']['category_mappings']['movies'] = request.form['category_mapping_movies']
    config['qbittorrent']['category_mappings']['tv'] = request.form['category_mapping_tv']
    
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
    
    # Reload CONFIG after saving
    global CONFIG
    with open(CONFIG_PATH, 'r') as config_file:
        CONFIG = json.load(config_file)
        
    return redirect(url_for('settings'))


@app.route('/ignore/<path:title>')
def ignore(title):
    with open(os.path.join(os.path.dirname(__file__), 'ignore.json'), 'r+') as f:
        ignored = json.load(f)
        if title not in ignored:
            ignored.append(title)
            f.seek(0)
            json.dump(ignored, f)
            f.truncate()
    return redirect(url_for('index'))

@app.route('/unignore/<path:title>')
def unignore(title):
    with open(os.path.join(os.path.dirname(__file__), 'ignore.json'), 'r+') as f:
        ignored = json.load(f)
        if title in ignored:
            ignored.remove(title)
            f.seek(0)
            json.dump(ignored, f)
            f.truncate()
    return redirect(url_for('index'))

@app.route('/ignored')
def ignored_shows():
    if os.path.exists(os.path.join(os.path.dirname(__file__), 'ignore.json')):
        with open(os.path.join(os.path.dirname(__file__), 'ignore.json'), 'r') as f:
            ignored = json.load(f)
    else:
        ignored = []
    return render_template('ignored.html', ignored=ignored)


@app.route('/scan')
def scan():
    global SCAN_STATUS, MOVIE_SCAN_STATUS
    
    if SCAN_STATUS['in_progress'] or MOVIE_SCAN_STATUS['in_progress']:
        return jsonify(success=False, error='Scan already in progress')
    
    # Start scan in background thread
    thread = threading.Thread(target=run_scan_thread)
    thread.daemon = True
    thread.start()
    
    return jsonify(success=True)

@app.route('/scan-status')
def scan_status():
    global SCAN_STATUS
    
    elapsed_time = None
    if SCAN_STATUS['start_time']:
        elapsed_seconds = (datetime.now() - SCAN_STATUS['start_time']).total_seconds()
        elapsed_time = f"{int(elapsed_seconds // 60)}m {int(elapsed_seconds % 60)}s"
    
    app.logger.debug(f"Returning scan status: {SCAN_STATUS}")
    return jsonify({
        'in_progress': SCAN_STATUS['in_progress'],
        'progress': round(SCAN_STATUS['progress'], 1),
        'current_show': SCAN_STATUS['current_show'],
        'processed_shows': SCAN_STATUS['processed_shows'],
        'total_shows': SCAN_STATUS['total_shows'],
        'status_message': SCAN_STATUS['status_message'],
        'elapsed_time': elapsed_time,
        'stop_requested': SCAN_STATUS['stop_requested']
    })

@app.route('/stop-scan')
def stop_scan():
    global SCAN_STATUS
    
    if not SCAN_STATUS['in_progress']:
        return jsonify(success=False, error='No scan is currently in progress')
    
    SCAN_STATUS['stop_requested'] = True
    return jsonify(success=True, message='Scan stop requested. Finishing current show and saving results...')



@app.route('/get-show-details', methods=['GET'])
def get_show_details_api():
    title = request.args.get('title')
    if not title:
        return jsonify(success=False, error='No title provided')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tv_shows WHERE title = ?", (title,))
        show_data = cursor.fetchone()

        if show_data:
            show_data = dict(show_data)
            if show_data['genres']:
                show_data['genres'] = json.loads(show_data['genres'])
            if show_data['networks']:
                show_data['networks'] = json.loads(show_data['networks'])

            # Fetch seasons and episodes
            cursor.execute("SELECT * FROM seasons WHERE tv_show_id = ?", (show_data['id'],))
            seasons_raw = cursor.fetchall()
            seasons_data = {}
            for season_row in seasons_raw:
                season_dict = dict(season_row)
                cursor.execute("SELECT * FROM episodes WHERE season_id = ?", (season_dict['id'],))
                episodes_raw = cursor.fetchall()
                season_episodes = {}
                for episode_row in episodes_raw:
                    episode_dict = dict(episode_row)
                    season_episodes[episode_dict['episode_number']] = episode_dict
                season_dict['episodes'] = season_episodes
                seasons_data[season_dict['season_number']] = season_dict
            show_data['seasons'] = seasons_data

            conn.close()
            return jsonify(success=True, data=show_data)
        else:
            conn.close()
            return jsonify(success=False, error='Show not found')
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/movies')
def movies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM movies")
    movies_data = cursor.fetchall()
    conn.close()

    movies_list = []
    for movie in movies_data:
        movies_list.append(dict(movie))

    return render_template('movies.html', movies=movies_list)

@app.route('/scan_movies')
def scan_movies():
    global SCAN_STATUS, MOVIE_SCAN_STATUS

    if SCAN_STATUS['in_progress'] or MOVIE_SCAN_STATUS['in_progress']:
        return jsonify(success=False, error='Scan already in progress')

    thread = threading.Thread(target=run_movie_scan_thread)
    thread.daemon = True
    thread.start()

    return jsonify(success=True)

@app.route('/movie_scan_status')
def movie_scan_status():
    global MOVIE_SCAN_STATUS

    elapsed_time = None
    if MOVIE_SCAN_STATUS['start_time']:
        elapsed_seconds = (datetime.now() - MOVIE_SCAN_STATUS['start_time']).total_seconds()
        elapsed_time = f"{int(elapsed_seconds // 60)}m {int(elapsed_seconds % 60)}s"

    app.logger.debug(f"Returning movie scan status: {MOVIE_SCAN_STATUS}")
    return jsonify({
        'in_progress': MOVIE_SCAN_STATUS['in_progress'],
        'progress': round(MOVIE_SCAN_STATUS['progress'], 1),
        'current_collection': MOVIE_SCAN_STATUS['current_collection'],
        'processed_collections': MOVIE_SCAN_STATUS['processed_collections'],
        'total_collections': MOVIE_SCAN_STATUS['total_collections'],
        'status_message': MOVIE_SCAN_STATUS['status_message'],
        'elapsed_time': elapsed_time,
        'stop_requested': MOVIE_SCAN_STATUS['stop_requested']
    })

@app.route('/stop_movie_scan')
def stop_movie_scan():
    global MOVIE_SCAN_STATUS

    if not MOVIE_SCAN_STATUS['in_progress']:
        return jsonify(success=False, error='No movie scan is currently in progress')

    MOVIE_SCAN_STATUS['stop_requested'] = True
    return jsonify(success=True, message='Movie scan stop requested.')

def run_movie_scan_thread():
    global MOVIE_SCAN_STATUS

    try:
        MOVIE_SCAN_STATUS['in_progress'] = True
        MOVIE_SCAN_STATUS['progress'] = 0
        MOVIE_SCAN_STATUS['status_message'] = 'Connecting to Plex server...'
        MOVIE_SCAN_STATUS['start_time'] = datetime.now()
        MOVIE_SCAN_STATUS['stop_requested'] = False

        plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
        plex_movies = plex.library.section(CONFIG['plex']['movie_library_section']).all()
        plex_movie_titles = {m.title for m in plex_movies}

        # Get all movie titles from the database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM movies")
        db_movie_titles = {row['title'] for row in cursor.fetchall()}
        conn.close()

        # Find and delete movies no longer in Plex
        movies_to_delete = db_movie_titles - plex_movie_titles
        if movies_to_delete:
            app.logger.debug(f"Deleting {len(movies_to_delete)} movies no longer in Plex: {movies_to_delete}")
            conn = get_db_connection()
            cursor = conn.cursor()
            for title in movies_to_delete:
                cursor.execute("DELETE FROM movies WHERE title = ?", (title,))
            conn.commit()
            conn.close()

        movies = plex_movies

        MOVIE_SCAN_STATUS['total_collections'] = len(movies) # Treat each movie as a collection for progress
        MOVIE_SCAN_STATUS['processed_collections'] = 0

        for movie in movies:
            if MOVIE_SCAN_STATUS['stop_requested']:
                MOVIE_SCAN_STATUS['status_message'] = 'Movie scan stopped by user.'
                break

            MOVIE_SCAN_STATUS['current_collection'] = movie.title
            MOVIE_SCAN_STATUS['status_message'] = f'Processing {movie.title}...'

            tmdb_id = get_movie_tmdb_id(movie)
            movie_details = None
            if tmdb_id:
                movie_details = get_movie_details(tmdb_id)
            
            if movie_details:
                insert_movie(
                    movie.title,
                    tmdb_id,
                    movie_details.get('poster_path'),
                    movie_details.get('overview'),
                    movie_details.get('release_date'),
                    movie_details.get('studio')
                )

            MOVIE_SCAN_STATUS['processed_collections'] += 1
            MOVIE_SCAN_STATUS['progress'] = (MOVIE_SCAN_STATUS['processed_collections'] / MOVIE_SCAN_STATUS['total_collections']) * 100
            time.sleep(random.uniform(0.1, 0.3))

        if not MOVIE_SCAN_STATUS['stop_requested']:
            MOVIE_SCAN_STATUS['status_message'] = 'Movie scan complete.'
            MOVIE_SCAN_STATUS['progress'] = 100

    except Exception as e:
        MOVIE_SCAN_STATUS['status_message'] = f'Error: {str(e)}'
    finally:
        MOVIE_SCAN_STATUS['in_progress'] = False
        MOVIE_SCAN_STATUS['stop_requested'] = False

@app.route('/search')
def search():
    return render_template('search.html')

@app.route('/search_prowlarr')
def search_prowlarr():
    query = request.args.get('query')
    if not query:
        return jsonify({'error': 'A search query is required'}), 400

    headers = {
        'X-Api-Key': CONFIG['prowlarr']['api_key']
    }
    params = {
        'query': query,
        'categories[]': [2000, 5000],
        'type': 'search'
    }
    try:
        response = requests.get(f"{CONFIG['prowlarr']['url']}/api/v1/search", params=params, headers=headers)
        response.raise_for_status()
        results = response.json()

        # Sort by seeders (descending)
        results.sort(key=lambda x: x.get('seeders', 0), reverse=True)

        # Extract resolution and codec
        for result in results:
            title = result.get('title', '').lower()
            if '2160p' in title or '4k' in title:
                result['resolution'] = '4K'
            elif '1080p' in title:
                result['resolution'] = '1080p'
            elif '720p' in title:
                result['resolution'] = '720p'
            else:
                result['resolution'] = 'Unknown'

            if 'h.265' in title or 'hevc' in title:
                result['codec'] = 'H.265'
            elif 'h.264' in title or 'avc' in title:
                result['codec'] = 'H.264'
            elif 'av1' in title:
                result['codec'] = 'AV1'
            else:
                result['codec'] = 'Unknown'

        # Check against database - extract show/movie name from torrent title
        conn = get_db_connection()
        cursor = conn.cursor()
        for result in results:
            result['owned'] = False  # Default to not owned
            torrent_title = result.get('title', '').lower()
            
            # Try to extract the actual show/movie name from torrent title
            # Remove common patterns like resolution, codec, release group, year
            import re
            
            # For movies: Extract title and year if present
            movie_pattern = r'^(.+?)\s*\(?(\d{4})\)?'
            movie_match = re.match(movie_pattern, torrent_title)
            if movie_match:
                clean_title = movie_match.group(1).strip()
                year = movie_match.group(2)
                
                # Check if this movie exists in database
                cursor.execute("SELECT * FROM movies WHERE LOWER(title) LIKE ? OR LOWER(title) LIKE ?", 
                             (f"%{clean_title}%", f"%{clean_title.replace(' ', '%')}%"))
                movie = cursor.fetchone()
                if movie:
                    result['owned'] = True
                    continue
            
            # For TV shows: Try various patterns
            # Remove common TV show patterns like SxxExx, season info, etc.
            tv_patterns = [
                r'^(.+?)\s+s\d+',  # ShowName S01
                r'^(.+?)\s+season\s+\d+',  # ShowName Season 1
                r'^(.+?)\s+\d{4}',  # ShowName 2024
                r'^(.+?)\s+complete',  # ShowName Complete
            ]
            
            for pattern in tv_patterns:
                tv_match = re.match(pattern, torrent_title, re.IGNORECASE)
                if tv_match:
                    clean_title = tv_match.group(1).strip()
                    cursor.execute("SELECT * FROM tv_shows WHERE LOWER(title) LIKE ? OR LOWER(title) LIKE ?", 
                                 (f"%{clean_title}%", f"%{clean_title.replace(' ', '%')}%"))
                    show = cursor.fetchone()
                    if show:
                        result['owned'] = True
                        break
            
            # Fallback: Try exact match with full title
            if not result['owned']:
                cursor.execute("SELECT * FROM movies WHERE LOWER(title) = ?", (torrent_title,))
                movie = cursor.fetchone()
                if movie:
                    result['owned'] = True
                else:
                    cursor.execute("SELECT * FROM tv_shows WHERE LOWER(title) = ?", (torrent_title,))
                    show = cursor.fetchone()
                    if show:
                        result['owned'] = True
                        
        conn.close()
        return jsonify(results)
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/downloads')
def downloads():
    return render_template('downloads.html')

@app.route('/download', methods=['POST'])
def download():
    magnet_or_url = request.json.get('magnet')
    categories = request.json.get('categories', [])
    app.logger.debug(f"Received download request for: {magnet_or_url}")
    app.logger.debug(f"Categories: {categories}")
    
    # Determine qBittorrent category based on Prowlarr categories
    qb_category = determine_qb_category(categories)
    app.logger.debug(f"Determined qBittorrent category: {qb_category}")
    
    if not magnet_or_url or magnet_or_url == 'undefined':
        app.logger.error("Magnet link/URL is missing or invalid")
        return jsonify({'error': 'Magnet link/URL is required'}), 400


    try:
        # Check if this is a Prowlarr proxy URL or a real magnet link
        if magnet_or_url.startswith('magnet:'):
            # It's already a real magnet link
            magnet_link = magnet_or_url
            app.logger.debug("Using direct magnet link")
        elif 'prowlarr' in magnet_or_url.lower() or CONFIG['prowlarr']['url'] in magnet_or_url:
            # It's a Prowlarr proxy URL, fetch the actual magnet link or torrent file
            app.logger.debug("Fetching from Prowlarr proxy URL")
            try:
                response = requests.get(magnet_or_url, timeout=30, allow_redirects=False)
                app.logger.debug(f"Prowlarr response status: {response.status_code}")
                app.logger.debug(f"Prowlarr response headers: {dict(response.headers)}")
                
                # Check if we got redirected to a magnet link
                if response.status_code in [301, 302, 303, 307, 308]:
                    location = response.headers.get('Location', '')
                    app.logger.debug(f"Redirect location: {location}")
                    if location.startswith('magnet:'):
                        magnet_link = location
                        app.logger.debug(f"Found magnet link in redirect: {magnet_link}")
                        # Now add the magnet link to qBittorrent
                        qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
                        qb.auth_log_in()
                        result = qb.torrents_add(urls=magnet_link, category=qb_category)
                        if result == 'Ok.':
                            app.logger.debug(f"Successfully sent magnet to qBittorrent: {magnet_link}")
                            return jsonify({'success': True})
                        else:
                            app.logger.error(f"Failed to add torrent. qBittorrent responded: {result}")
                            return jsonify({'error': f"qBittorrent error: {result}"}), 500
                
                # If no redirect or not a magnet link, try to download as torrent file
                response = requests.get(magnet_or_url, timeout=30, allow_redirects=True)
                response.raise_for_status()
                
                # Check if final URL is a magnet link after following redirects
                if response.url.startswith('magnet:'):
                    magnet_link = response.url
                    app.logger.debug(f"Got final magnet link: {magnet_link}")
                    qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
                    qb.auth_log_in()
                    result = qb.torrents_add(urls=magnet_link, category=qb_category)
                    if result == 'Ok.':
                        app.logger.debug(f"Successfully sent magnet to qBittorrent: {magnet_link}")
                        return jsonify({'success': True})
                    else:
                        app.logger.error(f"Failed to add torrent. qBittorrent responded: {result}")
                        return jsonify({'error': f"qBittorrent error: {result}"}), 500
                else:
                    # Try to add as torrent file
                    app.logger.debug("No magnet link found, trying to add as torrent file")
                    qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
                    qb.auth_log_in()
                    result = qb.torrents_add(torrent_files=response.content, category=qb_category)
                    if result == 'Ok.':
                        app.logger.debug("Successfully sent torrent file to qBittorrent")
                        return jsonify({'success': True})
                    else:
                        app.logger.error(f"Failed to add torrent file. qBittorrent responded: {result}")
                        return jsonify({'error': f"qBittorrent error: {result}"}), 500
                        
            except Exception as e:
                app.logger.error(f"Error fetching from Prowlarr URL: {e}")
                return jsonify({'error': f"Failed to fetch from Prowlarr: {str(e)}"}), 500
        else:
            # Assume it's a direct URL to a torrent file
            app.logger.debug("Treating as direct torrent file URL")
            try:
                response = requests.get(magnet_or_url, timeout=30)
                response.raise_for_status()
                qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
                qb.auth_log_in()
                result = qb.torrents_add(torrent_files=response.content, category=qb_category)
                if result == 'Ok.':
                    app.logger.debug("Successfully sent torrent file to qBittorrent")
                    return jsonify({'success': True})
                else:
                    app.logger.error(f"Failed to add torrent file. qBittorrent responded: {result}")
                    return jsonify({'error': f"qBittorrent error: {result}"}), 500
            except Exception as e:
                app.logger.error(f"Error downloading torrent file: {e}")
                return jsonify({'error': f"Failed to download torrent: {str(e)}"}), 500

        # If we got here with a direct magnet link, add it to qBittorrent
        if 'magnet_link' in locals():
            qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
            qb.auth_log_in()
            result = qb.torrents_add(urls=magnet_link, category=qb_category)
            if result == 'Ok.':
                app.logger.debug(f"Successfully sent magnet to qBittorrent: {magnet_link}")
                return jsonify({'success': True})
            else:
                app.logger.error(f"Failed to add torrent. qBittorrent responded: {result}")
                return jsonify({'error': f"qBittorrent error: {result}"}), 500
            
    except Exception as e:
        app.logger.error(f"Unexpected error in download function: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/downloads_status')
def downloads_status():
    try:
        qb = Client(host=CONFIG['qbittorrent']['host'], port=CONFIG['qbittorrent']['port'], username=CONFIG['qbittorrent']['username'], password=CONFIG['qbittorrent']['password'])
        try:
            qb.auth_log_in()
        except Exception as e:
            app.logger.error(f"qBittorrent login failed: {e}")
            return jsonify({'error': 'qBittorrent login failed'}), 500
        torrents = qb.torrents_info()
        torrent_list = []
        for torrent in torrents:
            torrent_list.append({
                'name': torrent.name,
                'size': torrent.size,
                'state': torrent.state,
                'progress': torrent.progress,
                'hash': torrent.hash
            })
        return jsonify(torrent_list)
        return jsonify(torrents)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=CONFIG['app']['debug'],
            host=CONFIG['app']['host'],
            port=CONFIG['app']['port'])