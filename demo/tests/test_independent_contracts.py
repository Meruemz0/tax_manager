from io import BytesIO
from zipfile import ZipFile
import pytest

def test_custom_blanks_and_legacy_lines_are_fillable_and_repeated_values_match():
    from tax_manager.contract_templates.schema import compile_template, fill_contract
    body, fields = compile_template(
        "地点：{{location}}，再次确认：{{location}}\n其他：________",
        [{"key": "location", "label": "签订地点", "type": "text"}],
    )
    assert len(fields) == 2
    assert fields[0] == {"key": "location", "label": "签订地点", "type": "text"}
    result = fill_contract(body, fields, {"location": "苏州"})
    assert result == "地点：苏州，再次确认：苏州\n其他：________"
    assert "________" not in body

def test_contract_fields_reject_ambiguous_definitions_and_invalid_typed_values():
    from tax_manager.contract_templates.schema import compile_template, fill_contract
    with pytest.raises(ValueError):
        compile_template("{{office}}", [])
    with pytest.raises(ValueError):
        compile_template("{{office}}", [{"key": "office", "label": "地点", "type": "script"}])
    body, fields = compile_template("费用{{fee}}日期{{when}}", [
        {"key": "fee", "label": "费用", "type": "money"},
        {"key": "when", "label": "日期", "type": "date"},
    ])
    with pytest.raises(ValueError):
        fill_contract(body, fields, {"fee": "-1", "when": "2026-10-02"})
    with pytest.raises(ValueError):
        fill_contract(body, fields, {"fee": "100", "when": "2026-02-30"})
    assert fill_contract(body, fields, {"fee": "100", "when": ""}) == "费用100.00日期________"

@pytest.fixture
def contract_client(tmp_path, monkeypatch):
    from tax_manager.app import create_app
    row = {"id": 2, "title": "独立测试合同", "description": "任意双方均可填写",
           "body": "委托方：{{customer_name}}\n乙方：{{provider_name}}\n地点：{{place}}\n金额：{{amount}}",
           "fields": [{"key": "place", "label": "签订地点", "type": "text"}]}
    class Result:
        def __init__(self, item=None, rows=None):
            self.item, self.rows = item, rows or []
        def fetchone(self): return self.item
        def fetchall(self): return self.rows
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def execute(self, sql, params=None):
            if sql.startswith("SET LOCAL"): return Result()
            if "FROM site_users" in sql: return Result({"id": 1, "username": "wfg1"})
            if "FROM contract_templates WHERE id" in sql: return Result(row if params == (2,) else None)
            if "FROM contract_templates" in sql: return Result(rows=[row])
            if sql.startswith("INSERT INTO contract_templates") or sql.startswith("UPDATE contract_templates"):
                return Result()
            raise AssertionError("Independent contracts must not access customer/order/profile tables: " + sql)
    monkeypatch.setattr("psycopg.connect", lambda *args, **kw: Connection())
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["csrf_token"] = "token"
    return client

def test_independent_workspace_displays_full_contract_and_inline_blanks(contract_client):
    response = contract_client.get("/contracts?template_id=2")
    assert response.status_code == 200
    assert "独立测试合同" in response.text
    assert 'data-contract-field="customer_name"' in response.text
    assert 'data-contract-field="place"' in response.text
    assert "受托服务方资料" not in response.text
    doc = contract_client.get("/api/contract-templates/2/document").json()
    assert any(field["label"] == "签订地点" for field in doc["fields"])

def test_independent_export_uses_only_filled_values_and_does_not_modify_template(contract_client):
    document = contract_client.get("/api/contract-templates/2/document").json()
    values = {"customer_name": "直接填写甲公司", "provider_name": "乙公司", "place": "苏州", "amount": "2500"}
    for kind, magic in (("docx", b"PK"), ("pdf", b"%PDF-")):
        response = contract_client.post("/contracts/2/generate", json={
            "format": kind, "values": values, "version": document["version"],
        }, headers={"X-CSRF-Token": "token"})
        assert response.status_code == 200
        assert response.content.startswith(magic)
        assert response.headers["Cache-Control"] == "private, no-store"
        if kind == "docx":
            with ZipFile(BytesIO(response.content)) as package:
                xml = package.read("word/document.xml").decode()
                assert "直接填写甲公司" in xml and "苏州" in xml and "2500.00" in xml
                assert "{{customer_name}}" not in xml
    assert contract_client.get("/api/contract-templates/2/document").json()["version"] == document["version"]
    stale = contract_client.post("/contracts/2/generate", json={
        "format": "docx", "values": values, "version": "stale",
    }, headers={"X-CSRF-Token": "token"})
    assert stale.status_code == 409

def test_template_can_save_new_custom_blank_definitions(contract_client):
    response = contract_client.post("/contract-templates", data={
        "csrf_token": "token", "title": "自定义合同", "body": "地点：{{custom_place}}",
        "field_key": "custom_place", "field_label": "地点", "field_type": "text",
    })
    assert response.status_code == 302

def test_contract_pages_and_export_require_login_and_csrf(contract_client):
    document = contract_client.get("/api/contract-templates/2/document").json()
    response = contract_client.post("/contracts/2/generate", json={
        "format": "docx", "version": document["version"], "values": {},
    })
    assert response.status_code == 400
    with contract_client.session_transaction() as state:
        state.clear()
    for url in ("/contracts", "/api/contract-templates/2/document", "/contract-templates"):
        assert contract_client.get(url).status_code == 302

def test_export_rejects_non_string_format_without_internal_error(contract_client):
    document = contract_client.get("/api/contract-templates/2/document").json()
    response = contract_client.post("/contracts/2/generate", json={
        "format": ["docx"], "version": document["version"], "values": {},
    }, headers={"X-CSRF-Token": "token"})
    assert response.status_code == 400

def test_field_type_must_be_a_string():
    from tax_manager.contract_templates.schema import compile_template
    with pytest.raises(ValueError):
        compile_template("{{place}}", [{"key": "place", "label": "地点", "type": ["text"]}])

def test_underscore_runs_inside_valid_custom_keys_are_preserved():
    from tax_manager.contract_templates.schema import compile_template, fill_contract
    body, fields = compile_template("地点：{{custom____place}}", [
        {"key": "custom____place", "label": "地点", "type": "text"}
    ])
    assert body == "地点：{{custom____place}}"
    assert fill_contract(body, fields, {"custom____place": "上海"}) == "地点：上海"
