from sqlalchemy.engine import make_url

from app import create_app
from config import TestConfig


def test_init_db_never_prints_the_database_password(tmp_path, monkeypatch):
    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'x.sqlite3'}"

    app = create_app(Cfg)
    # Simulate a Postgres URL with a secret for the message only; the engine is not used.
    secret_url = "postgresql+psycopg://user:SuperSecret123@db:5432/emp"
    app.config["SQLALCHEMY_DATABASE_URI"] = secret_url
    assert "SuperSecret123" not in make_url(secret_url).render_as_string(hide_password=True)
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert "SuperSecret123" not in result.output
