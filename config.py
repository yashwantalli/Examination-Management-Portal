"""Application configuration.

Everything that differs between a laptop and a real deployment comes from
environment variables, so no secret ever lives in the code base.

  EMP_ENV            development (default) | production
  EMP_SECRET_KEY     required in production
  DATABASE_URL       e.g. postgresql://user:pass@host/db  (EMP_DATABASE_URI also works)
  EMP_ADMIN_EMAIL / EMP_ADMIN_PASSWORD / EMP_ADMIN_NAME   seeded on first start
  EMP_TIMEZONE       wall-clock timezone used for booking windows (default Asia/Kolkata)

See .env.example for the full list.
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

DEV_SECRET = "dev-only-change-me"
DEV_ADMIN_PASSWORD = "Admin@123"


def _database_uri():
    uri = os.environ.get("DATABASE_URL") or os.environ.get("EMP_DATABASE_URI")
    if not uri:
        return "sqlite:///" + os.path.join(INSTANCE_DIR, "emp.sqlite3")
    return normalize_database_uri(uri)


def normalize_database_uri(uri):
    """Point every PostgreSQL URL at the psycopg 3 driver.

    Hosts hand out ``postgres://`` or ``postgresql://``; SQLAlchemy would pick the
    older psycopg2 driver for both, which this project does not install.
    """
    for prefix in ("postgres://", "postgresql://"):
        if uri.startswith(prefix):
            return "postgresql+psycopg://" + uri[len(prefix):]
    return uri


class Config:
    ENV = os.environ.get("EMP_ENV", "development").lower()
    IS_PRODUCTION = ENV == "production"

    SECRET_KEY = os.environ.get("EMP_SECRET_KEY", DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    TIMEZONE = os.environ.get("EMP_TIMEZONE", "Asia/Kolkata")

    # Development builds tables itself; production uses `flask db upgrade` migrations.
    AUTO_CREATE_TABLES = os.environ.get("EMP_AUTO_CREATE_TABLES", "0" if IS_PRODUCTION else "1") == "1"

    # Admin account seeded programmatically on first start (no public admin sign-up).
    ADMIN_EMAIL = os.environ.get("EMP_ADMIN_EMAIL", "admin@emp.local")
    ADMIN_PASSWORD = os.environ.get("EMP_ADMIN_PASSWORD", DEV_ADMIN_PASSWORD)
    ADMIN_NAME = os.environ.get("EMP_ADMIN_NAME", "Portal Administrator")

    # Optional SMTP settings. When MAIL_SERVER is empty, e-mails are only written
    # to the notifications outbox table (visible in the app) instead of being sent.
    MAIL_SERVER = os.environ.get("EMP_MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("EMP_MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("EMP_MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("EMP_MAIL_PASSWORD", "")
    MAIL_SENDER = os.environ.get("EMP_MAIL_SENDER", "no-reply@emp.local")
    MAIL_USE_TLS = os.environ.get("EMP_MAIL_USE_TLS", "1") == "1"

    CSRF_ENABLED = True

    # Cookies: only sent over HTTPS in production, never readable by JavaScript.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PRODUCTION


class TestConfig(Config):
    TESTING = True
    IS_PRODUCTION = False
    AUTO_CREATE_TABLES = True
    # Set TEST_DATABASE_URL to run the whole suite against PostgreSQL instead of SQLite.
    SQLALCHEMY_DATABASE_URI = normalize_database_uri(
        os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:"))
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CSRF_ENABLED = False
    MAIL_SERVER = ""
    SESSION_COOKIE_SECURE = False


def validate_config(cfg):
    """Refuse to start a production server with development defaults."""
    if not cfg.get("IS_PRODUCTION"):
        return
    problems = []
    if cfg.get("SECRET_KEY") == DEV_SECRET or len(cfg.get("SECRET_KEY") or "") < 32:
        problems.append("EMP_SECRET_KEY must be set to a random value of at least 32 characters")
    if cfg.get("ADMIN_PASSWORD") == DEV_ADMIN_PASSWORD:
        problems.append("EMP_ADMIN_PASSWORD must not be the default")
    if cfg.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
        problems.append("DATABASE_URL must point to a real database (SQLite is for development only)")
    if problems:
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
