from datetime import date

from tax_manager.app import create_app


def test_customer_create_api_accepts_all_optional_fields_and_separate_monthly_states(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fixed = datetime(2026, 9, 30, 13, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("tax_manager.customers.api.china_now", lambda: fixed)
    statements = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            statements.append((sql, params))
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "SELECT id FROM tag_categories" in sql:
                return Result({"id": params[0]})
            if "INSERT INTO customers" in sql:
                return Result({"id": 17})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/api/customers", json={
        "name": "API客户", "tax_identifier": "API-17", "contact_name": "甲",
        "contact_phone": "123", "note": "完整备注", "registered_at": "2024-01-15T09:30:00+08:00",
        "is_available": False, "taxpayer_identity_id": 3,
        "service_type_id": 4, "customer_source_id": 5,
        "monthly_bookkeeping": {"2024-01": True, "2024-02": False},
        "monthly_filings": {"2024-01": False, "2024-02": True},
    }, headers={"X-CSRF-Token": "token"})
    assert response.status_code == 201
    assert response.get_json()["id"] == 17
    customer_insert = next((sql, params) for sql, params in statements if "INSERT INTO customers" in sql)
    assert False in customer_insert[1] and all(i in customer_insert[1] for i in (3, 4, 5))
    assert fixed in customer_insert[1]
    assert any("INSERT INTO monthly_bookkeeping" in sql and params == (17, date(2024, 1, 1), True) for sql, params in statements)
    assert any("INSERT INTO monthly_filings" in sql and params == (17, date(2024, 2, 1), True) for sql, params in statements)


def test_customer_create_api_requires_only_name(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def fetchone(self):
            return {"id": 17, "username": "wfg1"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/api/customers", json={"name": "只填名称"}, headers={"X-CSRF-Token": "token"})
    assert response.status_code == 201
    assert response.get_json()["id"] == 17



def test_customer_create_api_accepts_other_files_in_one_multipart_request(tmp_path, monkeypatch):
    from io import BytesIO
    import json

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    inserts = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "INSERT INTO customers" in sql:
                return Result({"id": 23})
            if "INSERT INTO customer_files" in sql:
                inserts.append(sql)
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/api/customers",
        data={
            "payload": json.dumps({"name": "含附件客户"}),
            "other_files": (BytesIO(b"arbitrary bytes"), "contract.exe"),
        },
        headers={"X-CSRF-Token": "token"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    assert any("INSERT INTO customer_files" in sql for sql in inserts)
    assert len([p for p in (tmp_path / "other").rglob("*") if p.is_file()]) == 1
    rejected = client.post(
        "/api/customers",
        data={"payload": json.dumps({"name": "no-image"}),
              "images": (BytesIO(b"old image"), "capture.png")},
        headers={"X-CSRF-Token": "token"},
        content_type="multipart/form-data",
    )
    assert rejected.status_code == 400
    assert len(inserts) == 1



def test_customer_api_cleans_saved_file_if_metadata_insert_fails(tmp_path, monkeypatch):
    from io import BytesIO
    import json
    import pytest

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "INSERT INTO customers" in sql:
                return Result({"id": 99})
            if "INSERT INTO customer_files" in sql:
                raise RuntimeError("metadata write failed")
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    with pytest.raises(RuntimeError, match="metadata write failed"):
        client.post(
            "/api/customers",
            data={
                "payload": json.dumps({"name": "失败客户"}),
                "other_files": (BytesIO(b"test data"), "paper.bin"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": "token"},
        )
    assert not list((tmp_path / "other").rglob("*.*"))
