"""
app/config.py
-------------
Central configuration for all environments.

All constants consumed by the application come from here.
No magic numbers live in route handlers or engine modules.

Environment variables override defaults via python-dotenv.
"""

import os
from pathlib import Path

# Load .env if present (development only)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars may be set by the OS


class Config:
    """Base configuration shared across all environments."""

    # ── Flask core ───────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    DEBUG:      bool = False
    TESTING:    bool = False

    # ── Database ──────────────────────────────────────────────────────────
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'database' / 'audio_platform.db'}"
    )
    SQLALCHEMY_DATABASE_URI:    str  = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ── File upload ───────────────────────────────────────────────────────
    UPLOAD_FOLDER:    Path = BASE_DIR / "uploads"
    REPORTS_FOLDER:   Path = BASE_DIR / "reports_output"
    MAX_CONTENT_LENGTH: int = 50 * 1024 * 1024          # 50 MB
    ALLOWED_AUDIO_EXTENSIONS: frozenset = frozenset({"wav", "mp3", "flac"})

    # ── IBM watsonx.ai / Granite ──────────────────────────────────────────
    IBM_WATSONX_API_KEY:    str = os.environ.get("IBM_WATSONX_API_KEY",    "")
    IBM_WATSONX_PROJECT_ID: str = os.environ.get("IBM_WATSONX_PROJECT_ID", "")
    IBM_WATSONX_URL:        str = os.environ.get("IBM_WATSONX_URL",        "")
    IBM_GRANITE_MODEL_ID:   str = os.environ.get(
        "IBM_GRANITE_MODEL_ID", "ibm/granite-13b-chat-v2"
    )

    # ── Circuit Reliability Score weights ────────────────────────────────
    # Must match weights in health_score.py
    WEIGHT_POWER_MARGIN: float = 0.30
    WEIGHT_STABILITY:    float = 0.25
    WEIGHT_NOISE:        float = 0.25
    WEIGHT_DISTORTION:   float = 0.20

    # ── Logging ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FILE:  str = os.environ.get("LOG_FILE",  "app.log")

    # ── Pagination ────────────────────────────────────────────────────────
    HISTORY_PAGE_SIZE: int = 20

    @classmethod
    def ensure_dirs(cls) -> None:
        """Create upload and report directories if they do not exist."""
        cls.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
        cls.REPORTS_FOLDER.mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    DEBUG = True
    LOG_LEVEL = "DEBUG"


class ProductionConfig(Config):
    DEBUG    = False
    LOG_LEVEL = "WARNING"
    # Enforce strong secret key in production
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "CHANGE-ME-BEFORE-DEPLOYING")


class TestingConfig(Config):
    TESTING = True
    DEBUG   = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    UPLOAD_FOLDER = Config.BASE_DIR / "uploads" / "test"
    REPORTS_FOLDER = Config.BASE_DIR / "reports_output" / "test"


# ── Config selector ──────────────────────────────────────────────────────
_CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
}


def get_config(env: str | None = None) -> type[Config]:
    """Return the config class for the given environment string."""
    env = env or os.environ.get("FLASK_ENV", "development")
    return _CONFIG_MAP.get(env, DevelopmentConfig)
