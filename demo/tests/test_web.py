from contextlib import contextmanager
from datetime import date, datetime, timezone

from tax_manager.web import render_template, session
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
    ("GET", "/customers/7/edit"),
    ("POST", "/customers/new"), ("POST", "/customers/7/edit"),
    ("POST", "/customers/7/filing"), ("POST", "/customers/7/delete"),
])
def test_customer_routes_require_login(tmp_path, method, path):
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
    assert (tmp_path / ".session-key").read_bytes().hex() == first.secret_key


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


def test_customer_page_renders_filter_controls(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {
        "id": 7, "name": "测试客户", "tax_identifier": "T-7", "contact_name": "张三",
        "contact_phone": "", "note": "备注", "is_filed": False, "tax_month": date(2026, 9, 1),
        "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    from tax_manager.customers.search import parse_home_filters
    filters = parse_home_filters({}, date(2026, 9, 29))
    with app.test_request_context("/"):
        session["user_id"] = 1
        session["username"] = "wfg1"
        dashboard = render_template("customers/index.html", customers=[customer], filters=filters, current_month="2026-09", choices_by_kind={"taxpayer_identity": [], "service_type": [], "customer_source": []}).body.decode()
    assert 'name="filing_status"' in dashboard
    assert 'name="bookkeeping_status"' in dashboard
    assert 'href="/customers"' in dashboard
    assert 'href="/unassigned"' not in dashboard


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
        html = render_template("customers/detail.html", customer=customer, files=[], history=[{"month": row["tax_month"], "is_filed": row["is_filed"], "is_booked": False} for row in filings]).body.decode()
    assert 'name="tax_month" value="2026-08"' in html
    assert 'name="tax_month" value="2026-09"' in html
    assert "2026 年 08 月" in html
    assert "2026 年 09 月" in html




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
            if "SELECT registered_on, registered_at, is_available, deactivated_at FROM customers" in statement:
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
            if "SELECT registered_on, registered_at, is_available, deactivated_at FROM customers" in statement:
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
