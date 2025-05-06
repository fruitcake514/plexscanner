from flask import Flask, render_template, request, jsonify, redirect, url_for
from plexapi.server import PlexServer
import requests
import os
import json
from datetime import datetime
import time
import threading
import random

app = Flask(__name__)

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_PATH, 'r') as config_file:
    CONFIG = json.load(config_file)

# Set paths
JSON_PATH = os.path.join(os.path.dirname(__file__), CONFIG['app']['scan_results_path'])

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

# === UTILITIES ===
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
        SCAN_STATUS['partial_results'] = {}
        
        # Connect to Plex
        plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
        SCAN_STATUS['status_message'] = 'Fetching TV Shows...'
        shows = plex.library.section(CONFIG['plex']['library_section']).all()
        
        SCAN_STATUS['total_shows'] = len(shows)
        SCAN_STATUS['processed_shows'] = 0
        results = {}
        
        # Load existing results if available
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, 'r') as f:
                    existing_results = json.load(f)
                    # Copy any existing special keys (stats, timestamp)
                    for key in existing_results:
                        if key.startswith('_'):
                            results[key] = existing_results[key]
            except:
                pass
        
        for show in shows:
            # Check if stop was requested
            if SCAN_STATUS['stop_requested']:
                SCAN_STATUS['status_message'] = 'Scan stopped by user. Saving partial results...'
                break
                
            SCAN_STATUS['current_show'] = show.title
            SCAN_STATUS['status_message'] = f'Processing {show.title}...'
            
            # Get TMDB ID
            SCAN_STATUS['status_message'] = f'Finding TMDb match for {show.title}...'
            tmdb_id = get_tmdb_id(show)
            
            # Get poster
            poster_url = None
            details = None
            if tmdb_id:
                poster_url = get_show_poster(tmdb_id)
                details = get_show_details(tmdb_id)
            
            if not tmdb_id:
                results[show.title] = {
                    'status': 'Unknown',
                    'series_status': 'Unknown',
                    'missing_episodes': [],
                    'poster_url': poster_url,
                    'details': None
                }
                SCAN_STATUS['processed_shows'] += 1
                SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
                
                # Save partial results
                SCAN_STATUS['partial_results'] = results.copy()
                continue
            
            # Get series status
            SCAN_STATUS['status_message'] = f'Checking series status for {show.title}...'
            series_status = get_series_status(tmdb_id)
            
            # Compare episodes
            SCAN_STATUS['status_message'] = f'Comparing episodes for {show.title}...'
            existing, existing_episode_details = get_existing_episodes(show)
            all_eps = fetch_tmdb_episodes(tmdb_id)
            
            # Filter out future episodes when calculating missing episodes
            aired_missing = []
            future_episodes = []
            
            # Process missing episodes
            for season_num, ep_num in sorted(list(all_eps - existing)):
                # Get episode air date
                air_date = None
                has_aired = True
                
                # Check if we have season data to determine air date
                season_details = get_season_details(tmdb_id, season_num)
                if season_details and 'episodes' in season_details:
                    for ep in season_details.get('episodes', []):
                        if ep.get('episode_number') == ep_num:
                            air_date = ep.get('air_date')
                            if air_date:
                                try:
                                    air_date_obj = datetime.strptime(air_date, '%Y-%m-%d')
                                    # Set to start of day for today's date
                                    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                                    # Consider episodes released today as upcoming (not aired yet)
                                    has_aired = air_date_obj < today_start
                                except:
                                    has_aired = True
                            break
                
                if has_aired:
                    aired_missing.append((season_num, ep_num))
                else:
                    future_episodes.append((season_num, ep_num))
            
            missing = aired_missing
            
            # Organize missing episodes by season
            missing_by_season = {}
            for s, e in missing:
                if s not in missing_by_season:
                    missing_by_season[s] = []
                missing_by_season[s].append(e)
            
            # Get all seasons from TMDb
            all_seasons = set([s for s, _ in all_eps])
            seasons_data = {}
            
            for season_num in all_seasons:
                # Get detailed season info
                season_details = get_season_details(tmdb_id, season_num)
                
                if season_details and 'episodes' in season_details:
                    # Create a dictionary of episodes
                    season_episodes = {}
                    
                    for ep in season_details.get('episodes', []):
                        episode_num = ep.get('episode_number')
                        
                        # Check if episode has aired yet
                        air_date = ep.get('air_date')
                        has_aired = True
                        if air_date:
                            try:
                                air_date_obj = datetime.strptime(air_date, '%Y-%m-%d')
                                # Set to start of day for today's date
                                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                                # Consider episodes released today as upcoming (not aired yet)
                                has_aired = air_date_obj < today_start
                            except:
                                has_aired = True  # Default to assuming it has aired if date parsing fails
                        
                        # Common episode data
                        episode_data = {
                            'title': ep.get('name', f'Episode {episode_num}'),
                            'air_date': air_date,
                            'overview': ep.get('overview'),
                            'still_path': ep.get('still_path'),
                            'episode_number': episode_num,
                            'exists': (season_num, episode_num) in existing,
                            'has_aired': has_aired
                        }
                        
                        # Add details for existing episodes
                        if episode_data['exists'] and season_num in existing_episode_details and episode_num in existing_episode_details[season_num]:
                            episode_data.update(existing_episode_details[season_num][episode_num])
                        
                        # Add to our episodes dictionary
                        season_episodes[episode_num] = episode_data
                    
                    seasons_data[season_num] = {
                        'name': season_details.get('name', f'Season {season_num}'),
                        'overview': season_details.get('overview'),
                        'poster_path': season_details.get('poster_path'),
                        'episodes': season_episodes,
                    }
            
            # Organize future episodes by season
            future_by_season = {}
            for s, e in future_episodes:
                if s not in future_by_season:
                    future_by_season[s] = []
                future_by_season[s].append(e)
            
            results[show.title] = {
                'status': 'Complete' if not missing else 'Incomplete',
                'series_status': series_status,
                'missing_episodes': missing,
                'missing_by_season': missing_by_season,
                'future_episodes': future_episodes,
                'future_by_season': future_by_season,
                'tmdb_id': tmdb_id,
                'poster_url': poster_url,
                'details': details,
                'seasons': seasons_data
            }
            
            SCAN_STATUS['processed_shows'] += 1
            SCAN_STATUS['progress'] = (SCAN_STATUS['processed_shows'] / SCAN_STATUS['total_shows']) * 100
            
            # Save partial results after each show
            SCAN_STATUS['partial_results'] = results.copy()
            
            # Add a small delay to prevent rate limiting and to make progress visible
            time.sleep(random.uniform(0.2, 0.5))
        
        # Add timestamp to results
        results['_last_scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Add stats to results
        complete_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Complete')
        incomplete_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Incomplete')
        unknown_count = sum(1 for data in results.values() if isinstance(data, dict) and data.get('status') == 'Unknown')
        
        results['_stats'] = {
            'complete': complete_count,
            'incomplete': incomplete_count,
            'unknown': unknown_count,
            'total': complete_count + incomplete_count + unknown_count
        }
        
        # Save results
        SCAN_STATUS['status_message'] = 'Saving results...'
        with open(JSON_PATH, 'w') as f:
            json.dump(results, f, indent=2)
        
        if SCAN_STATUS['stop_requested']:
            SCAN_STATUS['status_message'] = f'Scan stopped by user. Processed {SCAN_STATUS["processed_shows"]} of {SCAN_STATUS["total_shows"]} shows.'
        else:
            SCAN_STATUS['status_message'] = f'Scan complete. Found {complete_count} complete, {incomplete_count} incomplete, {unknown_count} unknown shows.'
            SCAN_STATUS['progress'] = 100
        
        # Wait a moment before finishing
        time.sleep(1)
    except Exception as e:
        SCAN_STATUS['status_message'] = f'Error: {str(e)}'
        
        # Save partial results on error
        if SCAN_STATUS['partial_results']:
            try:
                with open(JSON_PATH, 'w') as f:
                    json.dump(SCAN_STATUS['partial_results'], f, indent=2)
            except:
                pass
    finally:
        SCAN_STATUS['in_progress'] = False
        SCAN_STATUS['stop_requested'] = False

@app.route('/')
def index():
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, 'r') as f:
            results = json.load(f)
    else:
        results = {}
    return render_template('index.html', results=results)

@app.route('/show/<path:title>')
def show_details(title):
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, 'r') as f:
            results = json.load(f)
        
        if title in results:
            show_data = results[title]
            # Fetch detailed season info for shows with missing episodes
            if show_data.get('missing_by_season'):
                tmdb_id = show_data.get('tmdb_id')
                seasons_data = {}
                for season_num in show_data['missing_by_season'].keys():
                    seasons_data[season_num] = get_season_details(tmdb_id, season_num)
                return render_template('show_details.html', title=title, show=show_data, seasons=seasons_data)
            
            return render_template('show_details.html', title=title, show=show_data)
    
    return redirect(url_for('index'))

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

@app.route('/fix-match', methods=['POST'])
def fix_match():
    title = request.form['title']
    new_tmdb_id = request.form['tmdb_id']
    
    try:
        plex = PlexServer(CONFIG['plex']['url'], CONFIG['plex']['token'])
        show = next((s for s in plex.library.section(CONFIG['plex']['library_section']).all() if s.title == title), None)
        if not show:
            return jsonify(success=False, error='Show not found')

        # Get new metadata
        existing, existing_episode_details = get_existing_episodes(show)
        all_eps = fetch_tmdb_episodes(new_tmdb_id)
        
        # Filter out future episodes when calculating missing episodes
        aired_missing = []
        future_episodes = []
        
        # Process missing episodes
        for season_num, ep_num in sorted(list(all_eps - existing)):
            # Get episode air date
            air_date = None
            has_aired = True
            
            # Check if we have season data to determine air date
            season_details = get_season_details(new_tmdb_id, season_num)
            if season_details and 'episodes' in season_details:
                for ep in season_details.get('episodes', []):
                    if ep.get('episode_number') == ep_num:
                        air_date = ep.get('air_date')
                        if air_date:
                            try:
                                air_date_obj = datetime.strptime(air_date, '%Y-%m-%d')
                                # Set to start of day for today's date
                                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                                # Consider episodes released today as upcoming (not aired yet)
                                has_aired = air_date_obj < today_start
                            except:
                                has_aired = True
                        break
            
            if has_aired:
                aired_missing.append((season_num, ep_num))
            else:
                future_episodes.append((season_num, ep_num))
        
        missing = aired_missing
        
        # Organize missing episodes by season
        missing_by_season = {}
        for s, e in missing:
            if s not in missing_by_season:
                missing_by_season[s] = []
            missing_by_season[s].append(e)
            
        series_status = get_series_status(new_tmdb_id)
        poster_url = get_show_poster(new_tmdb_id)
        details = get_show_details(new_tmdb_id)
        
        # Get all seasons from TMDb
        all_seasons = set([s for s, _ in all_eps])
        seasons_data = {}
        
        for season_num in all_seasons:
            # Get detailed season info
            season_details = get_season_details(new_tmdb_id, season_num)
            
            if season_details and 'episodes' in season_details:
                # Create a dictionary of episodes
                season_episodes = {}
                
                for ep in season_details.get('episodes', []):
                    episode_num = ep.get('episode_number')
                    
                    # Check if episode has aired yet
                    air_date = ep.get('air_date')
                    has_aired = True
                    if air_date:
                        try:
                            air_date_obj = datetime.strptime(air_date, '%Y-%m-%d')
                            has_aired = air_date_obj <= datetime.now()
                        except:
                            has_aired = True  # Default to assuming it has aired if date parsing fails
                    
                    # Common episode data
                    episode_data = {
                        'title': ep.get('name', f'Episode {episode_num}'),
                        'air_date': air_date,
                        'overview': ep.get('overview'),
                        'still_path': ep.get('still_path'),
                        'episode_number': episode_num,
                        'exists': (season_num, episode_num) in existing,
                        'has_aired': has_aired
                    }
                    
                    # Add details for existing episodes
                    if episode_data['exists'] and season_num in existing_episode_details and episode_num in existing_episode_details[season_num]:
                        episode_data.update(existing_episode_details[season_num][episode_num])
                    
                    # Add to our episodes dictionary
                    season_episodes[episode_num] = episode_data
                
                seasons_data[season_num] = {
                    'name': season_details.get('name', f'Season {season_num}'),
                    'overview': season_details.get('overview'),
                    'poster_path': season_details.get('poster_path'),
                    'episodes': season_episodes,
                }

        # Update results file
        if os.path.exists(JSON_PATH):
            with open(JSON_PATH, 'r') as f:
                results = json.load(f)
        else:
            results = {}

        # Organize future episodes by season
        future_by_season = {}
        for s, e in future_episodes:
            if s not in future_by_season:
                future_by_season[s] = []
            future_by_season[s].append(e)
        
        results[title] = {
            'status': 'Complete' if not missing else 'Incomplete',
            'series_status': series_status,
            'missing_episodes': missing,
            'missing_by_season': missing_by_season,
            'future_episodes': future_episodes,
            'future_by_season': future_by_season,
            'tmdb_id': new_tmdb_id,
            'poster_url': poster_url,
            'details': details,
            'seasons': seasons_data
        }

        # Update last scan time
        results['_last_scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with open(JSON_PATH, 'w') as f:
            json.dump(results, f, indent=2)

        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/get-show-details', methods=['GET'])
def get_show_details_api():
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
    app.run(debug=CONFIG['app']['debug'], 
            host=CONFIG['app']['host'], 
            port=CONFIG['app']['port'])
