-- Appraisal login. Finance grants stay in this database. The dev gate is not a login.
BEGIN;

UPDATE iam.identity_provider
SET configuration_status = 'DISABLED'
WHERE code = 'VGGOS_DEV_GATE';

INSERT INTO iam.identity_provider (code, issuer, tenant_reference, configuration_status)
VALUES (
  'APPRAISAL',
  'https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1',
  'qnorggoycwbbxdlvbcvq',
  'CONFIRMED'
);

CREATE TABLE iam.finance_grant (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email text NOT NULL CHECK (email = lower(email) AND position('@' IN email) > 1),
  display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
  external_subject text,
  scope text NOT NULL CHECK (scope IN ('books:read', 'bank:read', 'export', 'bank:unmask')),
  action text NOT NULL CHECK (action IN ('VIEW', 'EXPORT', 'UNMASK')),
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'BOUND')),
  clash_detail text,
  bound_at timestamptz,
  CHECK (
    (scope IN ('books:read', 'bank:read') AND action = 'VIEW')
    OR (scope = 'export' AND action = 'EXPORT')
    OR (scope = 'bank:unmask' AND action = 'UNMASK')
  ),
  CHECK (
    (status = 'BOUND' AND external_subject IS NOT NULL AND bound_at IS NOT NULL)
    OR (status = 'PENDING' AND external_subject IS NULL AND bound_at IS NULL)
  ),
  UNIQUE (email, scope, display_name)
);

COMMENT ON TABLE iam.finance_grant IS
  'Finance permission for one appraisal subject. Email is the allowlist key until the first bind, then only external_subject is used. Login does not insert rows.';

CREATE INDEX finance_grant_subject ON iam.finance_grant (external_subject) WHERE status = 'BOUND';

CREATE TABLE iam.grant_clash (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email text NOT NULL,
  attempted_subject text,
  detail text NOT NULL,
  reported_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE iam.grant_clash IS
  'An allowlist email matched more than one account. The grant stays pending and is not bound.';

CREATE TABLE iam.os_session (
  id text PRIMARY KEY CHECK (length(id) >= 32),
  principal_id bigint NOT NULL REFERENCES iam.principal(id),
  refresh_ciphertext bytea NOT NULL,
  access_ciphertext bytea NOT NULL,
  access_expires_at timestamptz NOT NULL,
  csrf_token text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN iam.os_session.refresh_ciphertext IS
  'AES-GCM ciphertext of the appraisal refresh token, bound to the session id. Not clear text. Do not log it.';
COMMENT ON COLUMN iam.os_session.access_ciphertext IS
  'AES-GCM ciphertext of the appraisal access token. It stays on the server for refresh and logout.';

CREATE INDEX os_session_principal ON iam.os_session (principal_id);

GRANT INSERT, UPDATE ON iam.principal TO vggos_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON iam.os_session TO vggos_app;
GRANT SELECT, UPDATE ON iam.finance_grant TO vggos_app;
GRANT SELECT, INSERT ON iam.grant_clash TO vggos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA iam TO vggos_app;

INSERT INTO core.app_migration (version) VALUES ('003_appraisal_iam');

COMMIT;
