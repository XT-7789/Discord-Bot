# X BOT V2 Beta 1.3B

## Discord OAuth Dashboard

- Added Login with Discord using `identify` and `guilds.members.read`.
- Verifies that the user belongs to the configured X BOT Discord server.
- Reads current Discord role IDs at every new login.
- Uses OAuth state validation to protect the callback.
- Keeps the password login as an emergency local Owner login.

## Dashboard Access Levels

- Owner: every page and Dashboard role management.
- Administrator: full operational control.
- Economy Manager: economy pages, excluding security and access configuration.
- Council: Users and Logs read-only.
- Viewer: Overview only.

Default mappings:

- Administration (`1531176720097476708`) -> Administrator
- X Council (`1523954152701431848`) -> Council

Owner can add more role mappings from Dashboard > Dashboard Access.

## Dashboard Audit

- Records authenticated POST/PUT/PATCH/DELETE requests.
- Stores Discord actor ID, display name, access level, endpoint, result, safe form detail, and time.
- Password and secret fields are redacted.
- Audit history appears in Dashboard > Logs.

The pre-upgrade database is stored at `backups/xwar-before-beta-1.3b.db`.
