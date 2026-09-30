"""Customer and unassigned image pages."""

from pathlib import Path

import click
from tax_manager.web import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from tax_manager.auth import login_required
from tax_manager.db import connect
from tax_manager.images.storage import InvalidImage, file_path, read_image, remove_image, save_image


blueprint = Blueprint("images", __name__)
TABLES = {"customer": "customer_images", "unassigned": "unassigned_images"}


def image_row(conn, kind: str, image_id: int, *, for_update: bool = False):
    if kind not in TABLES:
        abort(404)
    customer_field = "customer_id" if kind == "customer" else "NULL::bigint AS customer_id"
    row = conn.execute(
        f"SELECT id, storage_key, original_name, mime_type, size_bytes, {customer_field} "
        f"FROM {TABLES[kind]} WHERE id = %s" + (" FOR UPDATE" if for_update else ""), (image_id,),
    ).fetchone()
    if not row:
        abort(404)
    return row


def return_to(kind: str, row=None):
    if kind == "customer":
        return redirect(url_for("customers.detail", customer_id=row["customer_id"]))
    return redirect(url_for("images.unassigned"))


def uploaded_file():
    file = request.files.get("image")
    if not file:
        raise InvalidImage("请选择图片")
    return read_image(file.stream, file.filename)


def cleanup_new_file(kind: str, key: str):
    try:
        remove_image(kind, key)
    except OSError:
        current_app.logger.exception("Failed to clean up image after database error")


def remove_old_file(kind: str, key: str):
    try:
        remove_image(kind, key)
    except OSError:
        current_app.logger.exception("Failed to remove old image file")
        flash("数据库已更新，但旧图片文件未清理，请检查服务器日志", "error")


@blueprint.get("/unassigned")
@login_required
def unassigned():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, original_name, mime_type, size_bytes FROM unassigned_images ORDER BY id DESC"
        ).fetchall()
    return render_template("images/unassigned.html", images=rows)


@blueprint.post("/unassigned/images")
@login_required
def upload_unassigned():
    try:
        upload = uploaded_file()
    except InvalidImage as exc:
        flash(str(exc), "error")
        return redirect(url_for("images.unassigned"))
    key = save_image("unassigned", upload)
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO unassigned_images (storage_key, original_name, mime_type, size_bytes)
                   VALUES (%s, %s, %s, %s)""",
                (key, upload.original_name, upload.mime_type, upload.size_bytes),
            )
    except Exception:
        cleanup_new_file("unassigned", key)
        raise
    flash("图片已上传", "success")
    return redirect(url_for("images.unassigned"))


@blueprint.post("/customers/<int:customer_id>/images")
@login_required
def upload_customer(customer_id: int):
    with connect() as conn:
        if not conn.execute("SELECT id FROM customers WHERE id = %s", (customer_id,)).fetchone():
            abort(404)
    try:
        upload = uploaded_file()
    except InvalidImage as exc:
        flash(str(exc), "error")
        return redirect(url_for("customers.detail", customer_id=customer_id))
    key = save_image("customer", upload)
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO customer_images (customer_id, storage_key, original_name, mime_type, size_bytes)
                   VALUES (%s, %s, %s, %s, %s)""",
                (customer_id, key, upload.original_name, upload.mime_type, upload.size_bytes),
            )
    except Exception:
        cleanup_new_file("customer", key)
        raise
    flash("客户图片已上传", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.get("/images/<kind>/<int:image_id>/<action>")
@login_required
def file(kind: str, image_id: int, action: str):
    if action not in {"view", "download"}:
        abort(404)
    with connect() as conn:
        row = image_row(conn, kind, image_id)
    path = file_path(kind, row["storage_key"])
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        mimetype=row["mime_type"],
        as_attachment=action == "download",
        download_name=row["original_name"],
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@blueprint.post("/images/<kind>/<int:image_id>/rename")
@login_required
def rename(kind: str, image_id: int):
    name = request.form.get("original_name", "").strip()
    with connect() as conn:
        row = image_row(conn, kind, image_id)
        if not name or len(name) > 255 or any(char in name for char in "/\\\r\n"):
            flash("图片名称不能为空、不能包含路径符号且不能超过 255 字符", "error")
        else:
            updated = conn.execute(
                f"UPDATE {TABLES[kind]} SET original_name = %s WHERE id = %s RETURNING id",
                (name, image_id),
            ).fetchone()
            if not updated:
                abort(404)
            flash("图片名称已更新", "success")
    return return_to(kind, row)


@blueprint.post("/images/<kind>/<int:image_id>/replace")
@login_required
def replace(kind: str, image_id: int):
    with connect() as conn:
        row = image_row(conn, kind, image_id)
    try:
        upload = uploaded_file()
    except InvalidImage as exc:
        flash(str(exc), "error")
        return return_to(kind, row)
    key = save_image(kind, upload)
    try:
        with connect() as conn:
            current = image_row(conn, kind, image_id, for_update=True)
            updated = conn.execute(
                f"""UPDATE {TABLES[kind]} SET storage_key = %s, original_name = %s,
                    mime_type = %s, size_bytes = %s WHERE id = %s RETURNING id""",
                (key, upload.original_name, upload.mime_type, upload.size_bytes, image_id),
            ).fetchone()
            if not updated:
                abort(404)
    except Exception:
        cleanup_new_file(kind, key)
        raise
    remove_old_file(kind, current["storage_key"])
    flash("图片已替换", "success")
    return return_to(kind, row)


@blueprint.post("/images/<kind>/<int:image_id>/delete")
@login_required
def delete(kind: str, image_id: int):
    with connect() as conn:
        row = image_row(conn, kind, image_id, for_update=True)
        conn.execute(f"DELETE FROM {TABLES[kind]} WHERE id = %s", (image_id,))
    remove_old_file(kind, row["storage_key"])
    flash("图片已删除", "success")
    return return_to(kind, row)


@blueprint.cli.command("check-files")
def check_files_command():
    """Report missing and orphaned image files without changing data."""
    errors = 0
    with connect() as conn:
        for kind, table in TABLES.items():
            keys = [row["storage_key"] for row in conn.execute(f"SELECT storage_key FROM {table}").fetchall()]
            expected = set()
            invalid_keys = []
            for key in keys:
                try:
                    expected.add(file_path(kind, key))
                except ValueError:
                    invalid_keys.append(key)
            directory = Path(current_app.config["STORAGE_DIR"]) / kind
            actual = {path for path in directory.rglob("*") if path.is_file()} if directory.exists() else set()
            missing = expected - actual
            orphans = actual - expected
            click.echo(f"{kind}: {len(keys)} 条记录，缺失文件 {len(missing)}，孤立文件 {len(orphans)}，无效存储键 {len(invalid_keys)}")
            for path in sorted(missing | orphans):
                click.echo(f"  {path}")
            errors += len(missing) + len(orphans) + len(invalid_keys)
    if errors:
        raise click.ClickException("图片文件和数据库不一致，请从备份恢复缺失文件，或检查孤立文件")
