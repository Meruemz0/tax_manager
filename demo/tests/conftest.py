"""Compatibility adapter for the pre-migration behavioral tests."""

from base64 import b64decode, b64encode
from contextlib import contextmanager
import json

from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import Session
from starlette.requests import Request

from tax_manager.cli import create_cli
from tax_manager.web import RequestAdapter, app_context, request_context


class LegacyTestClient(TestClient):
    def request(self, method, url, **kwargs):
        if kwargs.get("follow_redirects") is None:
            kwargs["follow_redirects"] = False
        kwargs.pop("content_type", None)
        data = kwargs.get("data")
        if isinstance(data, dict):
            files = {}
            ordinary = {}
            for key, value in data.items():
                if isinstance(value, tuple) and len(value) == 2 and hasattr(value[0], "read"):
                    files[key] = (value[1], value[0])
                else:
                    ordinary[key] = value
            if files:
                kwargs["data"] = ordinary
                kwargs["files"] = files
        response = super().request(method, url, **kwargs)
        if response.headers.get("set-cookie", "").startswith("session=null;"):
            self.cookies.delete("session")
        return response

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def open(self, path, *, method="GET", **kwargs):
        return self.request(method, path, **kwargs)

    @contextmanager
    def session_transaction(self):
        signer = TimestampSigner(str(self.app.secret_key))
        cookie = self.cookies.get("session")
        try:
            data = json.loads(b64decode(signer.unsign(cookie.encode("utf-8")))) if cookie else {}
        except Exception:
            data = {}
        yield data
        signed = signer.sign(b64encode(json.dumps(data).encode("utf-8"))).decode("utf-8")
        self.cookies.set("session", signed)


def _test_request_context(self, path="/"):
    @contextmanager
    def context():
        scope = {
            "type": "http", "method": "GET", "path": path,
            "headers": [], "query_string": b"", "scheme": "http",
            "server": ("testserver", 80), "client": ("testclient", 123),
            "app": self, "session": Session(),
        }
        raw = Request(scope)
        with request_context(self, RequestAdapter(raw, {}, None)):
            yield
    return context()


def _test_cli_runner(self):
    class Runner:
        def invoke(self, args, **kwargs):
            return CliRunner().invoke(create_cli(self.app), args, **kwargs)

    runner = Runner()
    runner.app = self
    return runner


def _get_data(self, as_text=False):
    return self.text if as_text else self.content


FastAPI.test_client = lambda self: LegacyTestClient(self)
FastAPI.app_context = lambda self: app_context(self)
FastAPI.test_request_context = _test_request_context
FastAPI.test_cli_runner = _test_cli_runner
Response.get_data = _get_data
Response.get_json = lambda self: self.json()
Response.data = property(lambda self: self.content)


