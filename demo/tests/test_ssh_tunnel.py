"""Windows launcher must reach PostgreSQL through the same SSH hop as Navicat."""

import subprocess

from psycopg.conninfo import conninfo_to_dict

import check_database
import start_windows


def test_windows_launcher_uses_ssh_forwarding_for_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw%40word@192.0.2.10:15432/tax_db")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SSH_HOST", "192.0.2.10")
    monkeypatch.setenv("SSH_PORT", "22")
    monkeypatch.setenv("SSH_USER", "wfg1")
    monkeypatch.setenv("SSH_TARGET_HOST", "127.0.0.1")
    monkeypatch.setenv("SSH_LOCAL_PORT", "15433")

    launched = []

    class FakeProcess:
        stopped = False

        def poll(self):
            return None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout=None):
            return 0

    process = FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda command: launched.append(command) or process)

    attempts = 0

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def connect(address, timeout=None):
        nonlocal attempts
        assert address == ("127.0.0.1", 15433)
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError
        return FakeSocket()

    monkeypatch.setattr("socket.create_connection", connect)
    observed = {}

    class FakeServer:
        def serve_forever(self):
            observed["served"] = True

        def server_close(self):
            observed["closed"] = True

    def fake_make_server(host, port, app, threaded):
        observed["database_url"] = app.config["DATABASE_URL"]
        return FakeServer()

    monkeypatch.setattr(start_windows, "make_server", fake_make_server)
    monkeypatch.setattr(start_windows.webbrowser, "open", lambda url: True)

    assert start_windows.main() == 0
    assert conninfo_to_dict(observed["database_url"]) == {
        "user": "postgres", "password": "pw@word", "host": "127.0.0.1",
        "port": "15433", "dbname": "tax_db",
    }
    assert launched[0][:4] == ["ssh", "-N", "-L", "127.0.0.1:15433:127.0.0.1:15432"]
    assert launched[0][-1] == "wfg1@192.0.2.10"
    assert observed["served"] and observed["closed"] and process.stopped


def test_database_checker_uses_ssh_forwarding_without_printing_password(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:SECRETEXAMPLE@192.0.2.10:15432/tax_db")
    monkeypatch.setenv("SSH_HOST", "192.0.2.10")
    monkeypatch.setenv("SSH_PORT", "22")
    monkeypatch.setenv("SSH_USER", "wfg1")
    monkeypatch.setenv("SSH_TARGET_HOST", "127.0.0.1")
    monkeypatch.setenv("SSH_LOCAL_PORT", "15433")

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    attempts = 0

    def connect(address, timeout=None):
        nonlocal attempts
        assert address == ("127.0.0.1", 15433)
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError
        return FakeSocket()

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement):
            assert "to_regclass" in statement

        def fetchone(self):
            return ("site_users", "login_attempts")

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return FakeCursor()

    connected = []
    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr(subprocess, "Popen", lambda command: FakeProcess())
    monkeypatch.setattr(check_database.psycopg, "connect", lambda url, **kwargs: connected.append(url) or FakeConnection())

    assert check_database.main() == 0
    assert conninfo_to_dict(connected[0])["host"] == "127.0.0.1"
    assert conninfo_to_dict(connected[0])["port"] == "15433"
    assert "SECRETEXAMPLE" not in capsys.readouterr().out
