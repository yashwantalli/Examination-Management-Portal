import logging


def test_failed_login_is_logged_without_the_password(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/login", data={"email": "Nobody@Example.com", "password": "TopSecret99"})
    text = caplog.text
    assert "Failed login" in text and "nobody@example.com" in text
    assert "TopSecret99" not in text


def test_failed_api_login_is_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "nope-nope1"})
    assert "Failed API login" in caplog.text and "nope-nope1" not in caplog.text


def test_log_lines_cannot_be_forged_with_newlines_in_the_email(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/login", data={"email": "x\nINFO fake: Login: user 1 (admin)", "password": "p"})
    assert all(not line.startswith("INFO fake") for line in caplog.text.splitlines())
