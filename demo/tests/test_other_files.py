from io import BytesIO

import pytest

from tax_manager.app import create_app
from tax_manager.customer_files.storage import InvalidOtherFile, read_other_file


def test_other_file_accepts_any_format_up_to_twenty_megabytes():
    size = 20 * 1024 * 1024
    upload = read_other_file(BytesIO(b"MZ" + b"x" * (size - 2)), "program.exe")
    assert upload.original_name == "program.exe"
    assert upload.size_bytes == size
    with pytest.raises(InvalidOtherFile):
        read_other_file(BytesIO(b"x" * (size + 1)), "large.bin")
    with pytest.raises(InvalidOtherFile):
        read_other_file(BytesIO(b""), "empty.bin")


def test_customer_can_upload_multiple_files_without_a_count_limit(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    inserted = []

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
            if "FROM customers" in sql:
                return Result({"id": 7})
            if "count(*)" in sql:
                return Result({"count": 100})
            if "INSERT INTO customer_files" in sql:
                inserted.append(params)
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/files",
        data={"csrf_token": "token"},
        headers={"X-CSRF-Token": "token"},
        files=[("file", ("first.any", b"x" * (20 * 1024 * 1024), "application/octet-stream")),
               ("file", ("second.zzz", b"two", "application/octet-stream"))],
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7?files=open#customer-files")
    assert [params[2] for params in inserted] == ["first.any", "second.zzz"]
    assert [params[3] for params in inserted] == [20 * 1024 * 1024, 3]
    assert len([p for p in (tmp_path / "other").rglob("*") if p.is_file()]) == 2


def test_file_area_stays_collapsed_by_default(tmp_path):
    import re
    from tax_manager.web import render_template

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {"id": 7, "name": "client", "is_available": True}
    context = {"customer": customer, "files": [], "history": [],
               "assigned_categories": [], "available_categories": []}
    with app.test_request_context("/customers/7"):
        html = render_template("customers/detail.html", **context).body.decode()
    summary = re.search(r'<details[^>]*id="customer-files"[^>]*>', html)
    assert summary is not None
    assert "open" not in summary.group()
    assert re.search(r'<input[^>]*type="file"[^>]*multiple', html)
    with app.test_request_context("/customers/7?files=open"):
        expanded = render_template("customers/detail.html", files_open=True, **context).body.decode()
    assert re.search(r'<details[^>]*id="customer-files"[^>]*open', expanded)
