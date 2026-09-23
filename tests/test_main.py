from fastapi.testclient import TestClient


def test_health(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.DB_PATH", tmp_path / "app.db")
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert (tmp_path / "app.db").exists()
