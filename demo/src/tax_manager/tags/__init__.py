"""User-defined customer tag choices grouped by business field."""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from tax_manager.auth import login_required
from tax_manager.db import connect


blueprint = Blueprint("tags", __name__)
KIND_LABELS = {
    "taxpayer_identity": "纳税人身份",
    "service_type": "服务类型",
    "customer_source": "客户来源",
    "general": "其他标签",
}


def category_name(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name if 0 < len(name) <= 60 else None


def category_kind(raw) -> str | None:
    kind = "general" if raw is None else raw
    return kind if kind in KIND_LABELS else None


@blueprint.route("/tags", methods=["GET", "POST"])
@login_required
def manage():
    if request.method == "POST":
        name = category_name(request.form.get("name"))
        kind = category_kind(request.form.get("kind"))
        if not name or not kind:
            flash("请选择分类字段，名称不能为空且最多 60 字", "error")
        else:
            try:
                with connect() as conn:
                    conn.execute("INSERT INTO tag_categories (kind, name) VALUES (%s, %s)", (kind, name))
                flash("标签选项已添加", "success")
            except UniqueViolation:
                flash("该字段下的选项名称已存在", "error")
        return redirect(url_for("tags.manage"))
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, name FROM tag_categories ORDER BY kind, lower(name), id"
        ).fetchall()
    categories_by_kind = {
        kind: [row for row in rows if row["kind"] == kind] for kind in KIND_LABELS
    }
    return render_template(
        "tags/manage.html", categories=rows, categories_by_kind=categories_by_kind,
        kind_labels=KIND_LABELS,
    )


@blueprint.post("/tags/<int:category_id>/delete")
@login_required
def delete_category(category_id: int):
    with connect() as conn:
        row = conn.execute("DELETE FROM tag_categories WHERE id = %s RETURNING id", (category_id,)).fetchone()
        if not row:
            abort(404)
    flash("标签选项已删除", "success")
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
        if not conn.execute(
            "SELECT id FROM tag_categories WHERE id = %s AND kind = 'general'", (category_id,)
        ).fetchone():
            abort(404)
        conn.execute(
            """INSERT INTO customer_tag_categories (customer_id, category_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (customer_id, category_id),
        )
    flash("其他标签已关联客户", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.post("/customers/<int:customer_id>/tag-categories/<int:category_id>/delete")
@login_required
def unassign_category(customer_id: int, category_id: int):
    with connect() as conn:
        conn.execute(
            "DELETE FROM customer_tag_categories WHERE customer_id = %s AND category_id = %s",
            (customer_id, category_id),
        )
    flash("其他标签已移除", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@blueprint.get("/api/tag-categories")
@login_required
def list_categories():
    raw_kind = request.args.get("kind")
    if raw_kind is not None and category_kind(raw_kind) is None:
        return jsonify({"error": "标签字段无效"}), 400
    with connect() as conn:
        if raw_kind is None:
            rows = conn.execute(
                "SELECT id, kind, name FROM tag_categories ORDER BY kind, lower(name), id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, kind, name FROM tag_categories WHERE kind = %s ORDER BY lower(name), id",
                (raw_kind,),
            ).fetchall()
    return jsonify(rows)


@blueprint.post("/api/tag-categories")
@login_required
def create_category():
    payload = request.get_json(silent=True)
    name = category_name(payload.get("name") if isinstance(payload, dict) else None)
    kind = category_kind(payload.get("kind") if isinstance(payload, dict) else None)
    if not name or not kind:
        return jsonify({"error": "请选择标签字段，名称不能为空且最多 60 字"}), 400
    try:
        with connect() as conn:
            if kind == "general":
                row = conn.execute(
                    "INSERT INTO tag_categories (name) VALUES (%s) RETURNING id, name", (name,)
                ).fetchone()
            else:
                row = conn.execute(
                    "INSERT INTO tag_categories (kind, name) VALUES (%s, %s) RETURNING id, kind, name",
                    (kind, name),
                ).fetchone()
    except UniqueViolation:
        return jsonify({"error": "该字段下的选项名称已存在"}), 409
    return jsonify(row), 201
