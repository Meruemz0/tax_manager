from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tax_manager.customers.validation import CustomerInput, ValidationError


def test_customer_input_accepts_optional_timestamp_availability_and_single_choice_ids():
    data = CustomerInput.from_form({
        "name": "甲公司", "registered_at": "2026-09-15T14:30",
        "is_available": "0", "taxpayer_identity_id": "3",
        "service_type_id": "4", "customer_source_id": "5",
    })
    assert data.registered_at == datetime(2026, 9, 15, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert data.registered_on == date(2026, 9, 15)
    assert data.is_available is False
    assert (data.taxpayer_identity_id, data.service_type_id, data.customer_source_id) == (3, 4, 5)


def test_customer_input_keeps_name_as_only_required_field():
    data = CustomerInput.from_form({"name": "只填名称"})
    assert data.name == "只填名称"
    assert data.registered_at is None and data.is_available is None
    assert data.taxpayer_identity_id is None


@pytest.mark.parametrize("values", [
    {"name": "甲", "registered_at": "bad"},
    {"name": "甲", "is_available": "sometimes"},
    {"name": "甲", "service_type_id": "-1"},
    {"name": "甲", "customer_source_id": "1 OR 1=1"},
    {"name": 7},
])
def test_customer_input_rejects_invalid_new_fields(values):
    with pytest.raises(ValidationError):
        CustomerInput.from_form(values)


def test_new_customer_defaults_to_available_and_current_china_timestamp(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    fixed = datetime(2026, 9, 29, 13, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("tax_manager.customers.china_now", lambda: fixed)
    inserts = []

    class Result:
        def fetchone(self):
            return {"id": 9}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "INSERT INTO customers" in sql:
                inserts.append((sql, params))
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/customers/new", data={"csrf_token": "token", "name": "只填名称"})
    assert response.status_code == 302
    assert len(inserts) == 1
    assert "registered_at" in inserts[0][0] and "is_available" in inserts[0][0]
    assert fixed in inserts[0][1] and True in inserts[0][1]



def test_customer_edit_saves_availability_and_choice_fields(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    updates = []

    class Result:
        def __init__(self, row=None, rows=None):
            self.row, self.rows = row, rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result(row={"id": 1, "username": "wfg1"})
            if "SELECT id FROM tag_categories" in sql:
                return Result(row={"id": params[0]})
            if "FROM customers WHERE id = %s FOR UPDATE" in sql:
                return Result(row={"registered_on": date(2024, 1, 15)})
            if "UPDATE customers" in sql:
                updates.append((sql, params))
                return Result(row={"id": 7})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/customers/7/edit", data={
        "csrf_token": "token", "name": "甲", "registered_at": "2024-01-15T09:00",
        "is_available": "0", "taxpayer_identity_id": "3",
        "service_type_id": "4", "customer_source_id": "5",
    })
    assert response.status_code == 302
    assert len(updates) == 1
    assert "is_available" in updates[0][0] and "taxpayer_identity_id" in updates[0][0]
    assert False in updates[0][1] and 3 in updates[0][1] and 4 in updates[0][1] and 5 in updates[0][1]
