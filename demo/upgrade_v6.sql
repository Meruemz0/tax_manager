-- Incremental upgrade for an existing tax_db after upgrade_v5.sql.
-- Run this whole UTF-8 file in Navicat with "continue on error" disabled.
-- Safe to rerun: all existing customer, account and order rows are preserved.
BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS bookkeeping_start_month DATE;
ALTER TABLE customers
    DROP CONSTRAINT IF EXISTS customers_bookkeeping_start_month_first_day;
ALTER TABLE customers
    ADD CONSTRAINT customers_bookkeeping_start_month_first_day
    CHECK (bookkeeping_start_month IS NULL OR EXTRACT(DAY FROM bookkeeping_start_month) = 1);

-- The app writes a single encrypted challenge on first startup and verifies it
-- on every later startup. This prevents silent use of a wrong key after restore.
CREATE TABLE IF NOT EXISTS data_key_verification (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    challenge_ciphertext BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_system_accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    aad_token VARCHAR(32) NOT NULL UNIQUE,
    system_name VARCHAR(100) NOT NULL CHECK (length(btrim(system_name)) > 0),
    login_url VARCHAR(500),
    account_ciphertext BYTEA NOT NULL,
    password_ciphertext BYTEA NOT NULL,
    note VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customer_system_accounts_customer_idx
    ON customer_system_accounts (customer_id, id);

-- Lock the parent row so concurrent inserts cannot exceed ten entries.
CREATE OR REPLACE FUNCTION enforce_customer_system_account_limit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM customers WHERE id = NEW.customer_id FOR UPDATE;
    IF (SELECT count(*) FROM customer_system_accounts
        WHERE customer_id = NEW.customer_id AND id IS DISTINCT FROM NEW.id) >= 10 THEN
        RAISE EXCEPTION 'Each customer may have at most 10 system accounts';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS customer_system_accounts_limit ON customer_system_accounts;
CREATE TRIGGER customer_system_accounts_limit
    BEFORE INSERT OR UPDATE OF customer_id ON customer_system_accounts
    FOR EACH ROW EXECUTE FUNCTION enforce_customer_system_account_limit();

CREATE TABLE IF NOT EXISTS service_orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    service_name VARCHAR(150) NOT NULL CHECK (length(btrim(service_name)) > 0),
    order_date DATE NOT NULL,
    amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (amount >= 0),
    service_start_month DATE CHECK (service_start_month IS NULL OR EXTRACT(DAY FROM service_start_month) = 1),
    service_end_month DATE CHECK (service_end_month IS NULL OR EXTRACT(DAY FROM service_end_month) = 1),
    due_date DATE,
    contract_number VARCHAR(100),
    note VARCHAR(1000),
    is_completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (service_start_month IS NULL OR service_end_month IS NULL OR service_end_month >= service_start_month)
);
CREATE INDEX IF NOT EXISTS service_orders_customer_idx ON service_orders (customer_id, order_date DESC, id DESC);

CREATE TABLE IF NOT EXISTS order_receipts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    received_on DATE NOT NULL,
    method VARCHAR(50),
    note VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_receipts_order_idx ON order_receipts (order_id, received_on, id);

CREATE TABLE IF NOT EXISTS order_vouchers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
    storage_key VARCHAR(32) NOT NULL UNIQUE,
    name_ciphertext BYTEA NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 20971520),
    document_type VARCHAR(16) NOT NULL DEFAULT 'voucher'
        CHECK (document_type IN ('voucher', 'contract', 'receipt')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_vouchers_order_idx ON order_vouchers (order_id, id);

COMMIT;

