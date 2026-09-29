from io import BytesIO
from datetime import date

import pytest

from tax_manager.auth.passwords import verify_password
from tax_manager.customers.validation import CustomerInput, ValidationError, validate_filing_month
from tax_manager.app import create_app
from tax_manager.images.storage import InvalidImage, UploadedImage, file_path, read_image, save_image


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


def test_image_validator_checks_content_not_filename():
    with pytest.raises(InvalidImage):
        read_image(BytesIO(b"not a real png"), "screenshot.png")


def test_image_validator_accepts_small_png():
    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    stream.seek(0)
    upload = read_image(stream, "capture.png")
    assert upload.mime_type == "image/png"
    assert upload.size_bytes == len(upload.data)
    assert upload.original_name == "capture.png"


def test_file_key_collision_does_not_delete_existing_image(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://unused", "STORAGE_DIR": tmp_path})
    key = "a" * 32
    monkeypatch.setattr("tax_manager.images.storage.secrets.token_hex", lambda length: key)
    with app.app_context():
        path = file_path("customer", key)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"existing image")
        with pytest.raises(FileExistsError):
            save_image("customer", UploadedImage(b"new image", "new.png", "image/png", 9))
        assert path.read_bytes() == b"existing image"
