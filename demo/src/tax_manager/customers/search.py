"""Validated month and filter parameters for the customer home page."""

from dataclasses import dataclass
from datetime import date
import re
from typing import Mapping

from tax_manager.customers.validation import ValidationError


def month_start(raw: str, *, current: date) -> date:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
        raise ValidationError("月份格式应为 YYYY-MM")
    try:
        month = date.fromisoformat(f"{raw}-01")
    except ValueError as exc:
        raise ValidationError("月份无效") from exc
    if month > current.replace(day=1):
        raise ValidationError("不能选择未来月份")
    return month


def next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def optional_day(raw: str | None) -> date | None:
    if not raw:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValidationError("注册时间格式应为 YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError("注册时间无效") from exc


def optional_id(raw: str | None) -> int | None:
    if not raw:
        return None
    if not raw.isdecimal() or int(raw) <= 0:
        raise ValidationError("标签选项无效")
    return int(raw)


@dataclass(frozen=True)
class HomeFilters:
    month: date
    query: str
    taxpayer_identity_id: int | None
    service_type_id: int | None
    customer_source_id: int | None
    filing_status: bool | None
    bookkeeping_status: bool | None
    registered_from: date | None
    registered_to: date | None
    is_available: bool | None

    @property
    def has_criteria(self) -> bool:
        return bool(
            self.query
            or self.taxpayer_identity_id is not None
            or self.service_type_id is not None
            or self.customer_source_id is not None
            or self.filing_status is not None
            or self.bookkeeping_status is not None
            or self.registered_from is not None
            or self.registered_to is not None
            or self.is_available is not None
        )


def parse_home_filters(args: Mapping[str, str], today: date) -> HomeFilters:
    selected = month_start(args["month"], current=today) if args.get("month") else today.replace(day=1)

    def status(prefix: str) -> bool | None:
        raw = args.get(f"{prefix}_status")
        if raw in (None, ""):
            return None
        if raw not in {"0", "1"}:
            raise ValidationError("月度状态无效")
        return raw == "1"

    filing_status = status("filing")
    bookkeeping_status = status("bookkeeping")
    raw_available = args.get("is_available")
    if raw_available not in (None, "", "0", "1"):
        raise ValidationError("可用状态无效")
    is_available = None if raw_available in (None, "") else raw_available == "1"
    registered_from = optional_day(args.get("registered_from"))
    registered_to = optional_day(args.get("registered_to"))
    if registered_from and registered_to and registered_from > registered_to:
        raise ValidationError("注册时间起点不能晚于终点")
    return HomeFilters(
        selected, (args.get("q") or "").strip()[:100],
        optional_id(args.get("taxpayer_identity_id")),
        optional_id(args.get("service_type_id")),
        optional_id(args.get("customer_source_id")),
        filing_status, bookkeeping_status,
        registered_from, registered_to, is_available,
    )


def build_home_query(filters: HomeFilters) -> tuple[str, tuple]:
    """Default month view uses active customers; selected criteria search all customers."""
    joins = [
        "LEFT JOIN monthly_bookkeeping AS mb ON mb.customer_id = c.id AND mb.book_month = %s",
        "LEFT JOIN monthly_filings AS mf ON mf.customer_id = c.id AND mf.tax_month = %s",
        "LEFT JOIN tag_categories AS ti ON ti.id = c.taxpayer_identity_id",
        "LEFT JOIN tag_categories AS st ON st.id = c.service_type_id",
        "LEFT JOIN tag_categories AS cs ON cs.id = c.customer_source_id",
    ]
    params: list = [filters.month, filters.month]
    conditions: list[str] = []
    condition_params: list = []
    if not filters.has_criteria:
        conditions.extend((
            "c.is_available = TRUE",
            "(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date < %s",
        ))
        condition_params.append(next_month(filters.month))
    if filters.is_available is not None:
        conditions.append("c.is_available = %s")
        condition_params.append(filters.is_available)
    if filters.query:
        conditions.append("(c.name ILIKE %s OR COALESCE(c.tax_identifier, '') ILIKE %s)")
        condition_params.extend((f"%{filters.query}%", f"%{filters.query}%"))
    for column, selected_id in (
        ("taxpayer_identity_id", filters.taxpayer_identity_id),
        ("service_type_id", filters.service_type_id),
        ("customer_source_id", filters.customer_source_id),
    ):
        if selected_id is not None:
            conditions.append(f"c.{column} = %s")
            condition_params.append(selected_id)
    for alias, table, value in (
        ("mf_filter", "monthly_filings", filters.filing_status),
        ("mb_filter", "monthly_bookkeeping", filters.bookkeeping_status),
    ):
        if value is not None:
            month_column = "tax_month" if table == "monthly_filings" else "book_month"
            status_column = "is_filed" if table == "monthly_filings" else "is_booked"
            joins.append(
                f"JOIN {table} AS {alias} ON {alias}.customer_id = c.id "
                f"AND {alias}.{month_column} = %s"
            )
            params.append(filters.month)
            conditions.append(f"{alias}.{status_column} = %s")
            condition_params.append(value)
    if filters.registered_from:
        conditions.append("(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date >= %s")
        condition_params.append(filters.registered_from)
    if filters.registered_to:
        conditions.append("(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date <= %s")
        condition_params.append(filters.registered_to)
    sql = """SELECT c.id, c.name, c.tax_identifier, c.note,
                    mb.is_booked AS is_booked, mf.is_filed AS is_filed,
                    ti.name AS taxpayer_identity, st.name AS service_type,
                    cs.name AS customer_source
             FROM customers AS c\n""" + "\n".join(joins)
    if conditions:
        sql += "\nWHERE " + " AND ".join(conditions)
    sql += "\nORDER BY c.id DESC"
    return sql, tuple(params + condition_params)
