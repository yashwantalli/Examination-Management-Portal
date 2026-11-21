"""The migrations must build the same schema the models describe, and admin seeding must be safe."""
import pytest
from flask_migrate import check, downgrade, upgrade
from sqlalchemy import inspect

from app import create_app, ensure_admin
from config import TestConfig
from models import User, db


@pytest.fixture
def migrated_app(tmp_path):
    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'm.sqlite3'}"
        AUTO_CREATE_TABLES = False  # production behaviour: tables only come from migrations

    app = create_app(MigrationConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


def test_start_without_tables_does_not_crash_and_seeds_nothing(migrated_app):
    assert not inspect(db.engine).has_table("users")


def test_upgrade_builds_schema_and_admin_can_be_seeded(migrated_app):
    upgrade()
    assert {"users", "slots", "bookings", "alembic_version"} <= set(inspect(db.engine).get_table_names())
    ensure_admin(migrated_app)
    assert User.query.filter_by(role="admin").count() == 1


def test_ensure_admin_is_idempotent(migrated_app):
    upgrade()
    ensure_admin(migrated_app)
    ensure_admin(migrated_app)
    assert User.query.filter_by(role="admin").count() == 1


def test_models_and_migrations_have_no_drift(migrated_app):
    upgrade()
    check()  # raises if `flask db migrate` would generate changes


def test_downgrade_removes_everything(migrated_app):
    upgrade()
    downgrade(revision="base")
    assert set(inspect(db.engine).get_table_names()) <= {"alembic_version"}
