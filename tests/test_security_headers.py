from app import CONTENT_SECURITY_POLICY, create_app
from config import TestConfig


def test_pages_carry_security_headers(client):
    h = client.get("/login").headers
    assert h["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_json_and_error_responses_also_get_headers(client):
    assert client.get("/health").headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/no-such-page").headers["X-Frame-Options"] == "DENY"


def test_hsts_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/login").headers
    secure = client.get("/login", base_url="https://portal.example")
    assert secure.headers["Strict-Transport-Security"].startswith("max-age=")


def test_proxy_headers_are_trusted_only_when_enabled(tmp_path):
    class Behind(TestConfig):
        TRUST_PROXY = True

    on = create_app(Behind).test_client().get("/login", headers={"X-Forwarded-Proto": "https"})
    off = create_app(TestConfig).test_client().get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in on.headers
    assert "Strict-Transport-Security" not in off.headers


def test_templates_use_no_inline_scripts_or_external_assets():
    """The strict CSP would silently break these, so fail loudly instead."""
    import pathlib
    import re

    bad = []
    for path in pathlib.Path("templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"<script(?![^>]*\bsrc=)", text) or re.search(r"\son(click|submit|change|load)=", text):
            bad.append(f"{path}: inline script/handler")
        if re.search(r"(src|href)=\"https?://", text):
            bad.append(f"{path}: external asset")
    assert not bad, bad
