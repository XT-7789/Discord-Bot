# Personal overview and progression guidance

## Player flow

- `/overview` opens a personal overview; Main Menu also has a **My Overview** button. `/menu` still provides the complete feature catalogue.
- The current Economy goal stays visible with **Continue** and **Choose Goal**. Existing recipes, inventory and operation records determine the next step; no new reward is granted.
- **Customize** offers Wallet & Bank, Mines, Craft & Production, Market & Stocks, Nation, Missions and VIP. Default: Wallet, Production and Missions, below the goal.
- Select changes are a draft. **Save** persists per player in the existing `economy_logs` table; **Cancel** discards the draft. **Use Defaults** previews defaults before saving. Empty selection is allowed (goal-only view).
- At most four selected sections appear on one page. Refresh re-reads data, and linked pages can return to the overview.
- Mining energy is explicitly marked last recorded; opening Mines refreshes it. Market totals exclude expired listings without processing refunds merely by opening this overview.

## Growth guidance

- Economy and the overview show the next shop-tool improvement, current wallet, remaining XC and any displayed mining-level requirement.
- Only enabled available XC tools whose displayed stats are not lower than the equipped tool are suggested. Already-owned improvements lead to Backpack. If none remains, Research is offered.
- Tool purchase details compare equipped and selected power, luck, yield bonus and cooldown reduction. Research retains its existing current-to-next benefit preview.
- Sale results show the actual income and refreshed progress toward the next tool. Navigation never purchases, equips or consumes an item automatically.

No costs, odds, fees, rewards or SVIP entitlements are changed in this release. No extra database settings or website is added.

## Acceptance

103 tests passed on 2026-09-11:

```text
python -m unittest test_admin_dashboard test_staff_tools test_tier6 test_tier7 test_tier8 test_economy_journey test_economy_transactions test_svip test_player_overview -q
```

Automated checks cover persistence through independent connections, invalid preferences, rollback, owner rejection, draft/cancel/save, all sections and empty selection, pagination/component/text budgets, growth changes and non-mutating navigation. Existing Economy, War, Admin and SVIP regression suites are also run.

Phone Discord screenshots are still required for actual visual acceptance: default overview, Customize, a fully selected second page, a tool comparison and a sale result. Offline checks do not replace those screenshots.

After pulling this update, restart the existing Bot once for command sync; do not start a second copy. Reopen `/overview` for fresh components.
