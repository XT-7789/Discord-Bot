# X BOT Maintenance Log

## 2026-09-07 — Tier 8 Research and Easier Play

- Added a compact default Lobby with a guided next action, How to Play, direct Daily/Mining buttons and saved Easy/Detailed preference.
- Added `/research` with six technologies across Economy, Industry and Military, plus a research queue page.
- Added multi-project selection and one cost confirmation, serial scheduling, automatic completed benefits and confirmed cancellation with original-cost refunds.
- Integrated technology benefits into mining yield, stock fees, production duration, single/bulk City build and upgrade costs, attack Supply and territory defence.
- Added Research navigation from Economy and Lobby, and updated Economy's Lobby return to respect the saved home-screen preference.
- Added authenticated Dashboard controls with validated ranges, request tokens, recent order history and player deletion cleanup.
- Closed the Dashboard startup database connection after migrations to avoid leaving it open.
- Research transactions and order tokens prevent duplicate spending across repeat callbacks and independent database connections.
- Updated the new-player tutorial. Daily reward remains 50 XC.
- Verification uses disposable databases and offline Discord components; it does not log the PC Bot into Discord.
- Release verification: 24 tests across T6/T7/T8, including the real Bot command catalogue and the Research Dashboard.

## 2026-09-03 — Tier 7 target-selection hotfix

- Fixed Nation-war targets disappearing whenever an unrelated Alliance war was active elsewhere on the server.
- Fixed battle validation so an unrelated Alliance war no longer cancels a valid Nation war.
- Replaced the unrestricted Discord player picker with an active-enemy Nation menu, so invalid players cannot be selected and then cleared.
- Added enemy pagination for conflicts containing more than 25 target Nations.
- Added regression coverage for mixed Alliance/Nation wars and active-enemy menu rendering.

## 2026-09-03 — Tier 7 Warfront 2.0

### Player experience

- Replaced the old `/war` overview with a compact five-page centre: Overview, Attack, Defence, Army and Reports.
- Replaced the immediate old `/attack` result with a saved Attack Planner and optional target shortcut.
- Added multi-model force selection, three attack modes, a no-cost preview and one-click Quick Attack.
- Added Land, unit-model and Defence pagination, so Nations with more than 25 choices can reach every entry.
- Kept results in the original Discord message and added War Centre, Lobby, Attack Again and Reports navigation.
- Added one-click single and batch fortification, Garrison commitment, Capital Priority and Auto Reinforce.
- Changed Tier 7 default attack cooldowns to 30 seconds globally and 5 minutes for the same target. Both are configurable in Dashboard.

### Combat and data safety

- Added persistent battle plans, selected deployments, territory defence, defence profiles, battle reports and event audit records.
- Made confirmation idempotent: one plan can create only one battle report, even if the Launch button is pressed twice.
- Added an immediate SQLite battle transaction and confirmation-time revalidation, preventing two Bot processes or stale plans from resolving the same resources together.
- Revalidates conflict, target, front-line ownership, Capital rules, unit quantities, Supply, protection and cooldowns at confirmation time.
- Capital Land cannot be transferred, cannot be attacked while ordinary Land remains, and must still be connected to the attacker's active front.
- Captured Land changes the real map row. Cities on lost Land are preserved and safely detached instead of being deleted.
- Unit losses apply only to the troops committed to that plan; reserves are not accidentally destroyed.
- Added terrain, per-Land fortification, City defence, stance, Garrison, Air support, naval control, Supply, Readiness and Morale to combat calculations.
- Added recovery for expired drafts and interrupted resolving plans.
- Recalculates selected deployment quantities when an administrator changes an attack-mode force percentage.
- Caps large casualty summaries so a completed report cannot exceed Discord's Components V2 text limit.

### Dashboard

- Updated the Dashboard version label to Tier 7.
- Added complete Tier 7 balance controls with enforced minimum and maximum values.
- Added live health counters, safe repair, Nation defence profiles, terrain/fortification editing, plan control, battle audit and event history.
- Removed the unnecessary all-world database write lock from normal War Dashboard page loads.
- Added Tier 7 cleanup to the existing player-data deletion workflow.

### Bug fixes

- Mining and Sell Materials results now replace the current panel instead of creating an orphan output.
- Mining result pages now provide Mine Again, Sell Materials, Mining Hub, Economy and Lobby buttons.
- T7 command registration replaces `/war` and `/attack` instead of adding duplicate Discord commands.
- Old or expired plans automatically reopen as a fresh safe plan instead of leaving dead buttons.
- Extended T7 panel lifetime to cover the complete saved-plan lifetime and deferred phone-sensitive database actions before work begins.

### Verification

- Python syntax/import checks cover the bot, Dashboard, Economy and Tier 7 modules.
- Offline Discord construction and serialization tests cover all five Tier 7 pages, large paginated libraries and command-scope replacement.
- Temporary-database tests cover previews, exact territory capture, connected Capital protection, balance changes, single/batch fortification, expired-plan repair, bounded reports, duplicate confirmation and two-connection concurrency.
- Flask test rendering verifies that the full War Dashboard returns HTTP 200 with Tier 7 controls.
- The final suite contains 16 passing tests across Tier 6 and Tier 7.
