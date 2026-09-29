from contextlib import contextmanager
from datetime import date, datetime, timezone
from io import BytesIO

from flask import render_template, session
from PIL import Image
import pytest

from tax_manager.app import create_app


def test_public_home_is_reachable_without_database_access(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    monkeypatch.setattr("tax_manager.customers.connect", lambda: (_ for _ in ()).throw(AssertionError("no customer query")))
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert "登录" in response.get_data(as_text=True)
    assert "客户列表" not in response.get_data(as_text=True)


@pytest.mark.parametrize("method,path", [
    ("GET", "/customers"), ("GET", "/customers/new"), ("GET", "/customers/7"),
    ("GET", "/customers/7/edit"), ("GET", "/unassigned"),
    ("GET", "/images/customer/3/view"), ("GET", "/images/unassigned/3/download"),
    ("POST", "/customers/new"), ("POST", "/customers/7/edit"),
    ("POST", "/customers/7/filing"), ("POST", "/customers/7/delete"),
    ("POST", "/customers/7/images"), ("POST", "/unassigned/images"),
    ("POST", "/images/customer/3/rename"), ("POST", "/images/customer/3/replace"),
    ("POST", "/images/customer/3/delete"),
])
def test_customer_and_image_routes_require_login(tmp_path, method, path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as state:
        state["csrf_token"] = "token"
    response = client.open(path, method=method, data={"csrf_token": "token"} if method == "POST" else None)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_inactive_site_user_cannot_access_customer_data(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    statements = []

    class Result:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, statement, params=None):
            statements.append(statement)
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.customers.connect", lambda: (_ for _ in ()).throw(AssertionError("no customer query")))
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/customers")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert any("FROM site_users" in sql and "is_active = TRUE" in sql for sql in statements)
    with client.session_transaction() as state:
        assert "user_id" not in state


def test_session_key_is_created_and_reused(tmp_path):
    config = {"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path}
    first = create_app(config)
    second = create_app(config)
    assert first.secret_key == second.secret_key
    assert len(first.secret_key) >= 32
    assert (tmp_path / ".session-key").read_bytes() == first.secret_key


def test_login_post_requires_csrf_token(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    page = client.get("/login")
    assert page.status_code == 200
    assert "账号登录" in page.get_data(as_text=True)
    response = client.post("/login", data={"username": "wfg1", "password": "any-password"})
    assert response.status_code == 400


def test_login_reads_site_users_table(tmp_path, monkeypatch):
    from tax_manager.auth.passwords import hash_password

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    statements = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            statements.append((statement, params))
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1", "password_hash": hash_password("example-only-password")})
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    client = app.test_client()
    with client.session_transaction() as state:
        state["csrf_token"] = "token"
    response = client.post("/login", data={"csrf_token": "token", "username": "wfg1", "password": "example-only-password"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers")
    assert any("FROM site_users" in sql and params == ("wfg1",) for sql, params in statements)


def test_customer_and_image_pages_render_edit_controls(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {
        "id": 7, "name": "测试客户", "tax_identifier": "T-7", "contact_name": "张三",
        "contact_phone": "", "note": "备注", "is_filed": False, "tax_month": date(2026, 9, 1),
        "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    from tax_manager.customers.search import parse_home_filters
    filters = parse_home_filters({}, date(2026, 9, 29))
    image = {"id": 3, "original_name": "截图.png", "mime_type": "image/png", "size_bytes": 1024}
    with app.test_request_context("/"):
        session["user_id"] = 1
        session["username"] = "wfg1"
        dashboard = render_template("customers/index.html", customers=[customer], filters=filters, current_month="2026-09", choices_by_kind={"taxpayer_identity": [], "service_type": [], "customer_source": []})
        detail = render_template("customers/detail.html", customer=customer, images=[image])
        gallery = render_template("images/unassigned.html", images=[image])
    assert 'name="filing_status"' in dashboard
    assert 'name="bookkeeping_status"' in dashboard
    assert 'href="/customers"' in dashboard
    assert 'href="/unassigned"' in dashboard
    assert '/customers/7/edit' in detail
    assert '/images/customer/3/replace' in detail
    assert '/images/unassigned/3/delete' in gallery


def test_customer_detail_renders_editable_history_for_each_month(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {
        "id": 7, "name": "测试客户", "tax_identifier": None, "contact_name": None,
        "contact_phone": None, "note": None, "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "registered_on": date(2026, 8, 1), "tax_month": date(2026, 9, 1), "is_filed": False,
    }
    filings = [
        {"tax_month": date(2026, 9, 1), "is_filed": False},
        {"tax_month": date(2026, 8, 1), "is_filed": True},
    ]
    with app.test_request_context("/customers/7"):
        session["user_id"] = 1
        html = render_template("customers/detail.html", customer=customer, images=[], files=[], history=[{"month": row["tax_month"], "is_filed": row["is_filed"], "is_booked": False} for row in filings])
    assert 'name="tax_month" value="2026-08"' in html
    assert 'name="tax_month" value="2026-09"' in html
    assert "2026 年 08 月" in html
    assert "2026 年 09 月" in html


def test_upload_saves_file_and_metadata(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    writes = []

    class FakeResult:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConnection:
        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return FakeResult({"id": 1, "username": "wfg1"})
            if "FROM customers" in statement:
                return FakeResult({"id": 7})
            if "INSERT INTO customer_images" in statement:
                writes.append(params)
            return FakeResult()

    @contextmanager
    def fake_connect():
        yield FakeConnection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    picture = BytesIO()
    Image.new("RGB", (2, 2), "white").save(picture, format="PNG")
    picture.seek(0)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/images",
        data={"csrf_token": "token", "image": (picture, "capture.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7")
    assert len(writes) == 1
    assert writes[0][0] == 7
    assert writes[0][2] == "capture.png"
    assert writes[0][3] == "image/png"
    stored = list((tmp_path / "customer").rglob("*"))
    assert any(path.is_file() and path.stat().st_size == writes[0][4] for path in stored)


def test_customer_create_and_filing_update_use_form_values(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    statements = []

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            statements.append((statement, params))
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "INSERT INTO customers" in statement or "FROM customers" in statement:
                return Result({"id": 7, "registered_on": date(2024, 1, 15)})
            return Result(None)

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.customers.connect", fake_connect)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    created = client.post("/customers/new", data={"csrf_token": "token", "name": "  新客户  ", "tax_identifier": "  T-1  ", "registered_on": "2024-01-15"})
    changed = client.post("/customers/7/filing", data={"csrf_token": "token", "is_filed": "1"})
    assert created.status_code == 302 and created.headers["Location"].endswith("/customers/7")
    assert changed.status_code == 302
    assert next(params for sql, params in statements if "INSERT INTO customers" in sql)[:2] == ("新客户", "T-1")
    assert date(2024, 1, 15) in next(params for sql, params in statements if "INSERT INTO customers" in sql)
    assert next(params for sql, params in statements if "INSERT INTO monthly_filings" in sql and "DO UPDATE" in sql)[0] == 7


def test_filing_update_targets_selected_historical_month(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    writes = []

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

        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM customers" in statement:
                return Result({"id": 7, "registered_on": date(2024, 1, 15)})
            if "INSERT INTO monthly_filings" in statement:
                writes.append(params)
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/filing",
        data={"csrf_token": "token", "tax_month": "2024-01", "is_filed": "1", "return_to": "detail"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7")
    assert writes == [(7, date(2024, 1, 1), True)]
    invalid = client.post(
        "/customers/7/filing",
        data={"csrf_token": "token", "tax_month": "2023-12", "is_filed": "1"},
    )
    assert invalid.status_code == 400
    assert len(writes) == 1


def test_customer_detail_backfills_and_lists_each_month(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    statements = []
    customer = {
        "id": 7, "name": "测试客户", "tax_identifier": None, "contact_name": None,
        "contact_phone": None, "note": None, "registered_on": date(2024, 1, 15),
        "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
    }

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

        def execute(self, statement, params=None):
            statements.append(statement)
            if "FROM site_users" in statement:
                return Result(row={"id": 1, "username": "wfg1"})
            if "FROM customers AS c" in statement:
                return Result(row=customer)
            if "FROM monthly_filings" in statement:
                return Result(rows=[{"tax_month": date(2024, 1, 1), "is_filed": True}])
            return Result(rows=[])

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/customers/7")
    assert response.status_code == 200
    assert "2024 年 01 月" in response.get_data(as_text=True)
    assert any("generate_series" in sql and "ON CONFLICT" in sql for sql in statements)


def test_edit_registration_date_backfills_earlier_months(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
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

        def execute(self, statement, params=None):
            statements.append((statement, params))
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "SELECT registered_on, registered_at, is_available FROM customers" in statement:
                return Result({"registered_on": date(2024, 3, 10)})
            if "UPDATE customers" in statement:
                return Result({"id": 7})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/edit",
        data={"csrf_token": "token", "name": "测试客户", "registered_on": "2024-01-15"},
    )
    assert response.status_code == 302
    update_params = next(params for sql, params in statements if "UPDATE customers" in sql)
    assert date(2024, 1, 15) in update_params
    assert any("generate_series" in sql for sql, _ in statements)


def test_edit_cannot_erase_earlier_filed_month(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    updated = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "SELECT registered_on, registered_at, is_available FROM customers" in statement:
                return Result({"registered_on": date(2024, 1, 15)})
            if "SELECT 1 FROM monthly_filings" in statement:
                return Result({"exists": 1})
            if "UPDATE customers" in statement:
                updated.append(params)
                return Result({"id": 7})
            if "SELECT * FROM customers" in statement:
                return Result({"id": 7, "registered_on": date(2024, 1, 15)})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/edit",
        data={"csrf_token": "token", "name": "测试客户", "registered_on": "2024-03-10"},
    )
    assert response.status_code == 200
    assert not updated
    assert "已报税" in response.get_data(as_text=True)


def test_failed_image_metadata_write_removes_new_file(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def fetchone(self):
            return {"id": 7, "username": "wfg1"}

    class Connection:
        def execute(self, statement, params=None):
            if "INSERT INTO customer_images" in statement:
                raise RuntimeError("simulated database failure")
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    picture = BytesIO()
    Image.new("RGB", (2, 2), "white").save(picture, format="PNG")
    picture.seek(0)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    with pytest.raises(RuntimeError, match="simulated database failure"):
        client.post("/customers/7/images", data={"csrf_token": "token", "image": (picture, "capture.png")}, content_type="multipart/form-data")
    assert not list((tmp_path / "customer").rglob("*")) or not any(path.is_file() for path in (tmp_path / "customer").rglob("*"))


def test_rate_limit_rejects_login_before_password_hashing(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            if "login_attempts" in statement:
                return Result({"attempt_count": 21})
            return Result({"id": 1, "username": "wfg1", "password_hash": "unused"})

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.auth.verify_password", lambda *args: (_ for _ in ()).throw(AssertionError("hash should not run")))
    client = app.test_client()
    with client.session_transaction() as state:
        state["csrf_token"] = "token"
    response = client.post("/login", data={"csrf_token": "token", "username": "wfg1", "password": "any-password"})
    assert response.status_code == 429


def test_rename_does_not_claim_success_after_concurrent_delete(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "SELECT id, storage_key" in statement:
                return Result({"id": 3, "storage_key": "a" * 32, "original_name": "old.png", "mime_type": "image/png", "size_bytes": 10, "customer_id": None})
            return Result(None)

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/images/unassigned/3/rename", data={"csrf_token": "token", "original_name": "new.png"})
    assert response.status_code == 404


def test_password_reset_command_updates_existing_user(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    updates = []

    class Result:
        def fetchone(self):
            return {"id": 1}

    class Connection:
        def execute(self, statement, params=None):
            if "UPDATE site_users" in statement:
                updates.append(params)
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    result = app.test_cli_runner().invoke(args=["auth", "set-password", "wfg1"], input="new-test-password\nnew-test-password\n")
    assert result.exit_code == 0, result.output
    assert len(updates) == 1
    assert updates[0][1] == 1
    from tax_manager.auth.passwords import verify_password
    assert verify_password("new-test-password", updates[0][0])


def test_image_check_command_reports_orphan_file(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def fetchall(self):
            return []

    class Connection:
        def execute(self, statement, params=None):
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    from tax_manager.images.storage import file_path
    with app.app_context():
        orphan = file_path("unassigned", "a" * 32)
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")
    result = app.test_cli_runner().invoke(args=["images", "check-files"])
    assert result.exit_code != 0
    assert "孤立文件" in result.output


def test_customer_delete_removes_month_records_and_image_file(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    key = "b" * 32
    from tax_manager.images.storage import file_path
    with app.app_context():
        path = file_path("customer", key)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old image")
    from tax_manager.customer_files.storage import file_path as other_file_path
    other_key = "c" * 32
    with app.app_context():
        other_path = other_file_path(other_key)
        other_path.parent.mkdir(parents=True)
        other_path.write_bytes(b"old attachment")
    statements = []

    class Result:
        def __init__(self, row=None, rows=None):
            self.row, self.rows = row, rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, statement, params=None):
            statements.append(statement)
            if "FROM site_users" in statement or "FROM customers" in statement:
                return Result(row={"id": 7, "username": "wfg1"})
            if "SELECT storage_key FROM customer_images" in statement:
                return Result(rows=[{"storage_key": key}])
            if "SELECT storage_key FROM customer_files" in statement:
                return Result(rows=[{"storage_key": other_key}])
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.customers.connect", fake_connect)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/customers/7/delete", data={"csrf_token": "token"})
    assert response.status_code == 302
    assert not path.exists()
    assert not other_path.exists()
    assert any("DELETE FROM monthly_bookkeeping" in sql for sql in statements)
    assert any("DELETE FROM customer_files" in sql for sql in statements)
    assert any("DELETE FROM monthly_filings" in sql for sql in statements)
    assert any("DELETE FROM customers" in sql for sql in statements)


def test_replace_removes_locked_previous_file(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    from tax_manager.images.storage import file_path
    old_key, current_key = "a" * 32, "b" * 32
    with app.app_context():
        previous = file_path("unassigned", current_key)
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b"previous")
    saved = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM unassigned_images" in statement:
                key = current_key if "FOR UPDATE" in statement else old_key
                return Result({"id": 3, "storage_key": key, "original_name": "old.png", "mime_type": "image/png", "size_bytes": 8, "customer_id": None})
            if "UPDATE unassigned_images" in statement:
                saved.append(params)
                return Result({"id": 3})
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    picture = BytesIO()
    Image.new("RGB", (2, 2), "white").save(picture, format="PNG")
    picture.seek(0)
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/images/unassigned/3/replace", data={"csrf_token": "token", "image": (picture, "new.png")}, content_type="multipart/form-data")
    assert response.status_code == 302
    assert saved and not previous.exists()
    with app.app_context():
        assert file_path("unassigned", saved[0][0]).is_file()


def test_image_download_requires_login_and_serves_private_file(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    from tax_manager.images.storage import file_path
    key = "c" * 32
    with app.app_context():
        path = file_path("unassigned", key)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"image bytes")
    client = app.test_client()
    url = "/images/unassigned/3/download"
    assert client.get(url).headers["Location"].endswith("/login")
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            return Result({"id": 3, "storage_key": key, "original_name": "capture.png", "mime_type": "image/png", "size_bytes": 11, "customer_id": None})

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr("tax_manager.auth.connect", fake_connect)
    monkeypatch.setattr("tax_manager.images.connect", fake_connect)
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get(url)
    assert response.status_code == 200
    assert response.data == b"image bytes"
    assert response.headers["Cache-Control"] == "private, no-store"
