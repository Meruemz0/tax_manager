-- Run in Navicat against tax_db after upgrade_v3.sql, before restarting the app.
-- Idempotent; preserves historical image records and files.
BEGIN;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;
COMMIT;
