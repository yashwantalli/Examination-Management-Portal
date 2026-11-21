from sqlalchemy.orm import Session


def test_health_is_ok_and_needs_no_login(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


def test_health_reports_503_without_leaking_details_when_db_is_down(client, monkeypatch):
    def boom(self, *args, **kwargs):
        raise RuntimeError("password=hunter2 host=internal-db")

    monkeypatch.setattr(Session, "execute", boom)
    res = client.get("/health")
    assert res.status_code == 503
    assert res.get_json() == {"status": "error"}
    assert b"hunter2" not in res.data and b"internal-db" not in res.data
