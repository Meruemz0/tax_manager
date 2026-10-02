"""Authenticated API to create a complete customer record in one request."""

import json

from tax_manager.web import Blueprint, current_app, jsonify, request, url_for
from psycopg.errors import UniqueViolation

from tax_manager.auth import login_required
from tax_manager.customer_files.storage import (
    InvalidOtherFile, read_other_file, remove_other_file, save_other_file,
)
from tax_manager.customers import (
    ensure_monthly_bookkeeping, ensure_monthly_filings, validate_customer_choices,
)
from tax_manager.customers.validation import (
    CHINA_TZ, CustomerInput, ValidationError, china_now, china_today,
    optional_choice_id, validate_filing_month,
)
from tax_manager.db import connect
from tax_manager.customer_accounts import AccountInput, add_account
from tax_manager.orders import add_order, add_receipt
from tax_manager.orders.storage import (
    remove_encrypted_voucher, save_encrypted_voucher, voucher_name_aad,
)
from tax_manager.orders.validation import OrderInput, ReceiptInput
from tax_manager.security.crypto import encrypt_bytes


blueprint = Blueprint("customer_api", __name__)
CUSTOMER_FIELDS = {
    "name", "tax_identifier", "contact_name", "contact_phone", "note",
    "registered_on", "registered_at", "is_available",
    "taxpayer_identity_id", "service_type_id", "customer_source_id",
    "monthly_bookkeeping", "monthly_filings", "general_tag_ids",
    "bookkeeping_start_month", "system_accounts", "orders",
}


def request_payload() -> dict:
    if request.is_json:
        payload = request.get_json(silent=True)
    elif request.mimetype == "multipart/form-data":
        raw = request.form.get("payload")
        if raw is None:
            payload = dict(request.form)
        else:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError("payload 必须是有效 JSON 对象") from exc
    else:
        raise ValidationError("请使用 JSON 或 multipart/form-data")
    if not isinstance(payload, dict):
        raise ValidationError("请求内容必须是 JSON 对象")
    unknown = set(payload) - CUSTOMER_FIELDS
    if unknown:
        raise ValidationError(f"未知字段：{', '.join(sorted(unknown))}")
    return payload


def monthly_states(raw, registered_on, label: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 1200:
        raise ValidationError(f"{label}应为月份到布尔值的对象，最多 1200 个月份")
    result = {}
    for raw_month, value in raw.items():
        if not isinstance(raw_month, str) or not isinstance(value, bool):
            raise ValidationError(f"{label}的月份和状态无效")
        month = validate_filing_month(raw_month, registered_on, china_today())
        result[month] = value
    return result


@blueprint.post("/api/customers")
@login_required
def create_customer():
    created_files: list[str] = []
    created_vouchers: list[str] = []
    committed = False
    try:
        payload = request_payload()
        data = CustomerInput.from_form(payload)
        registered_at = data.registered_at or china_now()
        registered_on = registered_at.astimezone(CHINA_TZ).date()
        booked = monthly_states(payload.get("monthly_bookkeeping"), registered_on, "monthly_bookkeeping")
        filed = monthly_states(payload.get("monthly_filings"), registered_on, "monthly_filings")
        raw_general_ids = payload.get("general_tag_ids", [])
        if not isinstance(raw_general_ids, list):
            raise ValidationError("general_tag_ids 应为数组")
        general_ids = {optional_choice_id(value, "其他标签") for value in raw_general_ids}
        if None in general_ids or len(general_ids) > 100:
            raise ValidationError("其他标签选项无效或数量过多")
        if request.files.getlist("images"):
            raise ValidationError("图片上传功能已取消")
        raw_accounts = payload.get("system_accounts", [])
        if not isinstance(raw_accounts, list) or len(raw_accounts) > 10 or any(not isinstance(item, dict) for item in raw_accounts):
            raise ValidationError("system_accounts 应为最多 10 项的数组")
        for item in raw_accounts:
            AccountInput.from_mapping(item)
        raw_orders = payload.get("orders", [])
        if not isinstance(raw_orders, list) or len(raw_orders) > 100 or any(not isinstance(item, dict) for item in raw_orders):
            raise ValidationError("orders 应为最多 100 项的数组")
        voucher_uploads = []
        for index, item in enumerate(raw_orders):
            order = OrderInput.from_mapping(item)
            receipts = item.get("receipts", [])
            if not isinstance(receipts, list) or any(not isinstance(value, dict) for value in receipts):
                raise ValidationError("订单 receipts 应为数组")
            total = sum((ReceiptInput.from_mapping(value).amount for value in receipts), 0)
            if total > order.amount:
                raise ValidationError("订单收款总额不能超过订单金额")
            voucher_uploads.append([
                read_other_file(file.stream, file.filename)
                for file in request.files.getlist(f"order_vouchers_{index}") if file.filename
            ])
        other_uploads = [
            read_other_file(file.stream, file.filename)
            for file in request.files.getlist("other_files") if file.filename
        ]
        with connect() as conn:
            validate_customer_choices(conn, data)
            for category_id in general_ids:
                if not conn.execute(
                    "SELECT id FROM tag_categories WHERE id = %s AND kind = 'general'",
                    (category_id,),
                ).fetchone():
                    raise ValidationError("其他标签选项无效")
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
            customer_id = row["id"]
            ensure_monthly_filings(conn, customer_id)
            ensure_monthly_bookkeeping(conn, customer_id)
            for month, status in filed.items():
                conn.execute(
                    """INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (customer_id, tax_month)
                       DO UPDATE SET is_filed = EXCLUDED.is_filed, updated_at = now()""",
                    (customer_id, month, status),
                )
            for month, status in booked.items():
                conn.execute(
                    """INSERT INTO monthly_bookkeeping (customer_id, book_month, is_booked)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (customer_id, book_month)
                       DO UPDATE SET is_booked = EXCLUDED.is_booked, updated_at = now()""",
                    (customer_id, month, status),
                )
            for category_id in general_ids:
                conn.execute(
                    """INSERT INTO customer_tag_categories (customer_id, category_id)
                       VALUES (%s, %s)""",
                    (customer_id, category_id),
                )
            for account_values in raw_accounts:
                add_account(conn, customer_id, account_values)
            for index, order_values in enumerate(raw_orders):
                order_id = add_order(conn, customer_id, order_values)
                for receipt_values in order_values.get("receipts", []):
                    add_receipt(conn, customer_id, order_id, receipt_values)
                for upload in voucher_uploads[index]:
                    storage_key = save_encrypted_voucher(
                        current_app.config["STORAGE_DIR"], current_app.config["DATA_KEY"],
                        order_id, upload.data,
                    )
                    created_vouchers.append(storage_key)
                    name_ciphertext = encrypt_bytes(
                        current_app.config["DATA_KEY"], upload.original_name.encode("utf-8"),
                        voucher_name_aad(order_id, storage_key),
                    )
                    conn.execute(
                        """INSERT INTO order_vouchers
                           (order_id, storage_key, name_ciphertext, size_bytes, document_type)
                           VALUES (%s, %s, %s, %s, 'voucher')""",
                        (order_id, storage_key, name_ciphertext, upload.size_bytes),
                    )
            for upload in other_uploads:
                key = save_other_file(upload)
                created_files.append(key)
                conn.execute(
                    """INSERT INTO customer_files
                       (customer_id, storage_key, original_name, size_bytes)
                       VALUES (%s, %s, %s, %s)""",
                    (customer_id, key, upload.original_name, upload.size_bytes),
                )
        committed = True
    except (ValidationError, InvalidOtherFile, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except UniqueViolation:
        return jsonify({"error": "税号或标签已存在"}), 409
    except Exception:
        raise
    finally:
        # A successful transaction keeps the files; failed writes roll back metadata.
        if not committed:
            for key in created_vouchers:
                try:
                    remove_encrypted_voucher(current_app.config["STORAGE_DIR"], key)
                except OSError:
                    current_app.logger.exception("Failed to clean up voucher after API failure")
            for key in created_files:
                try:
                    remove_other_file(key)
                except OSError:
                    current_app.logger.exception("Failed to clean up other file after API failure")
    response = jsonify({"id": customer_id, "url": url_for("customers.detail", customer_id=customer_id)})
    response.status_code = 201
    response.headers["Location"] = url_for("customers.detail", customer_id=customer_id)
    return response
