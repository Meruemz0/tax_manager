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
from tax_manager.images.storage import InvalidImage, read_image, remove_image, save_image


blueprint = Blueprint("customer_api", __name__)
CUSTOMER_FIELDS = {
    "name", "tax_identifier", "contact_name", "contact_phone", "note",
    "registered_on", "registered_at", "is_available",
    "taxpayer_identity_id", "service_type_id", "customer_source_id",
    "monthly_bookkeeping", "monthly_filings", "general_tag_ids",
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
    created_images: list[str] = []
    created_files: list[str] = []
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
        image_uploads = [
            read_image(file.stream, file.filename)
            for file in request.files.getlist("images") if file.filename
        ]
        other_uploads = [
            read_other_file(file.stream, file.filename)
            for file in request.files.getlist("other_files") if file.filename
        ]
        if len(other_uploads) > 10:
            raise ValidationError("每个客户最多保存 10 个其他文件")
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
                   (name, tax_identifier, contact_name, contact_phone, note,
                    registered_on, registered_at, is_available,
                    taxpayer_identity_id, service_type_id, customer_source_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (data.name, data.tax_identifier, data.contact_name, data.contact_phone,
                 data.note, registered_on, registered_at,
                 True if data.is_available is None else data.is_available,
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
            for upload in image_uploads:
                key = save_image("customer", upload)
                created_images.append(key)
                conn.execute(
                    """INSERT INTO customer_images
                       (customer_id, storage_key, original_name, mime_type, size_bytes)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (customer_id, key, upload.original_name, upload.mime_type, upload.size_bytes),
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
    except (ValidationError, InvalidImage, InvalidOtherFile) as exc:
        return jsonify({"error": str(exc)}), 400
    except UniqueViolation:
        return jsonify({"error": "税号或标签已存在"}), 409
    except Exception:
        raise
    finally:
        # A successful transaction keeps the files; failed writes roll back metadata.
        if not committed:
            for key in created_images:
                try:
                    remove_image("customer", key)
                except OSError:
                    current_app.logger.exception("Failed to clean up image after API failure")
            for key in created_files:
                try:
                    remove_other_file(key)
                except OSError:
                    current_app.logger.exception("Failed to clean up other file after API failure")
    response = jsonify({"id": customer_id, "url": url_for("customers.detail", customer_id=customer_id)})
    response.status_code = 201
    response.headers["Location"] = url_for("customers.detail", customer_id=customer_id)
    return response
