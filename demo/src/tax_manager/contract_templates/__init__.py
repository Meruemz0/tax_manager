"""Independent contract filling, reusable templates and document exports."""

import hashlib
import json
from urllib.parse import quote

from fastapi.responses import Response
from psycopg.types.json import Jsonb

from tax_manager.auth import login_required
from tax_manager.contract_templates.documents import build_docx, build_pdf
from tax_manager.contract_templates.schema import FIELD_TYPES, compile_template, document_lines, fill_contract
from tax_manager.db import connect
from tax_manager.web import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

blueprint = Blueprint("contract_templates", __name__)
NEW_BODY = ("合同编号：{{contract_number}}\n委托方：{{customer_name}}\n"
            "受托方：{{provider_name}}\n服务内容：{{service_name}}\n"
            "金额：{{amount}}元\n签订日期：{{sign_date}}")

def _back():
    return redirect(url_for("contract_templates.index"))

def _template(conn, template_id):
    row = conn.execute(
        "SELECT id, title, description, body, fields FROM contract_templates WHERE id = %s AND deleted_at IS NULL",
        (template_id,),
    ).fetchone()
    if not row:
        abort(404)
    item = dict(row)
    item["body"], item["fields"] = compile_template(item["body"], item.get("fields", []))
    return item

def _all_templates(conn):
    return conn.execute(
        "SELECT id, title, description FROM contract_templates WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()

def _payload(item):
    content = {"title": item["title"], "body": item["body"], "fields": item["fields"]}
    version = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"id": item["id"], **content, "lines": document_lines(item["body"], item["fields"]),
            "version": version, "generate_url": url_for("contract_templates.generate", template_id=item["id"])}

def _definitions(form):
    keys = form.getlist("field_key")
    labels = form.getlist("field_label")
    types = form.getlist("field_type")
    if len(keys) != len(labels) or len(keys) != len(types):
        raise ValueError("空格配置不完整")
    return [{"key": key, "label": label, "type": kind}
            for key, label, kind in zip(keys, labels, types)]

def _template_input(form):
    title, description = (form.get("title") or "").strip(), (form.get("description") or "").strip()
    if not title or len(title) > 100 or len(description) > 255:
        raise ValueError("标题不能为空且最多 100 字，说明最多 255 字")
    body, fields = compile_template((form.get("body") or "").strip(), _definitions(form))
    return title, description, body, fields

def _form(item, action, is_copy=False, status=200):
    return render_template(
        "contract_templates/form.html", template=item, action=action,
        is_copy=is_copy, field_types=FIELD_TYPES,
    ), status

def _invalid_form(exc, action):
    flash(str(exc), "error")
    item = dict(request.form)
    try:
        item["fields"] = _definitions(request.form)
    except ValueError:
        item["fields"] = []
    return _form(item, action, status=400)

def _document_response(title, body, kind, filename):
    if kind == "docx":
        payload = build_docx(title, body)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif kind == "pdf":
        payload, media_type = build_pdf(title, body), "application/pdf"
    else:
        abort(400)
    return Response(payload, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "private, no-store",
    })

@blueprint.get("/contracts")
@login_required
def workspace():
    raw_id = request.args.get("template_id")
    selected = None
    with connect() as conn:
        templates = _all_templates(conn)
        if raw_id:
            try:
                template_id = int(raw_id)
            except ValueError:
                abort(400)
            selected = _payload(_template(conn, template_id))
    return render_template("contract_templates/workspace.html", templates=templates, selected=selected)

@blueprint.get("/api/contract-templates/<int:template_id>/document")
@login_required
def document(template_id):
    with connect() as conn:
        payload = _payload(_template(conn, template_id))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response

@blueprint.get("/contract-templates")
@login_required
def index():
    with connect() as conn:
        templates = _all_templates(conn)
    return render_template("contract_templates/index.html", templates=templates)

@blueprint.get("/contract-templates/new")
@login_required
def new():
    copy_id = request.args.get("copy")
    if copy_id:
        try:
            value = int(copy_id)
        except ValueError:
            abort(400)
        with connect() as conn:
            source = _template(conn, value)
    else:
        body, fields = compile_template(NEW_BODY)
        source = {"title": "", "description": "", "body": body, "fields": fields}
    return _form(source, url_for("contract_templates.create"), is_copy=bool(copy_id))

@blueprint.post("/contract-templates")
@login_required
def create():
    action = url_for("contract_templates.create")
    try:
        title, description, body, fields = _template_input(request.form)
    except ValueError as exc:
        return _invalid_form(exc, action)
    with connect() as conn:
        conn.execute(
            "INSERT INTO contract_templates (title, description, body, fields) VALUES (%s, %s, %s, %s)",
            (title, description, body, Jsonb(fields)),
        )
    flash("合同模板已创建", "success")
    return _back()

@blueprint.get("/contract-templates/<int:template_id>/edit")
@login_required
def edit(template_id):
    with connect() as conn:
        item = _template(conn, template_id)
    return _form(item, url_for("contract_templates.update", template_id=template_id))

@blueprint.post("/contract-templates/<int:template_id>/edit")
@login_required
def update(template_id):
    action = url_for("contract_templates.update", template_id=template_id)
    with connect() as conn:
        _template(conn, template_id)
        try:
            title, description, body, fields = _template_input(request.form)
        except ValueError as exc:
            return _invalid_form(exc, action)
        conn.execute(
            """UPDATE contract_templates SET title = %s, description = %s, body = %s,
               fields = %s, updated_at = now() WHERE id = %s AND deleted_at IS NULL""",
            (title, description, body, Jsonb(fields), template_id),
        )
    flash("合同模板已保存", "success")
    return _back()

@blueprint.post("/contract-templates/<int:template_id>/delete")
@login_required
def delete(template_id):
    with connect() as conn:
        _template(conn, template_id)
        conn.execute(
            "UPDATE contract_templates SET deleted_at = now(), updated_at = now() WHERE id = %s",
            (template_id,),
        )
    flash("合同模板已删除", "success")
    return _back()

@blueprint.get("/contract-templates/<int:template_id>/download/<kind>")
@login_required
def download_blank(template_id, kind):
    with connect() as conn:
        item = _template(conn, template_id)
    try:
        body = fill_contract(item["body"], item["fields"], {})
        return _document_response(item["title"], body, kind, f"{item['title']}-空白模板.{kind}")
    except ValueError as exc:
        abort(400, str(exc))

@blueprint.get("/customers/<int:customer_id>/orders/<int:order_id>/contract")
@login_required
def prepare(customer_id, order_id):
    return redirect(url_for("contract_templates.workspace"))

@blueprint.post("/contracts/<int:template_id>/generate")
@login_required
def generate(template_id):
    with connect() as conn:
        item = _template(conn, template_id)
    payload = _payload(item)
    if request.is_json:
        incoming = request.get_json(silent=True)
        if not isinstance(incoming, dict):
            return jsonify({"error": "填写内容无效"}), 400
        kind, version, values = incoming.get("format"), incoming.get("version"), incoming.get("values")
    else:
        kind, version = request.form.get("format"), request.form.get("version")
        values = {field["key"]: request.form.get("value__" + field["key"], "") for field in item["fields"]}
    if version != payload["version"]:
        return jsonify({"error": "模板已经修改，请重新选择模板并核对内容"}), 409
    if not isinstance(kind, str) or kind not in {"docx", "pdf"}:
        return jsonify({"error": "文件格式无效"}), 400
    try:
        body = fill_contract(item["body"], item["fields"], values)
        return _document_response(item["title"], body, kind, f"{item['title']}.{kind}")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
