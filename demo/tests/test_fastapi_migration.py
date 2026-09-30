from fastapi import FastAPI
from fastapi.testclient import TestClient

from tax_manager.app import create_app


def test_application_uses_fastapi_and_keeps_public_and_private_routes(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    assert isinstance(app, FastAPI)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    response = client.get("/customers", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_requires_csrf_token_before_accessing_database(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = TestClient(app)
    assert client.get("/login").status_code == 200
    monkeypatch.setattr(
        "tax_manager.auth.connect",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be contacted")),
    )
    response = client.post("/login", data={"username": "wfg1", "password": "not-used"})
    assert response.status_code == 400


def test_request_size_limit_applies_to_streamed_bodies(tmp_path):
    app = create_app({
        "TESTING": True, "DATABASE_URL": "postgresql://unused",
        "STORAGE_DIR": tmp_path, "MAX_CONTENT_LENGTH": 100,
    })
    client = TestClient(app)
    response = client.post(
        "/login", content=iter([b"username=", b"x" * 200]),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413
