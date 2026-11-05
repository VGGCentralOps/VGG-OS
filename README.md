# VGG OS

Group books and a view-only bank-account register. Advancly Technologies NIG Ltd. is the first Business Central company to load. SQL pack v0.3 is the database baseline. This service is separate from the appraisal app.

## Run

PostgreSQL 16 listens on `127.0.0.1:54329`. The application listens on `127.0.0.1:8096`.

```text
python scripts/bootstrap.py
python -m uvicorn app.web:app --host 127.0.0.1 --port 8096
```

The database password and the token encryption key are written to `%LOCALAPPDATA%\vggos\secrets.env`. That file is outside this folder. Business Central fields in it start blank. Put the appraisal publishable key there as `VGG_AUTH_ANON_KEY`. The process refuses to start if that key's role is `service_role`.

Sign in at `http://127.0.0.1:8096` with the appraisal email and password. `POST /api/session` is the only login. The browser cookie is an OS session id: `HttpOnly` and `SameSite=Lax`. On HTTPS it is also `Secure`. Password reset stays on the appraisal site. Browser origins for this service are `http://127.0.0.1:8096`, `http://localhost:8096`, and `https://vggos.vgg.tools`. They are not appraisal redirect URLs. The public name `vggos.vgg.tools` is DNS only.

While appraisal sign-in is being repaired, one local database account works on that same form. `python scripts/seed_temp_logins.py` creates `admin@vggos.local` with every current scope so data can be pulled before role-based access exists. The password is in the secrets file as `VGGOS_TEMP_ADMIN_PASSWORD`. These sessions do not call the appraisal project. Set `VGGOS_TEMP_DB_LOGIN=0` and restart to ignore that account. Remove `iam.temp_credential` when it is no longer needed.

## Business Central

Do not put the access key in this folder or in the browser. When Elijah provides the live HTTPS base URL, the auth method, the user name, and the access key, put them in the secrets file:

```text
VGGOS_BC_BASE_URL=https://host:port/instance
VGGOS_BC_AUTH_METHOD=basic
VGGOS_BC_USERNAME=
VGGOS_BC_ACCESS_KEY=
```

The base URL must be HTTPS and must not be an `/ODataV4/` address. The service appends `/api/vgg/finance/v1.0` itself. It then reads companies and continues only if Advancly id `74c0cf04-6359-ef11-a85a-7c1e52340759` is in the list. The posting-date range and the reconciliation month are entered on the Books screen. They are not guessed.

`/health` reports whether the database is up and whether Business Central is configured. It does not return the secret.

## Database

`vendor/VGG_OS_SQL_Pack_v0_3.zip` is the preserved installer. `scripts/bootstrap.py` installs it into an empty database, runs the pack's two test scripts, then applies `db/migrations`. Do not run `install.sql` again after `core.schema_version` contains `0.3.0`.

Mono is not called. Statement files are not read. No balance is stored. Dormancy is stored as a disabled rule with empty thresholds, and no account is flagged.
