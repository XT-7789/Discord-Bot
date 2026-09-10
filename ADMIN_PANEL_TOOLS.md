# Guided Discord Admin Tools

## Entry and coverage

Open `/admin` → Home → **Choose a management tool…**. Choose targets, move between fields, review the complete action, then Confirm. No slash-command syntax, member IDs or role IDs need to be typed. Numeric amounts, reasons and announcement text still require text entry.

The current `STAFF_SLASH_COMMANDS` catalogue contains 14 retained shortcuts. This panel covers 12 tools plus the `/admin` entry itself: **13/14 (92.9%)**. This denominator is the retained staff shortcut set, not every historical command or every Dashboard record editor.

| Group | Panel action | Existing command |
| --- | --- | --- |
| Members | Give / remove role | `role` |
| Members | Role members | `inrole` |
| Members | Inspect backpack | `inventory_check` |
| Members | Inspect armed forces | `forces_check` |
| Members | Set activity level | `setlevel` |
| Assets | Give items | `spawn` |
| Assets | Remove items | `remove_item` |
| Assets | Adjust currency | `economy_adjust` |
| Server | Post announcement | `say` |
| Server | Draw lottery | `lottery_draw` |
| War | Start alliance war | `war_start` |
| War | End alliance war | `war_end` |

`server_settings` remains available through its existing command. Applications, Tester Reports, Verification, Reward Codes, read-only Economy status and maintenance remain in the main Admin panel. No existing shortcut is removed. Economy rules remain editable only in Dashboard.

## Interaction design

- Native Discord user, role and text-channel selectors, retaining the selected ID on redraw.
- Item and alliance dropdowns with 25 options per page, including previous/next controls.
- Currency dropdown, range-checked number forms, reason/message forms, and optional-field reset to default.
- Review page includes target names/IDs and all selected values. Role review states Give versus Remove.
- Confirm is single-use, including simultaneous clicks. If role membership or active Lottery/War ID changed, reopen and review again.
- Commands reuse their existing callbacks and validation rather than reimplementing transactions. Results remain private and include Admin Home navigation.

## Access and safety

Each action checks panel ownership and current staff access. Confirmation refreshes the acting server member and target resources, checks declared Discord permissions, and runs existing Dashboard command-access rules (enabled state, roles, users, channels and cooldown), command checks, then the original callback. This does not grant Moderators new economic or administrator rights.

No production announcements, role changes, assets, lottery draws or war actions were executed during development. Tests replace command bodies and Discord network operations, while retaining the real source signatures, option choices and range declarations. Existing gameplay regression suites run separately.

## Verification

Run `python -m unittest test_staff_tools test_admin_dashboard test_tier6 test_tier7 test_tier8 -q` from the repository environment.

Coverage includes all 12 command adapters; every field's component serialization; catalogue ratio; paginated choices; native selection persistence; optional defaults; required/range errors; revoked ownership/staff/default permissions; Dashboard role/channel/disabled/cooldown denial; stale roles and rounds; callback checks; sequential and concurrent duplicate confirmation; private results; and unavailable commands.

Real Discord mobile screenshots remain pending. Deploy to the existing Bot and reopen `/admin`; old messages do not change automatically. No production deployment or restart was performed by this implementation.
