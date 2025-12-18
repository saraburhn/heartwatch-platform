# Deploy HeartWatch (public link)

This project is a Flask app. You can deploy it easily using Render.

## Render (recommended)

1) Upload the code to GitHub as a repository.
2) In Render: New + -> Web Service -> connect your GitHub repo.
3) Settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn wsgi:app`
4) Deploy -> you'll receive a public URL.

## Replit (quick demo)
- Create a Python Repl
- Upload all files
- Run
- Open the web view URL.
