from flask import Flask, render_template, request, jsonify
from plexapi.server import PlexServer
import requests
import os
import json
from datetime import datetime

app = Flask(__name__)

# === CONFIG ===
PLEX_URL = 'http://192.168.1.5:32400'
PLEX_TOKEN = 'xn1SyxxNZkX2mr5aqNnB'
TMDB_API_KEY = 'a2c079689712769699d8d20249314e73'
JSON_PATH = os.path.join(os.path.dirname(__file__), 'scan_results.json')

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
    plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    shows = plex.library.section('TV Shows').all()
    results = {}

    for show in shows:
        title = show.title
        tmdb_id = get_tmdb_id(show)
        if not tmdb_id:
            results[title] = {
                'status': 'Unknown',
                'series_status': 'Unknown',
                'missing_episodes': []
            }
            continue

        series_status = get_series_status(tmdb_id)
        existing = get_existing_episodes(show)
        all_eps = fetch_tmdb_episodes(tmdb_id)
        missing = sorted(list(all_eps - existing))

        results[title] = {
            'status': 'Complete' if not missing else 'Incomplete',
            'series_status': series_status,
            'missing_episodes': missing
        }

    with open(JSON_PATH, 'w') as f:
        json.dump(results, f, indent=2)

    return jsonify(success=True)

@app.route('/fix-match', methods=['POST'])
def fix_match():
    title = request.form['title']
    new_tmdb_id = request.form['tmdb_id']
    plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    show = next((s for s in plex.library.section('TV Shows').all() if s.title == title), None)
    if not show:
        return jsonify(success=False, error='Show not found')

    existing = get_existing_episodes(show)
    all_eps = fetch_tmdb_episodes(new_tmdb_id)
    missing = sorted(list(all_eps - existing))
    series_status = get_series_status(new_tmdb_id)

    with open(JSON_PATH, 'r') as f:
        results = json.load(f)

    results[title] = {
        'status': 'Complete' if not missing else 'Incomplete',
        'series_status': series_status,
        'missing_episodes': missing
    }

    with open(JSON_PATH, 'w') as f:
        json.dump(results, f, indent=2)

    return jsonify(success=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)

