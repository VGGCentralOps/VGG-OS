# VGG OS login through the appraisal Supabase project

Implement this before any real Advancly or bank data is reachable from a browser. The appraisal app at `C:\Users\Admin\Downloads\VGG Tools` already owns accounts. VGG OS does not get its own user list.

Same Supabase organisation does not share `auth.users`, sessions, or JWT secrets between projects. The OS database, whether it is another Supabase project or plain PostgreSQL, only stores finance data and finance permissions.

## The issuer

| Item | Value |
|---|---|
| Appraisal project ref | `qnorggoycwbbxdlvbcvq` |
| Auth base | `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1` |
| REST base | `https://qnorggoycwbbxdlvbcvq.supabase.co/rest/v1` |
| Expected issuer (`iss`) | `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1` |
| Expected audience (`aud`) | `authenticated` |
| Identity key | `iss` + `sub`. `sub` is `auth.users.id` and also `profiles.id` |
| Session signing | ES256. Public keys are at `https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1/.well-known/jwks.json` |

Confirm `iss` on the first real access token. Reject the token if `iss` or `aud` differs.

The publishable anon key in the appraisal client is an HS256 legacy API key. It identifies the project to the Auth API. It is not a session-signing key. Reject any access token whose `alg` is not `ES256`, and reject any `kid` that is not in that JWKS document. Fetch JWKS from that URL at runtime, cache it for minutes, and refresh it when a `kid` is unknown. Do not copy the key into the repo. Do not ask for the appraisal JWT secret unless a real access token arrives with `alg` of `HS256`.

## What the appraisal app does today

People sign in with email and password. `EmployeeLogin` calls `canonical_login_email` first, because old addresses (`@peopleos.co` and renamed mailboxes) are not the `auth.users.email`. Then it calls `signInWithPassword` on this project only.

`canonical_login_email(text)` is `SECURITY DEFINER`, granted to `anon` and `authenticated`. Call it with the anon key before the password grant. If the RPC fails, stop. Do not fall back to the typed address, or an alias will look like a wrong password.

There is no Entra login and no shared cookie across hosts. `vggos.vgg.tools` cannot see the session stored on `executive.vgg.tools`.

Appraisal roles are not finance roles. `user_roles.role = admin` and `employees.company_admin` grant appraisal admin. Ignore both. A person who can log in is not allowed to see a ledger until this database has a finance grant for their `sub`.

`profiles.must_change_password` is not a JWT claim. After a successful password grant, read that person’s own profile with their access token:

`GET /rest/v1/profiles?id=eq.<sub>&select=must_change_password,name,email`

Send `Authorization: Bearer <access_token>` and `apikey: <anon key>`. If the row is missing, the request fails, or `must_change_password` is true, do not open a VGG OS session. Tell them to finish the password change on the appraisal site. Fail closed.

Password reset stays on the appraisal hosts. Do not call `resetPasswordForEmail` with a `vggos.vgg.tools` redirect. The appraisal allowlist does not include that host, and recovery should keep using the company site that already works.

## Required sign-in flow

The browser never holds an appraisal access token or refresh token. An XSS bug on `vggos.vgg.tools` must not be able to call the appraisal REST API. The OS server sees the password only for the outbound Auth call, then drops it.

1. Browser `POST /api/session` with email and password over HTTPS. Rate-limit this route by IP and by email. Do not log the body.
2. Server calls `POST /rest/v1/rpc/canonical_login_email` with the anon key and `{ "_email": "<typed>" }`. Use the returned address for the next call.
3. Server calls `POST /auth/v1/token?grant_type=password` with header `apikey: <anon key>` and JSON `{ "email", "password" }`.
4. On 400, return a generic failure. Do not say whether the email exists.
5. Verify the returned `access_token` against the JWKS rules above before trusting `sub`. Check `exp`.
6. Read `must_change_password` as specified above. Refuse the session when the check fails.
7. Upsert a principal in this database keyed by issuer + `sub`. Store email and name as display fields only.
8. Load finance grants for that `sub`. No grant means 403: signed in, not permitted. Do not create a grant automatically.
9. Store the appraisal refresh token only in this server’s session row, encrypted at rest. Put a random OS session id in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie scoped to the OS host. Do not put the appraisal tokens in the cookie, in `localStorage`, or in the JSON response.
10. Discard the password.

Later requests authenticate with the OS cookie. The API resolves the session, confirms it is not expired, and authorises from finance grants. Refresh the appraisal session from the stored refresh token at `POST /auth/v1/token?grant_type=refresh_token` before it expires. If refresh fails, delete the OS session.

Logout deletes the OS session and calls the appraisal logout endpoint with the current access token so the refresh token dies there too. The access token can remain valid until `exp` (about an hour). That is acceptable only because the browser never received it.

## Grants

Create grants in the OS database, not in the appraisal project. A grant is the `sub`, a scope, and an action.

Minimum scopes for the first slice:

- `books:read` for Advancly trial balance and sync status
- `bank:read` for the Advancly register
- `export` separately from read
- no grant that means “all subsidiaries” until a group role is explicitly inserted

Seed the first people from a list this team will provide as emails. On first successful login, bind the email to `sub` once, then ignore email for authorisation. If two accounts could match one email, do not bind. Leave the grant pending and report it.

Bank-account numbers stay masked unless the grant says `bank:unmask`. The first seed should not include unmask.

## What this repo must not contain

- The appraisal service-role key
- The appraisal database password
- The appraisal JWT secret
- A copy of `employees`, reviews, leave, or planner data
- The Business Central secret in the frontend, in SQL, or in git
- The OS database service key in the browser

The anon key may live in the OS server environment. It is public in the appraisal client already. Treat a value that can read every table as the service role and reject it. The anon key’s JWT payload has `"role":"anon"`. The service role payload has `"role":"service_role"`. Check that before saving the env var.

If the OS data lives in its own Supabase project, that project’s service key stays on the OS server only. The browser talks to the OS API. It does not use either Supabase client to read finance tables. The OS project’s Auth users stay empty.

## What the OS build needs from the appraisal side

Put these in the OS server environment, not in the repository:

- `VGG_AUTH_URL=https://qnorggoycwbbxdlvbcvq.supabase.co`
- `VGG_AUTH_ANON_KEY` — the appraisal publishable key, role `anon`

Nothing else is required for verification while access tokens are ES256. Do not block the build on a JWT secret.

The appraisal team will separately send the first finance allowlist (name, email, scopes). Do not invent that list from the employee roster.

## What to send back before real data is exposed

Reply with these, and wait, before pointing a public host at live Business Central data:

1. Confirmation that `/api/session` is the only login, and that the JSON response and the cookie contain no appraisal access token or refresh token.
2. The check you run on `VGG_AUTH_ANON_KEY` to prove the role is `anon`.
3. A sample of a rejected token test: wrong `iss`, wrong `alg`, expired `exp`, and a login with no finance grant. Names of the tests are enough. Do not paste a real access token.
4. The exact browser origins you will use, including local dev. The appraisal redirect allowlist does not need them for this password flow. Send them anyway so nobody later adds a reset redirect by guesswork.
5. Where the refresh token is stored, and confirmation it is encrypted and not in logs.
6. The `sub` values of the allowlist after the first successful bind, so the appraisal side can confirm those are the intended people.

Until that reply exists, the UI can run against empty tables and a refused login. It must not serve Advancly balances to an unauthenticated browser.
