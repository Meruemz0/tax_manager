"""Application entry point. Start with: flask --app tax_manager.app:create_app run."""

from datetime import timedelta
import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, abort, render_template, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from tax_manager.auth.session_key import load_or_create_session_key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        STORAGE_DIR=os.environ.get("STORAGE_DIR"),
        MAX_CONTENT_LENGTH=11 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    for key in ("DATABASE_URL", "STORAGE_DIR"):
        if not app.config[key]:
            raise RuntimeError(f"{key} is required")
    storage_dir = Path(app.config["STORAGE_DIR"]).expanduser().resolve()
    if storage_dir.is_relative_to(Path(app.static_folder).resolve()):
        raise RuntimeError("STORAGE_DIR must not be inside the public static directory")
    app.config["STORAGE_DIR"] = storage_dir
    app.secret_key = load_or_create_session_key(storage_dir)

    @app.get("/")
    def home():
        return render_template("home.html")

    @app.context_processor
    def template_context():
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return {"csrf_token": token}

    @app.before_request
    def check_csrf():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("csrf_token")
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                abort(400, description="表单已过期，请刷新页面后重试")

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(error):
        return "上传内容超过 10 MB 限制", 413

    from tax_manager.auth import blueprint as auth_blueprint
    from tax_manager.customers import blueprint as customers_blueprint
    from tax_manager.images import blueprint as images_blueprint
    from tax_manager.tags import blueprint as tags_blueprint

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(customers_blueprint)
    app.register_blueprint(images_blueprint)
    app.register_blueprint(tags_blueprint)
    return app
