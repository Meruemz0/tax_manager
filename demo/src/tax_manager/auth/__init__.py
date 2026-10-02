"""Authentication pages and session checks."""

from functools import wraps
import hashlib
import secrets

import click
from tax_manager.web import Blueprint, flash, redirect, render_template, request, session, url_for

from tax_manager.auth.passwords import DUMMY_HASH, hash_password, verify_password
from tax_manager.db import connect


blueprint = Blueprint("auth", __name__)


def login_keys(username: str) -> tuple[str, str]:
    source = request.remote_addr or "unknown"
    return tuple(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in (f"source:{source}", f"account:{username}"))


def login_is_limited(keys: tuple[str, str]) -> bool:
    with connect() as conn:
        for key, limit in zip(keys, (20, 8)):
            row = conn.execute(
                """SELECT attempt_count FROM login_attempts
                   WHERE key = %s AND window_started_at > now() - interval '10 minutes'""",
                (key,),
            ).fetchone()
            if row and row["attempt_count"] >= limit:
                return True
    return False


def record_failed_login(keys: tuple[str, str]) -> None:
    with connect() as conn:
        for key in keys:
            conn.execute(
                """INSERT INTO login_attempts (key, attempt_count, window_started_at)
                   VALUES (%s, 1, now())
                   ON CONFLICT (key) DO UPDATE SET
                       attempt_count = CASE
                           WHEN login_attempts.window_started_at <= now() - interval '10 minutes' THEN 1
                           ELSE login_attempts.attempt_count + 1 END,
                       window_started_at = CASE
                           WHEN login_attempts.window_started_at <= now() - interval '10 minutes' THEN now()
                           ELSE login_attempts.window_started_at END""",
                (key,),
            )


def clear_login_attempts(keys: tuple[str, str]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM login_attempts WHERE key IN (%s, %s)", keys)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("auth.login"))
        with connect() as conn:
            user = conn.execute("SELECT id, username FROM site_users WHERE id = %s AND is_active = TRUE", (user_id,)).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    wrapped.requires_login = True
    return wrapped


@blueprint.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) > 100 or len(password) > 1024:
            flash("账号或密码错误", "error")
        else:
            keys = login_keys(username)
            if login_is_limited(keys):
                flash("尝试次数过多，请 10 分钟后重试", "error")
                return render_template("login.html"), 429
            with connect() as conn:
                user = conn.execute(
                    "SELECT id, username, password_hash FROM site_users WHERE username = %s AND is_active = TRUE",
                    (username,),
                ).fetchone()
            matches = verify_password(password, user["password_hash"] if user else DUMMY_HASH)
            if user and matches:
                clear_login_attempts(keys)
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("customers.index"))
            record_failed_login(keys)
            flash("账号或密码错误", "error")
    return render_template("login.html")


@blueprint.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@blueprint.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new) < 10 or len(new) > 1024:
            flash("新密码至少 10 个字符，最多 1024 个字符", "error")
        elif new != confirm:
            flash("两次输入的新密码不一致", "error")
        else:
            with connect() as conn:
                user = conn.execute("SELECT password_hash FROM site_users WHERE id = %s", (session["user_id"],)).fetchone()
                if not user or not verify_password(current, user["password_hash"]):
                    flash("当前密码错误", "error")
                else:
                    conn.execute("UPDATE site_users SET password_hash = %s WHERE id = %s", (hash_password(new), session["user_id"]))
                    flash("密码已更新", "success")
                    return redirect(url_for("customers.index"))
    return render_template("change_password.html")


@blueprint.cli.command("set-password")
@click.argument("username")
def set_password_command(username: str):
    """Set an existing user's password without putting it in shell history."""
    if len(username) > 100:
        raise click.ClickException("用户名过长")
    password = click.prompt("新密码", hide_input=True, confirmation_prompt=True)
    if len(password) < 10 or len(password) > 1024:
        raise click.ClickException("新密码必须是 10 到 1024 个字符")
    with connect() as conn:
        user = conn.execute("SELECT id FROM site_users WHERE username = %s", (username,)).fetchone()
        if not user:
            raise click.ClickException("账号不存在，请先执行 demo/init.sql")
        conn.execute("UPDATE site_users SET password_hash = %s WHERE id = %s", (hash_password(password), user["id"]))
    click.echo("密码已更新")
