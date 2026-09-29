-- Run in the existing tax_db AFTER demo/init.sql and demo/upgrade_v2.sql.
-- Navicat: run the entire UTF-8 file with "continue on error" disabled.
-- This migration is additive and safe to rerun. Back up the database first.

BEGIN;

-- Existing date-only registrations become midnight on that date in China.
-- New records use the actual current timestamp.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ;
UPDATE customers
SET registered_at = registered_on::timestamp AT TIME ZONE 'Asia/Shanghai'
WHERE registered_at IS NULL;
ALTER TABLE customers ALTER COLUMN registered_at SET DEFAULT now();
ALTER TABLE customers ALTER COLUMN registered_at SET NOT NULL;

ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_available BOOLEAN NOT NULL DEFAULT TRUE;

-- Preserve previous free-form tags as kind='general'; these three new kinds are
-- single-select customer fields. Filing/bookkeeping status never use this table.
ALTER TABLE tag_categories
    ADD COLUMN IF NOT EXISTS kind VARCHAR(32) NOT NULL DEFAULT 'general'
    CHECK (kind IN ('general', 'taxpayer_identity', 'service_type', 'customer_source'));
DROP INDEX IF EXISTS tag_categories_name_unique;
CREATE UNIQUE INDEX IF NOT EXISTS tag_categories_kind_name_unique
    ON tag_categories (kind, lower(btrim(name)));

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS taxpayer_identity_id BIGINT REFERENCES tag_categories(id) ON DELETE SET NULL;
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS service_type_id BIGINT REFERENCES tag_categories(id) ON DELETE SET NULL;
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS customer_source_id BIGINT REFERENCES tag_categories(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS monthly_bookkeeping (
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    book_month DATE NOT NULL CHECK (EXTRACT(DAY FROM book_month) = 1),
    is_booked BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, book_month)
);

INSERT INTO monthly_bookkeeping (customer_id, book_month, is_booked)
SELECT c.id, months.month_start::date, FALSE
FROM customers AS c
CROSS JOIN LATERAL generate_series(
    date_trunc('month', c.registered_at AT TIME ZONE 'Asia/Shanghai'),
    date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
    interval '1 month'
) AS months(month_start)
ON CONFLICT (customer_id, book_month) DO NOTHING;

CREATE TABLE IF NOT EXISTS customer_files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    storage_key VARCHAR(32) NOT NULL UNIQUE,
    original_name VARCHAR(255) NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 1048576),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customer_files_customer_id_idx
    ON customer_files (customer_id, id);

COMMIT;

-- SELECT column_name FROM information_schema.columns
-- WHERE table_name='customers' AND column_name IN
-- ('registered_at','is_available','taxpayer_identity_id','service_type_id','customer_source_id');
