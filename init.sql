-- Run this SQL while connected to tax_db.
-- Docker Compose creates tax_db via POSTGRES_DB before running this file.

CREATE TABLE IF NOT EXISTS customers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(200) NOT NULL CHECK (length(btrim(name)) > 0),
    tax_identifier VARCHAR(64),
    contact_name VARCHAR(100),
    contact_phone VARCHAR(50),
    note VARCHAR(2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS customers_tax_identifier_unique
    ON customers (tax_identifier) WHERE tax_identifier IS NOT NULL;

CREATE TABLE IF NOT EXISTS monthly_filings (
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    tax_month DATE NOT NULL CHECK (EXTRACT(DAY FROM tax_month) = 1),
    is_filed BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, tax_month)
);

CREATE TABLE IF NOT EXISTS customer_images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    storage_key VARCHAR(64) NOT NULL UNIQUE,
    original_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(20) NOT NULL CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS customer_images_customer_id_idx
    ON customer_images (customer_id, id);
