from datetime import date

import pytest

from tax_manager.auth.passwords import verify_password
from tax_manager.customers.validation import CustomerInput, ValidationError, validate_filing_month


TEST_HASH = (
    "scrypt$131072$8$1$00112233445566778899aabbccddeeff$"
    "0887b186c64591b322fc71a0228c7cbce886af601cb4195e10031bf87abc4e00"
)


def test_password_verifier_accepts_only_matching_password():
    assert verify_password("example-password", TEST_HASH)
    assert not verify_password("wrong", TEST_HASH)
    assert not verify_password("example-password", "malformed")


def test_customer_input_trims_fields_and_rejects_blank_name():
    data = CustomerInput.from_form({"name": "  华东公司  ", "tax_identifier": "  A-1  ", "note": "  备注  "})
    assert data.name == "华东公司"
    assert data.tax_identifier == "A-1"
    assert data.note == "备注"
    with pytest.raises(ValidationError):
        CustomerInput.from_form({"name": "   "})


def test_customer_input_enforces_database_limits():
    with pytest.raises(ValidationError):
        CustomerInput.from_form({"name": "甲" * 201})


def test_customer_note_accepts_255_characters_and_rejects_256():
    assert CustomerInput.from_form({"name": "客户", "note": "备" * 255}).note == "备" * 255
    with pytest.raises(ValidationError):
        CustomerInput.from_form({"name": "客户", "note": "备" * 256})


def test_filing_month_stays_between_registration_and_china_current_month():
    registered_on = date(2026, 8, 17)
    china_today = date(2026, 9, 29)
    assert validate_filing_month("2026-08", registered_on, china_today) == date(2026, 8, 1)
    assert validate_filing_month(None, registered_on, china_today) == date(2026, 9, 1)
    for invalid in ("2026-07", "2026-10", "2026-13", "2026-9", "0000-01", "not-a-month"):
        with pytest.raises(ValidationError):
            validate_filing_month(invalid, registered_on, china_today)


def test_customer_input_parses_editable_registration_date():
    data = CustomerInput.from_form({"name": "客户", "registered_on": "2024-02-29"})
    assert data.registered_on == date(2024, 2, 29)
    with pytest.raises(ValidationError):
        CustomerInput.from_form({"name": "客户", "registered_on": "2024-02-30"})
