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


def mode(raw: str | None) -> str:
    value = raw or "in"
    if value not in {"in", "not"}:
        raise ValidationError("筛选方式无效")
    return value


@dataclass(frozen=True)
class HomeFilters:
    month: date
    query: str
    taxpayer_identity_id: int | None
    taxpayer_identity_mode: str
    service_type_id: int | None
    service_type_mode: str
    customer_source_id: int | None
    customer_source_mode: str
    filing_month: date | None
    filing_status: bool | None
    filing_mode: str
    bookkeeping_month: date | None
    bookkeeping_status: bool | None
    bookkeeping_mode: str
    registered_from: date | None
    registered_to: date | None


def parse_home_filters(args: Mapping[str, str], today: date) -> HomeFilters:
    selected = month_start(args["month"], current=today) if args.get("month") else today.replace(day=1)

    def status(prefix: str) -> tuple[date | None, bool | None, str]:
        raw = args.get(f"{prefix}_status")
        selected_month = (
            month_start(args[f"{prefix}_month"], current=today)
            if args.get(f"{prefix}_month") else None
        )
        selected_mode = mode(args.get(f"{prefix}_mode"))
        if raw in (None, ""):
            return selected_month, None, selected_mode
        if raw not in {"0", "1"} or selected_month is None:
            raise ValidationError("请为月度状态选择月份和已完成/未完成")
        return selected_month, raw == "1", selected_mode

    filing_month, filing_status, filing_mode = status("filing")
    bookkeeping_month, bookkeeping_status, bookkeeping_mode = status("bookkeeping")
    registered_from = optional_day(args.get("registered_from"))
    registered_to = optional_day(args.get("registered_to"))
    if registered_from and registered_to and registered_from > registered_to:
        raise ValidationError("注册时间起点不能晚于终点")
    return HomeFilters(
        selected, (args.get("q") or "").strip()[:100],
        optional_id(args.get("taxpayer_identity_id")), mode(args.get("taxpayer_identity_mode")),
        optional_id(args.get("service_type_id")), mode(args.get("service_type_mode")),
        optional_id(args.get("customer_source_id")), mode(args.get("customer_source_mode")),
        filing_month, filing_status, filing_mode,
        bookkeeping_month, bookkeeping_status, bookkeeping_mode,
        registered_from, registered_to,
    )


def build_home_query(filters: HomeFilters) -> tuple[str, tuple]:
    """Build SQL only from fixed fragments; all user values remain bound parameters."""
    joins = [
        "LEFT JOIN monthly_bookkeeping AS mb ON mb.customer_id = c.id AND mb.book_month = %s",
        "LEFT JOIN monthly_filings AS mf ON mf.customer_id = c.id AND mf.tax_month = %s",
        "LEFT JOIN tag_categories AS ti ON ti.id = c.taxpayer_identity_id",
        "LEFT JOIN tag_categories AS st ON st.id = c.service_type_id",
        "LEFT JOIN tag_categories AS cs ON cs.id = c.customer_source_id",
    ]
    params: list = [filters.month, filters.month]
    conditions = [
        "c.is_available = TRUE",
        "(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date < %s",
    ]
    condition_params: list = [next_month(filters.month)]
    if filters.query:
        conditions.append("(c.name ILIKE %s OR COALESCE(c.tax_identifier, '') ILIKE %s)")
        condition_params.extend((f"%{filters.query}%", f"%{filters.query}%"))
    for column, selected_id, selected_mode in (
        ("taxpayer_identity_id", filters.taxpayer_identity_id, filters.taxpayer_identity_mode),
        ("service_type_id", filters.service_type_id, filters.service_type_mode),
        ("customer_source_id", filters.customer_source_id, filters.customer_source_mode),
    ):
        if selected_id is not None:
            operator = "=" if selected_mode == "in" else "IS DISTINCT FROM"
            conditions.append(f"c.{column} {operator} %s")
            condition_params.append(selected_id)
    for prefix, table, month, value, selected_mode in (
        ("mf_filter", "monthly_filings", filters.filing_month, filters.filing_status, filters.filing_mode),
        ("mb_filter", "monthly_bookkeeping", filters.bookkeeping_month, filters.bookkeeping_status, filters.bookkeeping_mode),
    ):
        if value is not None:
            month_column = "tax_month" if table == "monthly_filings" else "book_month"
            status_column = "is_filed" if table == "monthly_filings" else "is_booked"
            joins.append(f"LEFT JOIN {table} AS {prefix} ON {prefix}.customer_id = c.id AND {prefix}.{month_column} = %s")
            params.append(month)
            conditions.append("(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date < %s")
            condition_params.append(next_month(month))
            operator = "=" if selected_mode == "in" else "<>"
            conditions.append(f"COALESCE({prefix}.{status_column}, FALSE) {operator} %s")
            condition_params.append(value)
    if filters.registered_from:
        conditions.append("(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date >= %s")
        condition_params.append(filters.registered_from)
    if filters.registered_to:
        conditions.append("(c.registered_at AT TIME ZONE 'Asia/Shanghai')::date <= %s")
        condition_params.append(filters.registered_to)
    sql = """SELECT c.id, c.name, c.tax_identifier, c.note,
                    COALESCE(mb.is_booked, FALSE) AS is_booked,
                    COALESCE(mf.is_filed, FALSE) AS is_filed,
                    ti.name AS taxpayer_identity, st.name AS service_type,
                    cs.name AS customer_source
             FROM customers AS c\n""" + "\n".join(joins)
    sql += "\nWHERE " + " AND ".join(conditions) + "\nORDER BY c.id DESC"
    return sql, tuple(params + condition_params)
