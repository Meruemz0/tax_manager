"""Run isolated browser checks: uv run --frozen --with playwright==1.55.0 python tests/check_contract_browser.py."""
import asyncio
from base64 import b64encode
from io import BytesIO
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg
from itsdangerous import TimestampSigner
from playwright.sync_api import sync_playwright
import uvicorn
from tax_manager.app import create_app

def main():
    templates = {
        1: {"id": 1, "title": "正文填写测试", "description": "独立合同",
            "body": "委托方：{{customer_name}}\n乙方：{{provider_name}}\n再次确认：{{customer_name}}\n金额：{{amount}}元\n地点：________",
            "fields": []},
        2: {"id": 2, "title": "第二份合同", "description": "",
            "body": "服务：{{service_name}}", "fields": []},
    }
    class Result:
        def __init__(self, row=None, rows=None):
            self.row, self.rows = row, rows or []
        def fetchone(self): return self.row
        def fetchall(self): return self.rows
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None):
            if sql.startswith("SET LOCAL"): return Result()
            if "FROM site_users" in sql: return Result({"id": 1, "username": "tester"})
            if "FROM contract_templates WHERE id" in sql:
                return Result(templates.get(params[0]))
            if "FROM contract_templates" in sql:
                return Result(rows=list(templates.values()))
            if sql.startswith("INSERT INTO contract_templates"):
                template_id = max(templates) + 1
                templates[template_id] = dict(zip(("title", "description", "body"), params[:3]))
                templates[template_id].update(id=template_id, fields=params[3].obj)
                return Result()
            raise AssertionError("Unexpected DB access: " + sql)
    original_connect = psycopg.connect
    psycopg.connect = lambda *args, **kw: Connection()
    server = None
    with tempfile.TemporaryDirectory() as folder:
        try:
            app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused",
                              "STORAGE_DIR": Path(folder) / "uploads"})
            signer = TimestampSigner(str(app.secret_key))
            cookie = signer.sign(b64encode(json.dumps({"user_id": 1, "csrf_token": "test-token"}).encode())).decode()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                address = "http://127.0.0.1:" + str(listener.getsockname()[1])
                server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
                def run_server():
                    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as event_loop:
                        event_loop.run(server.serve(sockets=[listener]))
                thread = threading.Thread(target=run_server, daemon=True)
                thread.start()
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() > deadline: raise AssertionError("Server did not start")
                    time.sleep(.05)
                with sync_playwright() as runner:
                    browser = runner.chromium.launch(headless=True)
                    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
                    context.add_cookies([{"name": "session", "value": cookie, "url": address}])
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(address + "/contracts")
                    page.evaluate("window.documentMarker = 'same-document'")
                    page.locator("[data-contract-select]").first.click()
                    page.locator('[data-contract-field="customer_name"]').first.wait_for()
                    assert page.evaluate("window.documentMarker") == "same-document"
                    assert page.locator("#contract-paper").inner_text().find("再次确认") >= 0
                    assert page.locator("text=受托服务方资料").count() == 0
                    page.locator('[data-contract-field="customer_name"]').first.fill("甲公司<测试>")
                    assert page.locator('[data-contract-field="customer_name"]').nth(1).input_value() == "甲公司<测试>"
                    page.locator('[data-contract-field="provider_name"]').fill("乙公司")
                    page.locator('[data-contract-field="amount"]').fill("2500")
                    page.locator('[data-contract-field="blank_1"]').fill("苏州")
                    assert "4 / 4" in page.locator("#contract-fill-status").inner_text()
                    for kind in ("docx", "pdf"):
                        with page.expect_download() as pending:
                            page.locator('button[name="format"][value="' + kind + '"]').click()
                        download = pending.value
                        data = Path(download.path()).read_bytes()
                        if kind == "docx":
                            with ZipFile(BytesIO(data)) as archive:
                                xml = archive.read("word/document.xml").decode()
                                assert xml.count("甲公司&lt;测试&gt;") == 2
                                assert "苏州" in xml and "2500.00" in xml
                                assert "{{customer_name}}" not in xml
                        else:
                            assert data.startswith(b"%PDF-") and b"/FontFile2" in data
                        assert page.evaluate("window.documentMarker") == "same-document"
                    # A template edited in another tab must be reloadable without losing unchanged blanks.
                    old_version = page.locator('[name="version"]').input_value()
                    templates[1]["body"] += "\n新增条款：请再次核对。"
                    page.locator('button[name="format"][value="docx"]').click()
                    page.locator("#contract-error").wait_for(state="visible")
                    assert "模板已经修改" in page.locator("#contract-error").inner_text()
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("[data-contract-select]").first.click()
                    page.get_by_text("新增条款：请再次核对。", exact=True).wait_for()
                    assert page.locator('[name="version"]').input_value() != old_version
                    assert page.locator('[data-contract-field="customer_name"]').first.input_value() == "甲公司<测试>"
                    # Switching prompts before losing unsaved input.
                    page.once("dialog", lambda dialog: dialog.dismiss())
                    page.locator("[data-contract-select]").nth(1).click()
                    assert page.locator('[data-contract-field="customer_name"]').count() == 2
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("[data-contract-select]").nth(1).click()
                    page.locator('[data-contract-field="service_name"]').wait_for()
                    page.locator("[data-check-contract]").click()
                    assert page.locator(".contract-blank-missing").count() == 1
                    page.screenshot(path=str(Path(folder) / "workspace.png"), full_page=True)
                    # Configure a new blank and repeat it, then save and fill the result.
                    page.goto(address + "/contract-templates/new")
                    page.locator('[name="title"]').fill("新增可配置模板")
                    page.locator("[data-template-body]").fill("地点：")
                    page.locator("[data-add-contract-field]").click()
                    row = page.locator("[data-field-row]").last
                    row.locator('[name="field_label"]').fill("签订地点")
                    row.locator("[data-insert-contract-field]").click()
                    assert page.locator("[data-template-body]").input_value() == "地点：{{field_1}}{{field_1}}"
                    # Add/remove a second blank; it must disappear from body and definitions.
                    page.locator("[data-add-contract-field]").click()
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("[data-field-row]").last.locator("[data-remove-contract-field]").click()
                    assert "{{field_2}}" not in page.locator("[data-template-body]").input_value()
                    page.get_by_role("button", name="保存模板").click()
                    page.wait_for_url("**/contract-templates")
                    saved = templates[3]
                    assert saved["fields"] == [{"key": "field_1", "label": "签订地点", "type": "text"}]
                    page.goto(address + "/contracts?template_id=3")
                    page.locator('[data-contract-field="field_1"]').first.fill("上海")
                    assert page.locator('[data-contract-field="field_1"]').last.input_value() == "上海"
                    templates[4] = {"id": 4, "title": "自定义标识合同", "description": "",
                        "body": "填写值：{{constructor}}", "fields": [
                            {"key": "constructor", "label": "填写值", "type": "text"}]}
                    page.goto(address + "/contracts?template_id=4")
                    page.locator('[data-contract-field="constructor"]').fill("完整保留")
                    assert "1 / 1" in page.locator("#contract-fill-status").inner_text()
                    with page.expect_download() as pending:
                        page.locator('button[name="format"][value="docx"]').click()
                    with ZipFile(pending.value.path()) as archive:
                        assert "完整保留" in archive.read("word/document.xml").decode()
                    # Server rendering and the compact layout also work on a narrow screen.
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert not errors, errors
                    browser.close()
                    server.should_exit = True
                    thread.join(timeout=10)
                    assert not thread.is_alive(), "Test server did not stop"
                    server = None
                    print("Browser checks passed: select without reload, full body, synced blanks, DOCX/PDF, switch confirmation, template field CRUD, mobile layout.")
        finally:
            if server:
                server.should_exit = True
                thread.join(timeout=10)
            psycopg.connect = original_connect

if __name__ == "__main__":
    main()
