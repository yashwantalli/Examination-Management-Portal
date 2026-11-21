"""Fixtures shared by every test module."""
import pytest

from app import create_app
from config import TestConfig
from models import db


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
