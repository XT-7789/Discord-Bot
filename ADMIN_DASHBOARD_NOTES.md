# Admin / Dashboard Update Notes

## Delivered

- Discord Admin now uses the shared X SYSTEM-style LayoutView, including Applications, Tester Reports, Verification, Reward Codes and read-only Economy status.
- Shared Back/Home/Refresh/Close navigation retains selected records and page positions. Selectors paginate instead of cutting off after 25 entries.
- Owner and current staff permissions are rechecked for actions and modal submissions. Maintenance functionality is preserved.
- Dashboard retains its current Flask application, routes, login and access policy, with grouped navigation, dark/teal styling, readable forms and collapsible mobile/tablet navigation.
- Settings, Tier 6 Economy, Mining settings, Casino/VIP/game controls, existing system switches and Research settings/technology use explicit validation and atomic audited saves.
- Crash initialization no longer overwrites an existing administrator choice to inherit global bet limits. Default values are unchanged.

## Unchanged

No new gameplay, public website links, second configuration store, data reset, asset migration, reward/price/odds rebalance or VIP entitlement change. Existing player UI and transaction flows are retained. Detailed record editors remain accessible in their original routes.

## Loading the update

Restart the existing Bot and Dashboard after installing this code; do not launch a second production Bot. Reopen `/admin` to receive the new panel. Old Discord messages are not automatically replaced.

Once running, ordinary settings are read on the next relevant Bot action or panel refresh. Observe the field notes: starter-pickaxe initialization needs a restart; starting balance affects new accounts; VIP purchase settings affect future purchases; queued jobs and current Lottery pools keep stored values. Four legacy settings remain labeled as inactive rather than being silently wired into different gameplay.

This work did **not** restart or deploy the production services. No production repair/backup or real player transaction was executed.

## Verification and reference

- [Settings map](ADMIN_ECONOMY_SETTINGS_MAP.md): 72 unique setting keys, database/reader mapping, units, bounds and timing exceptions.
- [Acceptance checklist](ADMIN_DASHBOARD_ACCEPTANCE.md): 43 passing tests, page coverage, browser checks and outstanding real-device checks.
- `preview_admin_dashboard.py`: optional localhost-only, schema-only temporary preview with fictional records; not part of production startup.

Actual Discord phone screenshots remain required. A final sticky-notice browser recheck also remains open after a browser timeout; offline tests are not a substitute for either visual check.
