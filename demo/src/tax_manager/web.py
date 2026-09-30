"""Small request helpers around FastAPI, preserving the existing page contracts."""

from contextlib import contextmanager
from contextvars import ContextVar
import hmac
import re
import secrets
from urllib.parse import urlencode

import click
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool


_app_var = ContextVar("tax_manager_app")
_request_var = ContextVar("tax_manager_request")
_FORM_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@contextmanager
def app_context(app):
    token = _app_var.set(app)
    try:
        yield
    finally:
        _app_var.reset(token)


@contextmanager
def request_context(app, request_adapter):
    app_token = _app_var.set(app)
    request_token = _request_var.set(request_adapter)
    try:
        yield
    finally:
        _request_var.reset(request_token)
        _app_var.reset(app_token)


class _ContextProxy:
    def __init__(self, var):
        object.__setattr__(self, "_var", var)

    def __getattr__(self, name):
        return getattr(self._var.get(), name)

    def __setattr__(self, name, value):
        setattr(self._var.get(), name, value)


class _SessionProxy:
    @property
    def data(self):
        return _request_var.get().raw.session

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def clear(self):
        self.data.clear()

    @property
    def permanent(self):
        return True

    @permanent.setter
    def permanent(self, value):
        # SessionMiddleware's max_age applies consistently to all sessions.
        pass


class _Files:
    def __init__(self, form):
        self.form = form

    def get(self, name):
        values = self.getlist(name)
        return values[0] if values else None

    def getlist(self, name):
        result = []
        for value in self.form.getlist(name) if hasattr(self.form, "getlist") else []:
            if hasattr(value, "filename") and hasattr(value, "file"):
                result.append(_UploadedFile(value))
        return result


class _UploadedFile:
    def __init__(self, value):
        self.filename = value.filename
        self.stream = value.file


class RequestAdapter:
    def __init__(self, raw: Request, form, json_data):
        self.raw = raw
        self.method = raw.method
        self.form = form
        self.files = _Files(form)
        self.args = raw.query_params
        self.headers = raw.headers
        self.mimetype = raw.headers.get("content-type", "").split(";", 1)[0].lower()
        self.remote_addr = raw.client.host if raw.client else None
        self._json_data = json_data

    @property
    def is_json(self):
        return self.mimetype == "application/json" or self.mimetype.endswith("+json")

    def get_json(self, silent=False):
        return self._json_data


current_app = _ContextProxy(_app_var)
request = _ContextProxy(_request_var)
session = _SessionProxy()


def abort(status_code: int, description: str | None = None):
    raise HTTPException(status_code=status_code, detail=description)


def flash(message: str, category: str = "message"):
    session["_flashes"] = session.get("_flashes", []) + [(category, message)]


def get_flashed_messages(*, with_categories=False):
    messages = session.data.pop("_flashes", [])
    return messages if with_categories else [message for _, message in messages]


def url_for(endpoint: str, **values):
    app = _app_var.get()
    if endpoint == "static":
        return str(app.url_path_for("static", path=values["filename"]))
    path_params = set(app.state.route_params[endpoint])
    path = str(app.url_path_for(endpoint, **{
        key: value for key, value in values.items() if key in path_params
    }))
    query = {key: value for key, value in values.items() if key not in path_params}
    return path + ("?" + urlencode(query) if query else "")


def redirect(location: str):
    return RedirectResponse(location, status_code=302)


def render_template(name: str, **context):
    raw = _request_var.get().raw
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return _app_var.get().state.templates.TemplateResponse(
        request=raw,
        name=name,
        context={
            "csrf_token": token,
            "session": session,
            "url_for": url_for,
            "get_flashed_messages": get_flashed_messages,
            **context,
        },
    )


def jsonify(value):
    return JSONResponse(jsonable_encoder(value))


def send_file(path, *, mimetype, as_attachment=False, download_name=None):
    return FileResponse(
        path,
        media_type=mimetype,
        filename=download_name,
        content_disposition_type="attachment" if as_attachment else "inline",
    )


def _response(result):
    if isinstance(result, tuple):
        result, status_code = result
        result.status_code = status_code
    if isinstance(result, Response):
        return result
    raise TypeError(f"Route returned an unsupported response: {type(result).__name__}")


class RequestTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Enforce the total request size even without a Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _FORM_METHODS:
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers", [])).get(b"content-length")
        try:
            declared = int(header) if header is not None else None
        except ValueError:
            declared = None
        if declared is not None and declared > self.max_bytes:
            await Response("上传内容超过 64 MB 限制", status_code=413)(scope, receive, send)
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLarge:
            await Response("上传内容超过 64 MB 限制", status_code=413)(scope, receive, send)


class Blueprint:
    """FastAPI router for the existing server-rendered routes."""

    def __init__(self, name: str, import_name: str):
        self.name = name
        self.router = APIRouter()
        self.cli = click.Group(name=name)

    def route(self, path, *, methods=("GET",)):
        converted = re.sub(r"<int:(\w+)>", r"{\1:int}", path)
        converted = re.sub(r"<(\w+)>", r"{\1}", converted)

        def register(view):
            endpoint_name = f"{self.name}.{view.__name__}" if self.name else view.__name__
            path_params = re.findall(r"{(\w+)(?::\w+)?}", converted)

            async def endpoint(raw: Request):
                app = raw.app
                content_type = raw.headers.get("content-type", "").split(";", 1)[0].lower()
                form = await raw.form() if content_type in {
                    "application/x-www-form-urlencoded", "multipart/form-data",
                } else {}
                json_data = None
                if content_type == "application/json" or content_type.endswith("+json"):
                    try:
                        json_data = await raw.json()
                    except ValueError:
                        pass
                adapter = RequestAdapter(raw, form, json_data)
                with request_context(app, adapter):
                    if raw.method in _FORM_METHODS:
                        expected = session.get("csrf_token")
                        supplied = form.get("csrf_token") or raw.headers.get("X-CSRF-Token", "")
                        if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
                            return Response("表单已过期，请刷新页面后重试", status_code=400)
                    result = await run_in_threadpool(view, **raw.path_params)
                    return _response(result)

            self.router.add_api_route(
                converted, endpoint, methods=list(methods), name=endpoint_name,
                include_in_schema=path.startswith("/api/"),
            )
            self.router.routes[-1].tags = [self.name]
            self.router.routes[-1].path_param_names = path_params
            return view

        return register

    def get(self, path):
        return self.route(path, methods=("GET",))

    def post(self, path):
        return self.route(path, methods=("POST",))
