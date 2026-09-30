-- Run in Navicat against tax_db after upgrade_v4.sql, before restarting the app.
-- Existing customer files are preserved. Safe to rerun.
BEGIN;
ALTER TABLE customer_files DROP CONSTRAINT IF EXISTS customer_files_size_bytes_check;
ALTER TABLE customer_files ADD CONSTRAINT customer_files_size_bytes_check
    CHECK (size_bytes > 0 AND size_bytes <= 20971520);
COMMIT;
