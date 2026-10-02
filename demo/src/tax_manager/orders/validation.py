"""Validate service orders and their independently recorded receipts."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Mapping

from tax_manager.customers.validation import china_today


def _text(data: Mapping, key: str, limit: int, required: bool = False) -> str | None:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是文字")
    value = value.strip()
    if len(value) > limit or required and not value:
        raise ValueError(f"{key} 无效或超过 {limit} 字")
    return value or None


def _date(data: Mapping, key: str, default: date | None = None) -> date | None:
    raw = data.get(key)
    if raw in (None, ""):
        return default
    if key.endswith("_month") and isinstance(raw, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
        raw += "-01"
    if not isinstance(raw, str) or len(raw) != 10:
        raise ValueError(f"{key} 日期无效")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 日期无效") from exc


def _money(raw, *, positive: bool) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("金额无效") from exc
    if not value.is_finite() or value > Decimal("9999999999.99") or (value <= 0 if positive else value < 0) or value != value.quantize(Decimal("0.01")):
        raise ValueError("金额必须是不超过两位小数的有效数字")
    return value


def _bool(raw) -> bool:
    if raw in (True, "1", 1):
        return True
    if raw in (False, "0", 0, None, ""):
        return False
    raise ValueError("完成状态无效")


@dataclass(frozen=True)
class OrderInput:
    service_name: str
    order_date: date
    amount: Decimal
    service_start_month: date | None
    service_end_month: date | None
    due_date: date | None
    contract_number: str | None
    note: str | None
    is_completed: bool

    @classmethod
    def from_mapping(cls, data: Mapping) -> "OrderInput":
        start = _date(data, "service_start_month")
        end = _date(data, "service_end_month")
        if start and start.day != 1 or end and end.day != 1 or start and end and end < start:
            raise ValueError("服务月份必须填写每月第一天，且结束月份不能早于开始月份")
        return cls(
            _text(data, "service_name", 150, True),
            _date(data, "order_date", china_today()),
            _money(data.get("amount", "0"), positive=False),
            start, end, _date(data, "due_date"),
            _text(data, "contract_number", 100), _text(data, "note", 1000),
            _bool(data.get("is_completed")),
        )


@dataclass(frozen=True)
class ReceiptInput:
    amount: Decimal
    received_on: date
    method: str | None
    note: str | None

    @classmethod
    def from_mapping(cls, data: Mapping) -> "ReceiptInput":
        return cls(
            _money(data.get("amount"), positive=True),
            _date(data, "received_on", china_today()),
            _text(data, "method", 50), _text(data, "note", 255),
        )


def paid_status(amount: Decimal, receipts) -> str:
    if amount == 0:
        return "无需收款"
    paid = sum(receipts, Decimal("0"))
    if paid <= 0:
        return "未收款"
    if paid < amount:
        return "部分收款"
    return "已收款"
