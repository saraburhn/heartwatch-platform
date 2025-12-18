import os
import sqlite3
import datetime as dt
import random
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "instance", "heartwatch.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


# ================== DB ==================
def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _table_columns(db, table_name: str):
    try:
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {r["name"] for r in rows}
    except sqlite3.Error:
        return set()


def _ensure_column(db, table: str, col: str, col_type: str):
    cols = _table_columns(db, table)
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def init_db():
    db = get_db()

    # Create tables (safe)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            status TEXT,
            label INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            location TEXT,
            recipients TEXT,
            created_at TEXT
        );
    """)

    # Auto-migrate missing columns (fixes 500 after login)
    try:
        _ensure_column(db, "readings", "status", "TEXT")
        _ensure_column(db, "readings", "label", "INTEGER")
        _ensure_column(db, "readings", "created_at", "TEXT")

        _ensure_column(db, "contacts", "phone", "TEXT")
        _ensure_column(db, "contacts", "email", "TEXT")
        _ensure_column(db, "contacts", "created_at", "TEXT")

        _ensure_column(db, "alerts", "location", "TEXT")
        _ensure_column(db, "alerts", "recipients", "TEXT")
        _ensure_column(db, "alerts", "created_at", "TEXT")
    except sqlite3.Error:
        # إذا صار شي غريب بالملف القديم، نعمل إعادة إنشاء نظيفة
        try:
            db.close()
        except Exception:
            pass
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

        # افتح DB من جديد وأنشئ كل شيء
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                bpm INTEGER NOT NULL,
                status TEXT,
                label INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                bpm INTEGER NOT NULL,
                location TEXT,
                recipients TEXT,
                created_at TEXT
            );
        """)
        db.commit()

    db.commit()


@app.before_request
def _ensure_db():
    init_db()


# ================== AUTH ==================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


# ================== LOGIC ==================
def detect_status(bpm: int) -> str:
    if bpm > 150:
        return "critical"
    if bpm < 45 or bpm > 120:
        return "abnormal"
    return "normal"


# ================== ROUTES ==================
@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("register"))

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, generate_password_hash(password), dt.datetime.utcnow().isoformat())
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("This email is already registered.", "warning")
            return redirect(url_for("register"))

        flash("Account created. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = get_db().execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT ts, bpm, COALESCE(status,'normal') AS status FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1",
        (uid,)
    ).fetchone()

    recent = db.execute(
        "SELECT ts, bpm, COALESCE(status,'normal') AS status FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 50",
        (uid,)
    ).fetchall()
    recent_list = list(reversed([dict(r) for r in recent]))

    contacts = db.execute(
        "SELECT * FROM contacts WHERE user_id=? ORDER BY id DESC",
        (uid,)
    ).fetchall()

    return render_template("dashboard.html", latest=latest, recent=recent_list, contacts=contacts)


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    db = get_db()
    uid = session["user_id"]

    mode = request.form.get("mode", "normal")

    if mode == "normal":
        bpm = random.randint(60, 90)
    elif mode == "abnormal":
        bpm = random.choice([random.randint(121, 150), random.randint(35, 44)])
    elif mode == "attack":
        bpm = random.randint(155, 190)
    else:
        bpm = random.randint(40, 180)

    status = detect_status(int(bpm))
    now = dt.datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    db.execute(
        "INSERT INTO readings (user_id, ts, bpm, status, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, now, int(bpm), status, None, dt.datetime.utcnow().isoformat())
    )
    db.commit()

    return redirect(url_for("dashboard"))


@app.route("/alert", methods=["POST"])
@login_required
def alert():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT ts, bpm, COALESCE(status,'normal') AS status FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1",
        (uid,)
    ).fetchone()

    if latest is None:
        flash("No reading available. Simulate or upload first.", "warning")
        return redirect(url_for("dashboard"))

    location = request.form.get("location", "").strip() or "Demo location"
    contacts = db.execute(
        "SELECT name, phone, email FROM contacts WHERE user_id=?",
        (uid,)
    ).fetchall()
    recipients = ", ".join([f"{c['name']}" for c in contacts]) or "No contacts saved"

    db.execute(
        "INSERT INTO alerts (user_id, ts, bpm, location, recipients, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, dt.datetime.utcnow().isoformat(sep=" ", timespec="seconds"), int(latest["bpm"]), location, recipients, dt.datetime.utcnow().isoformat())
    )
    db.commit()

    flash("🚨 Emergency alert sent (simulated).", "danger")
    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a CSV file.", "warning")
            return redirect(url_for("upload"))

        import csv
        content = file.read().decode("utf-8", errors="ignore").splitlines()
        reader = csv.DictReader(content)

        db = get_db()
        uid = session["user_id"]
        inserted = 0

        for row in reader:
            ts = (row.get("timestamp") or "").strip()
            bpm_raw = (row.get("bpm") or "").strip()
            if not ts or not bpm_raw:
                continue
            try:
                bpm = int(float(bpm_raw))
            except ValueError:
                continue

            status = detect_status(bpm)
            db.execute(
                "INSERT INTO readings (user_id, ts, bpm, status, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, ts, bpm, status, None, dt.datetime.utcnow().isoformat())
            )
            inserted += 1

        db.commit()
        flash(f"Uploaded successfully. Inserted {inserted} rows.", "success")
        return redirect(url_for("dashboard"))

    return render_template("upload.html")


@app.route("/contacts", methods=["GET", "POST"])
@login_required
def contacts():
    db = get_db()
    uid = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if not name:
            flash("Contact name is required.", "warning")
            return redirect(url_for("contacts"))

        db.execute(
            "INSERT INTO contacts (user_id, name, phone, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, name, phone, email, dt.datetime.utcnow().isoformat())
        )
        db.commit()
        flash("Contact added.", "success")
        return redirect(url_for("contacts"))

    items = db.execute(
        "SELECT * FROM contacts WHERE user_id=? ORDER BY id DESC",
        (uid,)
    ).fetchall()
    return render_template("contacts.html", contacts=items)


@app.route("/history")
@login_required
def history():
    db = get_db()
    uid = session["user_id"]

    readings = db.execute(
        "SELECT ts, bpm, COALESCE(status,'normal') AS status, label FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 200",
        (uid,)
    ).fetchall()

    alerts = db.execute(
        "SELECT ts, bpm, location, recipients FROM alerts WHERE user_id=? ORDER BY ts DESC LIMIT 50",
        (uid,)
    ).fetchall()

    return render_template("history.html", readings=readings, alerts=alerts)


if __name__ == "__main__":
    app.run(debug=True)
