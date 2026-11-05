-- First migration after SQL pack v0.3.0. Do not edit or re-run the pack installer.
BEGIN;

CREATE TABLE core.app_migration (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE finance.gl_entry
  ADD COLUMN transaction_number bigint,
  ADD COLUMN entry_number bigint,
  ADD COLUMN dimension_set_id bigint,
  ADD COLUMN source_row_version bigint,
  ADD COLUMN debit_amount numeric(28,8),
  ADD COLUMN credit_amount numeric(28,8);

COMMENT ON COLUMN finance.gl_entry.debit_amount IS 'Present only when Business Central sent debitAmount. Never backfilled with zero.';
COMMENT ON COLUMN finance.gl_entry.credit_amount IS 'Present only when Business Central sent creditAmount. Never backfilled with zero.';

CREATE TABLE finance.bc_master_record (
  company_id bigint NOT NULL REFERENCES source.company(id),
  entity_set text NOT NULL,
  record_id text NOT NULL,
  business_key text,
  source_version text NOT NULL,
  source_row_version bigint,
  payload jsonb NOT NULL,
  source_deleted boolean NOT NULL DEFAULT false,
  raw_record_id bigint NOT NULL REFERENCES source.raw_record(id),
  PRIMARY KEY (company_id, entity_set, record_id)
);

CREATE TABLE finance.bc_gl_period_balance (
  company_id bigint NOT NULL REFERENCES source.company(id),
  gl_account_number text NOT NULL,
  posting_year integer NOT NULL,
  posting_month integer NOT NULL CHECK (posting_month BETWEEN 1 AND 12),
  amount numeric(28,8),
  debit_amount numeric(28,8),
  credit_amount numeric(28,8),
  entry_count bigint,
  max_entry_number bigint,
  run_id bigint NOT NULL REFERENCES source.ingestion_run(id),
  PRIMARY KEY (company_id, gl_account_number, posting_year, posting_month)
);

COMMENT ON TABLE finance.bc_gl_period_balance IS 'Business Central glBalancesByPeriod control totals. Null means the field was absent, not zero.';

CREATE TABLE finance.bank_account_secret (
  bank_account_id bigint PRIMARY KEY REFERENCES finance.bank_account(id),
  bank_account_number text,
  iban text
);

COMMENT ON TABLE finance.bank_account_secret IS 'Full bank identifiers. Register and export queries must not select this table.';

CREATE TABLE finance.bank_account_governance (
  bank_account_id bigint PRIMARY KEY REFERENCES finance.bank_account(id),
  bc_account_id text NOT NULL UNIQUE,
  accountable_owner text,
  data_route text NOT NULL DEFAULT 'UNASSIGNED' CHECK (data_route IN ('UNASSIGNED','MONO','STATEMENT_FILE')),
  link_status text NOT NULL DEFAULT 'NO_FEED' CHECK (link_status IN ('NO_FEED','LINKED','STALE')),
  in_latest_bc_list boolean NOT NULL DEFAULT true,
  CHECK (accountable_owner IS NULL OR length(btrim(accountable_owner)) > 0),
  CHECK (link_status = 'NO_FEED' OR data_route <> 'UNASSIGNED')
);

COMMENT ON TABLE finance.bank_account_governance IS 'Application fields keyed to the Business Central account id. The account record stays in Business Central. Owner stays empty until a person is named.';

CREATE TABLE finance.bank_dormancy_rule (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bank_account_id bigint REFERENCES finance.bank_account(id),
  applies_to text NOT NULL CHECK (applies_to IN ('GROUP_DEFAULT','ACCOUNT')),
  inactive_after_months integer,
  dormant_after_months integer,
  enabled boolean NOT NULL DEFAULT false,
  note text NOT NULL,
  CHECK (enabled = false),
  CHECK (inactive_after_months IS NULL),
  CHECK (dormant_after_months IS NULL),
  CHECK (applies_to <> 'GROUP_DEFAULT' OR bank_account_id IS NULL),
  CHECK (applies_to <> 'ACCOUNT' OR bank_account_id IS NOT NULL)
);

COMMENT ON TABLE finance.bank_dormancy_rule IS 'Stored so a signed rule can be added later. Thresholds stay empty and the rule stays disabled. Do not flag dormant from a missing or failed pull.';

INSERT INTO finance.bank_dormancy_rule (applies_to, enabled, note)
VALUES (
  'GROUP_DEFAULT',
  false,
  'No signed dormancy rule. Thresholds are empty and the rule is disabled.'
);

CREATE TABLE finance.bank_feed_observation (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  route text NOT NULL CHECK (route IN ('MONO','STATEMENT_FILE')),
  provider_account_key text NOT NULL,
  bc_account_id text,
  stale boolean NOT NULL DEFAULT false,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (route, provider_account_key)
);

COMMENT ON TABLE finance.bank_feed_observation IS 'Feed identifiers only. No balance is stored here. Mono is not called and statement files are not read in this slice.';

CREATE TABLE source.company_probe (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instance_id bigint REFERENCES source.instance(id),
  probed_at timestamptz NOT NULL DEFAULT now(),
  matched boolean NOT NULL,
  expected_company_id text NOT NULL,
  companies jsonb NOT NULL,
  message text NOT NULL
);

CREATE TABLE ops.bank_mismatch_task (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  mismatch_code text NOT NULL CHECK (mismatch_code IN ('IN_BC_NO_FEED','FEED_WITHOUT_BC','LINKED_STALE')),
  assignee_role text NOT NULL CHECK (assignee_role IN ('CENTRAL_OPS','TREASURY_AND_INTERNAL_AUDIT','ACCOUNT_OWNER')),
  bank_account_id bigint REFERENCES finance.bank_account(id),
  feed_reference text,
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
  detail text NOT NULL,
  opened_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  CHECK (
    (mismatch_code = 'IN_BC_NO_FEED' AND assignee_role = 'CENTRAL_OPS' AND bank_account_id IS NOT NULL AND feed_reference IS NULL)
    OR (mismatch_code = 'FEED_WITHOUT_BC' AND assignee_role = 'TREASURY_AND_INTERNAL_AUDIT' AND bank_account_id IS NULL AND feed_reference IS NOT NULL)
    OR (mismatch_code = 'LINKED_STALE' AND assignee_role = 'ACCOUNT_OWNER' AND bank_account_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX bank_mismatch_one_open_account
  ON ops.bank_mismatch_task (bank_account_id, mismatch_code)
  WHERE status = 'OPEN' AND bank_account_id IS NOT NULL;

CREATE UNIQUE INDEX bank_mismatch_one_open_feed
  ON ops.bank_mismatch_task (feed_reference, mismatch_code)
  WHERE status = 'OPEN' AND feed_reference IS NOT NULL;

CREATE TABLE ops.operator_notice (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  level text NOT NULL CHECK (level IN ('INFO','WARNING','ERROR')),
  message text NOT NULL
);

INSERT INTO iam.identity_provider (code, issuer, tenant_reference, configuration_status)
VALUES ('VGGOS_DEV_GATE', 'https://vggos.vgg.tools/dev-login', 'local', 'PENDING');

INSERT INTO iam.principal (provider_id, external_subject, display_name, principal_type)
SELECT id, 'dev-operator', 'Developer', 'USER'
FROM iam.identity_provider
WHERE code = 'VGGOS_DEV_GATE';

INSERT INTO core.app_migration (version) VALUES ('001_advancly_slice');

COMMIT;
