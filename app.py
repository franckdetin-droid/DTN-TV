import os, sqlite3, secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, g
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB per upload
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
DB_PATH = os.environ.get("DATABASE_PATH", str(APP_DIR / "proxiservice.db"))
if DB_PATH != ":memory:":
    Path(DB_PATH).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 20000")
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      phone TEXT DEFAULT '',
      whatsapp TEXT DEFAULT '',
      city TEXT DEFAULT '',
      neighborhood TEXT DEFAULT '',
      profession TEXT DEFAULT '',
      bio TEXT DEFAULT '',
      role TEXT NOT NULL DEFAULT 'provider',
      approved INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS services (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      category TEXT NOT NULL,
      description TEXT DEFAULT '',
      price TEXT DEFAULT '',
      city TEXT NOT NULL,
      neighborhood TEXT DEFAULT '',
      image TEXT DEFAULT '',
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      slot INTEGER NOT NULL UNIQUE CHECK(slot IN (1,2)),
      advertiser TEXT DEFAULT '',
      image TEXT DEFAULT '',
      target_url TEXT DEFAULT '',
      active INTEGER NOT NULL DEFAULT 0,
      starts_at TEXT DEFAULT '',
      ends_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
      sender_name TEXT NOT NULL,
      sender_contact TEXT DEFAULT '',
      body TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type TEXT NOT NULL,
      service_id INTEGER,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT ''
    );
    """)
    for slot in (1, 2):
        conn.execute("INSERT OR IGNORE INTO ads(slot,active) VALUES(?,0)", (slot,))
    # Local demo defaults. Change these before putting the site online.
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@proxiservice.com").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "3004")
    if admin_email and admin_password:
        row = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(name,email,password_hash,role,approved,created_at) VALUES(?,?,?,?,?,?)",
                ("Administrateur", admin_email, generate_password_hash(admin_password), "admin", 1, datetime.utcnow().isoformat())
            )
        else:
            # The environment configuration remains the source of truth for the admin password.
            conn.execute("UPDATE users SET password_hash=?, role='admin', approved=1 WHERE email=?",
                         (generate_password_hash(admin_password), admin_email))
    # Backwards-compatible schema upgrades: preserve all existing tables and records.
    user_columns = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "profile_photo" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_photo TEXT DEFAULT ''")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(client_id, provider_id, service_id)
    );
    CREATE TABLE IF NOT EXISTS conversation_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
      created_at TEXT NOT NULL,
      is_read INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_thread ON conversation_messages(conversation_id,id);
    CREATE INDEX IF NOT EXISTS idx_conversations_client ON conversations(client_id,updated_at);
    CREATE INDEX IF NOT EXISTS idx_conversations_provider ON conversations(provider_id,updated_at);
    CREATE TABLE IF NOT EXISTS reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
      rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
      comment TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      UNIQUE(client_id, service_id)
    );
    """)
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('app_download_url','')")
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('app_download_label','ProxiService')")
    conn.commit()
    conn.close()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_upload(field_name):
    f = request.files.get(field_name)
    if not f or not f.filename:
        return None
    if not allowed_file(f.filename):
        raise ValueError("Format d’image non accepté. Utilisez PNG, JPG, WEBP ou GIF.")
    ext = f.filename.rsplit(".", 1)[1].lower()
    header = f.stream.read(16)
    f.stream.seek(0)
    valid = {
        "png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": header.startswith(b"\xff\xd8\xff"),
        "jpeg": header.startswith(b"\xff\xd8\xff"),
        "gif": header.startswith((b"GIF87a", b"GIF89a")),
        "webp": len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP",
    }
    if not valid.get(ext, False):
        raise ValueError("Le contenu du fichier ne correspond pas à une image PNG, JPG, WEBP ou GIF valide.")
    safe_name = f"{secrets.token_hex(16)}.{ext}"
    f.save(UPLOAD_DIR / safe_name)
    return safe_name

def delete_upload(filename):
    if filename:
        path = UPLOAD_DIR / Path(filename).name
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token

@app.before_request
def protect_post_requests():
    if request.method == "POST":
        supplied = request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            abort(400, description="Formulaire expiré. Rechargez la page puis réessayez.")

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "current_year": datetime.utcnow().year, "csrf_token": csrf_token}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.path.startswith("/admin"):
                return redirect(url_for("admin_login"))
            flash("Connectez-vous pour accéder à cette page.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped

@app.route("/")
def index():
    conn = db()
    q = request.args.get("q", "").strip()
    city = request.args.get("city", "").strip()
    category = request.args.get("category", "").strip()
    sql = """SELECT s.*, u.name AS provider_name, u.phone, u.whatsapp
             FROM services s JOIN users u ON u.id=s.user_id
             WHERE s.active=1 AND u.approved=1"""
    params = []
    if q:
        sql += " AND (s.title LIKE ? OR s.description LIKE ? OR s.category LIKE ? OR u.name LIKE ?)"
        params += [f"%{q}%"] * 4
    if city:
        sql += " AND (s.city LIKE ? OR s.neighborhood LIKE ?)"
        params += [f"%{city}%", f"%{city}%"]
    if category:
        sql += " AND s.category=?"
        params.append(category)
    sql += " ORDER BY s.id DESC LIMIT 60"
    services = conn.execute(sql, params).fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM services WHERE active=1 ORDER BY category").fetchall()
    ads = conn.execute("SELECT * FROM ads WHERE active=1 ORDER BY slot").fetchall()
    conn.execute("INSERT INTO events(event_type,created_at) VALUES('home_view',?)", (datetime.utcnow().isoformat(),))
    conn.commit()
    return render_template("index.html", services=services, categories=categories, ads={a["slot"]: a for a in ads}, q=q, city=city, category=category)

@app.route("/application")
def application_page():
    row = db().execute("SELECT value FROM settings WHERE key='app_download_url'").fetchone()
    label = db().execute("SELECT value FROM settings WHERE key='app_download_label'").fetchone()
    download_url = row["value"] if row else ""
    app_label = label["value"] if label else "ProxiService"
    return render_template("application.html", download_url=download_url, app_label=app_label)

@app.route("/service/<int:service_id>")
def service_detail(service_id):
    conn = db()
    service = conn.execute("""SELECT s.*,u.id AS provider_id,u.name AS provider_name,u.phone,u.whatsapp,u.email,u.bio AS provider_bio,u.profession,u.city AS provider_city,u.neighborhood AS provider_neighborhood,u.profile_photo
                              FROM services s JOIN users u ON u.id=s.user_id
                              WHERE s.id=? AND s.active=1 AND u.approved=1""", (service_id,)).fetchone()
    if not service:
        abort(404)
    conn.execute("INSERT INTO events(event_type,service_id,created_at) VALUES('service_view',?,?)", (service_id, datetime.utcnow().isoformat()))
    conn.commit()
    rating = conn.execute("SELECT COALESCE(AVG(rating),0) AS average, COUNT(*) AS count FROM reviews WHERE provider_id=?", (service["provider_id"],)).fetchone()
    reviews = conn.execute("SELECT r.*,u.name AS client_name FROM reviews r JOIN users u ON u.id=r.client_id WHERE r.provider_id=? ORDER BY r.id DESC LIMIT 30", (service["provider_id"],)).fetchall()
    return render_template("service.html", service=service, rating=rating, reviews=reviews)

@app.route("/professionnel/<int:provider_id>")
def provider_profile(provider_id):
    provider = db().execute("SELECT id,name,profession,bio,phone,whatsapp,city,neighborhood,profile_photo,created_at FROM users WHERE id=? AND role='provider' AND approved=1", (provider_id,)).fetchone()
    if not provider:
        abort(404)
    services = db().execute("SELECT * FROM services WHERE user_id=? AND active=1 ORDER BY id DESC", (provider_id,)).fetchall()
    rating = db().execute("SELECT COALESCE(AVG(rating),0) AS average, COUNT(*) AS count FROM reviews WHERE provider_id=?", (provider_id,)).fetchone()
    reviews = db().execute("SELECT r.*,u.name AS client_name FROM reviews r JOIN users u ON u.id=r.client_id WHERE r.provider_id=? ORDER BY r.id DESC LIMIT 50", (provider_id,)).fetchall()
    return render_template("provider_profile.html", provider=provider, services=services, rating=rating, reviews=reviews)

@app.route("/client/register", methods=["GET", "POST"])
def client_register():
    if request.method == "POST":
        name=request.form.get("name", "").strip()[:100]
        email=request.form.get("email", "").strip().lower()[:254]
        password=request.form.get("password", "")
        if not name or "@" not in email or len(password)<8:
            flash("Indiquez votre nom, un e-mail valide et un mot de passe d’au moins 8 caractères.", "error")
        else:
            try:
                db().execute("INSERT INTO users(name,email,password_hash,role,approved,created_at) VALUES(?,?,?,?,?,?)", (name,email,generate_password_hash(password),"client",1,datetime.utcnow().isoformat()))
                db().commit()
                flash("Compte client créé. Connectez-vous pour retrouver vos conversations.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Cette adresse e-mail est déjà utilisée.", "error")
    return render_template("client_register.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip()
        city = request.form.get("city", "").strip()
        neighborhood = request.form.get("neighborhood", "").strip()
        profession = request.form.get("profession", "").strip()
        if not name or not email or len(password) < 8 or not city:
            flash("Remplissez les champs obligatoires. Le mot de passe doit contenir au moins 8 caractères.", "error")
            return render_template("register.html")
        try:
            db().execute("""INSERT INTO users(name,email,password_hash,phone,whatsapp,city,neighborhood,profession,role,approved,created_at)
                            VALUES(?,?,?,?,?,?,?,?, 'provider',1,?)""",
                         (name,email,generate_password_hash(password),phone,whatsapp,city,neighborhood,profession,datetime.utcnow().isoformat()))
            db().commit()
            flash("Compte professionnel créé. Vous pouvez maintenant vous connecter.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Cette adresse e-mail est déjà utilisée.", "error")
    return render_template("register.html")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Connexion administrateur avec un code unique, sans adresse e-mail."""
    if current_user() and current_user()["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        now = int(datetime.utcnow().timestamp())
        attempts = session.get("admin_attempts", [])
        attempts = [t for t in attempts if now - t < 900]
        if len(attempts) >= 5:
            error = "Trop de tentatives. Réessayez dans 15 minutes."
            session["admin_attempts"] = attempts
            return render_template("admin_login.html", error=error), 429
        code = request.form.get("code", "")
        configured_code = os.environ.get("ADMIN_PASSWORD", "3004")
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@proxiservice.com").strip().lower()
        if code and secrets.compare_digest(code, configured_code):
            admin = db().execute("SELECT id FROM users WHERE email=? AND role='admin'", (admin_email,)).fetchone()
            if admin:
                session.clear()
                session["user_id"] = admin["id"]
                session["admin_attempts"] = []
                flash("Accès administrateur autorisé.", "success")
                return redirect(url_for("admin_dashboard"))
            error = "Compte administrateur introuvable. Vérifiez ADMIN_EMAIL puis relancez l’application."
        else:
            attempts.append(now)
            session["admin_attempts"] = attempts
            error = "Code incorrect. Réessayez."
    return render_template("admin_login.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password) and user["approved"]:
            session.clear()
            session["user_id"] = user["id"]
            flash("Connexion réussie.", "success")
            next_url = request.args.get("next", "")
            from urllib.parse import urlparse
            parsed_next = urlparse(next_url)
            if next_url and not parsed_next.netloc and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard") if user["role"] == "admin" else (url_for("inbox") if user["role"] == "client" else url_for("my_services")))
        flash("Identifiants incorrects ou compte en attente de validation.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("index"))

@app.route("/my-services", methods=["GET", "POST"])
@login_required
def my_services():
    user = current_user()
    if user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        try:
            image = save_upload("image") or ""
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("my_services"))
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "").strip()
        city = request.form.get("city", "").strip()
        neighborhood = request.form.get("neighborhood", "").strip()
        if not title or not category or not city:
            delete_upload(image)
            flash("Le titre, la catégorie et la ville sont obligatoires.", "error")
            return redirect(url_for("my_services"))
        db().execute("""INSERT INTO services(user_id,title,category,description,price,city,neighborhood,image,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                     (user["id"],title,category,description,price,city,neighborhood,image,datetime.utcnow().isoformat()))
        db().commit()
        flash("Service publié.", "success")
        return redirect(url_for("my_services"))
    services = db().execute("SELECT * FROM services WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return render_template("my_services.html", services=services)

@app.route("/profil/modifier", methods=["GET", "POST"])
@login_required
def edit_profile():
    user=current_user()
    if user["role"] != "provider": abort(403)
    if request.method == "POST":
        try: photo=save_upload("profile_photo")
        except ValueError as exc:
            flash(str(exc), "error"); return redirect(url_for("edit_profile"))
        bio=request.form.get("bio", "").strip()[:2000]
        profession=request.form.get("profession", "").strip()[:100]
        if photo:
            delete_upload(user["profile_photo"])
            db().execute("UPDATE users SET profile_photo=?,bio=?,profession=? WHERE id=?", (photo,bio,profession,user["id"]))
        else:
            db().execute("UPDATE users SET bio=?,profession=? WHERE id=?", (bio,profession,user["id"]))
        db().commit(); flash("Profil mis à jour.", "success"); return redirect(url_for("provider_profile",provider_id=user["id"]))
    return render_template("edit_profile.html", user=user)

@app.route("/service/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def edit_service(service_id):
    user=current_user()
    service=db().execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    if not service or (service["user_id"] != user["id"] and user["role"] != "admin"):
        abort(404)
    if request.method == "POST":
        title=request.form.get("title", "").strip()[:150]
        category=request.form.get("category", "").strip()[:100]
        description=request.form.get("description", "").strip()[:3000]
        price=request.form.get("price", "").strip()[:100]
        city=request.form.get("city", "").strip()[:100]
        neighborhood=request.form.get("neighborhood", "").strip()[:100]
        if not title or not category or not city:
            flash("Le titre, la catégorie et la ville sont obligatoires.", "error")
            return redirect(url_for("edit_service", service_id=service_id))
        try:
            uploaded=save_upload("image")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("edit_service", service_id=service_id))
        image=uploaded or service["image"]
        db().execute("UPDATE services SET title=?,category=?,description=?,price=?,city=?,neighborhood=?,image=? WHERE id=?", (title,category,description,price,city,neighborhood,image,service_id))
        db().commit()
        if uploaded: delete_upload(service["image"])
        flash("Prestation mise à jour.", "success")
        return redirect(url_for("my_services") if user["role"] != "admin" else url_for("admin_dashboard"))
    return render_template("edit_service.html", service=service)

@app.route("/service/<int:service_id>/delete", methods=["POST"])
@login_required
def delete_service(service_id):
    user = current_user()
    service = db().execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    if not service or (service["user_id"] != user["id"] and user["role"] != "admin"):
        abort(403)
    delete_upload(service["image"])
    db().execute("DELETE FROM services WHERE id=?", (service_id,))
    db().commit()
    flash("Service supprimé.", "success")
    return redirect(url_for("admin_dashboard") if user["role"] == "admin" else (url_for("inbox") if user["role"] == "client" else url_for("my_services")))

@app.route("/message/<int:service_id>", methods=["GET", "POST"])
@login_required
def message_provider(service_id):
    user=current_user()
    service=db().execute("SELECT s.*,u.name AS provider_name,u.id AS provider_id FROM services s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.active=1 AND u.approved=1", (service_id,)).fetchone()
    if not service: abort(404)
    if user["role"] == "admin": abort(403)
    if user["role"] == "provider":
        if user["id"] != service["provider_id"]: abort(403)
        flash("Pour répondre à un client, ouvrez la conversation depuis votre boîte de réception.", "info")
        return redirect(url_for("inbox"))
    now=datetime.utcnow().isoformat()
    conn=db()
    conn.execute("INSERT OR IGNORE INTO conversations(client_id,provider_id,service_id,created_at,updated_at) VALUES(?,?,?,?,?)", (user["id"],service["provider_id"],service_id,now,now))
    conn.commit()
    conversation=conn.execute("SELECT id FROM conversations WHERE client_id=? AND provider_id=? AND service_id=?", (user["id"],service["provider_id"],service_id)).fetchone()
    return redirect(url_for("conversation_detail", conversation_id=conversation["id"]))

@app.route("/conversations/<int:conversation_id>", methods=["GET", "POST"])
@login_required
def conversation_detail(conversation_id):
    user=current_user()
    if user["role"] not in ("client", "provider"): abort(403)
    conn=db()
    conversation=conn.execute("SELECT c.*,cl.name AS client_name,p.name AS provider_name,s.title AS service_title FROM conversations c JOIN users cl ON cl.id=c.client_id JOIN users p ON p.id=c.provider_id LEFT JOIN services s ON s.id=c.service_id WHERE c.id=? AND (c.client_id=? OR c.provider_id=?)", (conversation_id,user["id"],user["id"])).fetchone()
    if not conversation: abort(404)
    if request.method == "POST":
        body=request.form.get("body", "").strip()
        if not body or len(body)>2000:
            flash("Le message doit contenir entre 1 et 2 000 caractères.", "error")
        else:
            now=datetime.utcnow().isoformat()
            conn.execute("INSERT INTO conversation_messages(conversation_id,sender_id,body,created_at) VALUES(?,?,?,?)", (conversation_id,user["id"],body,now))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now,conversation_id))
            conn.commit()
            return redirect(url_for("conversation_detail",conversation_id=conversation_id))
    messages=conn.execute("SELECT m.*,u.name AS sender_name,u.role AS sender_role FROM conversation_messages m JOIN users u ON u.id=m.sender_id WHERE m.conversation_id=? ORDER BY m.id", (conversation_id,)).fetchall()
    conn.execute("UPDATE conversation_messages SET is_read=1 WHERE conversation_id=? AND sender_id<>?", (conversation_id,user["id"]))
    conn.commit()
    return render_template("conversation.html", conversation=conversation, messages=messages)

@app.route("/conversations/<int:conversation_id>/poll")
@login_required
def conversation_poll(conversation_id):
    user=current_user()
    conversation=db().execute("SELECT id FROM conversations WHERE id=? AND (client_id=? OR provider_id=?)", (conversation_id,user["id"],user["id"])).fetchone()
    if not conversation: abort(404)
    after=max(0,request.args.get("after",default=0,type=int))
    rows=db().execute("SELECT m.id,m.body,m.created_at,m.sender_id,u.name AS sender_name FROM conversation_messages m JOIN users u ON u.id=m.sender_id WHERE m.conversation_id=? AND m.id>? ORDER BY m.id", (conversation_id,after)).fetchall()
    return {"messages":[dict(r) for r in rows]}

@app.route("/avis/<int:service_id>", methods=["POST"])
@login_required
def submit_review(service_id):
    user=current_user()
    if user["role"] != "client": abort(403)
    route_service=db().execute("SELECT id,user_id FROM services WHERE id=? AND active=1", (service_id,)).fetchone()
    if not route_service: abort(404)
    selected_service_id=request.form.get("service_id", "").strip()
    try: selected_service_id=int(selected_service_id) if selected_service_id else service_id
    except ValueError: selected_service_id=service_id
    service=db().execute("SELECT id,user_id FROM services WHERE id=? AND active=1", (selected_service_id,)).fetchone()
    if not service or service["user_id"] != route_service["user_id"]: abort(400, description="La prestation choisie ne correspond pas à ce profil.")
    try: rating=int(request.form.get("rating", "0"))
    except ValueError: rating=0
    comment=request.form.get("comment", "").strip()[:1000]
    if rating not in range(1,6):
        flash("Choisissez une note de 1 à 5 étoiles.", "error")
    else:
        try:
            db().execute("INSERT INTO reviews(client_id,provider_id,service_id,rating,comment,created_at) VALUES(?,?,?,?,?,?)", (user["id"],service["user_id"],service_id,rating,comment,datetime.utcnow().isoformat()))
            db().commit()
            flash("Merci, votre avis a été enregistré.", "success")
        except sqlite3.IntegrityError:
            flash("Vous avez déjà évalué cette prestation.", "error")
    return redirect(url_for("provider_profile",provider_id=service["user_id"]) + "#avis")

@app.route("/inbox")
@login_required
def inbox():
    user=current_user()
    legacy_messages=[]
    if user["role"] == "admin":
        rows=[]
        legacy_messages=db().execute("SELECT m.*,s.title FROM messages m JOIN services s ON s.id=m.service_id ORDER BY m.id DESC LIMIT 100").fetchall()
    elif user["role"] == "provider":
        rows=db().execute("SELECT c.*,cl.name AS other_name,s.title AS service_title,(SELECT body FROM conversation_messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last_message FROM conversations c JOIN users cl ON cl.id=c.client_id LEFT JOIN services s ON s.id=c.service_id WHERE c.provider_id=? ORDER BY c.updated_at DESC", (user["id"],)).fetchall()
        legacy_messages=db().execute("SELECT m.*,s.title FROM messages m JOIN services s ON s.id=m.service_id WHERE s.user_id=? ORDER BY m.id DESC LIMIT 100", (user["id"],)).fetchall()
    else:
        rows=db().execute("SELECT c.*,p.name AS other_name,s.title AS service_title,(SELECT body FROM conversation_messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last_message FROM conversations c JOIN users p ON p.id=c.provider_id LEFT JOIN services s ON s.id=c.service_id WHERE c.client_id=? ORDER BY c.updated_at DESC", (user["id"],)).fetchall()
    return render_template("inbox.html", messages=[], conversations=rows, legacy_messages=legacy_messages)

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = db()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users WHERE role='provider'").fetchone()[0],
        "services": conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
        "messages": conn.execute("SELECT (SELECT COUNT(*) FROM messages) + (SELECT COUNT(*) FROM conversation_messages)").fetchone()[0],
        "views": conn.execute("SELECT COUNT(*) FROM events WHERE event_type='service_view'").fetchone()[0],
        "today": conn.execute("SELECT COUNT(*) FROM events WHERE date(created_at)=date('now')").fetchone()[0],
        "pending": conn.execute("SELECT COUNT(*) FROM users WHERE role='provider' AND approved=0").fetchone()[0],
    }
    users = conn.execute("SELECT id,name,email,city,profession,approved,created_at FROM users WHERE role='provider' ORDER BY id DESC LIMIT 100").fetchall()
    services = conn.execute("""SELECT s.*,u.name AS provider_name FROM services s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100""").fetchall()
    ads = conn.execute("SELECT * FROM ads ORDER BY slot").fetchall()
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings").fetchall()}
    return render_template("admin.html", stats=stats, users=users, services=services, ads=ads, settings=settings)

@app.route("/admin/app-settings", methods=["POST"])
@login_required
@admin_required
def update_app_settings():
    download_url = request.form.get("app_download_url", "").strip()
    app_label = request.form.get("app_download_label", "ProxiService").strip()[:80] or "ProxiService"
    if download_url:
        from urllib.parse import urlparse
        parsed = urlparse(download_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            flash("Le lien doit commencer par https:// ou http://.", "error")
            return redirect(url_for("admin_dashboard"))
    conn = db()
    conn.execute("INSERT INTO settings(key,value) VALUES('app_download_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (download_url,))
    conn.execute("INSERT INTO settings(key,value) VALUES('app_download_label',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (app_label,))
    conn.commit()
    flash("Les paramètres de téléchargement de l’application ont été enregistrés.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/ad/<int:slot>", methods=["POST"])
@login_required
@admin_required
def update_ad(slot):
    if slot not in (1, 2):
        abort(404)
    conn = db()
    existing = conn.execute("SELECT * FROM ads WHERE slot=?", (slot,)).fetchone()
    image = existing["image"] if existing else ""
    try:
        uploaded = save_upload(f"image_{slot}")
        if uploaded:
            delete_upload(image)
            image = uploaded
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_dashboard"))
    advertiser = request.form.get("advertiser", "").strip()
    target_url = request.form.get("target_url", "").strip()
    active = 1 if request.form.get("active") == "on" else 0
    starts_at = request.form.get("starts_at", "").strip()
    ends_at = request.form.get("ends_at", "").strip()
    conn.execute("""UPDATE ads SET advertiser=?,image=?,target_url=?,active=?,starts_at=?,ends_at=? WHERE slot=?""",
                 (advertiser,image,target_url,active,starts_at,ends_at,slot))
    conn.commit()
    flash(f"Bannière {slot} mise à jour.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/user/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id):
    user = db().execute("SELECT * FROM users WHERE id=? AND role='provider'", (user_id,)).fetchone()
    if not user:
        abort(404)
    db().execute("UPDATE users SET approved=? WHERE id=?", (0 if user["approved"] else 1, user_id))
    db().commit()
    flash("Statut du professionnel modifié.", "success")
    return redirect(url_for("admin_dashboard"))

@app.errorhandler(413)
def too_large(_error):
    flash("Fichier trop volumineux. Taille maximale : 5 Mo.", "error")
    return redirect(request.referrer or url_for("index"))

@app.errorhandler(400)
def bad_request(error):
    message = getattr(error, "description", "Requête invalide.")
    return render_template("error.html", code=400, message=message), 400

@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Accès réservé."), 403

@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Page introuvable."), 404

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
