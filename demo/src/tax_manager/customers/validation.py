"""Validation at the customer form boundary."""

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Mapping
from zoneinfo import ZoneInfo


class ValidationError(ValueError):
    pass


def china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def validate_filing_month(raw: str | None, registered_on: date, china_today: date) -> date:
    if raw is None:
        month = china_today.replace(day=1)
    else:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
            raise ValidationError("报税月份格式应为 YYYY-MM")
        try:
            month = date.fromisoformat(f"{raw}-01")
        except ValueError as exc:
            raise ValidationError("报税月份无效") from exc
    if not registered_on.replace(day=1) <= month <= china_today.replace(day=1):
        raise ValidationError("报税月份必须在客户注册月份至本月之间")
    return month


@dataclass(frozen=True)
class CustomerInput:
    name: str
    tax_identifier: str | None
    contact_name: str | None
    contact_phone: str | None
    note: str | None
    registered_on: date | None = None

    @classmethod
    def from_form(cls, form: Mapping[str, str]) -> "CustomerInput":
        fields = {
            "name": (form.get("name") or "").strip(),
            "tax_identifier": (form.get("tax_identifier") or "").strip(),
            "contact_name": (form.get("contact_name") or "").strip(),
            "contact_phone": (form.get("contact_phone") or "").strip(),
            "note": (form.get("note") or "").strip(),
        }
        limits = {"name": 200, "tax_identifier": 64, "contact_name": 100, "contact_phone": 50, "note": 255}
        if not fields["name"]:
            raise ValidationError("客户名称不能为空")
        for key, limit in limits.items():
            if len(fields[key]) > limit:
                raise ValidationError(f"{key} 超过 {limit} 字符")
        raw_date = form.get("registered_on")
        registered_on = None
        if raw_date is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                raise ValidationError("客户注册日期格式应为 YYYY-MM-DD")
            try:
                registered_on = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValidationError("客户注册日期无效") from exc
            if registered_on > china_today():
                raise ValidationError("客户注册日期不能晚于中国时间今天")
        return cls(
            fields["name"],
            *(fields[key] or None for key in ("tax_identifier", "contact_name", "contact_phone", "note")),
            registered_on,
        )
