# First Business Central pull

Follow these in order. Each step is done only when its check passes. Do not skip ahead.

The first load is read-only Advancly: chart of accounts, bank accounts, general-ledger entries for one date range, and a trial-balance check. No post, no edit, no payment.

## Already done

- The VGG OS database is on the Central Ops Supabase project. SQL pack 0.3.0 and the four app migrations are installed. No Business Central rows are in it yet.
- openfortivpn is installed in Ubuntu on this PC.
- The VPN gateway is `63.33.230.187`, port `10433`, username `alfred.mulinge`. The password is already in the Ubuntu config. It is not the Business Central password.
- When the tunnel was up, `10.250.50.9` ports `8048`, `7148`, and `8080` were open.
- `http://10.250.50.9:8048/BCTEST/api/vgg/integration/v1.0` answered. A call with no login came back `401` and asked for Basic authentication.
- The bank table Elijah published is `BankAccount`.

## 1. Bring the tunnel up

Need: the Fortigate email code for this attempt. An older code does not work.

In a terminal:

```bat
wsl -d Ubuntu -u root -- openfortivpn -c /root/openfortivpn.conf
```

When it says `Two-factor authentication token:`, paste the code from the email that arrives after this command starts, then press Enter. Leave the window open. Closing it drops the tunnel.

Done when the window says `Tunnel is up and running.`

## 2. Prove the test service is reachable

Need: the tunnel window still open.

In a second terminal:

```bat
wsl -d Ubuntu -u root -- python3 -c "import socket; s=socket.create_connection(('10.250.50.9',8048),8); print('8048 open'); s.close()"
```

Done when it prints `8048 open`.

## 3. Put the Business Central login in the secrets file

Need: a Business Central username and a read-only web service access key. The VPN password is not this login. Do not paste the key into WhatsApp, Teams, or this chat.

File: `C:\Users\Admin\AppData\Local\vggos\secrets.env`

Fill these lines:

```text
VGGOS_BC_USERNAME=
VGGOS_BC_ACCESS_KEY=
VGGOS_BC_AUTH_METHOD=basic
```

Done when those three lines are saved and the access key is not empty.

## 4. Sign in to the API and read the company list

Need: step 2 and step 3. The call is GET only.

Address:

```text
http://10.250.50.9:8048/BCTEST/api/vgg/integration/v1.0/companies
```

Use Basic authentication with the username and access key from step 3. Send the call from Ubuntu, because the tunnel is inside Ubuntu. The Windows browser cannot open `10.250.50.9` on its own.

Done when the company list returns `200`. A `401` means the username or the key is wrong.

## 5. Confirm which company is Advancly

Need: the company list from step 4.

The sandbox workbook id is `74c0cf04-6359-ef11-a85a-7c1e52340759`, name Advancly Technologies NIG Ltd. Elijah’s sample id `8d0560c1-9061-ee11-8df5-6045bd6bccca` is not that company. Use the id that the live `BCTEST` list actually returns for Advancly.

Done when Advancly’s id on `BCTEST` is written down and it matches a row in the company list.

## 6. Read the table names from the service

Need: the same Basic login. GET this address:

```text
http://10.250.50.9:8048/BCTEST/api/vgg/integration/v1.0/$metadata
```

The bank set we already saw is `BankAccount`. The sync still expects these names until the metadata says otherwise:

- `generalLedgerSetups`
- `accountingPeriods`
- `glAccounts`
- `dimensions`
- `dimensionValues`
- `dimensionSetEntries`
- `bankAccounts`
- `glEntries`
- `glBalancesByPeriod`

Done when each of those is found in the metadata, or the real `BCTEST` name is written next to it. Do not guess a missing name.

## 7. Allow the app to call this address

Need: steps 4, 5, and 6. The app still refuses an `http://` address and still defaults to `/api/vgg/finance/v1.0`.

In the secrets file, after the names are known:

```text
VGGOS_BC_BASE_URL=http://10.250.50.9:8048/BCTEST
VGGOS_BC_API_ROUTE=/api/vgg/integration/v1.0
```

Then change the connector so this private HTTP address is allowed, and so the entity names from step 6 are the ones it requests. Restart the app after the secrets change.

Done when a read from the app process reaches `companies` and returns `200`.

## 8. Run the read from the machine that has the tunnel

Need: the tunnel window still open. The app on Windows does not travel through the Ubuntu tunnel.

The sync has to run inside Ubuntu while the tunnel is up, or Windows itself has to join the VPN. Do not start the sync on Windows and expect it to reach `10.250.50.9`.

Done when the process that will pull the rows can open port `8048`.

## 9. Choose the date range

Need: a posting-date start, a posting-date end, and the month to check against balances by period. One range is enough for the first load.

Done when the start date, end date, and check month are written down.

## 10. Pull Advancly, read only

Need: steps 5 through 9.

Pull, for that company and that date range:

- chart of accounts
- bank accounts
- general-ledger entries
- balances by period, as the check

GET only. If a POST is accepted, stop. Do not create, edit, close, post, pay, or change Business Central.

Done when the run finishes without a write to Business Central, and the database has Advancly rows for those four sets.

## 11. Check the result in VGG OS

Need: step 10.

Open the bank list and the trial balance for the check month. Account numbers stay masked. A missing amount stays blank. It is not zero.

Done when the bank list shows the Advancly accounts returned by the service, and the trial balance matches balances by period for that month.
