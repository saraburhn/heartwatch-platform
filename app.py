import os
import sqlite3
import datetime as dt
import random
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------- APP SETUP --------------------
APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "instance", "heartwatch.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.permanent_session_lifetime = dt.timedelta(days=7)

# -------------------- DATABASE --------------------
def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = get_db()
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
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            location TEXT
        );
    """)
    db.commit()

@app.before_request
def before_request():
    init_db()

# -------------------- AUTH --------------------
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

# -------------------- LOGIC --------------------
def detect_status(bpm):
    if bpm > 150:
        return "critical"
    if bpm < 45 or bpm > 120:
        return "abnormal"
    return "normal"

# -------------------- ROUTES --------------------
@app.route("/")
def index():
    return render_template("index.html", user=current_user())

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        password = request.form["password"]

        try:
            get_db().execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, generate_password_hash(password), dt.datetime.utcnow().isoformat())
            )
            get_db().commit()
            flash("Account created, login now.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already exists.", "danger")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        remember = request.form.get("remember")

        user = get_db().execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True if remember else False

        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    db = get_db()

    latest = db.execute(
        "SELECT * FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()

    recent = db.execute(
        "SELECT ts, bpm FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 20", (uid,)
    ).fetchall()

    return render_template(
        "dashboard.html",
        latest=latest,
        recent=list(reversed(recent))
    )

@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    bpm = random.randint(40, 180)
    status = detect_status(bpm)

    get_db().execute(
        "INSERT INTO readings (user_id, ts, bpm, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            session["user_id"],
            dt.datetime.utcnow().isoformat(),
            bpm,
            status,
            dt.datetime.utcnow().isoformat()
        )
    )
    get_db().commit()

    return redirect(url_for("dashboard"))

# ✅ ✅ ✅ ALERT ROUTE (المشكلة كانت هون)
@app.route("/alert", methods=["POST"])
@login_required
def alert():
    uid = session["user_id"]
    db = get_db()

    latest = db.execute(
        "SELECT bpm FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()

    if not latest:
        flash("No heart rate data.", "warning")
        return redirect(url_for("dashboard"))

    db.execute(
        "INSERT INTO alerts (user_id, ts, bpm, location) VALUES (?, ?, ?, ?)",
        (
            uid,
            dt.datetime.utcnow().isoformat(),
            latest["bpm"],
            "Demo Location"
        )
    )
    db.commit()

    flash("🚨 Emergency alert sent!", "danger")
    return redirect(url_for("dashboard"))

@app.route("/history")
@login_required
def history():
    uid = session["user_id"]
    alerts = get_db().execute(
        "SELECT * FROM alerts WHERE user_id=? ORDER BY ts DESC", (uid,)
    ).fetchall()
    return render_template("history.html", alerts=alerts)

# -------------------- RUN --------------------
if __name__ == "__main__":
    app.run(debug=True)
