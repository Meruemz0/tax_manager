-- Run after upgrade_v7.sql. Existing templates and legacy provider details are preserved.
BEGIN;
ALTER TABLE contract_templates ADD COLUMN IF NOT EXISTS fields JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE contract_templates DROP CONSTRAINT IF EXISTS contract_templates_fields_array;
ALTER TABLE contract_templates ADD CONSTRAINT contract_templates_fields_array
    CHECK (jsonb_typeof(fields) = 'array');
COMMIT;
