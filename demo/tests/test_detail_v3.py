from datetime import date, datetime, timezone

from tax_manager.web import render_template

from tax_manager.app import create_app


def test_customer_detail_keeps_images_and_shows_new_fields_history_and_files(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {
        "id": 7, "name": "甲客户", "tax_identifier": "T-7", "contact_name": "张先生",
        "contact_phone": "123", "note": "完整备注内容", "registered_on": date(2024, 1, 15),
        "registered_at": datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc),
        "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "is_available": False, "taxpayer_identity": "一般纳税人",
        "service_type": "代账", "customer_source": "转介绍",
    }
    history = [
        {"month": date(2024, 2, 1), "is_booked": True, "is_filed": False},
        {"month": date(2024, 1, 1), "is_booked": False, "is_filed": True},
    ]
    with app.test_request_context("/customers/7"):
        html = render_template(
            "customers/detail.html", customer=customer, history=history, images=[{
                "id": 5, "original_name": "截图.png", "mime_type": "image/png", "size_bytes": 42,
            }], files=[{"id": 8, "original_name": "合同.bin", "size_bytes": 88}],
            assigned_categories=[], available_categories=[],
        ).body.decode()
    for value in ("不可用", "一般纳税人", "代账", "转介绍", "完整备注内容", "已记账", "已报税", "截图.png", "合同.bin"):
        assert value in html
    assert "/customers/7/bookkeeping" in html
    assert "/customers/7/files/8/download" in html
