from io import BytesIO

import pytest

from tax_manager.app import create_app
from tax_manager.customer_files.storage import InvalidOtherFile, read_other_file


def test_other_file_accepts_any_format_up_to_one_megabyte():
    upload = read_other_file(BytesIO(b"MZ" + b"x" * (1024 * 1024 - 2)), "program.exe")
    assert upload.original_name == "program.exe"
    assert upload.size_bytes == 1024 * 1024
    with pytest.raises(InvalidOtherFile):
        read_other_file(BytesIO(b"x" * (1024 * 1024 + 1)), "large.bin")
    with pytest.raises(InvalidOtherFile):
        read_other_file(BytesIO(b""), "empty.bin")


def test_customer_cannot_upload_eleventh_other_file(tmp_path, monkeypatch):
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
            if "FROM customers" in sql:
                return Result({"id": 7})
            if "count(*)" in sql:
                return Result({"count": 10})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr("tax_manager.customer_files.save_other_file", lambda *args: (_ for _ in ()).throw(AssertionError("must not save")))
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/files",
        data={"csrf_token": "token", "file": (BytesIO(b"x"), "note.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7")
