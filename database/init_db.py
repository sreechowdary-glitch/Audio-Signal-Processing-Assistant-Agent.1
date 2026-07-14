"""
database/init_db.py
-------------------
Standalone database initialisation script.
Creates all tables in the SQLite database.

Usage:
    python database/init_db.py
"""

import sys
from pathlib import Path

# Ensure the project root is in the Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app

app = create_app("development")

with app.app_context():
    from app.extensions import db
    db.create_all()
    print("Database initialised: all tables created.")
