-- Private service role. Not a browser login and not an end-user grant.
-- The role itself is created by scripts/bootstrap.py so its password stays out of this file.
BEGIN;

GRANT USAGE ON SCHEMA core, iam, source, finance, audit, ops, ai TO vggos_app;
GRANT SELECT ON ALL TABLES IN SCHEMA core, iam, source, finance, audit, ops, ai TO vggos_app;

GRANT INSERT, UPDATE ON
  core.entity,
  core.currency,
  source.instance,
  source.company,
  source.sync_stream,
  source.ingestion_run,
  source.company_probe,
  finance.account,
  finance.gl_entry,
  finance.bc_master_record,
  finance.bank_account,
  finance.bank_account_secret,
  finance.bank_book_mapping,
  finance.bank_account_governance,
  ops.bank_mismatch_task,
  ops.operator_notice
TO vggos_app;

GRANT INSERT, UPDATE, DELETE ON finance.bc_gl_period_balance TO vggos_app;
GRANT INSERT ON source.raw_record, source.validation_result, audit.event, audit.export_event, ops.operator_notice TO vggos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, iam, source, finance, audit, ops TO vggos_app;
GRANT EXECUTE ON FUNCTION source.complete_run(bigint, bigint) TO vggos_app;

-- Balances and provider connections are not writable by this service until a feed exists.
REVOKE INSERT, UPDATE, DELETE ON finance.bank_balance_snapshot FROM vggos_app;
REVOKE INSERT, UPDATE, DELETE ON finance.bank_connection FROM vggos_app;
REVOKE INSERT, UPDATE, DELETE ON finance.bank_feed_observation FROM vggos_app;
REVOKE INSERT, UPDATE, DELETE ON finance.bank_dormancy_rule FROM vggos_app;

INSERT INTO core.app_migration (version) VALUES ('002_app_grants');

COMMIT;
