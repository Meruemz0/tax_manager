from datetime import date, datetime, timezone

from tax_manager.app import create_app
from tax_manager.web import render_template


def test_deactivation_timestamp_tracks_only_the_current_unavailable_period():
    from tax_manager.customers.availability import deactivated_at_for_change

    first = datetime(2026, 9, 30, 8, 35, tzinfo=timezone.utc)
    second = datetime(2026, 10, 1, 4, 12, tzinfo=timezone.utc)
    assert deactivated_at_for_change(True, False, None, first) == first
    assert deactivated_at_for_change(False, False, first, second) == first
    assert deactivated_at_for_change(False, True, first, second) is None
    assert deactivated_at_for_change(True, False, None, second) == second


def test_customer_detail_shows_china_hour_only_when_unavailable(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    customer = {
        "id": 7, "name": "customer", "registered_on": date(2024, 1, 15),
        "is_available": False,
        "deactivated_at": datetime(2026, 9, 30, 8, 35, tzinfo=timezone.utc),
    }
    with app.test_request_context("/customers/7"):
        html = render_template(
            "customers/detail.html", customer=customer, history=[], files=[],
            assigned_categories=[], available_categories=[],
        ).body.decode()
    assert "\u6ce8\u9500\u65f6\u95f4\uff1a2026-09-30 16\u65f6" in html
    assert "16:35" not in html
    assert "\u5ba2\u6237\u56fe\u7247" not in html
    assert "\u4e0a\u4f20\u56fe\u7247" not in html
    customer["deactivated_at"] = None
    with app.test_request_context("/customers/7"):
        legacy_html = render_template(
            "customers/detail.html", customer=customer, history=[], files=[],
            assigned_categories=[], available_categories=[],
        ).body.decode()
    assert "\u6ce8\u9500\u65f6\u95f4\uff1a\u672a\u8bb0\u5f55" in legacy_html
    customer["is_available"] = True
    with app.test_request_context("/customers/7"):
        available_html = render_template(
            "customers/detail.html", customer=customer, history=[], files=[],
            assigned_categories=[], available_categories=[],
        ).body.decode()
    assert "\u6ce8\u9500\u65f6\u95f4" not in available_html


def test_removed_image_routes_are_not_exposed(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    client = app.test_client()
    for path in ("/unassigned", "/unassigned/images", "/customers/7/images", "/images/customer/3/view"):
        assert client.get(path).status_code == 404
    assert "\u56fe\u7247\u5e93" not in client.get("/").get_data(as_text=True)
