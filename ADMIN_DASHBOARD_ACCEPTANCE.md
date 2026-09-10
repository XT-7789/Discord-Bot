# Admin / Dashboard Acceptance Record

Date: 2026-09-10. This is a local verification record, not a production deployment certificate.

## Automated checks

Command: `.venv/Scripts/python.exe -m unittest test_admin_dashboard test_tier6 test_tier7 test_tier8 -q`

Result: **43 tests passed**. Discord library deprecation warnings remain; no test failures.

Tests use temporary databases and independent connections. The source database is opened read-only for a temporary test copy. The browser preview copies schema only and uses fictional records. No real player assets, production maintenance, production settings, Discord posting or second production Bot were exercised.

| Area | Checked | Result |
| --- | --- | --- |
| Save linkage | Dashboard commit read from another connection through Bot readers; exact old/new audit values | Passed |
| Invalid input | Negative, noninteger, out-of-range, overflow, conflicting min/max, unexpected fields | Rejected; unchanged database |
| Switches | Existing feature flags plus Mining and Research | Passed |
| Permissions | Viewer/economy-manager denial under existing policy; Research CSRF | Passed; no role expansion |
| Database busy | Independent writer lock | Retry error; no partial save |
| Failed audit | Injected database trigger failure | Entire save rolled back |
| Existing transactions | Tier 6/7/8 suites, existing jobs and asset snapshots | Passed |
| Casino initialization | Explicit Crash global inheritance survives restart initialization | Passed |

## Discord Admin page-by-page

These are offline component/callback checks. They do not certify Discord mobile rendering.

| Page / flow | Checked | Result |
| --- | --- | --- |
| Home | New LayoutView container, page navigation, maintenance controls | Passed; maintenance services mocked |
| Applications | Form/application selectors, page offsets, selected record, accept/hold/deny, result refresh | Passed |
| Tester Reports | Report selector, pagination, reward/reject and results | Passed |
| Verification | Form selection, toggles, panel-post action | Passed; public posting mocked |
| Reward Codes | Selector, pagination, create/edit submission, duplicate handling, refreshed results | Passed |
| Economy | Features, Daily, Market, Casino, Production/Research sections | Passed; read-only |
| Shared navigation | Back history, Home, Refresh, Close; retained selection IDs and offsets | Passed |
| Component limits | More than 25 records, paginated dropdowns, maximum component count | Passed |
| Every action / modal | Owner check and current staff permission; revoked access | Passed |
| Error/results | Duplicate rollback and generic failure response | Passed |

## Dashboard route coverage

Every original grouped navigation destination is retained and successfully server-rendered by the automated test. This does not imply every record editor was visually inspected or submitted in a real browser.

| Navigation group | Pages checked for rendering |
| --- | --- |
| Overview | Overview; Dashboard Online |
| Players | Users; Levels & XP |
| Economy | Tier 6 Economy; Mining; Items & Categories; Item Shop; Recipes; Bills & Income; Role Shop; Market; Auction; Casino; Settings; Research |
| War | War; Diplomacy |
| Administration | Applications & Verification; Reward Codes; Command Access; Log Settings; Dashboard Access; Logs |

Existing login/logout and URLs are retained. Logout is not treated as a content page.

## Browser visual and interaction checks

Local isolated preview only; sample balances are fictional.

| Viewport | Pages / behavior | Result |
| --- | --- | --- |
| 1440 × 960 | Economy/Settings shared frame, grouped navigation, active link; Casino fields and VIP notes | Checked |
| 390 × 844 | Settings and Research readability; mobile navigation expand; single-column forms | Checked; 16px inputs, no horizontal document overflow |
| 820 × 1000 | Collapsible navigation breakpoint, Settings navigation and invalid min/max save | Checked; explicit failure and original values retained |
| Settings save | Dirty indicator, fictional Daily value change, save success and readback | Checked |

Final CSS adds sticky success/error notices to remain visible after restored scroll position. That final sticky positioning change has not received a completed browser recheck: the final browser interaction timed out. Do not mark it as visually accepted yet. Casino unit corrections were verified in source/metadata and automated tests after the preview server started; that preview still had the previous loaded metadata.

Browser screenshot artifact: `admin-dashboard-mobile.png` in the task's `artifacts` directory (Research at phone width). It is a browser preview, not a Discord screenshot.

## Still pending: real-device Discord acceptance

After deploying to the existing instance, reopen `/admin` and collect screenshots for:

- Home and all five subpages at normal mobile text size.
- Long application/report/code lists, next/previous pages, selected record after Refresh and Back.
- Each modal and success/error result; no clipped buttons or labels.
- Owner mismatch / revoked staff access using test accounts, not real asset changes.
- Economy read-only status after a safe staging setting save and panel refresh.

Do not run repair, backup, rewards or other production mutations simply to obtain screenshots. Those operations were mocked in this test run.
