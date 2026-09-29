"""Authenticated UI for customer attachments that are not images."""

from flask import Blueprint, abort, current_app, flash, redirect, request, send_file, url_for

from tax_manager.auth import login_required
from tax_manager.customer_files.storage import (
    InvalidOtherFile, file_path, read_other_file, remove_other_file,
    save_other_file, valid_filename,
)
from tax_manager.db import connect


blueprint = Blueprint("customer_files", __name__)


def detail_redirect(customer_id: int):
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.post("/customers/<int:customer_id>/files")
@login_required
def upload(customer_id: int):
    incoming = request.files.get("file")
    if not incoming:
        flash("请选择其他文件", "error")
        return detail_redirect(customer_id)
    try:
        upload_data = read_other_file(incoming.stream, incoming.filename)
    except InvalidOtherFile as exc:
        flash(str(exc), "error")
        return detail_redirect(customer_id)

    key = None
    try:
        with connect() as conn:
            customer = conn.execute(
                "SELECT id FROM customers WHERE id = %s FOR UPDATE", (customer_id,)
            ).fetchone()
            if not customer:
                abort(404)
            count = conn.execute(
                "SELECT count(*) AS count FROM customer_files WHERE customer_id = %s", (customer_id,)
            ).fetchone()["count"]
            if count >= 10:
                flash("每个客户最多保存 10 个其他文件", "error")
                return detail_redirect(customer_id)
            key = save_other_file(upload_data)
            conn.execute(
                """INSERT INTO customer_files (customer_id, storage_key, original_name, size_bytes)
                   VALUES (%s, %s, %s, %s)""",
                (customer_id, key, upload_data.original_name, upload_data.size_bytes),
            )
    except Exception:
        if key:
            try:
                remove_other_file(key)
            except OSError:
                current_app.logger.exception("Failed to clean up other file after database error")
        raise
    flash("其他文件已上传", "success")
    return detail_redirect(customer_id)


@blueprint.get("/customers/<int:customer_id>/files/<int:file_id>/download")
@login_required
def download(customer_id: int, file_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT storage_key, original_name FROM customer_files WHERE id = %s AND customer_id = %s",
            (file_id, customer_id),
        ).fetchone()
    if not row:
        abort(404)
    path = file_path(row["storage_key"])
    if not path.is_file():
        abort(404)
    response = send_file(
        path, mimetype="application/octet-stream", as_attachment=True,
        download_name=row["original_name"],
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@blueprint.post("/customers/<int:customer_id>/files/<int:file_id>/rename")
@login_required
def rename(customer_id: int, file_id: int):
    name = valid_filename(request.form.get("original_name", ""))
    if not name:
        flash("文件名称无效或超过 255 字", "error")
        return detail_redirect(customer_id)
    with connect() as conn:
        row = conn.execute(
            """UPDATE customer_files SET original_name = %s
               WHERE id = %s AND customer_id = %s RETURNING id""",
            (name, file_id, customer_id),
        ).fetchone()
        if not row:
            abort(404)
    flash("文件名称已更新", "success")
    return detail_redirect(customer_id)


@blueprint.post("/customers/<int:customer_id>/files/<int:file_id>/delete")
@login_required
def delete(customer_id: int, file_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT storage_key FROM customer_files WHERE id = %s AND customer_id = %s FOR UPDATE",
            (file_id, customer_id),
        ).fetchone()
        if not row:
            abort(404)
        conn.execute(
            "DELETE FROM customer_files WHERE id = %s AND customer_id = %s",
            (file_id, customer_id),
        )
    try:
        remove_other_file(row["storage_key"])
    except OSError:
        current_app.logger.exception("Failed to remove other file after database delete")
        flash("记录已删除，但文件清理失败，请检查服务器日志", "error")
    else:
        flash("其他文件已删除", "success")
    return detail_redirect(customer_id)
