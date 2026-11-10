# VGG OS — start here

Read this file first, then the source documents it lists. Build in this folder. Do not add VGG OS to the appraisal app at `C:\Users\Admin\Downloads\VGG Tools`.

Host the finished service at `vggos.vgg.tools`. That name is only DNS. It does not mean this app shares a repository, database, or login table with the appraisal app (`executive.vgg.tools`, `ghc.vgg.tools`, `vigipay.vgg.tools`).

## What this product is

One product, two workstreams, one database.

| Workstream | Owner of the requirement | First slice you can build now |
|---|---|---|
| Books | Elijah Bello, Business Central API | Read-only sync of **Advancly** into this app’s database, then a trial balance that matches Business Central |
| Bank accounts | Melissa Omede, Central Operations | A view-only register for Advancly’s bank accounts, taken from Business Central, plus the place where a Mono or statement feed will attach |

Business Central and TYMS are the records for whether an account exists. VGG OS shows accounts and raises tasks. If the screen and the record disagree, the record wins. VGG OS does not create or close bank accounts and does not move money.

Prepared Finance group reports and a Business Central extract are different facts. Label imported workbook figures `FINANCE_PREPARED` and ledger extracts as source facts. A successful Advancly load does not mean the group reports came from Business Central.

## Do not build yet

These documents describe the destination. They are not unblocked.

- The thirteen or fourteen group report layouts. `Group Finance Reporting List (1).xlsx` is encrypted. SQL pack v0.3 seeds fourteen **pending** names only.
- Calculated statements. Only two of eight input templates are named: chart of accounts and trial balance. The other six are placeholders.
- Live Mono linking, dormancy actions, and the board coverage view. Mono coverage, price, corporate-account behaviour, the dormancy rule, and the NGN/USD rate source are unsigned.
- TYMS. It is a second record for some subsidiaries. Access is not ready, and it is not integrated with Business Central. Skip it on the Advancly slice.
- Anything inside the appraisal Supabase project: employees, reviews, leave, planner.

## Source documents

Read these. They win over this brief if a detail conflicts. This brief only records which conflict to honour while building.

| Path | What it is |
|---|---|
| `C:\Users\Admin\Downloads\Finance\Finance\VGG_OS_Proposed_Implementation_Plan_v0_2.docx` | 3 Nov 2026. Delivery 1 is prepared reports plus one subsidiary. Mono is outside that delivery. |
| `C:\Users\Admin\Downloads\Finance\Finance\VGG_OS_Data_Architecture_and_Model_v0_3.docx` | Logical model. PostgreSQL. Server-side ingestion. Identity is an external issuer, not email. |
| `C:\Users\Admin\Downloads\Finance\Finance\VGG_OS_UI_and_Reporting_Specification_v0_1.docx` | Proposed screens and API shapes. Routes in that file are not already built. |
| `C:\Users\Admin\Downloads\Finance\Finance\VGG_OS_SQL_Pack_v0_3.zip` | Fresh PostgreSQL 15+ baseline. Install into an **empty** database. Do not run it over any existing app database. |
| `C:\Users\Admin\Downloads\Finance\Finance\BC230_API_Developer_Guide.pdf` | Same guide as the copy under COPs Engineering. Read-only `GET /api/vgg/finance/v1.0`. |
| `C:\Users\Admin\Downloads\COPs Engineering\ERP Governance\Business Central 23\Integration\BC230_API_Developer_Guide.pdf` | Duplicate of the guide. |
| `C:\Users\Admin\Downloads\COPs Engineering\ERP Governance\Business Central 23\Data\Sandbox Companies Integration ID.xlsx` | Sandbox company ids. |
| `C:\Users\Admin\Downloads\COPs Engineering\ERP Governance\ERP Issue_Enhancement Log.xlsx` | Why VGG OS exists: group reports and consolidation were pushed here from the ERP backlog. |
| `C:\Users\Admin\Downloads\VGGOS_Bank Account Governance_BRD.pdf` | 18 Oct 2026. Melissa. Requirements. |
| `C:\Users\Admin\Downloads\Solution Design_Bank Account Governance.pdf` | 23 Oct 2026. Melissa. BC/TYMS records, Mono, statement files, daily mismatch tasks. |
| `C:\Users\Admin\Downloads\Finance Business Case Centralized Bank Visibility.pdf` | Subsidiary pitch. 20 accounts, three pulls a week. Illustrative money. Do not treat it as the build scope. |
| `C:\Users\Admin\Downloads\Finance\Finance\VGG Finance Reporting Workflow Documentation (1).docx` | Raphael’s current manual reports. Intercompany first, weekly cash later, bank reconciliation not a target. |
| `C:\Users\Admin\Downloads\Finance\Finance\Prototype Spec Intercompany (1).docx` | October Excel prototype. Superseded as the build path by the November plan. Use it for intercompany rules later, not as the first screen. |

## Decisions already made

- Separate codebase from `C:\Users\Admin\Downloads\VGG Tools`. Same parent domain later.
- One PostgreSQL database for VGG OS. Schemas from SQL pack v0.3: `core`, `iam`, `source`, `finance`, `audit`, `ai`, plus empty `hr`, `ops`, `risk`, `legal`, `product`.
- The solution design’s “data warehouse” means staging tables in this database, not a second product.
- Browser never holds Business Central keys, Mono tokens, or a database password. A private backend checks every query and export. Published views do not enforce who may see a subsidiary.
- Two Business Central instances exist. They authenticate differently. A company id from one instance is meaningless on the other. Never append `/api/vgg/finance/v1.0` onto a URL that already ends in `/ODataV4/`.
- The guide’s sample host is a private HTTP address. Do not ship that. Get the live HTTPS base URL, instance, auth method, and secret from Elijah. Store the secret in a vault reference, not in a table and not in git.
- Login is the appraisal app’s Supabase Auth, project `qnorggoycwbbxdlvbcvq`. Implement it exactly as `AUTH-CONNECTION.md` in this folder describes. The identity key is that issuer plus the token subject. Finance permissions live only in this database. Do not create a second password list.
- Missing numbers stay missing. Never store a missing balance, missing period, or failed pull as zero.
- Account numbers are masked by default.
- Payments, cash pooling, mandate changes, and editing the account register in VGG OS are out of scope.

## Advancly, the first company

From the sandbox workbook (confirm with Elijah that this id is the instance he is opening):

- Display name: Advancly Technologies NIG Ltd.
- Sandbox company id: `74c0cf04-6359-ef11-a85a-7c1e52340759`

Cost and revenue centers already extracted for Advancly. Treat them as dimension values, not as a second chart of accounts.

- Cost: `ADLC010` Spark interest expense, `ADLC020` Fuse, `ADLC030` Serve, `ADLC040` Trade, `ADLC050` Embed
- Revenue: `ADL010` Spark, `ADL020` Fuse, `ADL030` Serve, `ADL050` Embed

A fixed-asset register extract exists for Advancly. Do not build fixed assets in this slice.

Sync key for every Business Central row: `(instance, company id, entity set, record id)`.

## Elijah slice — build this

Install SQL pack v0.3 into a new empty database (`install.sql`, then the two test scripts in the zip). Preserve the zip. After that, schema changes are new migrations. Do not re-run the installer.

Server-side connector, one company, read-only:

1. `GET .../api/vgg/finance/v1.0/companies` and match Advancly’s id. If it is absent, stop and show Elijah the list. Do not guess another company.
2. Load reference and master data the guide names for a first books view: `generalLedgerSetups`, `accountingPeriods`, `glAccounts`, `dimensions`, `dimensionValues`, `dimensionSetEntries`, `bankAccounts`.
3. Load `glEntries` in posting-date slices, follow `@odata.nextLink`, page with `Prefer: odata.maxpagesize=2000`. Upsert on the sync key. Keep decimals as decimals, not JavaScript numbers.
4. Checkpoint per the guide: overlap the previous watermark, advance the checkpoint only after the run commits, one run per stream at a time. A failed run leaves the last good checkpoint in place.
5. Reconcile to `glBalancesByPeriod` for one chosen month. Amounts on one `transactionNumber` sum to zero. Show rejected or unmapped rows. A second run adds no duplicate ledger lines.

Minimum screen:

- Sync runs: status, counts, checkpoint, last error.
- Advancly trial balance for a selected month, from the loaded entries, beside the Business Central control total.
- Advancly bank accounts from `bankAccounts`: number masked, currency, name. These are ledger bank accounts, not proof of a Mono link.

Do not render a profit-and-loss or a group pack from this load.

## Melissa slice — build this for Advancly only

Use the bank accounts just loaded as the expected register for one subsidiary. Each row needs: owning entity (Advancly), masked account number, currency, Business Central account id, accountable owner (empty until named), data route (`UNASSIGNED`, later `MONO` or `STATEMENT_FILE`), and link status.

Daily check, even before Mono exists, so the screen is honest:

- In Business Central, no feed connected → task for Central Ops.
- A feed id with no Business Central account → task for Treasury and Internal Audit. This stays empty until a feed exists.
- Linked but stale → task for the account owner. This stays empty until a feed exists.

Leave a Mono client interface and a statement-file drop behind those routes. Do not call Mono and do not invent balances. The business case and the BRD describe the later pilot: 10–15 accounts, 3–4 banks, one bank on statement files, 4–6 weeks. Alerts can be email until a task screen exists. Dormancy is a stored rule per account (group default overridable). Do not flag dormant from a missing or failed pull. The unsigned default from the design, for when Treasury confirms it, is inactive after 6–12 months without a customer-initiated transaction and dormant after 12 months. Bank charges, interest, and reversals do not count as activity. Do not turn that default on as if it were signed.

## Suggested build order

1. Empty PostgreSQL, SQL pack v0.3 installed, tests green.
2. Backend process with the Business Central secret outside the repo. Health check that does not print the secret.
3. Advancly sync and the reconciliation screen.
4. Bank-account register projected from that company’s `bankAccounts`, with the three mismatch states and masked numbers.
5. Only then ask Elijah for a second company, and ask Melissa for the Mono coverage list and one sample statement from an uncovered bank.

## Open items to ask, not invent

- Elijah: which instance, HTTPS base URL, auth method, secret handling, and whether the Advancly id above is that instance. Date range for the first ledger load. Which month Finance will accept as the reconciliation sample.
- Melissa / Treasury: named owner for each Advancly bank account. Which of those accounts Mono can cover. Who links a new account after the first bulk link. Where governance fields live (tables keyed to the Business Central id, or custom fields in Business Central).
- Finance: password for the group reporting workbook, and whether the two weekly cash sheets are one report or two. The pending catalogue codes are `COMPLIANCE`, `WEEKLY_CASH_1`, `WEEKLY_CASH_2`, `MANCO_FINANCIALS`, `VGG_SOFP`, `INCUBATED_INVESTMENTS`, `INTERCOMPANY_OWINGS`, `GROUP_MANAGEMENT`, `SERVICE_COMPANIES`, `ACTUAL_BUDGET`, `GROUP_MONTH_ON_MONTH`, `SUBMISSION_HEAT_MAP`, `PARTNERS_EXPENSES`, `DEBT_OBLIGATIONS`.
