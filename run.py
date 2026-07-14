"""
run.py — Development server entry point.
Do not use in production; use wsgi.py with Gunicorn.
"""

from app import create_app

app = create_app("development")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
