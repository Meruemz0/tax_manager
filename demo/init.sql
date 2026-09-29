-- Run against the intended database (tax_db by default).
-- This script is safe to rerun: existing customer, filing, and login rows are preserved.
-- Images are files in a persistent, private directory; these tables store metadata only.

BEGIN;

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

-- No search or ordering indexes: at this scale, list all rows and fetch a file by id.
CREATE TABLE IF NOT EXISTS unassigned_images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    storage_key VARCHAR(64) NOT NULL UNIQUE,
    original_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(20) NOT NULL CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS site_users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE CHECK (length(btrim(username)) > 0),
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Shared rate counters keep login throttling effective with multiple web workers.
CREATE TABLE IF NOT EXISTS login_attempts (
    key CHAR(64) PRIMARY KEY,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The application can query this view without depending on the database server's time zone.
-- A missing filing row is shown as not filed.
CREATE OR REPLACE VIEW customer_current_month_filing_status AS
SELECT
    c.id AS customer_id,
    c.name AS customer_name,
    date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date AS tax_month,
    COALESCE(m.is_filed, FALSE) AS is_filed
FROM customers AS c
LEFT JOIN monthly_filings AS m
    ON m.customer_id = c.id
   AND m.tax_month = date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date;

-- Five fictional customers; tax identifiers are deliberate demo markers.
INSERT INTO customers (name, tax_identifier, contact_name, note)
VALUES
    ('演示客户甲', 'DEMO-TAX-001', '联系人甲', '测试数据'),
    ('演示客户乙', 'DEMO-TAX-002', '联系人乙', '测试数据'),
    ('演示客户丙', 'DEMO-TAX-003', '联系人丙', '测试数据'),
    ('演示客户丁', 'DEMO-TAX-004', '联系人丁', '测试数据'),
    ('演示客户戊', 'DEMO-TAX-005', '联系人戊', '测试数据')
ON CONFLICT DO NOTHING;

-- Three filed and two not filed in the current China calendar month.
INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
SELECT
    c.id,
    date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date,
    demo.is_filed
FROM customers AS c
JOIN (VALUES
    ('DEMO-TAX-001', TRUE),
    ('DEMO-TAX-002', TRUE),
    ('DEMO-TAX-003', TRUE),
    ('DEMO-TAX-004', FALSE),
    ('DEMO-TAX-005', FALSE)
) AS demo(tax_identifier, is_filed)
    ON c.tax_identifier = demo.tax_identifier
ON CONFLICT DO NOTHING;

-- Initial demo account: wfg1. Its password verifier was generated from a discarded
-- random password; no published password can log in. After first installation,
-- set a private password with: flask --app tax_manager.app:create_app auth set-password wfg1
-- Re-running this script preserves the password of an existing wfg1 account.
INSERT INTO site_users (username, password_hash)
VALUES (
    'wfg1',
    'scrypt$131072$8$1$f37144a59be9ecab4a6e797b18fa7449$b87b832aed3f587d0aad795719466e49d028332a5f91e743520b8e50bc1aa263'
)
ON CONFLICT (username) DO NOTHING;

COMMIT;

-- Quick manual checks after execution:
-- SELECT customer_id, customer_name, tax_month, is_filed
-- FROM customer_current_month_filing_status ORDER BY customer_id;
-- SELECT username, is_active FROM site_users;
