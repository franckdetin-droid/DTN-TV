# ==============================================================================
# DTN TV — Régie TV + stockage personnel persistant configurable
# ==============================================================================

import os
import json
import uuid
import hashlib
import re
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, render_template, request, jsonify, send_file, Response, abort, redirect, session
from werkzeug.utils import secure_filename
from sqlalchemy import func, inspect, text

from storage_service import db, StorageService, StorageFile, StorageFolder, ShareLink
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_BYTES", 50 * 1024 * 1024 * 1024))
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "storage.sqlite3")).replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["STORAGE_FOLDER"] = os.environ.get("STORAGE_FOLDER", os.path.join(BASE_DIR, "media_storage_2tb"))
app.config["STORAGE_BACKEND"] = os.environ.get("STORAGE_BACKEND", "local")
app.config["STORAGE_CAPACITY_GB"] = int(os.environ.get("STORAGE_CAPACITY_GB", "2048"))
app.config["STORAGE_ENDPOINT"] = os.environ.get("STORAGE_ENDPOINT", "")
app.config["STORAGE_BUCKET"] = os.environ.get("STORAGE_BUCKET", "")
app.config["STORAGE_ACCESS_KEY"] = os.environ.get("STORAGE_ACCESS_KEY", "")
app.config["STORAGE_SECRET_KEY"] = os.environ.get("STORAGE_SECRET_KEY", "")
app.config["STORAGE_REGION"] = os.environ.get("STORAGE_REGION", "us-east-1")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

db.init_app(app)
storage = StorageService(app)

ADMIN_PIN = os.environ.get("ADMIN_PIN", "3004")

class ChannelAccount(db.Model):
    __tablename__ = "channel_accounts"
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    active = db.Column(db.Boolean, default=True)

class AnalyticsEvent(db.Model):
    __tablename__ = "analytics_events"
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(32), nullable=False, index=True)
    channel_id = db.Column(db.String(128), nullable=True, index=True)
    program_id = db.Column(db.String(128), nullable=True, index=True)
    program_title = db.Column(db.String(255), nullable=True)
    visitor_id = db.Column(db.String(128), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

def channel_account(channel_id):
    return ChannelAccount.query.filter_by(channel_id=channel_id, active=True).first()

def is_global_admin():
    return bool(session.get("admin_authenticated"))

def is_channel_admin(channel_id):
    return session.get("channel_admin_id") == channel_id

def can_manage_channel(channel_id):
    return is_global_admin() or is_channel_admin(channel_id)

def channel_admin_required(channel_id):
    if not can_manage_channel(channel_id):
        return jsonify({"error": "Accès administrateur de la chaîne requis"}), 401
    return None
DB_FILE = os.path.join(BASE_DIR, "channels_db.json")
os.makedirs(app.config["STORAGE_FOLDER"], exist_ok=True)

def load_channels():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erreur lecture DB: {e}")
    return []

def save_channels(channels):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(channels, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DB_FILE)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authenticated") and not session.get("channel_admin_id"):
            return jsonify({"error": "Authentification administrateur requise"}), 401
        return fn(*args, **kwargs)
    return wrapper

def normalize_days(days):
    if not isinstance(days, list):
        return ["Tous les jours"]
    valid = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    result = [d for d in days if d in valid]
    return result or ["Tous les jours"]

def minutes_from_time(value):
    try:
        h, m = map(int, str(value).split(":"))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return 0

def time_from_minutes(total):
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"

def calc_end_time(start, duration):
    try:
        duration = max(1, int(duration))
    except Exception:
        duration = 1800
    return time_from_minutes(minutes_from_time(start) + (duration + 59) // 60)

def youtube_id(url):
    try:
        raw = str(url or '').strip()
        if not raw:
            return None
        u = __import__('urllib.parse', fromlist=['urlparse']).urlparse(raw)
        host = (u.hostname or '').lower().removeprefix('www.')
        if host == 'youtu.be':
            value = u.path.strip('/').split('/')[0] if u.path else ''
            return value if re.fullmatch(r'[A-Za-z0-9_-]{11}', value or '') else None
        if host in ('youtube.com', 'm.youtube.com', 'youtube-nocookie.com'):
            if u.query:
                from urllib.parse import parse_qs
                value = (parse_qs(u.query).get('v') or [''])[0]
                if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
                    return value
            parts = [x for x in u.path.split('/') if x]
            if len(parts) >= 2 and parts[0].lower() in ('shorts', 'embed', 'live', 'v'):
                value = parts[1]
                return value if re.fullmatch(r'[A-Za-z0-9_-]{11}', value) else None
    except Exception:
        pass
    return None

def source_info(url):
    if not url:
        return "video", url
    yt = youtube_id(url)
    if yt:
        return "youtube", f"https://www.youtube.com/embed/{yt}?enablejsapi=1&playsinline=1&rel=0"
    low = url.lower().split("?")[0]
    if low.endswith(".m3u8"):
        return "hls", url
    if re.search(r"\.(mp4|webm|ogg|mov|m4v)(?:$)", low):
        return "video", url
    return "web", url


DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

def program_days(program):
    raw = program.get("days", program.get("day", ["Tous les jours"]))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return ["Tous les jours"]
    if "Tous les jours" in raw:
        return DAY_NAMES[:]
    return [d for d in raw if d in DAY_NAMES]

def program_is_live(program):
    return bool(program.get("isLive") or program.get("live") or program.get("mediaType") in ("live", "live_stream"))

def time_in_window(now_minutes, start_minutes, end_minutes, overnight=False):
    if overnight:
        return now_minutes >= start_minutes or now_minutes < end_minutes
    return start_minutes <= now_minutes < end_minutes

def get_current_program(channel, now=None):
    """
    Sélectionne le programme réellement à l'antenne selon le jour et l'heure.
    Un flux live sans heure de fin reste actif jusqu'à ce qu'un autre programme
    planifié commence. Une vidéo enregistrée utilise sa durée/heure de fin.
    """
    now = now or datetime.now().astimezone()
    day_name = DAY_NAMES[now.weekday()]
    now_min = now.hour * 60 + now.minute
    schedule = channel.get("schedule") or []

    eligible = []
    for index, p in enumerate(schedule):
        days = program_days(p)
        if day_name not in days:
            continue
        start = minutes_from_time(p.get("startTime", "00:00"))
        end_raw = p.get("endTime")
        end = minutes_from_time(end_raw) if end_raw else None
        live = program_is_live(p)

        if live and end_raw:
            active = time_in_window(now_min, start, end, end < start)
        elif live and not end_raw:
            active = now_min >= start
        elif end_raw:
            active = time_in_window(now_min, start, end, end < start)
        else:
            # Sans fin explicite, une durée connue détermine la fin.
            duration = int(p.get("durationSeconds") or 0)
            if duration > 0:
                end_calc = (start + (duration + 59) // 60) % 1440
                active = time_in_window(now_min, start, end_calc, end_calc < start)
            else:
                active = now_min >= start

        if active:
            eligible.append((start, index, p))

    if eligible:
        # Le programme ayant commencé le plus récemment est prioritaire.
        eligible.sort(key=lambda x: ((now_min - x[0]) % 1440, x[1]), reverse=True)
        return eligible[0][2], None

    # Aucun programme à l'antenne : chercher le prochain programme du jour.
    upcoming = []
    for index, p in enumerate(schedule):
        if day_name not in program_days(p):
            continue
        start = minutes_from_time(p.get("startTime", "00:00"))
        if start > now_min:
            upcoming.append((start, index, p))
    if upcoming:
        upcoming.sort(key=lambda x: (x[0], x[1]))
        return None, upcoming[0][2]

    return None, None

def reorder_schedule_for_current(channel):
    current, _next = get_current_program(channel)
    if not current:
        return channel
    schedule = channel.get("schedule") or []
    if current in schedule:
        channel["schedule"] = [current] + [p for p in schedule if p is not current]
    return channel

def enrich_program(data, video_url="", filename=None):
    title = str(data.get("title", "Programme Sans Titre")).strip() or "Programme Sans Titre"
    duration = int(data.get("durationSeconds") or 0)
    start = data.get("startTime") or "12:00"
    end = data.get("endTime") or calc_end_time(start, duration or 1800)
    if duration > 0 and (not data.get("endTime") or data.get("autoEnd")):
        end = calc_end_time(start, duration)
    playback, playback_url = source_info(video_url)
    is_live = bool(data.get("isLive") or data.get("mediaType") in ("live", "live_stream"))
    if is_live:
        playback = "hls" if playback == "hls" else playback
        end = None
    return {
        "id": f"prog-{uuid.uuid4().hex[:10]}",
        "title": title,
        "category": data.get("category", "Direct"),
        "description": data.get("description", ""),
        "mediaType": data.get("mediaType", "video"),
        "videoUrl": video_url,
        "playbackType": playback,
        "playbackUrl": playback_url,
        "durationSeconds": duration,
        "day": normalize_days(data.get("days", data.get("day", ["Tous les jours"]))),
        "startTime": start,
        "endTime": end,
        "autoEnd": bool(data.get("autoEnd", True)) and not is_live,
        "autoNext": bool(data.get("autoNext", True)),
        "isLive": is_live,
        "notes": data.get("notes", f"Fichier local : {filename}" if filename else "Diffusion programmée"),
        "subtitles": {"fr": f"Diffusion : {title}"}
    }

@app.context_processor
def inject_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = uuid.uuid4().hex
    return {"csrf_token": session["csrf_token"]}

@app.before_request
def csrf_check():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint not in ("auth_admin", "static"):
        if session.get("admin_authenticated"):
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not supplied or supplied != session.get("csrf_token"):
                return jsonify({"error": "Jeton CSRF invalide"}), 403

@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "Fichier trop volumineux"}), 413

@app.errorhandler(400)
def bad_request(_):
    if request.path.startswith("/api/") or request.path == "/upload":
        return jsonify({"error": "Requête invalide ou données incomplètes"}), 400
    return "Requête invalide", 400

@app.route("/api/analytics/event", methods=["POST"])
def analytics_event():
    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type", "view")).strip().lower()
    if event_type not in {"view", "play", "heartbeat"}:
        return jsonify({"error": "Type d'événement invalide"}), 400
    channel_id = str(data.get("channel_id", "")).strip()[:128] or None
    program_id = str(data.get("program_id", "")).strip()[:128] or None
    program_title = str(data.get("program_title", "")).strip()[:255] or None
    visitor_id = str(data.get("visitor_id", "")).strip()[:128] or None
    try:
        db.session.add(AnalyticsEvent(event_type=event_type, channel_id=channel_id, program_id=program_id, program_title=program_title, visitor_id=visitor_id))
        db.session.commit()
        return jsonify({"status": "ok"})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Statistique non enregistrée"}), 500

@app.route("/api/analytics/stats")
@admin_required
def analytics_stats():
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=6)
    month_start = day_start - timedelta(days=29)
    q = AnalyticsEvent.query
    if session.get("channel_admin_id") and not is_global_admin():
        q = q.filter_by(channel_id=session.get("channel_admin_id"))
    total_views = q.filter(AnalyticsEvent.event_type == "view").count()
    total_plays = q.filter(AnalyticsEvent.event_type == "play").count()
    today_views = q.filter(AnalyticsEvent.event_type == "view", AnalyticsEvent.created_at >= day_start).count()
    week_views = q.filter(AnalyticsEvent.event_type == "view", AnalyticsEvent.created_at >= week_start).count()
    month_views = q.filter(AnalyticsEvent.event_type == "view", AnalyticsEvent.created_at >= month_start).count()
    unique = q.filter(AnalyticsEvent.event_type.in_(["view", "play"]), AnalyticsEvent.visitor_id.isnot(None)).with_entities(func.count(func.distinct(AnalyticsEvent.visitor_id))).scalar() or 0
    online_cutoff = now - timedelta(seconds=75)
    online = q.filter(AnalyticsEvent.event_type == "heartbeat", AnalyticsEvent.created_at >= online_cutoff, AnalyticsEvent.visitor_id.isnot(None)).with_entities(func.count(func.distinct(AnalyticsEvent.visitor_id))).scalar() or 0
    channel_rows = q.filter(AnalyticsEvent.event_type == "view", AnalyticsEvent.channel_id.isnot(None)).with_entities(AnalyticsEvent.channel_id, func.count(AnalyticsEvent.id).label("views")).group_by(AnalyticsEvent.channel_id).order_by(func.count(AnalyticsEvent.id).desc()).all()
    channels = {c.get("id"): c for c in load_channels()}
    by_channel = [{"channel_id": cid, "name": channels.get(cid, {}).get("name", cid), "views": views} for cid, views in channel_rows]
    program_rows = q.filter(AnalyticsEvent.event_type == "play", AnalyticsEvent.program_id.isnot(None)).with_entities(AnalyticsEvent.program_id, AnalyticsEvent.program_title, func.count(AnalyticsEvent.id).label("plays")).group_by(AnalyticsEvent.program_id, AnalyticsEvent.program_title).order_by(func.count(AnalyticsEvent.id).desc()).limit(20).all()
    by_program = [{"program_id": pid, "title": title or "Programme", "plays": plays} for pid, title, plays in program_rows]
    daily = []
    for offset in range(6, -1, -1):
        start = day_start - timedelta(days=offset)
        end = start + timedelta(days=1)
        count = q.filter(AnalyticsEvent.event_type == "view", AnalyticsEvent.created_at >= start, AnalyticsEvent.created_at < end).count()
        daily.append({"date": start.strftime("%d/%m"), "views": count})
    return jsonify({"total_views": total_views, "total_plays": total_plays, "today_views": today_views, "week_views": week_views, "month_views": month_views, "unique_viewers": unique, "online_now": online, "by_channel": by_channel, "by_program": by_program, "daily": daily})

@app.route("/robot")
def robot_page():
    return render_template("robot.html")

def create_channel_for_robot(name, password, number="", logo_text="", slogan="", genre="Généraliste"):
    name = str(name or "").strip()[:100]
    password = str(password or "")
    if len(name) < 2:
        raise ValueError("Le nom de la chaîne est requis.")
    if len(password) < 6:
        raise ValueError("Le mot de passe doit contenir au moins 6 caractères.")
    channels = load_channels()
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "chaine"
    new_id = f"channel-{base}-{uuid.uuid4().hex[:6]}"
    new_channel = {"id": new_id, "name": name, "number": number or f"CH {len(channels)+1:02d}", "logoText": logo_text or name[:2].upper(), "slogan": slogan or "Télévision en Continu 24/7", "genre": genre or "Généraliste", "tickerActive": True, "tickerText": f"BIENVENUE SUR {name.upper()} — DIRECT 24H/24", "schedule": []}
    channels.append(new_channel); save_channels(channels)
    try:
        db.session.add(ChannelAccount(channel_id=new_id, password_hash=generate_password_hash(password)))
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Évite de laisser une chaîne sans compte admin si la base échoue.
        save_channels([c for c in channels if c.get("id") != new_id])
        raise ValueError("Impossible de créer le compte administrateur de la chaîne.")
    base_url = request.host_url.rstrip("/")
    return new_channel, f"{base_url}/channel/{new_id}/admin", f"{base_url}/live/{new_id}/playlist.m3u", f"{base_url}/live/{new_id}/stream.m3u8"

@app.route("/api/robot", methods=["POST"])
def robot_api():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    state = session.get("robot_create") or {}
    lower = message.lower()
    if not message:
        return jsonify({"reply": "Écris par exemple : créer une chaîne Nom: Ma TV | Mot de passe: MonPass123"})
    # Structured one-message creation
    m_name = re.search(r"(?:nom|chaîne|chaine)(?:\s+nom)?\s*[:=]\s*([^|;\n]+)", message, re.I)
    m_pass = re.search(r"(?:mot de passe|password|mdp)\s*[:=]\s*([^|;\n]+)", message, re.I)
    if any(x in lower for x in ("créer", "creer")) and m_name and m_pass:
        try:
            ch, admin_url, m3u_url, hls_url = create_channel_for_robot(m_name.group(1).strip(), m_pass.group(1).strip())
            session.pop("robot_create", None)
            return jsonify({"reply": f"Chaîne « {ch['name']} » créée. Voici son accès administrateur et ses liens de diffusion.", "channel": ch, "admin_url": admin_url, "m3u_url": m3u_url, "hls_url": hls_url})
        except ValueError as e:
            return jsonify({"reply": str(e)}), 400
    if lower.startswith(("créer", "creer")) or "crée une chaîne" in lower or "cree une chaine" in lower:
        session["robot_create"] = {"step": "name"}
        return jsonify({"reply": "D'accord. Donne-moi le nom de la chaîne."})
    if state.get("step") == "name":
        session["robot_create"] = {"step": "password", "name": message[:100]}
        return jsonify({"reply": "Nom enregistré. Maintenant choisis le mot de passe de cette chaîne (6 caractères minimum)."})
    if state.get("step") == "password":
        try:
            ch, admin_url, m3u_url, hls_url = create_channel_for_robot(state.get("name"), message, "", "", "", "Généraliste")
            session.pop("robot_create", None)
            return jsonify({"reply": f"C'est créé. La chaîne « {ch['name']} » est prête.", "channel": ch, "admin_url": admin_url, "m3u_url": m3u_url, "hls_url": hls_url})
        except ValueError as e:
            return jsonify({"reply": str(e)}), 400
    return jsonify({"reply": "Je peux créer une chaîne. Écris : créer une chaîne Nom: Ma TV | Mot de passe: MonPass123"})

@app.route("/")
def index():
    channels = load_channels()
    ch_id = request.args.get("channel")
    current_channel = next((c for c in channels if c.get("id") == ch_id), channels[0] if channels else None)
    if current_channel:
        reorder_schedule_for_current(current_channel)
    return render_template("index.html", channels=channels, current_channel=current_channel)

@app.route("/api/channels/<channel_id>/current")
def current_program_api(channel_id):
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch:
        return jsonify({"error": "Chaîne introuvable"}), 404
    current, next_program = get_current_program(ch)
    return jsonify({
        "channel": ch,
        "current": current,
        "next": next_program,
        "server_time": datetime.now().astimezone().isoformat()
    })

@app.route("/channel/<channel_id>/admin")
def channel_admin(channel_id):
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch:
        abort(404)
    return render_template("admin.html", channels=[ch], channel_mode=True, channel_id=channel_id, channel_name=ch.get("name", "Chaîne"))

@app.route("/api/channel-auth", methods=["POST"])
def auth_channel():
    data = request.get_json(silent=True) or {}
    channel_id = str(data.get("channel_id", "")).strip()
    password = str(data.get("password", ""))
    account = channel_account(channel_id)
    if not account or not check_password_hash(account.password_hash, password):
        return jsonify({"authenticated": False, "error": "Mot de passe incorrect"}), 401
    session.pop("admin_authenticated", None)
    session["channel_admin_id"] = channel_id
    session["csrf_token"] = uuid.uuid4().hex
    return jsonify({"authenticated": True, "csrf_token": session["csrf_token"], "channel_id": channel_id})

@app.route("/api/channel-logout", methods=["POST"])
def channel_logout():
    session.pop("channel_admin_id", None)
    session.pop("csrf_token", None)
    return jsonify({"status": "success"})

@app.route("/admin")
def admin():
    return render_template("admin.html", channels=load_channels(), channel_mode=False, channel_id="", channel_name="")

@app.route("/api/auth", methods=["POST"])
def auth_admin():
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin", "")).strip()
    if pin == str(ADMIN_PIN).strip():
        session.clear()
        session["admin_authenticated"] = True
        session["csrf_token"] = uuid.uuid4().hex
        return jsonify({"authenticated": True, "csrf_token": session["csrf_token"]})
    return jsonify({"authenticated": False, "error": "Code PIN incorrect"}), 401

@app.route("/api/logout", methods=["POST"])
@admin_required
def logout():
    session.clear()
    return jsonify({"status": "success"})

@app.route("/api/channels", methods=["GET", "POST"])
def handle_channels():
    if request.method == "POST":
        if not session.get("admin_authenticated"):
            return jsonify({"error": "Authentification administrateur requise"}), 401
        data = request.get_json(silent=True)
        if isinstance(data, list):
            save_channels(data)
            return jsonify({"status": "success", "count": len(data), "channels": data})
        return jsonify({"error": "Données invalides"}), 400
    all_channels = load_channels()
    if session.get("channel_admin_id") and not is_global_admin():
        all_channels = [c for c in all_channels if c.get("id") == session.get("channel_admin_id")]
    return jsonify(all_channels)

@app.route("/api/channels/add", methods=["POST"])
@admin_required
def add_channel():
    if not is_global_admin():
        return jsonify({"error": "Seul l'administrateur principal peut créer une chaîne ici"}), 403
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Le nom de la chaîne est requis"}), 400
    channels = load_channels()
    new_id = f"channel-{len(channels) + 1}-{uuid.uuid4().hex[:4]}"
    new_channel = {
        "id": new_id,
        "name": name,
        "number": data.get("number") or f"CH {str(len(channels) + 1).zfill(2)}",
        "logoText": data.get("logoText") or (name[:2].upper() if len(name) >= 2 else "V+"),
        "slogan": data.get("slogan", "Télévision en Continu 24/7"),
        "genre": data.get("genre", "Généraliste"),
        "tickerActive": True,
        "tickerText": data.get("tickerText", f"BIENVENUE SUR {name.upper()} — DIRECT 24H/24"),
        "schedule": []
    }
    channels.append(new_channel); save_channels(channels)
    password = str(data.get("password") or uuid.uuid4().hex[:10])
    account = ChannelAccount(channel_id=new_id, password_hash=generate_password_hash(password))
    db.session.add(account); db.session.commit()
    return jsonify({"status": "success", "channel": new_channel, "channels": channels, "admin_url": f"{request.host_url.rstrip('/')}/channel/{new_id}/admin", "password": password})

@app.route("/api/channels/<channel_id>/delete", methods=["POST"])
@admin_required
def delete_channel(channel_id):
    if not is_global_admin():
        return jsonify({"error": "Seul l'administrateur principal peut supprimer une chaîne"}), 403
    channels = load_channels()
    if len(channels) <= 1:
        return jsonify({"error": "Impossible de supprimer la dernière chaîne"}), 400
    channels = [c for c in channels if c.get("id") != channel_id]
    save_channels(channels)
    return jsonify({"status": "success", "channels": channels})

@app.route("/api/channels/<channel_id>/schedule/add", methods=["POST"])
@admin_required
def add_program(channel_id):
    if not can_manage_channel(channel_id):
        return jsonify({"error": "Vous n'avez pas accès à cette chaîne"}), 403
    data = request.get_json(silent=True) or {}
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch:
        return jsonify({"error": "Chaîne introuvable"}), 404
    video_url = str(data.get("videoUrl", "")).strip()
    duration = int(data.get("durationSeconds") or 0)
    is_live = bool(data.get("isLive") or data.get("mediaType") in ("live", "live_stream"))
    if data.get("autoStart") and ch.get("schedule"):
        previous = ch["schedule"][-1]
        previous_end = previous.get("endTime")
        if previous_end:
            data["startTime"] = previous_end
    # Pour une vidéo enregistrée, la fin est calculée automatiquement à partir
    # de la durée. Pour un flux live, aucune heure de fin n'est imposée.
    if not is_live and duration and not data.get("endTime"):
        data["endTime"] = calc_end_time(data.get("startTime", "12:00"), duration)
    if is_live:
        data["endTime"] = None
        data["durationSeconds"] = 0
        data["mediaType"] = "live"
    prog = enrich_program(data, video_url)
    if data.get("playNow"):
        ch.setdefault("schedule", []).insert(0, prog)
    else:
        ch.setdefault("schedule", []).append(prog)
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels, "program": prog})

@app.route("/api/channels/<channel_id>/schedule/<int:prog_idx>/delete", methods=["POST"])
@admin_required
def delete_program(channel_id, prog_idx):
    if not can_manage_channel(channel_id):
        return jsonify({"error": "Vous n'avez pas accès à cette chaîne"}), 403
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch or prog_idx < 0 or prog_idx >= len(ch.get("schedule", [])):
        return jsonify({"error": "Programme introuvable"}), 404
    del ch["schedule"][prog_idx]; save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route("/api/channels/<channel_id>/schedule/<int:prog_idx>/play", methods=["POST"])
@admin_required
def play_program_now(channel_id, prog_idx):
    if not can_manage_channel(channel_id):
        return jsonify({"error": "Vous n'avez pas accès à cette chaîne"}), 403
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch or prog_idx < 0 or prog_idx >= len(ch.get("schedule", [])):
        return jsonify({"error": "Programme introuvable"}), 404
    prog = ch["schedule"].pop(prog_idx); ch["schedule"].insert(0, prog)
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route("/api/channels/<channel_id>/ticker", methods=["POST"])
@admin_required
def update_ticker(channel_id):
    if not can_manage_channel(channel_id):
        return jsonify({"error": "Vous n'avez pas accès à cette chaîne"}), 403
    data = request.get_json(silent=True) or {}
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch: return jsonify({"error": "Chaîne introuvable"}), 404
    ch["tickerText"] = data.get("tickerText", ch.get("tickerText", ""))
    ch["tickerActive"] = data.get("tickerActive", True)
    save_channels(channels)
    return jsonify({"status": "success", "channel": ch, "channels": channels})

@app.route("/upload", methods=["POST"])
@admin_required
def upload_file():
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "Aucun fichier reçu. Le champ doit s'appeler 'file'."}), 400
    if not file.filename or not file.filename.strip():
        return jsonify({"error": "Nom de fichier vide"}), 400
    channel_id = request.form.get("channel_id") or session.get("channel_admin_id")
    if channel_id and not can_manage_channel(channel_id):
        return jsonify({"error": "Accès refusé à cette chaîne"}), 403
    try:
        folder_id = int(request.form.get("folder_id")) if request.form.get("folder_id") else None
        obj = storage.upload(file, folder_id=folder_id, owner_channel_id=channel_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print("Upload error:", repr(e))
        return jsonify({"error": "Impossible d'enregistrer le fichier dans le stockage"}), 500

    title = request.form.get("title", "").strip()
    if channel_id and title:
        channels = load_channels()
        ch = next((c for c in channels if c.get("id") == channel_id), None)
        if ch:
            duration = int(request.form.get("durationSeconds") or 0)
            data = {
                "title": title, "category": request.form.get("category", "Direct"),
                "startTime": request.form.get("startTime", "12:00"),
                "endTime": request.form.get("endTime") or None,
                "durationSeconds": duration, "days": json.loads(request.form.get("days", '["Tous les jours"]')),
                "autoEnd": request.form.get("autoEnd", "true") == "true",
                "autoNext": request.form.get("autoNext", "true") == "true",
                "autoStart": request.form.get("autoStart", "false") == "true",
                "playNow": request.form.get("play_now") == "true",
                "isLive": request.form.get("isLive", "false") == "true",
                "mediaType": "live" if request.form.get("isLive", "false") == "true" else "video"
            }
            if data.get("autoStart") and ch.get("schedule"):
                data["startTime"] = ch["schedule"][-1].get("endTime", data.get("startTime", "12:00"))
            prog = enrich_program(data, storage.download(obj) if storage.backend == "s3" else f"/media/{obj.storage_key}", obj.original_name)
            # Pour S3, le playback URL est généré à la demande via /storage/download/<id>.
            prog["storageFileId"] = obj.id
            if storage.backend == "s3":
                prog["videoUrl"] = f"/media/file/{obj.id}"
                prog["playbackUrl"] = prog["videoUrl"]
                prog["playbackType"] = "video"
            ch.setdefault("schedule", [])
            if data["playNow"]: ch["schedule"].insert(0, prog)
            else: ch["schedule"].append(prog)
            save_channels(channels)

    return jsonify({"status": "success", "filename": obj.original_name, "url": f"/api/storage/files/{obj.id}/download", "file_id": obj.id, "size_mb": round(obj.size / 1048576, 2)})

@app.route("/api/storage/status")
@admin_required
def storage_status():
    used = storage.used_bytes()
    q = StorageFile.query
    if session.get("channel_admin_id") and not is_global_admin():
        q = q.filter(StorageFile.owner_channel_id == session.get("channel_admin_id"))
    files_count = q.count()
    capacity = storage.capacity
    return jsonify({
        "backend": storage.backend,
        "max_capacity_gb": round(capacity / 1024**3, 2),
        "used_gb": round(used / 1024**3, 3),
        "free_gb": round(max(0, capacity - used) / 1024**3, 3),
        "files_count": files_count
    })

def file_json(obj):
    return {
        "id": obj.id, "name": obj.original_name, "size": obj.size,
        "size_mb": round(obj.size / 1048576, 2), "mime_type": obj.mime_type,
        "extension": obj.extension, "folder_id": obj.folder_id,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        "url": f"/api/storage/files/{obj.id}/download"
    }

@app.route("/api/storage/files")
@admin_required
def list_storage_files():
    q = request.args.get("q", "").strip()
    folder_id = request.args.get("folder_id", type=int)
    query = StorageFile.query
    if session.get("channel_admin_id") and not is_global_admin():
        query = query.filter(StorageFile.owner_channel_id == session.get("channel_admin_id"))
    if q: query = query.filter(StorageFile.original_name.ilike(f"%{q}%"))
    if folder_id is not None: query = query.filter(StorageFile.folder_id == folder_id)
    files = query.order_by(StorageFile.created_at.desc()).limit(500).all()
    return jsonify({"files": [file_json(x) for x in files]})

@app.route("/api/storage/files/<int:file_id>/download")
@admin_required
def storage_download(file_id):
    obj = db.session.get(StorageFile, file_id)
    if not obj: abort(404)
    if session.get("channel_admin_id") and not is_global_admin() and obj.owner_channel_id != session.get("channel_admin_id"):
        return jsonify({"error": "Fichier hors de votre espace"}), 403
    if storage.backend == "s3":
        return redirect(storage.download(obj))
    return send_file(storage.download(obj), as_attachment=True, download_name=obj.original_name, mimetype=obj.mime_type)

@app.route("/media/<path:filename>")
def serve_media(filename):
    # Compatibilité avec les anciennes URLs locales.
    safe = os.path.abspath(os.path.join(app.config["STORAGE_FOLDER"], filename))
    root = os.path.abspath(app.config["STORAGE_FOLDER"])
    if not safe.startswith(root + os.sep) or not os.path.isfile(safe):
        abort(404)
    return send_file(safe, conditional=True)

@app.route("/media/file/<int:file_id>")
def public_media_file(file_id):
    obj = db.session.get(StorageFile, file_id)
    if not obj:
        abort(404)
    if storage.backend == "s3":
        return redirect(storage.download(obj))
    return send_file(storage.download(obj), conditional=True, mimetype=obj.mime_type)

@app.route("/download/<path:filename>")
@admin_required
def download_media(filename):
    return serve_media(filename)

@app.route("/api/storage/files/<int:file_id>", methods=["PUT", "DELETE"])
@admin_required
def manage_storage_file(file_id):
    obj = db.session.get(StorageFile, file_id)
    if not obj: return jsonify({"error": "Fichier introuvable"}), 404
    if session.get("channel_admin_id") and not is_global_admin() and obj.owner_channel_id != session.get("channel_admin_id"):
        return jsonify({"error": "Fichier hors de votre espace"}), 403
    if request.method == "DELETE":
        storage.delete(obj); return jsonify({"status": "success"})
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name: return jsonify({"error": "Nouveau nom requis"}), 400
    storage.rename(obj, name)
    return jsonify({"status": "success", "file": file_json(obj)})

@app.route("/api/storage/folders", methods=["GET", "POST"])
@admin_required
def storage_folders():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        name = secure_filename(str(data.get("name", "")).strip())
        if not name: return jsonify({"error": "Nom de dossier requis"}), 400
        parent_id = data.get("parent_id")
        folder = StorageFolder(name=name, parent_id=parent_id)
        db.session.add(folder); db.session.commit()
        return jsonify({"status": "success", "folder": {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}})
    folders = StorageFolder.query.order_by(StorageFolder.name.asc()).all()
    return jsonify({"folders": [{"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in folders]})

@app.route("/api/storage/folders/<int:folder_id>", methods=["PUT", "DELETE"])
@admin_required
def manage_folder(folder_id):
    folder = db.session.get(StorageFolder, folder_id)
    if not folder: return jsonify({"error": "Dossier introuvable"}), 404
    if request.method == "DELETE":
        if folder.files or folder.children:
            return jsonify({"error": "Dossier non vide"}), 400
        db.session.delete(folder); db.session.commit()
        return jsonify({"status": "success"})
    data = request.get_json(silent=True) or {}
    name = secure_filename(str(data.get("name", "")).strip())
    if not name: return jsonify({"error": "Nom requis"}), 400
    folder.name = name; db.session.commit()
    return jsonify({"status": "success"})

@app.route("/api/share/<int:file_id>", methods=["POST"])
@admin_required
def create_share(file_id):
    obj = db.session.get(StorageFile, file_id)
    if not obj: return jsonify({"error": "Fichier introuvable"}), 404
    if session.get("channel_admin_id") and not is_global_admin() and obj.owner_channel_id != session.get("channel_admin_id"):
        return jsonify({"error": "Fichier hors de votre espace"}), 403
    data = request.get_json(silent=True) or {}
    exp = data.get("expirationMinutes")
    try: exp = int(exp) if exp not in (None, "", 0) else None
    except Exception: exp = None
    token = uuid.uuid4().hex + uuid.uuid4().hex
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(minutes=exp) if exp else None
    link = ShareLink(file_id=obj.id, token_hash=token_hash, expires_at=expires, revoked=False)
    db.session.add(link); db.session.commit()
    return jsonify({"status": "success", "url": f"{request.host_url.rstrip('/')}/share/{token}", "expires_at": expires.isoformat() if expires else None, "share_id": link.id})

@app.route("/share/<token>")
def public_share(token):
    h = hashlib.sha256(token.encode()).hexdigest()
    link = ShareLink.query.filter_by(token_hash=h, revoked=False).first()
    if not link: return jsonify({"error": "Lien invalide ou révoqué"}), 404
    now = datetime.now(timezone.utc)
    if link.expires_at and link.expires_at.replace(tzinfo=timezone.utc) < now:
        return jsonify({"error": "Lien expiré"}), 410
    obj = db.session.get(StorageFile, link.file_id)
    if not obj: return jsonify({"error": "Fichier introuvable"}), 404
    if storage.backend == "s3":
        return redirect(storage.download(obj))
    return send_file(storage.download(obj), as_attachment=True, download_name=obj.original_name, mimetype=obj.mime_type)

@app.route("/api/share/<int:share_id>", methods=["DELETE"])
@admin_required
def revoke_share(share_id):
    link = db.session.get(ShareLink, share_id)
    if not link: return jsonify({"error": "Lien introuvable"}), 404
    link.revoked = True; db.session.commit()
    return jsonify({"status": "success"})

@app.route("/live/<channel_id>/playlist.m3u")
def get_channel_m3u(channel_id):
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch:
        abort(404)
    now = datetime.now().astimezone()
    day_name = DAY_NAMES[now.weekday()]
    current, _next_program = get_current_program(ch, now)
    schedule = []
    for p in ch.get("schedule", []):
        if day_name not in program_days(p):
            continue
        start = minutes_from_time(p.get("startTime", "00:00"))
        if current and p.get("id") == current.get("id"):
            priority = -1
        elif start >= now.hour * 60 + now.minute:
            priority = start
        else:
            priority = 10000 + start
        schedule.append((priority, start, p))
    schedule.sort(key=lambda x: (x[0], x[1]))
    base = request.host_url.rstrip("/")
    lines = ["#EXTM3U"]
    seen = set()
    for _, _, p in schedule:
        pid = p.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        url = p.get("playbackUrl") or p.get("videoUrl")
        if not url:
            continue
        if not str(url).startswith(("http://", "https://")):
            url = base + str(url)
        lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" group-title="DTN TV",{p.get("title", ch["name"])}')
        lines.append(url)
    if len(lines) == 1:
        lines.extend([f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}",{ch["name"]}', f'{base}/live/{channel_id}/stream.m3u8'])
    return Response("\n".join(lines) + "\n", mimetype="application/x-mpegurl")

@app.route("/live/playlist.m3u")
def get_m3u():
    channels = load_channels()
    m3u = "#EXTM3U\n"
    for ch in channels:
        current, next_program = get_current_program(ch)
        p = current or next_program
        # Le générateur privilégie une vraie URL de flux déjà compatible IPTV.
        # Les fichiers MP4 restent accessibles par /media/file/<id>.
        video_url = (p.get("playbackUrl") or p.get("videoUrl")) if p else None
        if not video_url:
            video_url = f"{request.host_url.rstrip('/')}/live/{ch['id']}/stream.m3u8"
        elif not str(video_url).startswith(("http://", "https://")):
            video_url = f"{request.host_url.rstrip('/')}{video_url}"
        m3u += f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" group-title="DTN TV",Canal {ch["number"]} - {ch["name"]}\n{video_url}\n'
    return Response(m3u, mimetype="application/x-mpegurl")

@app.route("/live/<channel_id>/stream.m3u8")
def channel_live_stream(channel_id):
    channels = load_channels()
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    if not ch:
        abort(404)
    current, next_program = get_current_program(ch)
    p = current or next_program
    if not p:
        return Response("#EXTM3U\n", mimetype="application/vnd.apple.mpegurl")
    url = p.get("playbackUrl") or p.get("videoUrl")
    if not url:
        return Response("#EXTM3U\n", mimetype="application/vnd.apple.mpegurl")
    if str(url).lower().split("?")[0].endswith(".m3u8"):
        return redirect(url)
    return jsonify({
        "error": "Le programme actuel n'est pas un flux HLS .m3u8",
        "type": p.get("playbackType", "video"),
        "source": url
    }), 415

with app.app_context():
    db.create_all()
    # Migration légère pour les installations existantes : rattache les fichiers à une chaîne.
    try:
        inspector = inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("storage_files")}
        if "owner_channel_id" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE storage_files ADD COLUMN owner_channel_id VARCHAR(128)"))
    except Exception as e:
        print("Migration storage owner ignorée:", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
