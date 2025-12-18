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

# Avoid re-creating tables on EVERY request
_DB_READY = False


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Safer SQLite defaults
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 3000;")
        except Exception:
            pass

        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
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
            label INTEGER,
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
        """
    )
    db.commit()


@app.before_request
def _ensure_db_once():
    global _DB_READY
    if not _DB_READY:
        init_db()
        _DB_READY = True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def detect_status(bpm: int, recent_bpms=None) -> str:
    """
    Rule-based detection:
    - normal: 45..120
    - abnormal: <45 or 121..150
    - critical: >150 OR repeated abnormal pattern
    """
    if bpm > 150:
        return "critical"

    if bpm < 45 or bpm > 120:
        if recent_bpms and sum(1 for x in recent_bpms[-2:] if (x < 45 or x > 120)) >= 2:
            return "critical"
        return "abnormal"

    return "normal"


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def _normalize_row_keys(row: dict) -> dict:
    """Make CSV headers case-insensitive: {'Timestamp': '...'} -> {'timestamp': '...'}"""
    return {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def _pick(row: dict, keys: list[str]) -> str:
    for k in keys:
        v = row.get(k, "")
        if v != "":
            return v
    return ""


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": dt.datetime.utcnow().isoformat()}


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
                (email, generate_password_hash(password), dt.datetime.utcnow().isoformat()),
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

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT * FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()

    recent = db.execute(
        "SELECT ts, bpm, status FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 50", (uid,)
    ).fetchall()
    recent_list = list(reversed([dict(r) for r in recent]))

    contacts = db.execute(
        "SELECT * FROM contacts WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()

    return render_template("dashboard.html", latest=latest, recent=recent_list, contacts=contacts)


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    db = get_db()
    uid = session["user_id"]

    last_bpms = db.execute(
        "SELECT bpm FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 5", (uid,)
    ).fetchall()
    last_bpms = [r["bpm"] for r in last_bpms]

    mode = request.form.get("mode", "normal")  # normal/abnormal/attack/random

    if mode == "normal":
        bpm = random.randint(60, 90)
    elif mode == "abnormal":
        bpm = random.choice([random.randint(121, 150), random.randint(35, 44)])
    elif mode == "attack":
        bpm = random.randint(155, 190)
    else:
        bpm = random.choices(
            population=[
                random.randint(60, 90),
                random.randint(121, 150),
                random.randint(35, 44),
                random.randint(155, 190),
            ],
            weights=[0.90, 0.07, 0.02, 0.01],
            k=1,
        )[0]

    status = detect_status(int(bpm), recent_bpms=last_bpms)
    ts = dt.datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    db.execute(
        "INSERT INTO readings (user_id, ts, bpm, label, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, ts, int(bpm), None, status, dt.datetime.utcnow().isoformat()),
    )
    db.commit()

    flash(f"Simulated reading saved: {bpm} bpm ({status}).", "success")
    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a CSV file.", "warning")
            return redirect(url_for("upload"))

        raw = file.read().decode("utf-8", errors="ignore")
        if not raw.strip():
            flash("Empty file.", "danger")
            return redirect(url_for("upload"))

        import csv
        from io import StringIO

        reader = csv.DictReader(StringIO(raw))

        # Accept flexible headers (case-insensitive)
        header_lower = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
        has_ts = any(x in header_lower for x in ["timestamp", "ts", "time", "datetime"])
        has_bpm = any(x in header_lower for x in ["bpm", "heart_rate", "heartrate", "hr"])

        if not (has_ts and has_bpm):
            flash(
                "CSV must include timestamp and bpm columns (case-insensitive). "
                "Allowed examples: timestamp/ts/time + bpm/hr/heart_rate. (label optional)",
                "danger",
            )
            return redirect(url_for("upload"))

        db = get_db()
        uid = session["user_id"]
        inserted = 0

        last_bpms = db.execute(
            "SELECT bpm FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 5", (uid,)
        ).fetchall()
        last_bpms = [r["bpm"] for r in last_bpms]

        for row in reader:
            r = _normalize_row_keys(row)

            ts = _pick(r, ["timestamp", "ts", "time", "datetime"])
            bpm_raw = _pick(r, ["bpm", "hr", "heart_rate", "heartrate"])
            label_raw = _pick(r, ["label"])

            if not ts or not bpm_raw:
                continue

            try:
                bpm = int(float(bpm_raw))
            except ValueError:
                continue

            status = detect_status(bpm, recent_bpms=last_bpms)
            last_bpms.append(bpm)

            label = None
            if label_raw != "":
                try:
                    label = int(float(label_raw))
                except ValueError:
                    label = None

            db.execute(
                "INSERT INTO readings (user_id, ts, bpm, label, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, ts, bpm, label, status, dt.datetime.utcnow().isoformat()),
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
            (uid, name, phone, email, dt.datetime.utcnow().isoformat()),
        )
        db.commit()
        flash("Contact added.", "success")
        return redirect(url_for("contacts"))

    items = db.execute(
        "SELECT * FROM contacts WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()

    return render_template("contacts.html", contacts=items)


@app.route("/contacts/delete/<int:cid>", methods=["POST"])
@login_required
def delete_contact(cid):
    db = get_db()
    uid = session["user_id"]

    db.execute("DELETE FROM contacts WHERE id=? AND user_id=?", (cid, uid))
    db.commit()

    flash("Contact deleted.", "info")
    return redirect(url_for("contacts"))


@app.route("/alert", methods=["POST"])
@login_required
def alert():
    db = get_db()
    uid = session["user_id"]

    latest = db.execute(
        "SELECT * FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)
    ).fetchone()
    if latest is None:
        flash("No reading available. Simulate or upload first.", "warning")
        return redirect(url_for("dashboard"))

    location = request.form.get("location", "").strip() or "GPS: 29.3759, 47.9774 (demo)"

    contacts = db.execute(
        "SELECT name, phone, email FROM contacts WHERE user_id=?", (uid,)
    ).fetchall()

    recipients = ", ".join(
        [f"{c['name']}({c['phone'] or c['email'] or 'no-contact'})" for c in contacts]
    ) or "No contacts saved"

    db.execute(
        "INSERT INTO alerts (user_id, ts, bpm, location, recipients, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            uid,
            dt.datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
            int(latest["bpm"]),
            location,
            recipients,
            dt.datetime.utcnow().isoformat(),
        ),
    )
    db.commit()

    flash(f"🚨 Emergency alert simulated and saved. Recipients: {recipients}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/history")
@login_required
def history():
    db = get_db()
    uid = session["user_id"]

    readings = db.execute(
        "SELECT ts, bpm, status, label FROM readings WHERE user_id=? ORDER BY ts DESC LIMIT 200",
        (uid,),
    ).fetchall()

    alerts = db.execute(
        "SELECT ts, bpm, location, recipients FROM alerts WHERE user_id=? ORDER BY ts DESC LIMIT 50",
        (uid,),
    ).fetchall()

    return render_template("history.html", readings=readings, alerts=alerts)


if __name__ == "__main__":
    app.run(debug=True)
