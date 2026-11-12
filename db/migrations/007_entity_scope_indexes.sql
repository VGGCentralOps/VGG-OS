-- Speed entity-scoped screens: overview, trial balance, banks, shell period.
BEGIN;

CREATE INDEX IF NOT EXISTS gl_entry_entity_posting
  ON finance.gl_entry (entity_id, posting_date DESC)
  WHERE NOT source_deleted;

CREATE INDEX IF NOT EXISTS gl_entry_entity_alive
  ON finance.gl_entry (entity_id)
  WHERE NOT source_deleted;

CREATE INDEX IF NOT EXISTS bank_account_entity
  ON finance.bank_account (entity_id);

CREATE INDEX IF NOT EXISTS bank_ledger_entity_posting
  ON finance.bank_ledger_entry (entity_id, posting_date DESC)
  WHERE NOT source_deleted;

CREATE INDEX IF NOT EXISTS bc_gl_period_balance_company_period
  ON finance.bc_gl_period_balance (company_id, posting_year DESC, posting_month DESC);

CREATE INDEX IF NOT EXISTS account_entity_active
  ON finance.account (entity_id)
  WHERE active IS DISTINCT FROM false;

INSERT INTO core.app_migration (version) VALUES ('007_entity_scope_indexes');

COMMIT;
