"""
app/extensions.py
-----------------
Shared extension singletons — instantiated here, initialised in app factory.
Avoids circular imports between models and the factory.
"""

import logging
from logging.handlers import RotatingFileHandler

from flask_sqlalchemy import SQLAlchemy

# ── SQLAlchemy singleton ─────────────────────────────────────────────────────
db = SQLAlchemy()


# ── Logging setup ─────────────────────────────────────────────────────────────
def configure_logging(app) -> None:
    """
    Attach rotating file + console handlers to the Flask app logger.
    Called inside create_app() after config is loaded.
    """
    log_level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    log_file  = app.config.get("LOG_FILE", "app.log")

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (rotating, max 5 MB × 3 backups)
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    # Root logger — catches all module-level loggers
    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    app.logger.setLevel(log_level)
    app.logger.info("Logging configured (level=%s, file=%s)", app.config["LOG_LEVEL"], log_file)
