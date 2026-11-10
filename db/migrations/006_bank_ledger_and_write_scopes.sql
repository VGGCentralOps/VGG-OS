-- Bank movements from Business Central bankAccountLedgerEntries, the entity-set map seen at
-- probe time, and separate write permissions. Viewing no longer implies changing data.
BEGIN;

CREATE TABLE finance.bank_ledger_entry (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  company_id bigint NOT NULL REFERENCES source.company(id),
  entity_id bigint NOT NULL REFERENCES core.entity(id),
  source_entry_key text NOT NULL,
  source_version text NOT NULL,
  source_row_version bigint,
  bank_account_id bigint REFERENCES finance.bank_account(id),
  bc_bank_account_number text NOT NULL,
  entry_number bigint,
  posting_date date NOT NULL,
  document_type text,
  document_number text,
  external_document_number text,
  description text,
  currency_code text,
  amount numeric(28,8),
  amount_lcy numeric(28,8),
  remaining_amount numeric(28,8),
  open boolean,
  statement_status text,
  statement_number text,
  bal_account_type text,
  bal_account_number text,
  transaction_number bigint,
  dimension_set_id bigint,
  reversed boolean,
  raw_record_id bigint NOT NULL REFERENCES source.raw_record(id),
  source_deleted boolean NOT NULL DEFAULT false,
  UNIQUE (company_id, source_entry_key)
);

COMMENT ON TABLE finance.bank_ledger_entry IS
  'Business Central bank account ledger entries (book side). Not a bank statement and not a balance. Null amount means Business Central did not send one; it is never zero-filled.';

CREATE INDEX bank_ledger_entry_account_date ON finance.bank_ledger_entry (bank_account_id, posting_date DESC);
CREATE INDEX bank_ledger_entry_company_date ON finance.bank_ledger_entry (company_id, posting_date DESC);

ALTER TABLE source.company_probe ADD COLUMN entity_sets jsonb;

GRANT SELECT, INSERT, UPDATE ON finance.bank_ledger_entry TO vggos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA finance TO vggos_app;
GRANT UPDATE ON source.company_probe TO vggos_app;

ALTER TABLE finance.bank_ledger_entry ENABLE ROW LEVEL SECURITY;
CREATE POLICY bank_ledger_entry_app ON finance.bank_ledger_entry
  FOR ALL TO vggos_app
  USING (true)
  WITH CHECK (true);

DO $$
DECLARE
  item record;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'REVOKE ALL ON TABLE finance.bank_ledger_entry FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'REVOKE ALL ON TABLE finance.bank_ledger_entry FROM authenticated';
  END IF;
  FOR item IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'iam.finance_grant'::regclass
      AND contype = 'c'
      AND (pg_get_constraintdef(oid) LIKE '%scope%' OR pg_get_constraintdef(oid) LIKE '%action%')
  LOOP
    EXECUTE format('ALTER TABLE iam.finance_grant DROP CONSTRAINT %I', item.conname);
  END LOOP;
END $$;

ALTER TABLE iam.finance_grant
  ADD CONSTRAINT finance_grant_scope_known CHECK (
    scope IN ('books:read', 'bank:read', 'export', 'bank:unmask', 'books:sync', 'files:write')
  ),
  ADD CONSTRAINT finance_grant_action_known CHECK (
    action IN ('VIEW', 'EXPORT', 'UNMASK', 'SYNC', 'WRITE')
  ),
  ADD CONSTRAINT finance_grant_scope_action CHECK (
    (scope IN ('books:read', 'bank:read') AND action = 'VIEW')
    OR (scope = 'export' AND action = 'EXPORT')
    OR (scope = 'bank:unmask' AND action = 'UNMASK')
    OR (scope = 'books:sync' AND action = 'SYNC')
    OR (scope = 'files:write' AND action = 'WRITE')
  );

INSERT INTO core.app_migration (version) VALUES ('006_bank_ledger_and_write_scopes');

COMMIT;
