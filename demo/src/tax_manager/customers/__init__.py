"""Customer records and monthly filing workflow."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from tax_manager.auth import login_required
from tax_manager.customers.validation import CustomerInput, ValidationError, china_today, validate_filing_month
from tax_manager.db import connect
from tax_manager.images.storage import remove_image


blueprint = Blueprint("customers", __name__)


def ensure_monthly_filings(conn, customer_id: int | None = None) -> None:
    """Persist missing months from each customer's registration through China's current month."""
    conn.execute(
        """INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
           SELECT c.id, months.month_start::date, FALSE
           FROM customers AS c
           CROSS JOIN LATERAL generate_series(
               date_trunc('month', c.registered_on::timestamp),
               date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
               interval '1 month'
           ) AS months(month_start)
           WHERE (%s::bigint IS NULL OR c.id = %s)
           ON CONFLICT (customer_id, tax_month) DO NOTHING""",
        (customer_id, customer_id),
    )


@blueprint.get("/customers")
@login_required
def index():
    query = request.args.get("q", "").strip()[:100]
    with connect() as conn:
        ensure_monthly_filings(conn)
        rows = conn.execute(
            """SELECT c.id, c.name, c.tax_identifier, c.contact_name, c.contact_phone,
                      v.tax_month, v.is_filed
               FROM customers AS c
               JOIN customer_current_month_filing_status AS v ON v.customer_id = c.id
               WHERE %s = '' OR c.name ILIKE %s OR COALESCE(c.tax_identifier, '') ILIKE %s
               ORDER BY c.id DESC""",
            (query, f"%{query}%", f"%{query}%"),
        ).fetchall()
    return render_template("customers/index.html", customers=rows, query=query)


@blueprint.route("/customers/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        try:
            data = CustomerInput.from_form(request.form)
            with connect() as conn:
                row = conn.execute(
                    """INSERT INTO customers (name, tax_identifier, contact_name, contact_phone, note, registered_on)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (data.name, data.tax_identifier, data.contact_name, data.contact_phone,
                     data.note, data.registered_on or china_today()),
                ).fetchone()
                ensure_monthly_filings(conn, row["id"])
            flash("客户已添加", "success")
            return redirect(url_for("customers.detail", customer_id=row["id"]))
        except ValidationError as exc:
            flash(str(exc), "error")
        except UniqueViolation:
            flash("税号已存在", "error")
    return render_template(
        "customers/form.html",
        customer=request.form if request.method == "POST" else {"registered_on": china_today().isoformat()},
        editing=False,
    )


@blueprint.get("/customers/<int:customer_id>")
@login_required
def detail(customer_id: int):
    with connect() as conn:
        customer = conn.execute(
            "SELECT c.* FROM customers AS c WHERE c.id = %s", (customer_id,),
        ).fetchone()
        if not customer:
            abort(404)
        ensure_monthly_filings(conn, customer_id)
        filings = conn.execute(
            "SELECT tax_month, is_filed FROM monthly_filings WHERE customer_id = %s ORDER BY tax_month DESC",
            (customer_id,),
        ).fetchall()
        images = conn.execute(
            "SELECT id, original_name, mime_type, size_bytes FROM customer_images WHERE customer_id = %s ORDER BY id DESC",
            (customer_id,),
        ).fetchall()
        categories = conn.execute(
            "SELECT id, name FROM tag_categories ORDER BY lower(name), id"
        ).fetchall()
        assigned_categories = conn.execute(
            """SELECT t.id, t.name FROM customer_tag_categories AS ct
               JOIN tag_categories AS t ON t.id = ct.category_id
               WHERE ct.customer_id = %s ORDER BY lower(t.name), t.id""",
            (customer_id,),
        ).fetchall()
    assigned_ids = {category["id"] for category in assigned_categories}
    available_categories = [category for category in categories if category["id"] not in assigned_ids]
    return render_template(
        "customers/detail.html", customer=customer, filings=filings, images=images,
        assigned_categories=assigned_categories, available_categories=available_categories,
    )


@blueprint.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit(customer_id: int):
    if request.method == "POST":
        try:
            data = CustomerInput.from_form(request.form)
            with connect() as conn:
                existing = conn.execute(
                    "SELECT registered_on FROM customers WHERE id = %s FOR UPDATE", (customer_id,)
                ).fetchone()
                if not existing:
                    abort(404)
                registered_on = data.registered_on or existing["registered_on"]
                if registered_on.replace(day=1) > existing["registered_on"].replace(day=1):
                    earlier_filed = conn.execute(
                        """SELECT 1 FROM monthly_filings
                           WHERE customer_id = %s AND tax_month < %s AND is_filed = TRUE LIMIT 1""",
                        (customer_id, registered_on.replace(day=1)),
                    ).fetchone()
                    if earlier_filed:
                        raise ValidationError("新注册日期之前已有已报税记录，请先核对历史月份")
                    conn.execute(
                        "DELETE FROM monthly_filings WHERE customer_id = %s AND tax_month < %s",
                        (customer_id, registered_on.replace(day=1)),
                    )
                row = conn.execute(
                    """UPDATE customers SET name = %s, tax_identifier = %s,
                       contact_name = %s, contact_phone = %s, note = %s,
                       registered_on = %s, updated_at = now()
                       WHERE id = %s RETURNING id""",
                    (data.name, data.tax_identifier, data.contact_name, data.contact_phone,
                     data.note, registered_on, customer_id),
                ).fetchone()
                if not row:
                    abort(404)
                ensure_monthly_filings(conn, customer_id)
            flash("客户资料已更新", "success")
            return redirect(url_for("customers.detail", customer_id=customer_id))
        except ValidationError as exc:
            flash(str(exc), "error")
        except UniqueViolation:
            flash("税号已存在", "error")
    with connect() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id = %s", (customer_id,)).fetchone()
    if not customer:
        abort(404)
    return render_template("customers/form.html", customer=request.form if request.method == "POST" else customer, editing=True)


@blueprint.post("/customers/<int:customer_id>/filing")
@login_required
def set_filing(customer_id: int):
    value = request.form.get("is_filed")
    if value not in {"0", "1"}:
        abort(400)
    with connect() as conn:
        customer = conn.execute("SELECT id, registered_on FROM customers WHERE id = %s", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        try:
            tax_month = validate_filing_month(request.form.get("tax_month"), customer["registered_on"], china_today())
        except ValidationError:
            abort(400)
        conn.execute(
            """INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
               VALUES (%s, %s, %s)
               ON CONFLICT (customer_id, tax_month)
               DO UPDATE SET is_filed = EXCLUDED.is_filed, updated_at = now()""",
            (customer_id, tax_month, value == "1"),
        )
    flash("报税状态已更新", "success")
    if request.form.get("return_to") == "detail":
        return redirect(url_for("customers.detail", customer_id=customer_id))
    return redirect(url_for("customers.index"))


@blueprint.post("/customers/<int:customer_id>/delete")
@login_required
def delete(customer_id: int):
    with connect() as conn:
        customer = conn.execute("SELECT id FROM customers WHERE id = %s FOR UPDATE", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        keys = [row["storage_key"] for row in conn.execute(
            "SELECT storage_key FROM customer_images WHERE customer_id = %s FOR UPDATE", (customer_id,)
        ).fetchall()]
        conn.execute("DELETE FROM customer_images WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM monthly_filings WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
    for key in keys:
        try:
            remove_image("customer", key)
        except OSError:
            current_app.logger.exception("Failed to remove customer image file")
            flash("客户已删除，但有图片文件未清理，请检查服务器日志", "error")
    flash("客户已删除", "success")
    return redirect(url_for("customers.index"))
