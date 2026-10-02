"""FastAPI application entry point."""

import asyncio
from datetime import timedelta
import logging
import os
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.formparsers import MultiPartParser

from tax_manager.auth.session_key import load_or_create_session_key
from tax_manager.security.crypto import load_or_create_data_key
from tax_manager.security.key_verifier import verify_or_initialize_data_key
from tax_manager.customers.availability import format_china_hour
from tax_manager.web import BodyLimitMiddleware, Blueprint, get_flashed_messages, render_template, session, url_for


def create_app(test_config: dict | None = None) -> FastAPI:
    app = FastAPI(title="税务客户管理")
    app.config = {
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "STORAGE_DIR": os.environ.get("STORAGE_DIR"),
        "MAX_CONTENT_LENGTH": 64 * 1024 * 1024,
        "TESTING": False,
        "COOKIE_SECURE": os.environ.get("COOKIE_SECURE") == "1",
    }
    if test_config:
        app.config.update(test_config)
    # Keep uploads in memory until our code encrypts order documents. The body
    # middleware limits each request to MAX_CONTENT_LENGTH.
    MultiPartParser.spool_max_size = app.config["MAX_CONTENT_LENGTH"] + 1
    for key in ("DATABASE_URL", "STORAGE_DIR"):
        if not app.config[key]:
            raise RuntimeError(f"{key} is required")
    storage_dir = Path(app.config["STORAGE_DIR"]).expanduser().resolve()
    static_dir = Path(__file__).parent / "static"
    if storage_dir.is_relative_to(static_dir.resolve()):
        raise RuntimeError("STORAGE_DIR must not be inside the public static directory")
    app.config["STORAGE_DIR"] = storage_dir
    data_key_path = app.config.get("DATA_KEY_FILE") or os.environ.get("DATA_KEY_FILE")
    app.config["DATA_KEY"] = load_or_create_data_key(
        storage_dir, Path(data_key_path) if data_key_path else None
    )
    if not app.config["TESTING"]:
        with psycopg.connect(app.config["DATABASE_URL"], connect_timeout=5, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            verify_or_initialize_data_key(conn, app.config["DATA_KEY"])
    app.secret_key = load_or_create_session_key(storage_dir).hex()
    app.logger = logging.getLogger("tax_manager")
    app.state.multipart_slots = asyncio.Semaphore(1)
    app.state.templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    app.state.templates.env.filters["china_hour"] = format_china_hour
    app.state.templates.env.globals.update(
        url_for=url_for, session=session, get_flashed_messages=get_flashed_messages
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=app.config["MAX_CONTENT_LENGTH"])
    app.add_middleware(
        SessionMiddleware,
        secret_key=app.secret_key,
        max_age=int(timedelta(hours=8).total_seconds()),
        same_site="lax",
        https_only=app.config["COOKIE_SECURE"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return response

    main = Blueprint("", __name__)

    @main.get("/")
    def home():
        return render_template("home.html")

    from tax_manager.auth import blueprint as auth_blueprint
    from tax_manager.customers import blueprint as customers_blueprint
    from tax_manager.customers.api import blueprint as customer_api_blueprint
    from tax_manager.tags import blueprint as tags_blueprint
    from tax_manager.customer_files import blueprint as customer_files_blueprint
    from tax_manager.customer_accounts import blueprint as customer_accounts_blueprint
    from tax_manager.orders import blueprint as orders_blueprint

    app.state.route_params = {}
    for item in (
        main, auth_blueprint, customers_blueprint, customer_api_blueprint,
        tags_blueprint, customer_files_blueprint, customer_accounts_blueprint, orders_blueprint,
    ):
        for route in item.router.routes:
            app.state.route_params[route.name] = re.findall(
                r"{(\w+)(?::\w+)?}", route.path
            )
        app.include_router(item.router)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app
