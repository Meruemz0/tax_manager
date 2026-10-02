"""Service orders, payments, and encrypted supporting documents."""

from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi.responses import Response

from tax_manager.auth import login_required
from tax_manager.customer_files.storage import InvalidOtherFile, read_other_file
from tax_manager.db import connect
from tax_manager.orders.storage import (
    read_encrypted_voucher, remove_encrypted_voucher, save_encrypted_voucher,
    voucher_name_aad, voucher_path,
)
from tax_manager.orders.validation import OrderInput, ReceiptInput, paid_status
from tax_manager.security.crypto import decrypt_bytes, encrypt_bytes
from tax_manager.web import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for


blueprint = Blueprint("orders", __name__)
DOCUMENT_TYPES = {"voucher": "原始凭证", "contract": "合同", "receipt": "收款凭据"}


def _customer_back(customer_id):
    return redirect(url_for("customers.detail", customer_id=customer_id) + "#service-orders")


def _order_back(customer_id, order_id):
    return redirect(url_for("orders.detail", customer_id=customer_id, order_id=order_id))


def _get_order(conn, customer_id, order_id, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = conn.execute(
        "SELECT * FROM service_orders WHERE id = %s AND customer_id = %s" + suffix,
        (order_id, customer_id),
    ).fetchone()
    if not row:
        abort(404)
    return row


def add_order(conn, customer_id: int, values) -> int:
    data = OrderInput.from_mapping(values)
    if not conn.execute("SELECT id FROM customers WHERE id = %s FOR UPDATE", (customer_id,)).fetchone():
        abort(404)
    row = conn.execute(
        """INSERT INTO service_orders
           (customer_id, service_name, order_date, amount, service_start_month,
            service_end_month, due_date, contract_number, note, is_completed, completed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   CASE WHEN %s THEN now() ELSE NULL END)
           RETURNING id""",
        (customer_id, data.service_name, data.order_date, data.amount,
         data.service_start_month, data.service_end_month, data.due_date,
         data.contract_number, data.note, data.is_completed, data.is_completed),
    ).fetchone()
    return row["id"]


def add_receipt(conn, customer_id: int, order_id: int, values) -> int:
    data = ReceiptInput.from_mapping(values)
    order = _get_order(conn, customer_id, order_id, lock=True)
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM order_receipts WHERE order_id = %s",
        (order_id,),
    ).fetchone()["total"]
    if paid + data.amount > order["amount"]:
        raise ValueError("本次收款超过订单剩余应收金额")
    row = conn.execute(
        """INSERT INTO order_receipts (order_id, amount, received_on, method, note)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (order_id, data.amount, data.received_on, data.method, data.note),
    ).fetchone()
    return row["id"]


@blueprint.get("/orders")
@login_required
def index():
    query = (request.args.get("q") or "").strip()
    payment = request.args.get("payment", "all")
    completion = request.args.get("completion", "all")
    from_raw = request.args.get("from_date", "")
    to_raw = request.args.get("to_date", "")
    if len(query) > 100 or payment not in {"all", "unpaid", "partial", "paid", "no_charge"} or completion not in {"all", "yes", "no"}:
        abort(400)
    try:
        from_date = date.fromisoformat(from_raw) if from_raw else None
        to_date = date.fromisoformat(to_raw) if to_raw else None
    except ValueError:
        abort(400)
    if from_date and to_date and from_date > to_date:
        abort(400)
    with connect() as conn:
        rows = conn.execute(
            """SELECT o.*, c.name AS customer_name,
               COALESCE((SELECT SUM(r.amount) FROM order_receipts AS r
                         WHERE r.order_id = o.id), 0) AS paid_total
               FROM service_orders AS o
               JOIN customers AS c ON c.id = o.customer_id
               ORDER BY o.order_date DESC, o.id DESC"""
        ).fetchall()
    orders = []
    for order in rows:
        order["payment_status"] = paid_status(order["amount"], [order["paid_total"]])
        if query and query.casefold() not in " ".join(
            str(order.get(field) or "") for field in ("customer_name", "service_name", "contract_number", "id")
        ).casefold():
            continue
        if from_date and order["order_date"] < from_date or to_date and order["order_date"] > to_date:
            continue
        if completion == "yes" and not order["is_completed"] or completion == "no" and order["is_completed"]:
            continue
        status_filter = {
            "unpaid": "未收款", "partial": "部分收款", "paid": "已收款", "no_charge": "无需收款",
        }
        if payment != "all" and order["payment_status"] != status_filter[payment]:
            continue
        orders.append(order)
    total = sum((order["amount"] for order in orders), Decimal("0"))
    received = sum((order["paid_total"] for order in orders), Decimal("0"))
    return render_template(
        "orders/index.html", orders=orders, query=query, payment=payment,
        completion=completion, from_date=from_raw, to_date=to_raw,
        total=total, received=received,
    )


@blueprint.post("/customers/<int:customer_id>/orders")
@login_required
def create(customer_id: int):
    try:
        with connect() as conn:
            order_id = add_order(conn, customer_id, request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return _customer_back(customer_id)
    flash("服务订单已添加", "success")
    return _order_back(customer_id, order_id)


@blueprint.get("/customers/<int:customer_id>/orders/<int:order_id>")
@login_required
def detail(customer_id: int, order_id: int):
    with connect() as conn:
        order = _get_order(conn, customer_id, order_id)
        customer = conn.execute(
            "SELECT id, name FROM customers WHERE id = %s", (customer_id,)
        ).fetchone()
        receipts = conn.execute(
            "SELECT id, amount, received_on, method, note FROM order_receipts WHERE order_id = %s ORDER BY received_on, id",
            (order_id,),
        ).fetchall()
        documents = conn.execute(
            """SELECT id, storage_key, name_ciphertext, size_bytes, document_type, created_at
               FROM order_vouchers WHERE order_id = %s ORDER BY id DESC""",
            (order_id,),
        ).fetchall()
    for document in documents:
        document["original_name"] = decrypt_bytes(
            current_app.config["DATA_KEY"], document["name_ciphertext"],
            voucher_name_aad(order_id, document["storage_key"]),
        ).decode("utf-8")
    paid = sum((row["amount"] for row in receipts), Decimal("0"))
    return render_template(
        "orders/detail.html", customer=customer, order=order, receipts=receipts,
        documents=documents, document_types=DOCUMENT_TYPES,
        paid_total=paid, remaining=order["amount"] - paid,
        payment_status=paid_status(order["amount"], (row["amount"] for row in receipts)),
    )


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/edit")
@login_required
def edit(customer_id: int, order_id: int):
    try:
        data = OrderInput.from_mapping(request.form)
        with connect() as conn:
            order = _get_order(conn, customer_id, order_id, lock=True)
            paid = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM order_receipts WHERE order_id = %s",
                (order_id,),
            ).fetchone()["total"]
            if data.amount < paid:
                raise ValueError("订单金额不能小于已有收款总额")
            conn.execute(
                """UPDATE service_orders SET service_name = %s, order_date = %s, amount = %s,
                   service_start_month = %s, service_end_month = %s, due_date = %s,
                   contract_number = %s, note = %s, is_completed = %s,
                   completed_at = CASE WHEN %s THEN COALESCE(completed_at, now()) ELSE NULL END,
                   updated_at = now()
                   WHERE id = %s AND customer_id = %s""",
                (data.service_name, data.order_date, data.amount, data.service_start_month,
                 data.service_end_month, data.due_date, data.contract_number, data.note,
                 data.is_completed, data.is_completed, order_id, customer_id),
            )
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("订单已更新", "success")
    return _order_back(customer_id, order_id)


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/delete")
@login_required
def delete(customer_id: int, order_id: int):
    with connect() as conn:
        _get_order(conn, customer_id, order_id, lock=True)
        keys = [row["storage_key"] for row in conn.execute(
            "SELECT storage_key FROM order_vouchers WHERE order_id = %s", (order_id,)
        ).fetchall()]
        conn.execute("DELETE FROM service_orders WHERE id = %s AND customer_id = %s", (order_id, customer_id))
    for key in keys:
        try:
            remove_encrypted_voucher(current_app.config["STORAGE_DIR"], key)
        except OSError:
            current_app.logger.exception("Failed to remove deleted order document")
    flash("订单已删除", "success")
    return _customer_back(customer_id)


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/receipts")
@login_required
def create_receipt(customer_id: int, order_id: int):
    try:
        with connect() as conn:
            add_receipt(conn, customer_id, order_id, request.form)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("收款已登记", "success")
    return _order_back(customer_id, order_id)


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/receipts/<int:receipt_id>/edit")
@login_required
def edit_receipt(customer_id: int, order_id: int, receipt_id: int):
    try:
        data = ReceiptInput.from_mapping(request.form)
        with connect() as conn:
            order = _get_order(conn, customer_id, order_id, lock=True)
            existing = conn.execute(
                "SELECT id FROM order_receipts WHERE id = %s AND order_id = %s FOR UPDATE",
                (receipt_id, order_id),
            ).fetchone()
            if not existing:
                abort(404)
            other_paid = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM order_receipts WHERE order_id = %s AND id <> %s",
                (order_id, receipt_id),
            ).fetchone()["total"]
            if other_paid + data.amount > order["amount"]:
                raise ValueError("修改后的收款超过订单金额")
            conn.execute(
                """UPDATE order_receipts SET amount = %s, received_on = %s, method = %s, note = %s,
                   updated_at = now() WHERE id = %s AND order_id = %s""",
                (data.amount, data.received_on, data.method, data.note, receipt_id, order_id),
            )
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("收款记录已更新", "success")
    return _order_back(customer_id, order_id)


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/receipts/<int:receipt_id>/delete")
@login_required
def delete_receipt(customer_id: int, order_id: int, receipt_id: int):
    with connect() as conn:
        _get_order(conn, customer_id, order_id, lock=True)
        row = conn.execute(
            "DELETE FROM order_receipts WHERE id = %s AND order_id = %s RETURNING id",
            (receipt_id, order_id),
        ).fetchone()
        if not row:
            abort(404)
    flash("收款记录已删除", "success")
    return _order_back(customer_id, order_id)


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/vouchers")
@login_required
def upload_vouchers(customer_id: int, order_id: int):
    document_type = request.form.get("document_type", "voucher")
    if document_type not in DOCUMENT_TYPES:
        flash("文件类别无效", "error")
        return _order_back(customer_id, order_id)
    incoming = [file for file in request.files.getlist("file") if file.filename]
    if not incoming:
        flash("请选择文件", "error")
        return _order_back(customer_id, order_id)
    try:
        uploads = [read_other_file(file.stream, file.filename) for file in incoming]
    except InvalidOtherFile as exc:
        flash(str(exc), "error")
        return _order_back(customer_id, order_id)
    created = []
    try:
        with connect() as conn:
            _get_order(conn, customer_id, order_id, lock=True)
            key = current_app.config["DATA_KEY"]
            for upload in uploads:
                storage_key = save_encrypted_voucher(
                    current_app.config["STORAGE_DIR"], key, order_id, upload.data
                )
                created.append(storage_key)
                encrypted_name = encrypt_bytes(
                    key, upload.original_name.encode("utf-8"),
                    voucher_name_aad(order_id, storage_key),
                )
                conn.execute(
                    """INSERT INTO order_vouchers
                       (order_id, storage_key, name_ciphertext, size_bytes, document_type)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (order_id, storage_key, encrypted_name, upload.size_bytes, document_type),
                )
    except Exception:
        for storage_key in created:
            remove_encrypted_voucher(current_app.config["STORAGE_DIR"], storage_key)
        raise
    flash(f"已上传 {len(uploads)} 个文件", "success")
    return _order_back(customer_id, order_id)


@blueprint.get("/customers/<int:customer_id>/orders/<int:order_id>/vouchers/<int:voucher_id>/download")
@login_required
def download_voucher(customer_id: int, order_id: int, voucher_id: int):
    with connect() as conn:
        _get_order(conn, customer_id, order_id)
        row = conn.execute(
            "SELECT storage_key, name_ciphertext FROM order_vouchers WHERE id = %s AND order_id = %s",
            (voucher_id, order_id),
        ).fetchone()
    if not row:
        abort(404)
    path = voucher_path(current_app.config["STORAGE_DIR"], row["storage_key"])
    if not path.is_file():
        abort(404)
    name = decrypt_bytes(
        current_app.config["DATA_KEY"], row["name_ciphertext"],
        voucher_name_aad(order_id, row["storage_key"]),
    ).decode("utf-8")
    data = read_encrypted_voucher(
        current_app.config["STORAGE_DIR"], current_app.config["DATA_KEY"],
        order_id, row["storage_key"],
    )
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })


@blueprint.post("/customers/<int:customer_id>/orders/<int:order_id>/vouchers/<int:voucher_id>/delete")
@login_required
def delete_voucher(customer_id: int, order_id: int, voucher_id: int):
    with connect() as conn:
        _get_order(conn, customer_id, order_id, lock=True)
        row = conn.execute(
            "DELETE FROM order_vouchers WHERE id = %s AND order_id = %s RETURNING storage_key",
            (voucher_id, order_id),
        ).fetchone()
        if not row:
            abort(404)
    try:
        remove_encrypted_voucher(current_app.config["STORAGE_DIR"], row["storage_key"])
    except OSError:
        current_app.logger.exception("Failed to remove deleted order document")
    flash("文件已删除", "success")
    return _order_back(customer_id, order_id)

