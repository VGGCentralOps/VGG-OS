-- Temporary local passwords. Remove this login when appraisal sign-in is working again.
-- Passwords are not stored in this file. scripts/seed_temp_logins.py writes the hashes.
BEGIN;

INSERT INTO iam.identity_provider (code, issuer, tenant_reference, configuration_status)
VALUES ('TEMP_DB', 'https://vggos.vgg.tools/temp-db-login', 'local', 'CONFIRMED')
ON CONFLICT (code) DO UPDATE
SET configuration_status = 'CONFIRMED';

CREATE TABLE iam.temp_credential (
  email text PRIMARY KEY CHECK (email = lower(email) AND position('@' IN email) > 1),
  display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
  password_hash text NOT NULL CHECK (length(password_hash) > 20),
  external_subject text NOT NULL UNIQUE CHECK (length(btrim(external_subject)) > 0),
  active boolean NOT NULL DEFAULT true
);

COMMENT ON TABLE iam.temp_credential IS
  'Temporary database sign-in while appraisal auth is being repaired. Not a second user directory. Delete these rows to remove the logins.';

GRANT SELECT ON iam.temp_credential TO vggos_app;

INSERT INTO core.app_migration (version) VALUES ('004_temp_db_login');

COMMIT;
