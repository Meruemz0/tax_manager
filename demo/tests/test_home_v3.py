from datetime import date

import pytest

from tax_manager.customers.search import build_home_query, parse_home_filters
from tax_manager.customers.validation import ValidationError


def test_home_defaults_to_china_month_and_only_available_registered_customers():
    filters = parse_home_filters({}, date(2026, 9, 29))
    sql, params = build_home_query(filters)
    assert filters.month == date(2026, 9, 1)
    assert "c.is_available = TRUE" in sql
    assert "c.registered_at AT TIME ZONE 'Asia/Shanghai'" in sql
    assert date(2026, 10, 1) in params
    assert "monthly_bookkeeping" in sql and "monthly_filings" in sql


def test_home_filters_search_all_customers_by_selected_values_and_date_range():
    filters = parse_home_filters({
        "month": "2026-09", "taxpayer_identity_id": "3",
        "service_type_id": "4", "customer_source_id": "5",
        "filing_status": "1", "bookkeeping_status": "0",
        "registered_from": "2026-01-01", "registered_to": "2026-09-29",
    }, date(2026, 9, 29))
    sql, params = build_home_query(filters)
    assert filters.has_criteria
    assert "c.is_available = TRUE" not in sql
    assert date(2026, 10, 1) not in params
    for column in ("taxpayer_identity_id", "service_type_id", "customer_source_id"):
        assert f"c.{column} = %s" in sql
    assert "mf_filter.is_filed = %s" in sql
    assert "mb_filter.is_booked = %s" in sql
    assert "JOIN monthly_filings AS mf_filter" in sql
    assert "JOIN monthly_bookkeeping AS mb_filter" in sql
    assert params.count(date(2026, 9, 1)) == 4
    assert date(2026, 1, 1) in params and date(2026, 9, 29) in params


def test_single_tag_or_date_filter_searches_all_customers():
    for args in ({"service_type_id": "4"}, {"registered_from": "2026-01-01"}):
        sql, params = build_home_query(parse_home_filters(args, date(2026, 9, 29)))
        assert "c.is_available = TRUE" not in sql
        assert date(2026, 10, 1) not in params


def test_monthly_status_filter_requires_an_existing_record_for_that_month():
    filters = parse_home_filters(
        {"month": "2026-08", "filing_status": "0"}, date(2026, 9, 29)
    )
    sql, params = build_home_query(filters)
    assert "JOIN monthly_filings AS mf_filter" in sql
    assert params.count(date(2026, 8, 1)) == 3
    assert "mf_filter.is_filed = %s" in sql
    assert "COALESCE(mf_filter.is_filed, FALSE)" not in sql


@pytest.mark.parametrize("values", [
    {"month": "2026-13"}, {"month": "2026-10"},
    {"service_type_id": "1 OR 1=1"},
    {"filing_status": "maybe"}, {"registered_from": "2026-02-30"},
    {"registered_from": "2026-10-01", "registered_to": "2026-01-01"},
])
def test_home_rejects_invalid_filter_values(values):
    with pytest.raises(ValidationError):
        parse_home_filters(values, date(2026, 9, 29))


def test_logged_in_home_renders_selected_month_columns_and_truncated_note(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    monkeypatch.setattr("tax_manager.customers.china_today", lambda: date(2026, 9, 29))
    statements = []

    class Result:
        def __init__(self, row=None, rows=None):
            self.row, self.rows = row, rows or []

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
            statements.append((sql, params))
            if "FROM site_users" in sql:
                return Result(row={"id": 1, "username": "wfg1"})
            if "SELECT c.id, c.name, c.tax_identifier, c.note" in sql:
                return Result(rows=[{
                    "id": 7, "name": "甲客户", "tax_identifier": "DEMO-1",
                    "is_booked": True, "is_filed": False,
                    "taxpayer_identity": "一般纳税人", "service_type": "代账",
                    "customer_source": "转介绍", "note": "备" * 25,
                }])
            return Result(rows=[])

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/customers?month=2026-09")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for value in ("首页", "本月记账", "本月报税", "一般纳税人", "代账", "转介绍", "已记账", "未报税"):
        assert value in html
    assert "备" * 20 in html and "备" * 21 not in html
    assert any("c.is_available = TRUE" in sql for sql, _ in statements)
    assert 'name="service_type_mode"' not in html
    assert 'name="filing_mode"' not in html
    assert 'data-month-toggle' in html
    assert 'type="month" name="month" value="2026-09"' in html
    assert 'name="filing_month"' not in html
    assert 'name="bookkeeping_month"' not in html
    assert 'class="home-list-stage"' in html



def test_filtered_home_renders_all_customer_result_without_false_monthly_status(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    monkeypatch.setattr("tax_manager.customers.china_today", lambda: date(2026, 9, 29))
    queries = []

    class Result:
        def __init__(self, row=None, rows=None):
            self.row, self.rows = row, rows or []

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
            if "FROM site_users" in sql:
                return Result(row={"id": 1, "username": "wfg1"})
            if "SELECT c.id, c.name, c.tax_identifier, c.note" in sql:
                queries.append((sql, params))
                return Result(rows=[{
                    "id": 18, "name": "later customer", "tax_identifier": None,
                    "note": None, "is_booked": None, "is_filed": None,
                    "taxpayer_identity": None, "service_type": "service choice",
                    "customer_source": None,
                }])
            if "SELECT id, kind, name FROM tag_categories" in sql:
                return Result(rows=[{"id": 4, "kind": "service_type", "name": "service choice"}])
            return Result(rows=[])

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get(
        "/customers?month=2026-09&service_type_id=4&is_available=0",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "later customer" in html
    assert "service choice" in html
    assert html.count('<span class="badge neutral">—</span>') == 2
    assert "c.is_available = TRUE" not in queries[0][0]
    assert "c.service_type_id = %s" in queries[0][0]
    assert "c.is_available = %s" in queries[0][0]
    assert 4 in queries[0][1] and False in queries[0][1]
    assert 'id="home-results"' in html
    assert 'aria-expanded="true"' in html
    assert 'id="home-filter-panel" hidden' not in html


def test_bookkeeping_update_is_independent_from_filing_and_accepts_historical_month(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    writes = []

    class Result:
        def fetchone(self):
            return {"id": 7, "registered_on": date(2024, 1, 15)}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            if "INSERT INTO monthly_bookkeeping" in sql:
                writes.append((sql, params))
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/bookkeeping",
        data={"csrf_token": "token", "book_month": "2024-02", "is_booked": "1", "return_to": "detail"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7")
    assert len(writes) == 1
    assert writes[0][1] == (7, date(2024, 2, 1), True)
    assert "monthly_filings" not in writes[0][0]


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False)])
def test_availability_filter_searches_all_customers(value, expected):
    filters = parse_home_filters({"is_available": value}, date(2026, 9, 29))
    sql, params = build_home_query(filters)
    assert filters.has_criteria
    assert "c.is_available = %s" in sql
    assert params[-1] is expected
    assert date(2026, 10, 1) not in params


def test_invalid_availability_filter_is_rejected():
    with pytest.raises(ValidationError):
        parse_home_filters({"is_available": "maybe"}, date(2026, 9, 29))


def test_home_filter_panel_starts_closed_and_async_validation_returns_error(tmp_path, monkeypatch):
    from tax_manager.app import create_app

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    monkeypatch.setattr("tax_manager.customers.china_today", lambda: date(2026, 9, 29))

    class Result:
        def fetchone(self):
            return {"id": 1, "username": "wfg1"}

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    page = client.get("/customers")
    html = page.get_data(as_text=True)
    assert 'data-home-filter-toggle' in html
    assert 'id="home-filter-panel" hidden' in html
    assert 'name="is_available"' in html
    invalid = client.get("/customers?is_available=maybe", headers={"X-Requested-With": "XMLHttpRequest"})
    assert invalid.status_code == 400
    assert "error" in invalid.get_json()
