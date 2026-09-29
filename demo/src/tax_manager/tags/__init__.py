"""User-defined customer tag categories."""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from tax_manager.auth import login_required
from tax_manager.db import connect


blueprint = Blueprint("tags", __name__)


def category_name(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name if 0 < len(name) <= 60 else None


@blueprint.route("/tags", methods=["GET", "POST"])
@login_required
def manage():
    if request.method == "POST":
        name = category_name(request.form.get("name"))
        if not name:
            flash("分类名称不能为空且最多 60 字", "error")
        else:
            try:
                with connect() as conn:
                    conn.execute("INSERT INTO tag_categories (name) VALUES (%s)", (name,))
                flash("标签分类已添加", "success")
            except UniqueViolation:
                flash("分类名称已存在", "error")
        return redirect(url_for("tags.manage"))
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM tag_categories ORDER BY lower(name), id").fetchall()
    return render_template("tags/manage.html", categories=rows)


@blueprint.post("/tags/<int:category_id>/delete")
@login_required
def delete_category(category_id: int):
    with connect() as conn:
        row = conn.execute("DELETE FROM tag_categories WHERE id = %s RETURNING id", (category_id,)).fetchone()
        if not row:
            abort(404)
    flash("标签分类已删除", "success")
    return redirect(url_for("tags.manage"))


@blueprint.post("/customers/<int:customer_id>/tag-categories")
@login_required
def assign_category(customer_id: int):
    raw = request.form.get("category_id", "")
    if not raw.isdecimal() or int(raw) <= 0:
        abort(400)
    category_id = int(raw)
    with connect() as conn:
        if not conn.execute("SELECT id FROM customers WHERE id = %s", (customer_id,)).fetchone():
            abort(404)
        if not conn.execute("SELECT id FROM tag_categories WHERE id = %s", (category_id,)).fetchone():
            abort(404)
        conn.execute(
            """INSERT INTO customer_tag_categories (customer_id, category_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (customer_id, category_id),
        )
    flash("标签分类已关联客户", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.post("/customers/<int:customer_id>/tag-categories/<int:category_id>/delete")
@login_required
def unassign_category(customer_id: int, category_id: int):
    with connect() as conn:
        conn.execute(
            "DELETE FROM customer_tag_categories WHERE customer_id = %s AND category_id = %s",
            (customer_id, category_id),
        )
    flash("标签分类已移除", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.get("/api/tag-categories")
@login_required
def list_categories():
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM tag_categories ORDER BY lower(name), id").fetchall()
    return jsonify(rows)


@blueprint.post("/api/tag-categories")
@login_required
def create_category():
    payload = request.get_json(silent=True)
    name = category_name(payload.get("name") if isinstance(payload, dict) else None)
    if not name:
        return jsonify({"error": "分类名称不能为空且最多 60 字"}), 400
    try:
        with connect() as conn:
            row = conn.execute(
                "INSERT INTO tag_categories (name) VALUES (%s) RETURNING id, name", (name,)
            ).fetchone()
    except UniqueViolation:
        return jsonify({"error": "分类名称已存在"}), 409
    return jsonify(row), 201
