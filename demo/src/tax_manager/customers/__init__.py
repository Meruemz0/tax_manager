"""Customer records, monthly statuses, and the private customer home page."""

from datetime import datetime
from tax_manager.web import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from tax_manager.auth import login_required
from tax_manager.customers.validation import CHINA_TZ, CustomerInput, ValidationError, china_now, china_today, validate_filing_month
from tax_manager.customers.search import build_home_query, parse_home_filters
from tax_manager.customers.availability import deactivated_at_for_change
from tax_manager.db import connect
from tax_manager.images.storage import remove_image
from tax_manager.customer_files.storage import remove_other_file
from tax_manager.orders.storage import remove_encrypted_voucher
from tax_manager.orders.validation import paid_status


blueprint = Blueprint("customers", __name__)


def ensure_monthly_filings(conn, customer_id: int | None = None) -> None:
    """Persist missing months from each customer's registration through China's current month."""
    conn.execute(
        """INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
           SELECT c.id, months.month_start::date, FALSE
           FROM customers AS c
           CROSS JOIN LATERAL generate_series(
               date_trunc('month', c.registered_at AT TIME ZONE 'Asia/Shanghai'),
               date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
               interval '1 month'
           ) AS months(month_start)
           WHERE (%s::bigint IS NULL OR c.id = %s)
           ON CONFLICT (customer_id, tax_month) DO NOTHING""",
        (customer_id, customer_id),
    )


def ensure_monthly_bookkeeping(conn, customer_id: int | None = None) -> None:
    conn.execute(
        """INSERT INTO monthly_bookkeeping (customer_id, book_month, is_booked)
           SELECT c.id, months.month_start::date, FALSE
           FROM customers AS c
           CROSS JOIN LATERAL generate_series(
               date_trunc('month', c.registered_at AT TIME ZONE 'Asia/Shanghai'),
               date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
               interval '1 month'
           ) AS months(month_start)
           WHERE (%s::bigint IS NULL OR c.id = %s)
           ON CONFLICT (customer_id, book_month) DO NOTHING""",
        (customer_id, customer_id),
    )


@blueprint.get("/customers")
@login_required
def index():
    try:
        filters = parse_home_filters(request.args, china_today())
    except ValidationError as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("customers.index"))
    with connect() as conn:
        ensure_monthly_filings(conn)
        ensure_monthly_bookkeeping(conn)
        sql, params = build_home_query(filters)
        rows = conn.execute(sql, params).fetchall()
        choices = conn.execute(
            "SELECT id, kind, name FROM tag_categories ORDER BY kind, lower(name), id"
        ).fetchall()
    choices_by_kind = {
        kind: [choice for choice in choices if choice["kind"] == kind]
        for kind in ("taxpayer_identity", "service_type", "customer_source")
    }
    return render_template(
        "customers/index.html", customers=rows, filters=filters,
        choices_by_kind=choices_by_kind, current_month=china_today().strftime("%Y-%m"),
    )


def validate_customer_choices(conn, data: CustomerInput) -> None:
    for kind, selected_id in (
        ("taxpayer_identity", data.taxpayer_identity_id),
        ("service_type", data.service_type_id),
        ("customer_source", data.customer_source_id),
    ):
        if selected_id is not None and not conn.execute(
            "SELECT id FROM tag_categories WHERE id = %s AND kind = %s",
            (selected_id, kind),
        ).fetchone():
            raise ValidationError("所选标签不属于对应字段")


def customer_form_choices(conn) -> dict:
    rows = conn.execute(
        """SELECT id, kind, name FROM tag_categories
           WHERE kind IN ('taxpayer_identity', 'service_type', 'customer_source')
           ORDER BY kind, lower(name), id"""
    ).fetchall()
    return {
        kind: [row for row in rows if row["kind"] == kind]
        for kind in ("taxpayer_identity", "service_type", "customer_source")
    }


@blueprint.route("/customers/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        try:
            data = CustomerInput.from_form(request.form)
            registered_at = data.registered_at or china_now()
            registered_on = registered_at.astimezone(CHINA_TZ).date()
            with connect() as conn:
                validate_customer_choices(conn, data)
                row = conn.execute(
                    """INSERT INTO customers
                       (name, tax_identifier, contact_name, contact_phone, note, bookkeeping_start_month,
                        registered_on, registered_at, is_available, deactivated_at,
                        taxpayer_identity_id, service_type_id, customer_source_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (data.name, data.tax_identifier, data.contact_name, data.contact_phone,
                     data.note, data.bookkeeping_start_month, registered_on, registered_at,
                     True if data.is_available is None else data.is_available,
                     china_now() if data.is_available is False else None,
                     data.taxpayer_identity_id, data.service_type_id, data.customer_source_id),
                ).fetchone()
                ensure_monthly_filings(conn, row["id"])
                ensure_monthly_bookkeeping(conn, row["id"])
            flash("客户已添加", "success")
            return redirect(url_for("customers.detail", customer_id=row["id"]))
        except ValidationError as exc:
            flash(str(exc), "error")
        except UniqueViolation:
            flash("税号已存在", "error")
    with connect() as conn:
        choices_by_kind = customer_form_choices(conn)
    return render_template(
        "customers/form.html",
        customer=request.form if request.method == "POST" else {},
        editing=False, choices_by_kind=choices_by_kind,
        registered_at_value=request.form.get("registered_at", "") if request.method == "POST" else "",
    )


@blueprint.get("/customers/<int:customer_id>")
@login_required
def detail(customer_id: int):
    with connect() as conn:
        customer = conn.execute(
            """SELECT c.*, ti.name AS taxpayer_identity, st.name AS service_type,
                      cs.name AS customer_source
               FROM customers AS c
               LEFT JOIN tag_categories AS ti ON ti.id = c.taxpayer_identity_id
               LEFT JOIN tag_categories AS st ON st.id = c.service_type_id
               LEFT JOIN tag_categories AS cs ON cs.id = c.customer_source_id
               WHERE c.id = %s""",
            (customer_id,),
        ).fetchone()
        if not customer:
            abort(404)
        ensure_monthly_filings(conn, customer_id)
        ensure_monthly_bookkeeping(conn, customer_id)
        filings = conn.execute(
            "SELECT tax_month, is_filed FROM monthly_filings WHERE customer_id = %s ORDER BY tax_month DESC",
            (customer_id,),
        ).fetchall()
        bookkeeping = conn.execute(
            "SELECT book_month, is_booked FROM monthly_bookkeeping WHERE customer_id = %s ORDER BY book_month DESC",
            (customer_id,),
        ).fetchall()
        files = conn.execute(
            "SELECT id, original_name, size_bytes FROM customer_files WHERE customer_id = %s ORDER BY id DESC",
            (customer_id,),
        ).fetchall()
        accounts = conn.execute(
            """SELECT id, system_name, login_url, note, created_at
               FROM customer_system_accounts WHERE customer_id = %s ORDER BY id""",
            (customer_id,),
        ).fetchall()
        orders = conn.execute(
            """SELECT o.*, COALESCE((SELECT SUM(r.amount) FROM order_receipts AS r
               WHERE r.order_id = o.id), 0) AS paid_total,
               (SELECT count(*) FROM order_vouchers AS v WHERE v.order_id = o.id) AS document_count
               FROM service_orders AS o WHERE o.customer_id = %s ORDER BY o.order_date DESC, o.id DESC""",
            (customer_id,),
        ).fetchall()
        categories = conn.execute(
            "SELECT id, name FROM tag_categories WHERE kind = 'general' ORDER BY lower(name), id"
        ).fetchall()
        assigned_categories = conn.execute(
            """SELECT t.id, t.name FROM customer_tag_categories AS ct
               JOIN tag_categories AS t ON t.id = ct.category_id
               WHERE ct.customer_id = %s AND t.kind = 'general'
               ORDER BY lower(t.name), t.id""",
            (customer_id,),
        ).fetchall()
    months = {
        row["tax_month"]: {"month": row["tax_month"], "is_filed": row["is_filed"], "is_booked": False}
        for row in filings
    }
    for row in bookkeeping:
        record = months.setdefault(
            row["book_month"], {"month": row["book_month"], "is_filed": False, "is_booked": False}
        )
        record["is_booked"] = row["is_booked"]
    history = [months[month] for month in sorted(months, reverse=True)]
    current = months.get(china_today().replace(day=1), {"is_booked": False, "is_filed": False})
    assigned_ids = {category["id"] for category in assigned_categories}
    available_categories = [category for category in categories if category["id"] not in assigned_ids]
    for order in orders:
        order["payment_status"] = paid_status(order["amount"], [order["paid_total"]])
    registered_at = customer.get("registered_at")
    registered_at_china = registered_at.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M") if registered_at else str(customer["registered_on"])
    return render_template(
        "customers/detail.html", customer=customer, history=history, current_record=current,
        registered_at_china=registered_at_china, files_open=request.args.get("files") == "open",
        files=files, accounts=accounts, orders=orders, assigned_categories=assigned_categories,
        available_categories=available_categories,
    )


@blueprint.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit(customer_id: int):
    if request.method == "POST":
        try:
            data = CustomerInput.from_form(request.form)
            with connect() as conn:
                existing = conn.execute(
                    "SELECT registered_on, registered_at, is_available, deactivated_at FROM customers WHERE id = %s FOR UPDATE",
                    (customer_id,),
                ).fetchone()
                if not existing:
                    abort(404)
                validate_customer_choices(conn, data)
                old_on = existing["registered_on"]
                registered_at = data.registered_at or existing.get("registered_at")
                if registered_at is None:
                    registered_at = datetime.combine(old_on, datetime.min.time(), CHINA_TZ)
                registered_on = registered_at.astimezone(CHINA_TZ).date()
                if registered_on.replace(day=1) > old_on.replace(day=1):
                    earlier_filed = conn.execute(
                        """SELECT 1 FROM monthly_filings
                           WHERE customer_id = %s AND tax_month < %s AND is_filed = TRUE LIMIT 1""",
                        (customer_id, registered_on.replace(day=1)),
                    ).fetchone()
                    earlier_booked = conn.execute(
                        """SELECT 1 FROM monthly_bookkeeping
                           WHERE customer_id = %s AND book_month < %s AND is_booked = TRUE LIMIT 1""",
                        (customer_id, registered_on.replace(day=1)),
                    ).fetchone()
                    if earlier_filed or earlier_booked:
                        raise ValidationError("新注册时间之前已有已记账或已报税记录，请先核对历史月份")
                    conn.execute(
                        "DELETE FROM monthly_filings WHERE customer_id = %s AND tax_month < %s",
                        (customer_id, registered_on.replace(day=1)),
                    )
                    conn.execute(
                        "DELETE FROM monthly_bookkeeping WHERE customer_id = %s AND book_month < %s",
                        (customer_id, registered_on.replace(day=1)),
                    )
                new_available = existing.get("is_available", True) if data.is_available is None else data.is_available
                deactivated_at = deactivated_at_for_change(
                    existing.get("is_available", True), new_available,
                    existing.get("deactivated_at"), china_now(),
                )
                row = conn.execute(
                    """UPDATE customers SET name = %s, tax_identifier = %s,
                       contact_name = %s, contact_phone = %s, note = %s, bookkeeping_start_month = %s,
                       registered_on = %s, registered_at = %s, is_available = %s,
                       deactivated_at = %s, taxpayer_identity_id = %s, service_type_id = %s, customer_source_id = %s,
                       updated_at = now()
                       WHERE id = %s RETURNING id""",
                    (data.name, data.tax_identifier, data.contact_name, data.contact_phone,
                     data.note, data.bookkeeping_start_month, registered_on, registered_at,
                     new_available, deactivated_at,
                     data.taxpayer_identity_id, data.service_type_id, data.customer_source_id,
                     customer_id),
                ).fetchone()
                if not row:
                    abort(404)
                ensure_monthly_filings(conn, customer_id)
                ensure_monthly_bookkeeping(conn, customer_id)
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
        choices_by_kind = customer_form_choices(conn)
    fallback_timestamp = datetime.combine(customer["registered_on"], datetime.min.time(), CHINA_TZ)
    registered_at_value = (
        request.form.get("registered_at", "")
        if request.method == "POST" else
        (customer.get("registered_at") or fallback_timestamp).astimezone(CHINA_TZ).strftime("%Y-%m-%dT%H:%M")
    )
    return render_template(
        "customers/form.html", customer=request.form if request.method == "POST" else customer,
        editing=True, choices_by_kind=choices_by_kind, registered_at_value=registered_at_value,
    )


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
    return redirect(url_for("customers.index", month=tax_month.strftime("%Y-%m")))


@blueprint.post("/customers/<int:customer_id>/bookkeeping")
@login_required
def set_bookkeeping(customer_id: int):
    value = request.form.get("is_booked")
    if value not in {"0", "1"}:
        abort(400)
    with connect() as conn:
        customer = conn.execute(
            "SELECT id, registered_on FROM customers WHERE id = %s", (customer_id,)
        ).fetchone()
        if not customer:
            abort(404)
        try:
            book_month = validate_filing_month(
                request.form.get("book_month"), customer["registered_on"], china_today()
            )
        except ValidationError:
            abort(400)
        conn.execute(
            """INSERT INTO monthly_bookkeeping (customer_id, book_month, is_booked)
               VALUES (%s, %s, %s)
               ON CONFLICT (customer_id, book_month)
               DO UPDATE SET is_booked = EXCLUDED.is_booked, updated_at = now()""",
            (customer_id, book_month, value == "1"),
        )
    flash("记账状态已更新", "success")
    if request.form.get("return_to") == "detail":
        return redirect(url_for("customers.detail", customer_id=customer_id))
    return redirect(url_for("customers.index", month=book_month.strftime("%Y-%m")))


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
        file_keys = [row["storage_key"] for row in conn.execute(
            "SELECT storage_key FROM customer_files WHERE customer_id = %s FOR UPDATE", (customer_id,)
        ).fetchall()]
        voucher_keys = [row["storage_key"] for row in conn.execute(
            """SELECT v.storage_key FROM order_vouchers AS v
               JOIN service_orders AS o ON o.id = v.order_id WHERE o.customer_id = %s""",
            (customer_id,),
        ).fetchall()]
        conn.execute("DELETE FROM customer_files WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM customer_images WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM monthly_bookkeeping WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM monthly_filings WHERE customer_id = %s", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
    for key in keys:
        try:
            remove_image("customer", key)
        except OSError:
            current_app.logger.exception("Failed to remove customer image file")
            flash("客户已删除，但有图片文件未清理，请检查服务器日志", "error")
    for key in file_keys:
        try:
            remove_other_file(key)
        except OSError:
            current_app.logger.exception("Failed to remove customer other file")
            flash("客户已删除，但有其他文件未清理，请检查服务器日志", "error")
    for key in voucher_keys:
        try:
            remove_encrypted_voucher(current_app.config["STORAGE_DIR"], key)
        except OSError:
            current_app.logger.exception("Failed to remove customer order document")
    flash("客户已删除", "success")
    return redirect(url_for("customers.index"))
