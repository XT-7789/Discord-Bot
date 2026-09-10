# Guided Discord Admin Tools

## Entry and coverage

Open `/admin` → Home → **Members / Player Assets / Server Tools / Alliance War**. Choose a tool and targets, move between fields, review the complete action, then Confirm. No slash-command syntax, member IDs or role IDs need to be typed. Numeric amounts, reasons and announcement text still require text entry.

Home has five sections: Needs attention, Manage players, Manage server, Access & rewards, and System. Pending counts appear on the review buttons. Backup/repair controls live on Maintenance and require confirmation. Home appears once in the footer. Actual phone rendering still needs screenshot acceptance.

## Secondary-page refresh

- Shared large section headings, short English instructions and paired action buttons. Section navigation follows page content rather than separating record pickers from their actions. Back / Home / Refresh / Close stay in one footer row.
- Reward Codes: All / Enabled / Disabled filters; automatic read-only preview of the first code on initial entry; selected status, per-redemption currency/item rewards and usage limits; explicit Enable Code / Disable Code labels. Fully redeemed codes are identified separately from disabled codes. Selections and filters survive refresh and returning from another section. A selection outside the current list is explicitly identified.
- Applications: separate Post a form and Review an application sections, with independent list-page indicators. No open form disables the posting button. Read full answers opens an owner-checked, read-only paged reader without truncating long answers.
- Tester Reports: record list, selected preview and review actions, then the feedback-posting tool. Read full report uses the same complete-content reader; returning keeps the selected record.
- Verification: role mapping separated from posting/open/close actions; unset roles display Not configured. Economy Status remains read only. Maintenance separates health inspection, backups and repairs.
- Tool categories separate inspections from state-changing tools. Multi-field forms offer Jump to a field, retaining previously entered values. Results use the same Admin heading and return to the originating category.
- Filters, record selection, full-content reading and field navigation do not execute transactions or change permissions. No rewards, prices, stored player assets or Dashboard settings are changed by this UI update.

Verification: 71 combined Admin, staff-tool, Tier 6/7/8 and Economy journey tests passed against disposable databases. New coverage includes filters, empty lists, complete long-answer/report pagination, restored selection, owner/permission rejection and field jumping without command execution. Maintenance services remain mocked. Fresh Discord phone screenshots are still required after deployment; offline checks do not replace visual acceptance.

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
