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

# ================== DATABASE ==================
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
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            location TEXT,
            recipients TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    db.commit()

@app.before_request
def before():
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
def detect_status(bpm):
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
        email = request.form.get("email","").lower().strip()
        password = request.form.get("password","")
        if not email or not password:
            flash("Email and password required", "danger")
            return redirect("/register")

        try:
            get_db().execute(
                "INSERT INTO users (email,password_hash,created_at) VALUES (?,?,?)",
                (email, generate_password_hash(password), dt.datetime.utcnow().isoformat())
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            flash("Email already exists", "warning")
            return redirect("/register")

        flash("Account created", "success")
        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").lower().strip()
        password = request.form.get("password","")

        user = get_db().execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid login", "danger")
            return redirect("/login")

        session.clear()
        session["user_id"] = user["id"]
        return redirect("/dashboard")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT * FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()

    recent = db.execute(
        "SELECT ts,bpm,status FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 50", (uid,)
    ).fetchall()

    contacts = db.execute(
        "SELECT * FROM contacts WHERE user_id=?", (uid,)
    ).fetchall()

    return render_template(
        "dashboard.html",
        latest=latest,
        recent=list(reversed([dict(r) for r in recent])),
        contacts=contacts
    )

@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    mode = request.form.get("mode","normal")

    if mode == "normal":
        bpm = random.randint(60,90)
    elif mode == "abnormal":
        bpm = random.choice([random.randint(121,150), random.randint(35,44)])
    elif mode == "attack":
        bpm = random.randint(155,190)
    else:
        bpm = random.randint(40,180)

    status = detect_status(bpm)
    now = dt.datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    get_db().execute(
        "INSERT INTO readings (user_id,ts,bpm,status,created_at) VALUES (?,?,?,?,?)",
        (session["user_id"], now, bpm, status, now)
    )
    get_db().commit()

    return redirect("/dashboard")

@app.route("/alert", methods=["POST"])
@login_required
def alert():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT bpm FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()

    if not latest:
        return redirect("/dashboard")

    db.execute(
        "INSERT INTO alerts (user_id,ts,bpm,location,recipients,created_at) VALUES (?,?,?,?,?,?)",
        (uid, dt.datetime.utcnow().isoformat(), latest["bpm"], "Demo", "Demo", dt.datetime.utcnow().isoformat())
    )
    db.commit()

    return redirect("/dashboard")

@app.route("/upload", methods=["GET","POST"])
@login_required
def upload():
    if request.method == "POST":
        import csv
        file = request.files.get("file")
        if not file:
            return redirect("/upload")

        reader = csv.DictReader(file.read().decode("utf-8").splitlines())
        for row in reader:
            bpm = int(row["bpm"])
            ts = row["timestamp"]
            status = detect_status(bpm)
            get_db().execute(
                "INSERT INTO readings (user_id,ts,bpm,status,created_at) VALUES (?,?,?,?,?)",
                (session["user_id"], ts, bpm, status, dt.datetime.utcnow().isoformat())
            )
        get_db().commit()
        return redirect("/dashboard")

    return render_template("upload.html")

@app.route("/contacts", methods=["GET","POST"])
@login_required
def contacts():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name")
        phone = request.form.get("phone")
        email = request.form.get("email")
        db.execute(
            "INSERT INTO contacts (user_id,name,phone,email,created_at) VALUES (?,?,?,?,?)",
            (session["user_id"], name, phone, email, dt.datetime.utcnow().isoformat())
        )
        db.commit()
        return redirect("/contacts")

    items = db.execute(
        "SELECT * FROM contacts WHERE user_id=?", (session["user_id"],)
    ).fetchall()

    return render_template("contacts.html", contacts=items)

if __name__ == "__main__":
    app.run(debug=True)
