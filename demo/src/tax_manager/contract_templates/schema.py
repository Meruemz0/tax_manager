"""Compile reusable blanks and validate independent contract input."""

from datetime import date
from decimal import Decimal, InvalidOperation
import re

from tax_manager.contract_templates.documents import FIELDS, TOKEN, validate_template_body

FIELD_TYPES = {"text": "文字", "textarea": "多行文字", "date": "日期",
               "month": "年月", "money": "金额", "day": "每月日期（1—31）"}
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
DEFAULT_TYPES = {
    "amount": "money", "order_date": "date", "due_date": "date", "sign_date": "date",
    "service_start_month": "month", "service_end_month": "month",
    "materials_day": "day", "payment_terms": "textarea", "extra_terms": "textarea",
}

def compile_template(body, definitions=None):
    if not isinstance(body, str):
        raise ValueError("模板正文必须是文字")
    definitions = [] if definitions is None else definitions
    if not isinstance(definitions, list) or len(definitions) > 80:
        raise ValueError("空格配置最多 80 项")
    known = {}
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ValueError("空格配置无效")
        key, label, kind = (definition.get(k) for k in ("key", "label", "type"))
        if not isinstance(key, str) or not KEY.fullmatch(key) or key in known:
            raise ValueError("空格标识无效或重复")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("空格名称不能为空且最多 80 字")
        if not isinstance(kind, str) or kind not in FIELD_TYPES:
            raise ValueError("空格类型无效")
        known[key] = {"key": key, "label": label.strip(), "type": kind}
    used = set(TOKEN.findall(body)) | known.keys()
    blank_count = 0
    def replace_blank(_):
        nonlocal blank_count
        while True:
            blank_count += 1
            key = f"blank_{blank_count}"
            if key not in used:
                break
        used.add(key)
        known[key] = {"key": key, "label": f"填写内容 {blank_count}", "type": "text"}
        return "{{" + key + "}}"
    # Legacy underlines in plain text are blanks; underscores within token keys are not.
    pieces, cursor = [], 0
    for match in TOKEN.finditer(body):
        pieces.append(re.sub(r"_{4,}", replace_blank, body[cursor:match.start()]))
        pieces.append(match.group())
        cursor = match.end()
    pieces.append(re.sub(r"_{4,}", replace_blank, body[cursor:]))
    body = "".join(pieces)
    validate_template_body(body, known.keys())
    ordered = []
    for key in dict.fromkeys(TOKEN.findall(body)):
        definition = known.get(key) or {
            "key": key, "label": FIELDS[key], "type": DEFAULT_TYPES.get(key, "text"),
        }
        ordered.append(definition)
    if len(ordered) > 80 or len(TOKEN.findall(body)) > 200:
        raise ValueError("模板空格数量过多")
    return body, ordered

def document_lines(body, fields):
    lookup = {field["key"]: field for field in fields}
    lines = []
    for line in body.split("\n"):
        parts, cursor = [], 0
        for match in TOKEN.finditer(line):
            if match.start() > cursor:
                parts.append({"text": line[cursor:match.start()]})
            parts.append({"field": lookup[match.group(1)]})
            cursor = match.end()
        if cursor < len(line):
            parts.append({"text": line[cursor:]})
        lines.append(parts)
    return lines

def fill_contract(body, fields, values):
    if not isinstance(values, dict) or set(values) - {f["key"] for f in fields}:
        raise ValueError("填写内容包含未知空格")
    result = {}
    for field in fields:
        value = values.get(field["key"], "")
        if not isinstance(value, str):
            raise ValueError(f'{field["label"]}必须是文字')
        value = value.strip()
        maximum = 2000 if field["type"] == "textarea" else 500
        if len(value) > maximum:
            raise ValueError(f'{field["label"]}最多 {maximum} 字')
        if value:
            try:
                if field["type"] == "date":
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    value = date.fromisoformat(value).isoformat()
                elif field["type"] == "month":
                    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
                        raise ValueError()
                    date.fromisoformat(value + "-01")
                elif field["type"] == "day":
                    if not re.fullmatch(r"\d{1,2}", value) or not 1 <= int(value) <= 31:
                        raise ValueError()
                    value = str(int(value))
                elif field["type"] == "money":
                    amount = Decimal(value)
                    if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99") or amount != amount.quantize(Decimal(".01")):
                        raise ValueError()
                    value = format(amount, ".2f")
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f'{field["label"]}格式无效') from exc
        result[field["key"]] = value or "________"
    rendered = TOKEN.sub(lambda match: result[match.group(1)], body)
    if len(rendered) > 120000:
        raise ValueError("合同内容过长，请缩短填写内容")
    return rendered
