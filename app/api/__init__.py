"""
app/api/__init__.py
-------------------
API Blueprint package.
Registers all route blueprints under the /api prefix.
"""

from flask import Blueprint

# Import all route modules so their blueprints are defined
from app.api.audio_routes   import audio_bp
from app.api.circuit_routes import circuit_bp
from app.api.granite_routes import granite_bp
from app.api.history_routes import history_bp
from app.api.report_routes  import report_bp


def register_blueprints(app):
    """Register all API blueprints on the Flask app under /api prefix."""
    app.register_blueprint(audio_bp,   url_prefix="/api")
    app.register_blueprint(circuit_bp, url_prefix="/api")
    app.register_blueprint(granite_bp, url_prefix="/api")
    app.register_blueprint(history_bp, url_prefix="/api")
    app.register_blueprint(report_bp,  url_prefix="/api")
