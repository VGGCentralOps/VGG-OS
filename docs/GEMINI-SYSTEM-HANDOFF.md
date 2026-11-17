# VGG OS — full system handoff for Gemini

**Audience:** an external model (Gemini) that has not seen this repo.  
**Purpose:** describe the whole product accurately so you can reason, review, or extend it without inventing features.  
**Repo:** `VGG OS` (local path often `C:\Users\Admin\Downloads\VGG OS`).  
**Baseline date of this write-up:** November 2026. Prefer code and migrations over older comments if they conflict.

---

## 1. One-sentence product

**VGG OS** is a private FastAPI + Jinja web app over PostgreSQL that shows **group finance books** and a **view-only bank account register**, starting with **Advancly Technologies NIG Ltd.** synced read-only from **Business Central**. It is **not** the appraisal/HR app (VGG Tools), not a bank product, and not a platform of planned modules.

---

## 2. What it is / what it is not

### It is

- A **Finance-first** operator UI for Venture Garden Group Central Ops.
- Two workstreams, **one database**:
  1. **Books** — BC company data → G/L, accounts, trial balance vs BC control totals.
  2. **Banks** — BC bank accounts register, BC book balances, bank ledger movements, mismatch tasks.
- A thin product shell: Overview, Reports catalogue, Books, Trial balance, Bank accounts, Movements, Repositories, Settings.
- Multi-company **aware**: company tabs switch entity scope; Advancly has the loaded ledger today; other companies (e.g. Avitech) can appear empty until pulled.

### It is not

- Not appraisal / people / leave / planner (that lives elsewhere).
- Not a place to create/close bank accounts or move money.
- Not Mono live banking (Mono client is stubbed closed; feed balances are not stored).
- Not a report import → validate → publish workflow (catalogue names only).
- Not a multi-domain “platform home” with HR/Ops/Risk modules (those schemas are reserved empty).
- Not allowed to invent missing amounts as zero.

---

## 3. Stack

| Layer | Choice |
|---|---|
| Language | Python 3 |
| Web | FastAPI + Jinja2 HTML templates |
| Process | `uvicorn app.web:app --host 127.0.0.1 --port 8096` |
| Database | PostgreSQL (compose locally on `127.0.0.1:54329`; production has used Central Ops Supabase) |
| Driver | `psycopg` with a small connection pool (`app/db.py`) |
| Front-end | Server-rendered HTML + `app/static/app.css`, `nav.js`, `theme.js` (no React SPA) |
| Auth libs | httpx, PyJWT, cryptography, itsdangerous |
| Tests | pytest under `tests/` |

**Dependencies:** `requirements.txt`.

---

## 4. How to run locally

```text
python scripts/bootstrap.py
python -m uvicorn app.web:app --host 127.0.0.1 --port 8096
```

- App: `http://127.0.0.1:8096`
- Secrets file (outside the repo): `%LOCALAPPDATA%\vggos\secrets.env`  
  (override with env `VGGOS_SECRETS_FILE`)
- Browser origins allowed for this service:
  - `http://127.0.0.1:8096`
  - `http://localhost:8096`
  - `https://vggos.vgg.tools` (DNS public name)
  - `https://vgg-os.onrender.com`

**Health:** `GET /health` → JSON like:

```json
{
  "service": "vgg-os",
  "database": "up",
  "business_central": "configured",
  "sql_pack": "0.3.0"
}
```

Does not return secrets. 503 if DB down / settings failed.

---

## 5. Repository layout

```text
VGG OS/
  app/                 # FastAPI application
    web.py             # routes, page shell, guards
    queries.py         # read queries for screens
    sync_service.py    # Business Central pull
    bc_client.py / bc_url.py / bc_entities.py
    entity_scope.py    # company switch cookie + resolution
    login_flow.py / temp_login.py / session_* / appraisal.py / jwks.py
    repository.py      # library files
    feeds.py           # Mono/statement stubs (always closed)
    templates/         # Jinja pages
    static/            # CSS + JS
  db/migrations/       # 001–007 after SQL pack
  scripts/             # bootstrap, seed logins, pull_advancly, pull_books, …
  docs/                # operator / engineer docs (this file lives here)
  vendor/VGG_OS_SQL_Pack_v0_3.zip   # DB installer baseline
  tests/
  docker-compose.yml   # local Postgres 16
  README.md
```

---

## 6. Database baseline

### SQL pack v0.3

- Installer zip: `vendor/VGG_OS_SQL_Pack_v0_3.zip`
- Version string in app: `0.3.0` (`app/constants.py` → `SQL_PACK_VERSION`)
- `scripts/bootstrap.py` installs pack into empty DB, runs pack tests, then applies `db/migrations`
- **Do not** re-run `install.sql` once `core.schema_version` contains `0.3.0`

### Schemas (pack)

| Schema | Role |
|---|---|
| `core` | entities, periods, currencies, schema_version, … |
| `source` | BC instance/company, sync streams, runs, raw/master records |
| `finance` | accounts, G/L, banks, reports catalogue, repository, … |
| `iam` | principals, grants, sessions, temp credentials |
| `ops` | bank mismatch tasks, notices |
| `audit` | export / audit events |
| `ai`, `hr`, `risk`, `legal`, `product` | reserved / future |

### App migrations after pack

| Migration | Purpose |
|---|---|
| `001_advancly_slice.sql` | BC masters, period balances, bank governance/secrets/dormancy/feeds, probes, mismatch tasks |
| `002_app_grants.sql` | `vggos_app` grants; revokes writes on feed/snapshot tables |
| `003_appraisal_iam.sql` | finance grants + OS sessions + appraisal provider |
| `004_temp_db_login.sql` | temporary local/operator logins |
| `005_repository.sql` | file library nodes/blobs |
| `006_bank_ledger_and_write_scopes.sql` | bank ledger entries + `books:sync` / `files:write` |
| `007_entity_scope_indexes.sql` | indexes for entity-scoped screens |

### Core entity / company model

- `core.entity` — group and legal entities (`VGG_GROUP`, `ADVANCLY`, derived codes for other BC companies).
- `source.company` — one BC company GUID bound to one entity.
- Sync creates/updates entities under `VGG_GROUP`. Advancly keeps stable code `ADVANCLY`. Other companies get codes like `{NAME}_{first8hex}` (`entity_code_for` in `sync_service.py`).

---

## 7. Authentication and permissions

### Session cookie

- Name: `vggos_session`
- Random OS session id; **HttpOnly**, **SameSite=Lax**; **Secure** on HTTPS
- Browser must **not** receive appraisal access/refresh tokens

### Company preference cookie

- Name: `vggos_entity` (not HttpOnly)
- Set when pages render; also written client-side on company tab switch
- Resolution order: `?entity=` → cookie → `ADVANCLY` if present → first loaded company

### Scopes

| Scope | Meaning |
|---|---|
| `books:read` | Overview, reports, books, trial balance, repositories browse |
| `bank:read` | Bank accounts + movements |
| `export` | CSV exports |
| `bank:unmask` | Full account numbers (secrets table) |
| `books:sync` | Start BC sync |
| `files:write` | Upload/delete library files |

### Current login mode (important)

Defaults in settings/seed:

- `VGGOS_TEMP_DB_LOGIN=1` (on)
- `VGGOS_APPRAISAL_LOGIN=0` (off)

So production/operator access today is typically via **`iam.temp_credential`** accounts (e.g. `ops@vggos.vgg.tools`, `admin@vggos.local`), **not** live appraisal password login. Appraisal path exists in code for when allowlist + `VGGOS_APPRAISAL_LOGIN=1` are ready.

Login endpoint: **`POST /api/session` only**.

Anon key for appraisal (when used) must be role `anon`; process **refuses** `service_role`.

---

## 8. Business Central sync

### Secrets (never in browser / never in git)

```text
VGGOS_BC_BASE_URL=https://host:port/BC230
VGGOS_BC_API_ROUTE=          # blank → /api/vgg/finance/v1.0
VGGOS_BC_AUTH_METHOD=basic
VGGOS_BC_USERNAME=
VGGOS_BC_ACCESS_KEY=
```

Base URL must be HTTPS and must not be an `/ODataV4/` address.

### First company

- Name: Advancly Technologies NIG Ltd.
- BC company id: `74c0cf04-6359-ef11-a85a-7c1e52340759`
- Local entity code: `ADVANCLY`

### Pull entry points

- `scripts/pull_advancly.py` — Advancly only
- `scripts/pull_books.py --scope one|all`
- `POST /sync` exists for `books:sync` (background thread), but the **Books HTML page currently has no sync form** — operators usually pull via scripts.

### Entity sets

Matched carefully (`app/bc_entities.py`); missing names are never guessed.

Typical streams:

- Master-ish: `generalLedgerSetups`, `accountingPeriods`, `glAccounts`, `dimensions`, `dimensionValues`, `dimensionSetEntries`, `bankAccounts`
- Ledger: `glEntries`
- Optional: `glBalancesByPeriod`, `bankAccountLedgerEntries`

Checkpoints advance only after successful commit. Amounts stay decimals; absent fields stay null (never coerced to 0).

### Multi-company

`run_all_companies` can bind every BC company into `source.company` / `core.entity`. UI can switch among them. Only companies that have been pulled have G/L / banks; empty companies show empty states + “Empty” on the company tab.

---

## 9. Navigation and screens

Sidebar IA (`app/templates/base.html`):

| Group | Screens |
|---|---|
| **Finance** | Overview `/`, Reports `/reports`, Books `/books`, Trial balance `/trial-balance` |
| **Banks** | Bank accounts `/bank-accounts`, Movements `/bank-movements` |
| **Library** | Repositories `/repositories` |
| **Account** | Settings `/settings` |

Context bar above content:

- **Company tabs** (soft client navigation + prefetch)
- **Period** shortcuts into trial balance / movements when a period exists

### Screen behaviours

**Overview `/`**  
Coverage KPIs for the selected company, company table, recent G/L, active banks, open mismatch tasks, library snippets. Optimized to few remote DB round trips (DB is often far from the app).

**Reports `/reports`**  
Catalogue from `finance.report` (status, cadence). Pack seeds ~14 **PENDING** report names. No publish/import UI.

**Books `/books`**  
Inventory of loaded companies and BC datasets (counts / samples). Dimension codes for Advancly are shown as reference constants.

**Trial balance `/trial-balance`**  
Month picker from loaded periods; local G/L net vs BC `glBalancesByPeriod` control; differences highlighted; CSV with `export`.

**Bank accounts `/bank-accounts`**  
Register: masked account, currency, name, **BC book balance** (from synced `bankAccounts` payload `balance` / `balanceLCY`), ownership/route/link, entry counts, last posted, mismatch panels.  
CSV includes BC balances.  
Missing balance stays blank/Missing — not invented as zero.  
This is **BC book balance**, not a Mono/bank-feed observation.

**Bank movements `/bank-movements`**  
BC bank ledger lines; filters for account / year / month; CSV.

**Repositories `/repositories`**  
Folder/file library in Postgres (≤50 MB files). Write needs `files:write`.

**Settings `/settings`**  
Theme (light/dark) + session info.

**Closed feed endpoints**

- `POST /bank-feeds/mono/pull` → 423
- `POST /bank-feeds/statement-file` → 423

---

## 10. Entity switching (design intent)

Requirement: even if only Advancly has data, the tunnel must let an operator jump company → company. When more companies get live BC data (often after env/base URL changes + pulls), UI should not need a rewrite.

Implementation:

1. List companies bound in `source.company`
2. Tabs set `?entity=` and cookie `vggos_entity`
3. Queries for TB / banks / movements / overview filter by `core.entity.code`
4. Soft navigation swaps `#main` + `#contextbar` without full reload; other companies are prefetched

Performance note: early Overview called heavy `books_inventory` on every switch (~multi-second against remote Postgres). That path was collapsed; shell company list is briefly cached; entity indexes added in migration `007`.

---

## 11. Front-end / visual system

- Server-rendered Jinja; CSP-friendly (no inline handlers; behaviour in `nav.js` / `theme.js`)
- Fonts: Manrope + Plus Jakarta Sans
- Brand colours from VGG Tools parent: blue `#298ac2`, green `#58be3c`
- Current look: quieter paper background (`#f5f7f7`), soft glass panels, denser boards
- Theme toggle: light / dark via Settings + `theme.js`
- Static cache-busting via `?v=` on CSS/JS

UI/UX handover pack `VGG_OS_UI_UX_Handover_v0_3` was treated as an **interaction north star**, not a pixel clone. Adopted: Finance IA, reports catalogue, entity/period context, quieter tokens. Dropped: platform module directory, role switcher, import/publish, fake cash/heat maps, illustrative figures.

---

## 12. Money and honesty rules

- Store and compute with `Decimal` / numeric — not JS floats for money truth.
- **Missing ≠ zero.** Null/absent BC fields stay null; UI shows “Missing” / blank.
- Bank **feed** balances are not stored (Mono closed).
- Bank **BC book** balances *are* shown from already-synced `bankAccounts` payloads.
- Account numbers masked by default; unmask is a separate scope.

---

## 13. Intentionally out of scope (do not invent)

- Mono consent, live refresh, or cash-position product screen
- Statement file parsing into balances
- Report workbook import / maker-checker publish
- Figure drawers / evidence drawers as production flows
- Platform home, subsidiaries cross-module workspace, planned HR/Ops modules
- Role preview switcher in the UI
- TYMS as a second source on the Advancly slice
- Recalculating group management packs from subsidiary sums without Finance rules
- Putting BC keys, Mono tokens, or DB passwords in the browser

---

## 14. Important constants

```text
SQL_PACK_VERSION = 0.3.0
ADVANCLY_COMPANY_ID = 74c0cf04-6359-ef11-a85a-7c1e52340759
ADVANCLY_ENTITY_CODE = ADVANCLY
Default API route = /api/vgg/finance/v1.0
```

---

## 15. Key files for navigation

| Concern | File |
|---|---|
| Routes / shell | `app/web.py` |
| Screen queries | `app/queries.py` |
| BC sync | `app/sync_service.py` |
| Entity set matching | `app/bc_entities.py` |
| Company scope | `app/entity_scope.py` |
| Login | `app/login_flow.py`, `app/temp_login.py` |
| Nav / soft switch | `app/templates/base.html`, `app/static/nav.js` |
| Bank list UI | `app/templates/bank_accounts.html` |
| Theme tokens | `app/static/app.css` |
| Operator start | `docs/START-HERE.md`, `README.md` |
| Auth design | `docs/AUTH-CONNECTION.md` |
| BC pull checklist | `docs/BC-PULL-CHECKLIST.md` |

---

## 16. How to talk about the system safely

When proposing changes:

1. Prefer **entity-scoped** reads once a company is selected.
2. Never invent balances, periods, or coverage.
3. Keep secrets out of templates and client JS.
4. Treat the UI/UX handover as guidance; ship only what real SQL pack + BC data can back.
5. Assume DB latency can be high (remote Postgres) — minimize round trips on hot paths (Overview, company switch).
6. Distinguish **BC book balance** (shown) from **bank-feed/Mono balance** (not implemented).

---

## 17. Short architecture diagram

```text
Browser (Jinja UI)
    |  cookie: vggos_session, vggos_entity
    v
FastAPI (app/web.py)
    |-- IAM (temp login today; appraisal path optional)
    |-- Queries (entity-scoped finance/bank reads)
    |-- SyncService (optional / scripted BC pulls)
    v
PostgreSQL (SQL pack 0.3 + migrations 001–007)
    ^
Business Central API (read-only; Advancly first; other companies bindable)
```

---

*End of handoff. If code and this document disagree, trust the code and migrations.*
