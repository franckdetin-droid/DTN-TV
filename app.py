# ==============================================================================
# VISION+ TV WEB — SERVEUR PRINCIPAL & RÉGIE COMPLÈTE 24/7 (app.py)
# Gestion des Chaînes, Grille des Programmes, Upload 2 To & Flux Streaming
# ==============================================================================

import os
import json
import uuid
from flask import Flask, render_template, request, jsonify, send_file, Response, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'vision_plus_tv_secret_key_2026')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 * 1024  # Support jusqu'à 50 Go par upload

ADMIN_PIN = os.environ.get('ADMIN_PIN', '3004')
DB_FILE = os.path.join(os.path.dirname(__file__), 'channels_db.json')
STORAGE_FOLDER = os.path.join(os.path.dirname(__file__), 'media_storage_2tb')
os.makedirs(STORAGE_FOLDER, exist_ok=True)

def load_channels():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Erreur lecture DB: {e}")
    return []

def save_channels(channels):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(channels, f, indent=2, ensure_ascii=False)

@app.route('/')
def index():
    channels = load_channels()
    ch_id = request.args.get('channel', 'channel-1')
    current_channel = next((c for c in channels if c['id'] == ch_id), channels[0] if channels else None)
    return render_template('index.html', channels=channels, current_channel=current_channel)

@app.route('/admin')
def admin():
    channels = load_channels()
    return render_template('admin.html', channels=channels)

@app.route('/api/auth', methods=['POST'])
def auth_admin():
    data = request.get_json() or {}
    pin = data.get('pin', '')
    if str(pin).strip() == str(ADMIN_PIN).strip():
        return jsonify({"authenticated": True, "token": "session_token_ok"})
    return jsonify({"authenticated": False, "error": "Code PIN incorrect (par défaut: 3004)"}), 401

@app.route('/api/channels', methods=['GET', 'POST'])
def handle_channels():
    if request.method == 'POST':
        data = request.get_json()
        if data is not None:
            save_channels(data)
            return jsonify({"status": "success", "count": len(data), "channels": data})
        return jsonify({"error": "Données invalides"}), 400
    return jsonify(load_channels())

@app.route('/api/channels/add', methods=['POST'])
def add_channel():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "Le nom de la chaîne est requis"}), 400
    
    channels = load_channels()
    new_id = f"channel-{len(channels) + 1}-{uuid.uuid4().hex[:4]}"
    new_num = data.get('number') or f"CH {str(len(channels) + 1).zfill(2)}"
    new_logo = data.get('logoText') or (name[:2].upper() if len(name) >= 2 else "V+")
    
    new_channel = {
        "id": new_id,
        "name": name,
        "number": new_num,
        "logoText": new_logo,
        "slogan": data.get('slogan', 'Télévision en Continu 24/7'),
        "genre": data.get('genre', 'Généraliste'),
        "tickerActive": True,
        "tickerText": data.get('tickerText', f"BIENVENUE SUR {name.upper()} — DIRECT 24H/24"),
        "schedule": []
    }
    
    channels.append(new_channel)
    save_channels(channels)
    return jsonify({"status": "success", "channel": new_channel, "channels": channels})

@app.route('/api/channels/<channel_id>/delete', methods=['POST'])
def delete_channel(channel_id):
    channels = load_channels()
    if len(channels) <= 1:
        return jsonify({"error": "Impossible de supprimer la dernière chaîne"}), 400
    channels = [c for c in channels if c['id'] != channel_id]
    save_channels(channels)
    return jsonify({"status": "success", "channels": channels})

@app.route('/api/channels/<channel_id>/schedule/add', methods=['POST'])
def add_program(channel_id):
    data = request.get_json() or {}
    channels = load_channels()
    ch = next((c for c in channels if c['id'] == channel_id), None)
    if not ch:
        return jsonify({"error": "Chaîne introuvable"}), 404
    
    title = data.get('title', 'Programme Sans Titre').strip()
    video_url = data.get('videoUrl', '').strip()
    play_now = data.get('playNow', False)
    
    prog = {
        "id": f"prog-{uuid.uuid4().hex[:6]}",
        "title": title,
        "category": data.get('category', 'Direct'),
        "description": data.get('description', ''),
        "mediaType": data.get('mediaType', 'video'),
        "videoUrl": video_url,
        "durationSeconds": int(data.get('durationSeconds', 1800)),
        "day": "Tous les jours",
        "startTime": data.get('startTime', '12:00'),
        "endTime": data.get('endTime', '13:00'),
        "notes": data.get('notes', 'Diffusion 24/7'),
        "subtitles": {
            "fr": f"Sous-titrage direct : {title}"
        }
    }
    
    if 'schedule' not in ch or not isinstance(ch['schedule'], list):
        ch['schedule'] = []
        
    if play_now:
        ch['schedule'].insert(0, prog)
    else:
        ch['schedule'].append(prog)
        
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route('/api/channels/<channel_id>/schedule/<int:prog_idx>/delete', methods=['POST'])
def delete_program(channel_id, prog_idx):
    channels = load_channels()
    ch = next((c for c in channels if c['id'] == channel_id), None)
    if not ch or 'schedule' not in ch or prog_idx >= len(ch['schedule']):
        return jsonify({"error": "Programme introuvable"}), 404
    
    del ch['schedule'][prog_idx]
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route('/api/channels/<channel_id>/schedule/<int:prog_idx>/play', methods=['POST'])
def play_program_now(channel_id, prog_idx):
    channels = load_channels()
    ch = next((c for c in channels if c['id'] == channel_id), None)
    if not ch or 'schedule' not in ch or prog_idx >= len(ch['schedule']):
        return jsonify({"error": "Programme introuvable"}), 404
    
    prog = ch['schedule'].pop(prog_idx)
    ch['schedule'].insert(0, prog)
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route('/api/channels/<channel_id>/ticker', methods=['POST'])
def update_ticker(channel_id):
    data = request.get_json() or {}
    channels = load_channels()
    ch = next((c for c in channels if c['id'] == channel_id), None)
    if not ch:
        return jsonify({"error": "Chaîne introuvable"}), 404
    
    ch['tickerText'] = data.get('tickerText', ch.get('tickerText', ''))
    ch['tickerActive'] = data.get('tickerActive', True)
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "Aucun fichier fourni"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Nom de fichier vide"}), 400
        
    filename = secure_filename(file.filename)
    dest = os.path.join(STORAGE_FOLDER, filename)
    file.save(dest)
    file_size_mb = round(os.path.getsize(dest) / (1024 * 1024), 2)
    video_url = f"/media/{filename}"
    
    channel_id = request.form.get('channel_id')
    title = request.form.get('title')
    play_now = request.form.get('play_now') == 'true'
    
    if channel_id and title:
        channels = load_channels()
        ch = next((c for c in channels if c['id'] == channel_id), None)
        if ch:
            prog = {
                "id": f"prog-{uuid.uuid4().hex[:6]}",
                "title": title,
                "category": request.form.get('category', 'Direct'),
                "description": request.form.get('description', ''),
                "mediaType": "video",
                "videoUrl": video_url,
                "durationSeconds": 1800,
                "day": "Tous les jours",
                "startTime": request.form.get('startTime', '12:00'),
                "endTime": request.form.get('endTime', '13:00'),
                "notes": f"Fichier local : {filename} ({file_size_mb} Mo)",
                "subtitles": { "fr": f"Diffusion : {title}" }
            }
            if 'schedule' not in ch or not isinstance(ch['schedule'], list):
                ch['schedule'] = []
            if play_now:
                ch['schedule'].insert(0, prog)
            else:
                ch['schedule'].append(prog)
            save_channels(channels)
            
    return jsonify({
        "status": "success",
        "filename": filename,
        "url": video_url,
        "size_mb": file_size_mb
    })

@app.route('/api/storage/status')
def storage_status():
    files_list = []
    total_bytes = 0
    if os.path.exists(STORAGE_FOLDER):
        for f in os.listdir(STORAGE_FOLDER):
            fp = os.path.join(STORAGE_FOLDER, f)
            if os.path.isfile(fp):
                sz = os.path.getsize(fp)
                total_bytes += sz
                files_list.append({
                    "name": f,
                    "size_mb": round(sz / (1024 * 1024), 2),
                    "url": f"/media/{f}"
                })
    used_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
    return jsonify({
        "max_capacity_gb": 2048,
        "used_gb": used_gb,
        "free_gb": round(2048 - used_gb, 2),
        "files": files_list
    })

@app.route('/download/<path:filename>')
def download_media(filename):
    return send_from_directory(STORAGE_FOLDER, filename, as_attachment=True)

@app.route('/media/<path:filename>')
def serve_media(filename):
    return send_from_directory(STORAGE_FOLDER, filename)

@app.route('/live/playlist.m3u')
def get_m3u():
    channels = load_channels()
    m3u = '#EXTM3U x-tvg-url=""\n'
    for ch in channels:
        video_url = ch['schedule'][0]['videoUrl'] if ch.get('schedule') and ch['schedule'][0].get('videoUrl') else f"{request.host_url}api/stream/{ch['id']}"
        m3u += f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" tvg-logo="{ch.get("logoText", "V+")}" group-title="VISION+ TV",Canal {ch["number"]} - {ch["name"]}\n'
        m3u += f'{video_url}\n'
    return Response(m3u, mimetype='application/x-mpegurl')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=False)
