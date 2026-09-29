from tax_manager.app import create_app


def test_tag_category_api_requires_login(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    response = app.test_client().get("/api/tag-categories")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_tag_category_api_creates_user_defined_category(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    inserted = []

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result({"id": 1, "username": "wfg1"})
            if "INSERT INTO tag_categories" in statement:
                inserted.append(params)
                return Result({"id": 3, "name": "税务类型"})
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/api/tag-categories",
        json={"name": "  税务类型  "},
        headers={"X-CSRF-Token": "token"},
    )
    assert response.status_code == 201
    assert response.get_json() == {"id": 3, "name": "税务类型"}
    assert inserted == [("税务类型",)]


def test_tag_management_page_has_create_form(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

    class Result:
        def fetchone(self):
            return {"id": 1, "username": "wfg1"}

        def fetchall(self):
            return [{"id": 3, "kind": "taxpayer_identity", "name": "一般纳税人"}]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, params=None):
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/tags")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="name"' in html
    assert "一般纳税人" in html


def test_customer_can_be_assigned_a_tag_category(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    inserted = []

    class Result:
        def fetchone(self):
            return {"id": 7, "username": "wfg1"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, params=None):
            if "INSERT INTO customer_tag_categories" in statement:
                inserted.append(params)
            return Result()

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["csrf_token"] = "token"
    response = client.post(
        "/customers/7/tag-categories",
        data={"csrf_token": "token", "category_id": "3"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customers/7")
    assert inserted == [(7, 3)]


def test_customer_detail_shows_assigned_and_available_categories(tmp_path, monkeypatch):
    from datetime import date, datetime, timezone

    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})

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

        def execute(self, statement, params=None):
            if "FROM site_users" in statement:
                return Result(row={"id": 1, "username": "wfg1"})
            if "FROM customers AS c" in statement:
                return Result(row={
                    "id": 7, "name": "测试客户", "registered_on": date(2024, 1, 15),
                    "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                })
            if "FROM tag_categories" in statement and "customer_tag_categories" not in statement:
                return Result(rows=[{"id": 3, "name": "已关联分类"}, {"id": 4, "name": "待关联分类"}])
            if "FROM customer_tag_categories" in statement:
                return Result(rows=[{"id": 3, "name": "已关联分类"}])
            return Result(rows=[])

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = 1
    response = client.get("/customers/7")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "已关联分类" in html and "待关联分类" in html
    assert 'value="4"' in html and 'value="3"' not in html
    assert "/customers/7/tag-categories/3/delete" in html


def test_tag_kinds_distinguish_three_single_choice_fields_from_general_tags():
    from tax_manager.tags import category_kind

    assert category_kind("taxpayer_identity") == "taxpayer_identity"
    assert category_kind("service_type") == "service_type"
    assert category_kind("customer_source") == "customer_source"
    assert category_kind(None) == "general"
    assert category_kind("filing") is None
