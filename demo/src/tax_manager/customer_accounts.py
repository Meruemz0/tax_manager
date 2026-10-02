"""Encrypted external-system credentials for each customer."""

from dataclasses import dataclass
import secrets
from urllib.parse import urlsplit

from tax_manager.auth import login_required
from tax_manager.db import connect
from tax_manager.security.crypto import decrypt_bytes, encrypt_bytes
from tax_manager.web import Blueprint, abort, current_app, flash, jsonify, redirect, request, url_for


blueprint = Blueprint("customer_accounts", __name__)


@dataclass(frozen=True)
class AccountInput:
    system_name: str
    login_url: str | None
    account_name: str
    password: str
    note: str | None

    @classmethod
    def from_mapping(cls, values):
        def field(key, maximum, required=False):
            value = values.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是文字")
            value = value.strip() if key not in {"password", "account_name"} else value
            if len(value) > maximum or required and not value:
                raise ValueError(f"{key} 无效或超过 {maximum} 字")
            return value
        system = field("system_name", 100, True)
        login_url = field("login_url", 500)
        if login_url:
            parsed = urlsplit(login_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("登录网址必须是有效的 http/https 地址，且不能在网址中包含账号密码")
        return cls(system, login_url or None, field("account_name", 255), field("password", 1024), field("note", 255) or None)


def aad(customer_id: int, token: str, field: str) -> bytes:
    return f"customer:{customer_id}:account:{token}:{field}".encode("ascii")


def add_account(conn, customer_id: int, values) -> int:
    data = AccountInput.from_mapping(values)
    if not conn.execute("SELECT id FROM customers WHERE id = %s FOR UPDATE", (customer_id,)).fetchone():
        abort(404)
    count = conn.execute(
        "SELECT count(*) AS n FROM customer_system_accounts WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()["n"]
    if count >= 10:
        raise ValueError("每个客户最多保存 10 条系统账号")
    token = secrets.token_hex(16)
    key = current_app.config["DATA_KEY"]
    row = conn.execute(
        """INSERT INTO customer_system_accounts
           (customer_id, aad_token, system_name, login_url, account_ciphertext, password_ciphertext, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (customer_id, token, data.system_name, data.login_url,
         encrypt_bytes(key, data.account_name.encode("utf-8"), aad(customer_id, token, "name")),
         encrypt_bytes(key, data.password.encode("utf-8"), aad(customer_id, token, "password")),
         data.note),
    ).fetchone()
    return row["id"]


def _back(customer_id):
    return redirect(url_for("customers.detail", customer_id=customer_id) + "#system-accounts")


@blueprint.post("/customers/<int:customer_id>/accounts")
@login_required
def create(customer_id: int):
    try:
        with connect() as conn:
            add_account(conn, customer_id, request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("系统账号已保存", "success")
    return _back(customer_id)


@blueprint.post("/customers/<int:customer_id>/accounts/<int:account_id>/edit")
@login_required
def edit(customer_id: int, account_id: int):
    try:
        data = AccountInput.from_mapping(request.form)
        with connect() as conn:
            row = conn.execute(
                """SELECT aad_token, account_ciphertext, password_ciphertext
                   FROM customer_system_accounts WHERE id = %s AND customer_id = %s FOR UPDATE""",
                (account_id, customer_id),
            ).fetchone()
            if not row:
                abort(404)
            key = current_app.config["DATA_KEY"]
            token = row["aad_token"]
            account_ciphertext = (
                encrypt_bytes(key, data.account_name.encode("utf-8"), aad(customer_id, token, "name"))
                if data.account_name else row["account_ciphertext"]
            )
            password_ciphertext = (
                encrypt_bytes(key, data.password.encode("utf-8"), aad(customer_id, token, "password"))
                if data.password else row["password_ciphertext"]
            )
            conn.execute(
                """UPDATE customer_system_accounts SET system_name = %s, login_url = %s,
                   account_ciphertext = %s, password_ciphertext = %s, note = %s, updated_at = now()
                   WHERE id = %s AND customer_id = %s""",
                (data.system_name, data.login_url, account_ciphertext, password_ciphertext, data.note, account_id, customer_id),
            )
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("系统账号已更新", "success")
    return _back(customer_id)


@blueprint.post("/customers/<int:customer_id>/accounts/<int:account_id>/delete")
@login_required
def delete(customer_id: int, account_id: int):
    with connect() as conn:
        row = conn.execute(
            "DELETE FROM customer_system_accounts WHERE id = %s AND customer_id = %s RETURNING id",
            (account_id, customer_id),
        ).fetchone()
        if not row:
            abort(404)
    flash("系统账号已删除", "success")
    return _back(customer_id)


@blueprint.post("/api/customers/<int:customer_id>/accounts/<int:account_id>/copy")
@login_required
def copy(customer_id: int, account_id: int):
    body = request.get_json(silent=True)
    field = body.get("field") if isinstance(body, dict) else None
    if field not in {"account", "password"}:
        abort(400)
    with connect() as conn:
        row = conn.execute(
            """SELECT aad_token, account_ciphertext, password_ciphertext
               FROM customer_system_accounts WHERE id = %s AND customer_id = %s""",
            (account_id, customer_id),
        ).fetchone()
    if not row:
        abort(404)
    token = row["aad_token"]
    column = "account_ciphertext" if field == "account" else "password_ciphertext"
    label = "name" if field == "account" else "password"
    value = decrypt_bytes(current_app.config["DATA_KEY"], row[column], aad(customer_id, token, label)).decode("utf-8")
    response = jsonify({"value": value})
    response.headers["Cache-Control"] = "private, no-store"
    return response

