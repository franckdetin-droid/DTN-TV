# ==============================================================================
# SERVEUR DÉDIÉ STOCKAGE 2 To & STREAMING FLUIDE (storage_server.py)
# Support HTTP 206 Partial Content (Range Requests) pour vidéos lourdes
# ==============================================================================

import os
import re
from flask import Flask, request, Response, jsonify

app = Flask(__name__)
STORAGE_DIR = os.path.join(os.path.dirname(__file__), 'media_storage_2tb')
os.makedirs(STORAGE_DIR, exist_ok=True)

MAX_CAPACITY_GB = 2048  # 2 To

def get_storage_stats():
    total_bytes = 0
    file_count = 0
    for root, dirs, files in os.walk(STORAGE_DIR):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.exists(fp):
                total_bytes += os.path.getsize(fp)
                file_count += 1
    used_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
    free_gb = round(MAX_CAPACITY_GB - used_gb, 2)
    return {
        "max_capacity_gb": MAX_CAPACITY_GB,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "file_count": file_count
    }

@app.route('/api/storage/status')
def storage_status():
    return jsonify(get_storage_stats())

@app.route('/stream/<filename>')
def stream_video(filename):
    path = os.path.join(STORAGE_DIR, filename)
    if not os.path.exists(path):
        return Response("Fichier vidéo non trouvé", status=404)

    file_size = os.path.getsize(path)
    range_header = request.headers.get('Range', None)

    if not range_header:
        with open(path, 'rb') as f:
            data = f.read()
        return Response(data, 200, mimetype='video/mp4')

    byte1, byte2 = 0, None
    m = re.search(r'bytes=(\d+)-(\d*)', range_header)
    if m:
        g = m.groups()
        byte1 = int(g[0])
        if g[1]:
            byte2 = int(g[1])

    if byte2 is None:
        byte2 = file_size - 1

    length = byte2 - byte1 + 1
    with open(path, 'rb') as f:
        f.seek(byte1)
        data = f.read(length)

    rv = Response(data, 206, mimetype='video/mp4', direct_passthrough=True)
    rv.headers.add('Content-Range', f'bytes {byte1}-{byte2}/{file_size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv

if __name__ == '__main__':
    port = int(os.environ.get('STORAGE_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
