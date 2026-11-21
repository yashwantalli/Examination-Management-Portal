"""Examination Management Portal (EMP) — application entry point.

Run with:  python app.py         (or:  flask --app app run)
The SQLite database and all tables are created automatically on start-up and
the Admin account is seeded from configuration (see config.py / README.md).
"""
import os
import secrets

import click
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from config import BASE_DIR, INSTANCE_DIR, Config, validate_config
from models import User, db

login_manager = LoginManager()
migrate = Migrate()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # A deactivated account is logged out on its next request.
    return user if user is not None and user.is_active else None


@login_manager.unauthorized_handler
def _unauthorized():
    if request.path.startswith("/api/"):
        return jsonify(error="Authentication required. POST /api/v1/auth/login first."), 401
    return redirect(url_for("auth.login", next=request.full_path))


def create_app(config_object=Config):
    app = Flask(__name__, instance_path=INSTANCE_DIR)
    app.config.from_object(config_object)
    validate_config(app.config)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(BASE_DIR, "migrations"))
    login_manager.init_app(app)

    from routes.admin import bp as admin_bp
    from routes.api import bp as api_bp
    from routes.auth import bp as auth_bp
    from routes.examiner import bp as examiner_bp
    from routes.student import bp as student_bp

    for bp in (auth_bp, admin_bp, examiner_bp, student_bp, api_bp):
        app.register_blueprint(bp)

    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    _register_csrf(app)
    _register_security_headers(app)
    _register_template_helpers(app)
    _register_errors(app)
    _register_cli(app)

    with app.app_context():
        init_database(app)

    @app.route("/health")
    def health():
        """Liveness + database check for the hosting platform. Reveals nothing sensitive."""
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:  # any failure means "not healthy"; details go to the log only
            app.logger.exception("Health check failed")
            return jsonify(status="error"), 503
        return jsonify(status="ok"), 200

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for(f"{current_user.role}.dashboard"))
        return render_template("index.html")

    return app


# --------------------------------------------------------------------------- database
def init_database(app):
    """Prepare the database at start-up.

    Development and tests build tables with ``create_all()``. Production builds them with
    ``flask db upgrade`` (Alembic migrations) and sets EMP_AUTO_CREATE_TABLES=0, so the schema
    is always versioned and reproducible.
    """
    if app.config["AUTO_CREATE_TABLES"]:
        db.create_all()
    ensure_admin(app)


def ensure_admin(app):
    """Seed the Admin superuser if none exists. Safe to run from several workers at once."""
    if not inspect(db.engine).has_table(User.__tablename__):
        app.logger.warning("Database tables are missing. Run `flask db upgrade` first.")
        return
    email = app.config["ADMIN_EMAIL"].strip().lower()
    if User.query.filter_by(role="admin").first():
        return
    admin = User(name=app.config["ADMIN_NAME"], email=email, role="admin", is_active=True)
    admin.set_password(app.config["ADMIN_PASSWORD"])
    db.session.add(admin)
    try:
        db.session.commit()
        app.logger.info("Admin account created: %s", email)
    except IntegrityError:  # another worker created it first
        db.session.rollback()


# --------------------------------------------------------------------------- CSRF
def _register_csrf(app):
    """Lightweight CSRF protection for all HTML form posts.

    The JSON API is protected differently: it only accepts application/json bodies,
    which browsers cannot send cross-site without a CORS pre-flight.
    """

    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _check_csrf():
        if not app.config.get("CSRF_ENABLED") or request.method in ("GET", "HEAD", "OPTIONS"):
            return
        if request.path.startswith("/api/"):
            if request.method in ("POST", "PUT", "PATCH") and not request.is_json:
                return jsonify(error="Content-Type must be application/json"), 415
            return
        token = request.form.get("_csrf")
        if not token or token != session.get("_csrf"):
            abort(400, description="Your form expired. Please go back, refresh and try again.")


# --------------------------------------------------------------------------- security headers
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",   # a few templates use style="..." attributes
    "img-src 'self' data:",               # Bootstrap embeds small SVG icons as data: URIs
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _register_security_headers(app):
    @app.after_request
    def _headers(response):
        h = response.headers
        h.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.is_secure:  # HSTS is only meaningful (and only honoured) over https
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


# --------------------------------------------------------------------------- templates
def _register_template_helpers(app):
    @app.template_filter("dt")
    def _dt(value, fmt="%d %b %Y, %H:%M"):
        return value.strftime(fmt) if value else "—"

    @app.template_filter("marks")
    def _marks(value):
        return f"{value:g}" if value is not None else "—"

    @app.context_processor
    def _inject():
        from models import local_now
        return {"now": local_now()}


def _register_errors(app):
    def _render(code, title, message):
        if request.path.startswith("/api/"):
            return jsonify(error=message), code
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def _400(e):
        return _render(400, "Bad request", getattr(e, "description", "Bad request."))

    @app.errorhandler(403)
    def _403(e):
        return _render(403, "Access denied", "You do not have permission to open this page.")

    @app.errorhandler(404)
    def _404(e):
        return _render(404, "Not found", "The page or record you requested does not exist.")


# --------------------------------------------------------------------------- CLI
def _register_cli(app):
    @app.cli.command("init-db")
    def init_db_cmd():
        """Create tables (development only) and seed the admin account."""
        init_database(app)
        shown = make_url(app.config["SQLALCHEMY_DATABASE_URI"]).render_as_string(hide_password=True)
        click.echo("Database ready at " + shown)

    @app.cli.command("seed-demo")
    @click.option("--reset", is_flag=True, help="Drop all tables first.")
    def seed_demo_cmd(reset):
        """Load demo courses, examinations, examiners, students and bookings."""
        if app.config.get("IS_PRODUCTION"):
            raise click.ClickException("seed-demo creates accounts with known passwords; disabled in production.")
        from seed import seed_demo
        if reset:
            db.drop_all()
            init_database(app)
        seed_demo()
        click.echo("Demo data loaded. See README.md for the demo accounts.")


if __name__ == "__main__":
    # `flask --app app run` also works: Flask discovers the create_app() factory.
    app = create_app()
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=int(os.environ.get("PORT", "5000")))
