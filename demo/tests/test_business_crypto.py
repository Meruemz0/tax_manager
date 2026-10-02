"""Behavioral tests for encrypted client credentials and voucher bytes."""

import pytest


def test_data_key_is_stable_and_missing_key_is_not_silently_replaced(tmp_path):
    from tax_manager.security.crypto import load_or_create_data_key

    storage = tmp_path / "files"
    first = load_or_create_data_key(storage)
    assert len(first) == 32
    assert load_or_create_data_key(storage) == first
    (tmp_path / "files.data-key").unlink()
    with pytest.raises(RuntimeError, match="加密密钥"):
        load_or_create_data_key(storage)


def test_credentials_and_vouchers_round_trip_but_tampering_is_rejected(tmp_path):
    from tax_manager.security.crypto import decrypt_bytes, encrypt_bytes, load_or_create_data_key

    key = load_or_create_data_key(tmp_path / "files")
    first = encrypt_bytes(key, b"external-password", b"account:17:password")
    second = encrypt_bytes(key, b"external-password", b"account:17:password")
    assert first != second
    assert b"external-password" not in first
    assert decrypt_bytes(key, first, b"account:17:password") == b"external-password"
    with pytest.raises(ValueError):
        decrypt_bytes(key, first, b"account:18:password")
    with pytest.raises(ValueError):
        decrypt_bytes(key, first[:-1] + bytes([first[-1] ^ 1]), b"account:17:password")


def test_order_amount_and_receipts_are_independent_of_completion():
    from decimal import Decimal
    from tax_manager.orders.validation import OrderInput, ReceiptInput, paid_status

    order = OrderInput.from_mapping({"service_name": "代理记账", "amount": "300.00", "is_completed": "1"})
    receipt = ReceiptInput.from_mapping({"amount": "100.00", "received_on": "2026-09-30"})
    assert order.is_completed is True
    assert paid_status(order.amount, [receipt.amount]) == "部分收款"
    assert paid_status(order.amount, []) == "未收款"
    assert paid_status(Decimal("0"), []) == "无需收款"
    assert paid_status(order.amount, [receipt.amount, receipt.amount, receipt.amount]) == "已收款"
    with pytest.raises(ValueError):
        OrderInput.from_mapping({"service_name": "代理记账", "amount": "-1"})
    with pytest.raises(ValueError):
        OrderInput.from_mapping({"service_name": "代理记账", "amount": "1e1000"})
    with pytest.raises(ValueError):
        ReceiptInput.from_mapping({"amount": "0", "received_on": "2026-09-30"})


def test_voucher_storage_contains_only_ciphertext(tmp_path):
    from tax_manager.orders.storage import read_encrypted_voucher, save_encrypted_voucher
    from tax_manager.security.crypto import load_or_create_data_key

    key = load_or_create_data_key(tmp_path / "files")
    storage_key = save_encrypted_voucher(tmp_path / "files", key, 12, b"invoice-original")
    disk = tmp_path / "files" / "order_vouchers" / storage_key[:2] / storage_key
    assert b"invoice-original" not in disk.read_bytes()
    assert read_encrypted_voucher(tmp_path / "files", key, 12, storage_key) == b"invoice-original"
    with pytest.raises(ValueError):
        read_encrypted_voucher(tmp_path / "files", key, 13, storage_key)


def test_copy_account_requires_login_and_returns_only_requested_secret(tmp_path, monkeypatch):
    from tax_manager.app import create_app
    from tax_manager.security.crypto import encrypt_bytes, load_or_create_data_key

    key = load_or_create_data_key(tmp_path)
    token = "ab" * 16
    row = {
        "aad_token": token,
        "account_ciphertext": encrypt_bytes(key, b"login-name", f"customer:7:account:{token}:name".encode()),
        "password_ciphertext": encrypt_bytes(key, b"login-secret", f"customer:7:account:{token}:password".encode()),
    }

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM customer_system_accounts" in sql:
                return Result(row if params == (5, 7) else None)
            return Result(None)

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    assert client.post("/api/customers/7/accounts/5/copy", json={"field": "password"}).status_code == 400
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/api/customers/7/accounts/5/copy", json={"field": "password"}, headers={"X-CSRF-Token": "token"})
    assert response.status_code == 200
    assert response.json() == {"value": "login-secret"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.post("/api/customers/8/accounts/5/copy", json={"field": "password"}, headers={"X-CSRF-Token": "token"}).status_code == 404



def test_bookkeeping_start_month_is_optional_but_must_be_a_month():
    from datetime import date
    from tax_manager.customers.validation import CustomerInput, ValidationError

    assert CustomerInput.from_form({"name": "A"}).bookkeeping_start_month is None
    assert CustomerInput.from_form({"name": "A", "bookkeeping_start_month": "2026-09"}).bookkeeping_start_month == date(2026, 9, 1)
    with pytest.raises(ValueError):
        CustomerInput.from_form({"name": "A", "bookkeeping_start_month": "2026-13"})
    with pytest.raises(ValidationError):
        CustomerInput.from_form({"name": "A", "bookkeeping_start_month": "0000-01"})


def test_new_account_is_encrypted_and_tenth_is_last_allowed(tmp_path):
    from tax_manager.app import create_app
    from tax_manager.customer_accounts import add_account, aad
    from tax_manager.security.crypto import decrypt_bytes

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        count = 9
        inserted = None

        def execute(self, sql, params=None):
            if "FROM customers" in sql:
                return Result({"id": 7})
            if "count(*)" in sql:
                return Result({"n": self.count})
            if "INSERT INTO customer_system_accounts" in sql:
                self.inserted = params
                return Result({"id": 22})
            raise AssertionError(sql)

    conn = Connection()
    with app.app_context():
        assert add_account(conn, 7, {"system_name": "Tax", "account_name": "test@example.com", "password": "secret"}) == 22
        token = conn.inserted[1]
        assert b"secret" not in conn.inserted[5]
        assert b"test@example.com" not in conn.inserted[4]
        assert decrypt_bytes(app.config["DATA_KEY"], conn.inserted[5], aad(7, token, "password")) == b"secret"
        conn.count = 10
        with pytest.raises(ValueError, match="10"):
            add_account(conn, 7, {"system_name": "Tax", "account_name": "x", "password": "y"})



def test_customer_api_creates_nested_accounts_and_service_orders(tmp_path, monkeypatch):
    from decimal import Decimal
    from tax_manager.app import create_app

    seen = []

    class Result:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "INSERT INTO customers" in sql:
                return Result({"id": 7})
            if "FROM customers" in sql:
                return Result({"id": 7})
            if "count(*) AS n FROM customer_system_accounts" in sql:
                return Result({"n": 0})
            if "INSERT INTO customer_system_accounts" in sql:
                seen.append(("account", params))
                return Result({"id": 4})
            if "INSERT INTO service_orders" in sql:
                seen.append(("order", params))
                return Result({"id": 8})
            if "FROM service_orders" in sql:
                return Result({"id": 8, "amount": Decimal("300")})
            if "COALESCE(SUM(amount)" in sql:
                return Result({"total": Decimal("0")})
            if "INSERT INTO order_receipts" in sql:
                seen.append(("receipt", params))
                return Result({"id": 2})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/api/customers", json={
        "name": "Example", "bookkeeping_start_month": "2026-09",
        "system_accounts": [{"system_name": "Tax", "account_name": "test", "password": "secret"}],
        "orders": [{"service_name": "代理记账", "amount": "300", "receipts": [{"amount": "100"}]}],
    }, headers={"X-CSRF-Token": "token"})
    assert response.status_code == 201
    assert [kind for kind, _ in seen] == ["account", "order", "receipt"]
    assert b"secret" not in seen[0][1][5]



def test_service_order_accepts_browser_month_inputs():
    from datetime import date
    from tax_manager.orders.validation import OrderInput

    order = OrderInput.from_mapping({
        "service_name": "Bookkeeping", "amount": "1200",
        "service_start_month": "2026-09", "service_end_month": "2026-12",
    })
    assert order.service_start_month == date(2026, 9, 1)
    assert order.service_end_month == date(2026, 12, 1)



def test_customer_detail_exposes_account_copy_and_order_management_without_plaintext(tmp_path):
    from decimal import Decimal
    from tax_manager.app import create_app
    from tax_manager.web import render_template

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {"id": 7, "name": "Example", "is_available": True, "bookkeeping_start_month": None}
    accounts = [{"id": 4, "system_name": "Tax", "login_url": None, "note": None}]
    orders = [{"id": 8, "service_name": "Bookkeeping", "amount": Decimal("300"),
               "paid_total": Decimal("100"), "payment_status": "部分收款",
               "is_completed": False, "order_date": "2026-09-30", "document_count": 2}]
    with app.test_request_context("/customers/7"):
        html = render_template("customers/detail.html", customer=customer, history=[], files=[],
                               assigned_categories=[], available_categories=[],
                               accounts=accounts, orders=orders).body.decode()
    assert "复制账号" in html
    assert "显示账号" in html
    assert "复制密码" in html
    assert "Bookkeeping" in html
    assert "部分收款" in html
    assert "/customers/7/orders/8" in html
    assert "login-secret" not in html



def test_order_detail_shows_receipts_and_multiple_documents(tmp_path):
    from datetime import date
    from decimal import Decimal
    from tax_manager.app import create_app
    from tax_manager.web import render_template

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {"id": 7, "name": "Example"}
    order = {"id": 8, "service_name": "Bookkeeping", "order_date": date(2026, 9, 30),
             "amount": Decimal("300"), "service_start_month": None, "service_end_month": None,
             "due_date": None, "contract_number": None, "note": None, "is_completed": False}
    receipts = [{"id": 2, "amount": Decimal("100"), "received_on": date(2026, 9, 30), "method": None, "note": None}]
    documents = [{"id": 3, "original_name": "a.pdf", "size_bytes": 10, "document_type": "voucher"},
                 {"id": 4, "original_name": "b.pdf", "size_bytes": 20, "document_type": "voucher"}]
    with app.test_request_context("/customers/7/orders/8"):
        html = render_template("orders/detail.html", customer=customer, order=order,
                               receipts=receipts, documents=documents,
                               document_types={"voucher": "原始凭证"},
                               paid_total=Decimal("100"), remaining=Decimal("200"),
                               payment_status="部分收款").body.decode()
    assert "新增收款" in html
    assert "a.pdf" in html and "b.pdf" in html
    assert "未完成" in html and "部分收款" in html



def test_order_overview_filters_across_customers_by_payment(tmp_path, monkeypatch):
    from datetime import date
    from decimal import Decimal
    from tax_manager.app import create_app

    rows = [
        {"id": 8, "customer_id": 7, "customer_name": "Alpha", "service_name": "Bookkeeping A",
         "order_date": date(2026, 9, 30), "amount": Decimal("300"), "paid_total": Decimal("100"),
         "is_completed": True, "due_date": None},
        {"id": 9, "customer_id": 8, "customer_name": "Beta", "service_name": "Bookkeeping B",
         "order_date": date(2026, 9, 29), "amount": Decimal("400"), "paid_total": Decimal("0"),
         "is_completed": False, "due_date": None},
    ]

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

        def fetchall(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM service_orders AS o" in sql:
                return Result(rows)
            return Result(None)

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.get("/orders?payment=partial")
    assert response.status_code == 200
    assert "Bookkeeping A" in response.text
    assert "Bookkeeping B" not in response.text
    assert "Alpha" in response.text



def test_uploaded_voucher_bytes_do_not_roll_to_plaintext_tempfile(tmp_path):
    from fastapi import Request
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    @app.post("/_inspect_upload_spool")
    async def inspect_upload_spool(raw: Request):
        form = await raw.form()
        return {"rolled": form["file"].file._rolled}

    response = app.test_client().post(
        "/_inspect_upload_spool", files={"file": ("large.pdf", b"x" * (2 * 1024 * 1024))}
    )
    assert response.status_code == 200
    assert response.json() == {"rolled": False}



def test_order_voucher_upload_and_download_use_encrypted_storage(tmp_path, monkeypatch):
    from decimal import Decimal
    from tax_manager.app import create_app

    saved = {}

    class Result:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM service_orders" in sql:
                return Result({"id": 8, "customer_id": 7, "amount": Decimal("100")})
            if "INSERT INTO order_vouchers" in sql:
                saved.update(storage_key=params[1], name_ciphertext=params[2])
                return Result()
            if "FROM order_vouchers" in sql:
                return Result(saved if params == (3, 8) else None)
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post("/customers/7/orders/8/vouchers", data={"csrf_token": "token"},
                           headers={"X-CSRF-Token": "token"}, files={"file": ("original.pdf", b"raw-voucher")})
    assert response.status_code == 302
    disk = tmp_path / "order_vouchers" / saved["storage_key"][:2] / saved["storage_key"]
    assert b"raw-voucher" not in disk.read_bytes()
    downloaded = client.get("/customers/7/orders/8/vouchers/3/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"raw-voucher"
    assert downloaded.headers["Cache-Control"] == "private, no-store"



def test_failed_voucher_disk_write_leaves_no_file(tmp_path, monkeypatch):
    from tax_manager.orders.storage import save_encrypted_voucher
    from tax_manager.security.crypto import load_or_create_data_key

    key = load_or_create_data_key(tmp_path / "files")
    def fail_fsync(_):
        raise OSError("simulated disk failure")
    monkeypatch.setattr("tax_manager.orders.storage.os.fsync", fail_fsync)
    with pytest.raises(OSError):
        save_encrypted_voucher(tmp_path / "files", key, 8, b"raw-voucher")
    assert not any(p.is_file() for p in (tmp_path / "files" / "order_vouchers").rglob("*"))



def test_anonymous_or_invalid_csrf_multipart_is_rejected_before_parsing(tmp_path, monkeypatch):
    from starlette.formparsers import MultiPartParser
    from tax_manager.app import create_app

    parsed = []
    real_parse = MultiPartParser.parse
    async def record_parse(self):
        parsed.append(True)
        return await real_parse(self)
    monkeypatch.setattr(MultiPartParser, "parse", record_parse)

    class Result:
        def fetchone(self):
            return {"id": 1, "username": "wfg1"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    route = "/customers/7/orders/8/vouchers"
    anonymous = client.post(route, files={"file": ("a.pdf", b"x" * (2 * 1024 * 1024))})
    assert anonymous.status_code == 302
    assert parsed == []
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    invalid = client.post(route, files={"file": ("a.pdf", b"x" * (2 * 1024 * 1024))},
                          data={"csrf_token": "wrong"})
    assert invalid.status_code == 400
    assert parsed == []


def test_database_key_verifier_initializes_then_rejects_wrong_key():
    from tax_manager.security.key_verifier import verify_or_initialize_data_key

    state = {"ciphertext": None, "has_encrypted_data": False}

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, params=None):
            if "pg_advisory_xact_lock" in sql:
                return Result()
            if "SELECT challenge_ciphertext" in sql:
                return Result({"challenge_ciphertext": state["ciphertext"]} if state["ciphertext"] else None)
            if "EXISTS(SELECT 1 FROM customer_system_accounts)" in sql:
                return Result({"has_encrypted_data": state["has_encrypted_data"]})
            if "INSERT INTO data_key_verification" in sql:
                state["ciphertext"] = params[0]
                return Result()
            raise AssertionError(sql)

    conn = Connection()
    verify_or_initialize_data_key(conn, b"a" * 32)
    assert state["ciphertext"] != b"tax-manager-data-key-v1"
    verify_or_initialize_data_key(conn, b"a" * 32)
    with pytest.raises(RuntimeError, match="密钥"):
        verify_or_initialize_data_key(conn, b"b" * 32)
    state["ciphertext"] = None
    state["has_encrypted_data"] = True
    with pytest.raises(RuntimeError, match="密钥"):
        verify_or_initialize_data_key(conn, b"b" * 32)
