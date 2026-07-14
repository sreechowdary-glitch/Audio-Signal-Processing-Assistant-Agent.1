"""
app/__init__.py
---------------
Flask application factory.

Usage
-----
    from app import create_app
    app = create_app('development')
"""

import logging
from flask import Flask, render_template

from app.config     import get_config
from app.extensions import db, configure_logging

logger = logging.getLogger(__name__)


def create_app(env: str | None = None) -> Flask:
    """
    Create and configure the Flask application.

    Parameters
    ----------
    env : 'development' | 'production' | 'testing'
          Falls back to FLASK_ENV environment variable, then 'development'.

    Returns
    -------
    Flask application instance
    """
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )

    # ── Load configuration ────────────────────────────────────────────────
    config_class = get_config(env)
    app.config.from_object(config_class)
    config_class.ensure_dirs()

    # ── Logging ───────────────────────────────────────────────────────────
    configure_logging(app)

    # ── Database ──────────────────────────────────────────────────────────
    db.init_app(app)
    with app.app_context():
        # Import models so SQLAlchemy registers them before create_all
        from app.models import (      # noqa: F401
            AnalysisSession,
            AudioAnalysisResult,
            CircuitDiagnosticResult,
            ReportRecord,
        )
        db.create_all()
        logger.info("Database tables created/verified.")

    # ── Register API blueprints ───────────────────────────────────────────
    from app.api import register_blueprints
    register_blueprints(app)

    # ── Frontend route ────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return render_template("index.html")

    # ── Global error handlers ─────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        from app.utils.response_utils import not_found_response
        return not_found_response(str(e))

    @app.errorhandler(413)
    def request_too_large(e):
        from app.utils.response_utils import error_response
        return error_response("File exceeds the 50 MB maximum upload limit.", 413)

    @app.errorhandler(500)
    def internal_error(e):
        from app.utils.response_utils import server_error_response
        logger.error("Unhandled 500: %s", e)
        return server_error_response("An unexpected error occurred.")

    @app.errorhandler(405)
    def method_not_allowed(e):
        from app.utils.response_utils import error_response
        return error_response(f"Method not allowed: {e}", 405)

    logger.info(
        "Application created [env=%s, debug=%s, db=%s]",
        env or "development",
        app.config["DEBUG"],
        app.config["SQLALCHEMY_DATABASE_URI"],
    )
    return app
