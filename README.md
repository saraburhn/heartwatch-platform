# HeartWatch – Smartwatch Heart Attack Detection & Emergency Alerting (Demo Platform)

This is a simple, editable **real web app** (Flask + SQLite) that supports:
- Account creation (Register/Login/Logout)
- Heart-rate simulation + abnormal detection
- Upload CSV heart-rate readings
- Heart-rate history dashboard
- Emergency contacts management
- Emergency alert **simulation** (stores an alert record and shows recipients)

## 1) Run locally (Windows / macOS / Linux)

### A) Create virtualenv (recommended)
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
```

### B) Install dependencies
```bash
pip install -r requirements.txt
```

### C) Start the web server
```bash
python app.py
```
Then open: http://127.0.0.1:5000

## 2) Dataset included
- `data/sample_heart_rate_dataset.csv`
- Columns: `user_id, timestamp, bpm, label`
  - label: 0 = normal, 1 = abnormal, 2 = simulated heart-attack spike

## 3) Upload data
After login, go to **Upload CSV** and upload the dataset (or your own CSV).
Required columns: `timestamp,bpm` (label optional).

## 4) Deploy (easy)
- **Render** / **Railway** / **PythonAnywhere**: Upload the project, set start command `python app.py`
- Or Dockerize later if you want.

## Notes
- Detection is rule-based by default (safe demo).
- You can replace it with an ML model later (e.g., logistic regression) once you decide your feature set.
