import pytest

from app import create_app
from config import TestConfig
from extensions import limiter
from models import db


@pytest.fixture
def limited_client():
    class Limited(TestConfig):
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = "memory://"

    app = create_app(Limited)
    with app.app_context():
        limiter.reset()
        yield app.test_client()
        db.session.remove()
        db.drop_all()
        limiter.reset()


def test_login_is_blocked_after_ten_attempts_a_minute(limited_client):
    codes = [limited_client.post("/login", data={"email": "x@y.z", "password": "wrong"}).status_code
             for _ in range(12)]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


def test_blocked_login_page_explains_what_happened(limited_client):
    for _ in range(10):
        limited_client.post("/login", data={"email": "x@y.z", "password": "wrong"})
    res = limited_client.post("/login", data={"email": "x@y.z", "password": "wrong"})
    assert res.status_code == 429 and b"Too many attempts" in res.data
    assert "Retry-After" in res.headers


def test_api_login_is_limited_and_answers_json(limited_client):
    body = {"email": "x@y.z", "password": "wrong"}
    codes = [limited_client.post("/api/v1/auth/login", json=body).status_code for _ in range(11)]
    assert codes[-1] == 429
    res = limited_client.post("/api/v1/auth/login", json=body)
    assert res.is_json and "Too many" in res.get_json()["error"]


def test_normal_browsing_and_health_are_never_limited(limited_client):
    assert all(limited_client.get("/login").status_code == 200 for _ in range(30))
    assert all(limited_client.get("/health").status_code == 200 for _ in range(30))


def test_limits_are_off_by_default_in_tests(client):
    assert all(client.post("/login", data={"email": "a@b.c", "password": "x"}).status_code == 401
               for _ in range(15))
