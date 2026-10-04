"""Contract template rendering and document generation."""

from io import BytesIO
from zipfile import ZipFile

import pytest


def test_template_tokens_are_validated_and_inserted_as_plain_text():
    from tax_manager.contract_templates.documents import render_tokens, validate_template_body

    body = "委托方：{{customer_name}}\n服务：{{service_name}}\n金额：{{amount}}"
    validate_template_body(body)
    result = render_tokens(body, {
        "customer_name": "示例<客户>",
        "service_name": "代理记账",
        "amount": "1200.00",
    })
    assert result == "委托方：示例<客户>\n服务：代理记账\n金额：1200.00"
    with pytest.raises(ValueError, match="未知"):
        validate_template_body("{{secret_password}}")
    with pytest.raises(ValueError, match="缺少"):
        render_tokens(body, {"customer_name": "仅有名称"})
    with pytest.raises(ValueError, match="占位符"):
        validate_template_body("{{customer_name")


def test_generated_docx_and_pdf_contain_the_rendered_contract():
    from tax_manager.contract_templates.documents import build_docx, build_pdf

    title = "代理记账服务合同"
    body = "委托方：示例客户\n受托方：示例服务方\n\n一、服务范围\n代理记账。"
    docx = build_docx(title, body)
    assert docx[:2] == b"PK"
    with ZipFile(BytesIO(docx)) as package:
        xml = package.read("word/document.xml").decode("utf-8")
        assert "示例客户" in xml
        assert "代理记账。" in xml
    pdf = build_pdf(title, body)
    assert pdf.startswith(b"%PDF-")
    assert b"/FontFile2" in pdf
    assert len(pdf) > 1000
    with pytest.raises(ValueError, match="DOCX"):
        build_pdf("合同", "客户：𠀋")

def test_template_pages_require_login_and_allow_create_and_delete(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    calls = []

    class Result:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))
            if "FROM site_users" in sql:
                return Result({"id": 1, "username": "wfg1"})
            if "FROM contract_templates WHERE id" in sql:
                return Result({"id": 3, "title": "测试模板", "description": "",
                               "body": "客户：{{customer_name}}"})
            if "FROM contract_templates" in sql:
                return Result(rows=[])
            if "FROM contract_party_profile" in sql:
                return Result({"provider_name": "", "provider_tax_id": "",
                               "provider_contact": "", "provider_phone": ""})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    assert client.get("/contract-templates").status_code == 302
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    assert client.get("/contract-templates").status_code == 200
    new_page = client.get("/contract-templates/new")
    assert new_page.status_code == 200
    assert b"{{customer_name}}" in new_page.content
    assert client.get("/contract-templates/3/edit").status_code == 200
    assert client.get("/contract-templates/3/download/docx").content.startswith(b"PK")
    assert client.get("/contract-templates/3/download/pdf").content.startswith(b"%PDF-")
    created = client.post("/contract-templates", data={
        "csrf_token": "token", "title": "自定义模板",
        "description": "测试", "body": "客户：{{customer_name}}",
    })
    assert created.status_code == 302
    assert any("INSERT INTO contract_templates" in sql for sql, _ in calls)
    invalid = client.post("/contract-templates", data={
        "csrf_token": "token", "title": "待修改",
        "description": "", "body": "错误 {{not_a_field}}",
    })
    assert invalid.status_code == 400
    assert "错误 {{not_a_field}}" in invalid.text
    deleted = client.post("/contract-templates/3/delete", data={"csrf_token": "token"})
    assert deleted.status_code == 302
    assert any("SET deleted_at = now()" in sql for sql, _ in calls)


def test_old_order_contract_link_redirects_to_independent_workspace(tmp_path, monkeypatch):
    from tax_manager.app import create_app
    class Result:
        def fetchone(self): return {"id": 1, "username": "wfg1"}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None):
            assert "FROM site_users" in sql or sql.startswith("SET LOCAL")
            return Result()
    monkeypatch.setattr("psycopg.connect", lambda *args, **kw: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/customers/7/orders/8/contract")
    assert response.status_code == 302
    assert response.headers["Location"] == "/contracts"


def test_shipped_contract_samples_have_valid_placeholders():
    import re
    from pathlib import Path
    from tax_manager.contract_templates.documents import validate_template_body

    sql = (Path(__file__).resolve().parents[1] / "upgrade_v7.sql").read_text(encoding="utf-8")
    bodies = re.findall(r"\$template\$\n(.*?)\n\$template\$", sql, re.S)
    assert len(bodies) == 3
    for body in bodies:
        validate_template_body(body)
        assert "{{customer_name}}" in body
        assert "{{provider_name}}" in body
        assert "{{amount}}" in body
        assert "{{sign_date}}" in body
