# IAM implementation guide

Read this in addition to `START-HERE.md`. Do not edit `START-HERE.md`. This guide replaces the line there that says to use a temporary developer login. Login is the appraisal app’s existing Supabase Auth. Build that now, before any Advancly or bank screen is reachable without a session.

The appraisal app lives at `C:\Users\Admin\Downloads\VGG Tools`. VGG OS stays in this folder. Same Supabase organisation does not share `auth.users`, sessions, or signing keys across projects.

## Issuer

| Item | Value |
|---|---|
| Appraisal project ref | `qnorggoycwbbxdlvbcvq` |
| Auth base | `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1` |
| REST base | `https://qnorggoycwbbxdlvbcvq.supabase.co/rest/v1` |
| Expected `iss` | `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1` |
| Expected `aud` | `authenticated` |
| Identity | `iss` + `sub`. `sub` is `auth.users.id` and `profiles.id` in the appraisal project |
| Session signing | ES256. Public keys: `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1/.well-known/jwks.json` |

Confirm `iss` on the first real access token. Reject the token when `iss` or `aud` differs.

The appraisal publishable key is an HS256 API key whose payload role is `anon`. It is how the server calls Auth. It does not sign user sessions. Reject an access token unless `alg` is `ES256` and `kid` is present in that JWKS document. Fetch the JWKS at runtime, cache it for a few minutes, and refresh it when a `kid` is unknown. Do not commit the key. Do not request the appraisal JWT secret unless a real access token arrives with `alg` `HS256`.

## How appraisal login works

The appraisal screen resolves the address, then checks the password.

1. `POST /rest/v1/rpc/canonical_login_email` with body `{ "_email": "<typed>" }`. The function is granted to `anon`. Old `@peopleos.co` addresses and renamed mailboxes are not the `auth.users` email. If this call fails, stop. Do not try the typed address anyway.
2. `POST /auth/v1/token?grant_type=password` with header `apikey: <anon key>` and JSON `{ "email", "password" }`, using the resolved address.

There is no Entra login and no cookie shared with `executive.vgg.tools`, `ghc.vgg.tools`, or `vigipay.vgg.tools`. A person signed in on those hosts is signed out on `vggos.vgg.tools` until this flow exists.

Appraisal admin is not a finance permission. Ignore `user_roles.role = admin` and `employees.company_admin`. A successful password check only proves identity.

`profiles.must_change_password` is not inside the token. After the password grant, call:

`GET /rest/v1/profiles?id=eq.<sub>&select=must_change_password,name,email`

with `Authorization: Bearer <access_token>` and `apikey: <anon key>`. If the call fails, the row is missing, or `must_change_password` is true, do not open a VGG OS session. Tell the person to finish the password change on the appraisal site.

Leave password reset on the appraisal site. Do not send `resetPasswordForEmail` at a `vggos.vgg.tools` redirect. That host is not on the appraisal allowlist.

## Sign-in to build

The browser must not receive an appraisal access token or refresh token. Otherwise a script bug on the OS host could call the appraisal API as that person. The OS server may see the password only for the Auth request, then discard it.

1. `POST /api/session` accepts email and password over HTTPS. Rate-limit by IP and by email. Do not log the body.
2. Resolve the email with `canonical_login_email`, as above.
3. Perform the password grant, as above.
4. A 400 from Auth is one generic failure. Do not reveal whether the email exists.
5. Verify `access_token` with the JWKS rules before reading `sub`. Reject a bad `exp`.
6. Apply the `must_change_password` check. Failure does not create a session.
7. Upsert a principal in the OS database on issuer + `sub`. Email and name are display fields.
8. Load finance grants for that `sub`. No grant is a 403: authenticated, not permitted. Never insert a grant because login succeeded.
9. Keep the appraisal refresh token only in the server session row, encrypted at rest. Set a random OS session id in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie for the OS host. The cookie, the response JSON, and browser storage do not contain appraisal tokens.
10. Drop the password.

Every later request uses the OS cookie. Resolve the session, then authorise from finance grants. Renew the appraisal session with `POST /auth/v1/token?grant_type=refresh_token` before it expires. A failed refresh deletes the OS session.

Logout deletes the OS session and calls the appraisal logout endpoint with the current access token. The access token can stay valid until `exp`, about one hour. That is acceptable because the browser never had it.

## Grants in the OS database

Do not write finance permissions into the appraisal project. A grant is `sub`, a scope, and an action.

For the first slice the scopes are:

- `books:read` — Advancly trial balance and sync status
- `bank:read` — Advancly bank register
- `export` — separate from read
- `bank:unmask` — full account numbers. Do not give this to the first seed

The appraisal team will send the first allowlist as names and emails. On the first good login, bind that email to `sub` once. After that, authorise by `sub` only. If one email could match two accounts, do not bind it. Leave the grant pending and report the clash.

## Secrets

Server environment only, never committed:

- `VGG_AUTH_URL=https://qnorggoycwbbxdlvbcvq.supabase.co`
- `VGG_AUTH_ANON_KEY` — appraisal publishable key

Before trusting the anon key, decode it and require `"role":"anon"`. Refuse the process if the role is `service_role`.

Do not put any of these in this repo or in the browser: the appraisal service-role key, the appraisal database password, the appraisal JWT secret, the OS database service key, the Business Central secret. Do not copy `employees`, reviews, leave, or planner rows into this database.

If OS data is a second Supabase project, that project’s service key stays on the OS server. Leave that project’s Auth users empty. The browser calls the OS API only. It does not use a Supabase client to read finance tables.

## Send this back before live data

Do not serve Advancly balances on a public host until these are answered:

1. `/api/session` is the only login, and neither the JSON nor the cookie contains an appraisal access token or refresh token.
2. The anon-key role check is in place and rejects `service_role`.
3. Tests reject a wrong `iss`, a wrong `alg`, an expired `exp`, and a login that has no finance grant. Name the tests. Do not paste a real access token.
4. Every browser origin you will use, including local dev. They are not added to the appraisal redirect allowlist for this password flow. List them so a reset link is not pointed here later by guesswork.
5. Where the refresh token is stored, that it is encrypted, and that it is absent from logs.
6. The `sub` values bound from the allowlist, so the appraisal side can confirm the people.
