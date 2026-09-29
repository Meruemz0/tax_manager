"""Validation at the customer form boundary."""

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


class ValidationError(ValueError):
    pass


def china_today() -> date:
    return datetime.now(CHINA_TZ).date()


def china_now() -> datetime:
    return datetime.now(CHINA_TZ)


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


def optional_choice_id(raw: Any, label: str) -> int | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not (
        isinstance(raw, int) or isinstance(raw, str) and raw.isdecimal()
    ) or int(raw) <= 0:
        raise ValidationError(f"{label}选项无效")
    return int(raw)


@dataclass(frozen=True)
class CustomerInput:
    name: str
    tax_identifier: str | None
    contact_name: str | None
    contact_phone: str | None
    note: str | None
    registered_on: date | None = None
    registered_at: datetime | None = None
    is_available: bool | None = None
    taxpayer_identity_id: int | None = None
    service_type_id: int | None = None
    customer_source_id: int | None = None

    @classmethod
    def from_form(cls, form: Mapping[str, Any]) -> "CustomerInput":
        def text_field(key: str) -> str:
            raw = form.get(key)
            if raw is None:
                return ""
            if not isinstance(raw, str):
                raise ValidationError(f"{key} 必须是文字")
            return raw.strip()

        fields = {key: text_field(key) for key in (
            "name", "tax_identifier", "contact_name", "contact_phone", "note"
        )}
        limits = {"name": 200, "tax_identifier": 64, "contact_name": 100, "contact_phone": 50, "note": 255}
        if not fields["name"]:
            raise ValidationError("客户名称不能为空")
        for key, limit in limits.items():
            if len(fields[key]) > limit:
                raise ValidationError(f"{key} 超过 {limit} 字符")

        raw_date = form.get("registered_on")
        registered_on = None
        if raw_date is not None:
            if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                raise ValidationError("客户注册日期格式应为 YYYY-MM-DD")
            try:
                registered_on = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValidationError("客户注册日期无效") from exc
            if registered_on > china_today():
                raise ValidationError("客户注册日期不能晚于中国时间今天")

        registered_at = None
        raw_timestamp = form.get("registered_at")
        if raw_timestamp not in (None, ""):
            if not isinstance(raw_timestamp, str):
                raise ValidationError("客户注册时间无效")
            try:
                registered_at = datetime.fromisoformat(raw_timestamp)
            except ValueError as exc:
                raise ValidationError("客户注册时间无效") from exc
            if registered_at.tzinfo is None:
                registered_at = registered_at.replace(tzinfo=CHINA_TZ)
            if registered_at > china_now():
                raise ValidationError("客户注册时间不能晚于中国时间现在")
            registered_on = registered_at.astimezone(CHINA_TZ).date()
        elif registered_on is not None:
            registered_at = datetime.combine(registered_on, datetime.min.time(), CHINA_TZ)

        raw_available = form.get("is_available")
        if raw_available in (None, ""):
            is_available = None
        elif raw_available is True or raw_available in ("1", 1):
            is_available = True
        elif raw_available is False or raw_available in ("0", 0):
            is_available = False
        else:
            raise ValidationError("可用状态无效")

        return cls(
            fields["name"],
            *(fields[key] or None for key in ("tax_identifier", "contact_name", "contact_phone", "note")),
            registered_on,
            registered_at,
            is_available,
            optional_choice_id(form.get("taxpayer_identity_id"), "纳税人身份"),
            optional_choice_id(form.get("service_type_id"), "服务类型"),
            optional_choice_id(form.get("customer_source_id"), "客户来源"),
        )
