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
DB_PATH = os.environ.get("DATABASE_PATH", str(APP_DIR / "proxiservice.db"))

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
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

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "current_year": datetime.utcnow().year}

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
    service = conn.execute("""SELECT s.*,u.name AS provider_name,u.phone,u.whatsapp,u.email,u.bio AS provider_bio
                              FROM services s JOIN users u ON u.id=s.user_id
                              WHERE s.id=? AND s.active=1 AND u.approved=1""", (service_id,)).fetchone()
    if not service:
        abort(404)
    conn.execute("INSERT INTO events(event_type,service_id,created_at) VALUES('service_view',?,?)", (service_id, datetime.utcnow().isoformat()))
    conn.commit()
    return render_template("service.html", service=service)

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
        code = request.form.get("code", "")
        configured_code = os.environ.get("ADMIN_PASSWORD", "3004")
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@proxiservice.com").strip().lower()
        if code and secrets.compare_digest(code, configured_code):
            admin = db().execute("SELECT id FROM users WHERE email=? AND role='admin'", (admin_email,)).fetchone()
            if admin:
                session.clear()
                session["user_id"] = admin["id"]
                flash("Accès administrateur autorisé.", "success")
                return redirect(url_for("admin_dashboard"))
            error = "Compte administrateur introuvable. Vérifiez ADMIN_EMAIL puis relancez l’application."
        else:
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
            return redirect(url_for("admin_dashboard") if user["role"] == "admin" else url_for("my_services"))
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
    return redirect(url_for("admin_dashboard") if user["role"] == "admin" else url_for("my_services"))

@app.route("/message/<int:service_id>", methods=["GET", "POST"])
def message_provider(service_id):
    service = db().execute("""SELECT s.*,u.name AS provider_name FROM services s JOIN users u ON u.id=s.user_id
                              WHERE s.id=? AND s.active=1 AND u.approved=1""", (service_id,)).fetchone()
    if not service:
        abort(404)
    if request.method == "POST":
        sender_name = request.form.get("sender_name", "").strip()[:100]
        sender_contact = request.form.get("sender_contact", "").strip()[:160]
        body = request.form.get("body", "").strip()[:2000]
        if not sender_name or len(body) < 2:
            flash("Indiquez votre nom et un message.", "error")
        else:
            db().execute("INSERT INTO messages(service_id,sender_name,sender_contact,body,created_at) VALUES(?,?,?,?,?)",
                         (service_id,sender_name,sender_contact,body,datetime.utcnow().isoformat()))
            db().commit()
            flash("Votre message a été transmis. Le prestataire pourra le consulter dans son espace.", "success")
            return redirect(url_for("service_detail", service_id=service_id))
    return render_template("message.html", service=service)

@app.route("/inbox")
@login_required
def inbox():
    user = current_user()
    if user["role"] == "admin":
        rows = db().execute("""SELECT m.*,s.title FROM messages m JOIN services s ON s.id=m.service_id ORDER BY m.id DESC LIMIT 200""").fetchall()
    else:
        rows = db().execute("""SELECT m.*,s.title FROM messages m JOIN services s ON s.id=m.service_id
                               WHERE s.user_id=? ORDER BY m.id DESC LIMIT 200""", (user["id"],)).fetchall()
    return render_template("inbox.html", messages=rows)

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = db()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users WHERE role='provider'").fetchone()[0],
        "services": conn.execute("SELECT COUNT(*) FROM services").fetchone()[0],
        "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
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
