from flask import Flask, render_template, request, jsonify
from plexapi.server import PlexServer
import requests
import os
import json
from datetime import datetime
import time
import threading
import random

app = Flask(__name__)

# === CONFIG ===
PLEX_URL = 'http://192.168.1.5:32400'
PLEX_TOKEN = 'xn1SyxxNZkX2mr5aqNnB'
TMDB_API_KEY = 'a2c079689712769699d8d20249314e73'
JSON_PATH = os.path.join(os.path.dirname(__file__), 'scan_results.json')

# Global variables for scan status
SCAN_STATUS = {
    'in_progress': False,
    'progress': 0,
    'current_show': '',
    'total_shows': 0,
    'processed_shows': 0,
    'status_message': '',
    'start_time': None
}

# === UTILITIES ===
def get_tmdb_id(plex_show):
    title = plex_show.title
    guid = plex_show.guid or ''
    if 'tmdb' in guid:
        return guid.split('//')[1].split('?')[0]
    for guid in plex_show.guids:
        if 'tmdb' in guid.id.lower():
            return guid.id.split('//')[1].split('?')[0]
    search_url = f'https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={requests.utils.quote(title)}'
    try:
        r = requests.get(search_url)
        r.raise_for_status()
        results = r.json()['results']
        if results:
            return str(results[0]['id'])
    except:
        pass
    return None

def get_series_status(tmdb_id):
    url = f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}'
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
                    if last_date > datetime.now():
                        return "Ongoing"
                except:
                    pass
            return status
    except:
        return "Unknown"

def fetch_tmdb_episodes(tmdb_id):
    eps = set()
    try:
        show_info = requests.get(f'https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}').json()
        for season_num in range(1, show_info.get('number_of_seasons', 0) + 1):
            season = requests.get(f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_num}?api_key={TMDB_API_KEY}').json()
            for ep in season.get('episodes', []):
                eps.add((season_num, ep['episode_number']))
    except:
        pass
    return eps

def get_existing_episodes(plex_show):
    existing = set()
    for episode in plex_show.episodes():
        if episode.seasonNumber and episode.index:
            existing.add((episode.seasonNumber, episode.index))
    return existing

def run_scan_thread():
    global SCAN_STATUS
    
    try:
        SCAN_STATUS['in_progress'] = True
        SCAN_STATUS['progress'] = 0
        SCAN_STATUS['status_message'] = 'Connecting to Plex server...'
        SCAN_STATUS['start_time'] = datetime.now()
        
        # Connect to Plex
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        SCAN_STATUS['status_message'] = 'Fetching TV Shows...'
        shows = plex.library.section('TV Shows').all()
        
        SCAN_STATUS['total_shows'] = len(shows)
        SCAN_STATUS['processed_shows'] = 0
        results = {}
        
        for show in shows:
            SCAN_STATUS['current_show'] = show.title
            SCAN_STATUS['status_message'] = f'Processing {show.title}...'
            
            # Get TMDB ID
            SCAN_STATUS['status_message'] = f'Finding TMDb match for {show.title}...'
            tmdb_id = get_tmdb_id(show)
            
            if not tmdb_id:
                results[show.title] = {
                    'status': 'Unknown',
                    'series_status': 'Unknown',
                    'missing_episodes': []
                }
                SCAN_STATUS['processed_shows'] += 1
                SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
                continue
            
            # Get series status
            SCAN_STATUS['status_message'] = f'Checking series status for {show.title}...'
            series_status = get_series_status(tmdb_id)
            
            # Compare episodes
            SCAN_STATUS['status_message'] = f'Comparing episodes for {show.title}...'
            existing = get_existing_episodes(show)
            all_eps = fetch_tmdb_episodes(tmdb_id)
            missing = sorted(list(all_eps - existing))
            
            results[show.title] = {
                'status': 'Complete' if not missing else 'Incomplete',
                'series_status': series_status,
                'missing_episodes': missing
            }
            
            SCAN_STATUS['processed_shows'] += 1
            SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
            
            # Add a small delay to prevent rate limiting and to make progress visible
            time.sleep(random.uniform(0.2, 0.5))
        
        # Add timestamp to results
        results['_last_scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save results
        SCAN_STATUS['status_message'] = 'Saving results...'
        with open(JSON_PATH, 'w') as f:
            json.dump(results, f, indent=2)
        
        # Calculate stats
        complete_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Complete')
        incomplete_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Incomplete')
        unknown_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Unknown')
        
        SCAN_STATUS['status_message'] = f'Scan complete. Found {complete_count} complete, {incomplete_count} incomplete, {unknown_count} unknown shows.'
        SCAN_STATUS['progress'] = 100
        
        # Wait a moment before finishing
        time.sleep(1)
    except Exception as e:
        SCAN_STATUS['status_message'] = f'Error: {str(e)}'
    finally:
        SCAN_STATUS['in_progress'] = False

@app.route('/')
def index():
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, 'r') as f:
            results = json.load(f)
    else:
        results = {}
    return render_template('index.html', results=results)

@app.route('/scan')
def scan():
    global SCAN_STATUS
    
    if SCAN_STATUS['in_progress']:
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
    
    return jsonify({
        'in_progress': SCAN_STATUS['in_progress'],
        'progress': round(SCAN_STATUS['progress'], 1),
        'current_show': SCAN_STATUS['current_show'],
        'processed_shows': SCAN_STATUS['processed_shows'],
        'total_shows': SCAN_STATUS['total_shows'],
        'status_message': SCAN_STATUS['status_message'],
        'elapsed_time': elapsed_time
    })

@app.route('/fix-match', methods=['POST'])
def fix_match():
    title = request.form['title']
    new_tmdb_id = request.form['tmdb_id']
    
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        show = next((s for s in plex.library.section('TV Shows').all() if s.title == title), None)
        if not show:
            return jsonify(success=False, error='Show not found')

        # Get new metadata
        existing = get_existing_episodes(show)
        all_eps = fetch_tmdb_episodes(new_tmdb_id)
        missing = sorted(list(all_eps - existing))
        series_status = get_series_status(new_tmdb_id)

        # Update results file
        if os.path.exists(JSON_PATH):
            with open(JSON_PATH, 'r') as f:
                results = json.load(f)
        else:
            results = {}

        results[title] = {
            'status': 'Complete' if not missing else 'Incomplete',
            'series_status': series_status,
            'missing_episodes': missing,
            'tmdb_id': new_tmdb_id  # Store the TMDB ID for reference
        }

        # Update last scan time
        results['_last_scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with open(JSON_PATH, 'w') as f:
            json.dump(results, f, indent=2)

        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/get-show-details', methods=['GET'])
def get_show_details():
    title = request.args.get('title')
    if not title:
        return jsonify(success=False, error='No title provided')
    
    try:
        # Load current results
        if os.path.exists(JSON_PATH):
            with open(JSON_PATH, 'r') as f:
                results = json.load(f)
            
            if title in results:
                return jsonify(success=True, data=results[title])
            else:
                return jsonify(success=False, error='Show not found')
        else:
            return jsonify(success=False, error='No scan results available')
    except Exception as e:
        return jsonify(success=False, error=str(e))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
