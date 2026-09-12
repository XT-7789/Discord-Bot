# Beginner usability audit

Goal: new players can understand the UI, choose an action, and complete the existing economy loop without guessing commands. Game values and entitlements stay unchanged.

Implemented and covered by offline regressions:
- Main menu prioritizes Play, Economy, Profile and Missions, with Warfront as an optional secondary entry and personal overview retained.
- Play opens a no-bet Memory Match in two clicks, displays the daily reward allowance, resumes active games and links to Casino, earning and cosmetic Collection titles.
- Memory Match uses explicit Continue after a mismatch, persists active state, expires after 30 minutes and settles each completion only once.
- Panel-launched Casino games now show a cost review before payment. Blackjack, Coinflip and Slots provide mobile amount buttons; insufficient balance and cooldown states link to Free Game.
- First-use menu guidance and English Help explain Mine → Craft → Sell, optional War/Casino, and separate XC / War Credits.
- Overview and Economy next-step buttons name their destinations instead of generic Continue.
- Recipe detail lists remaining materials and directs insufficient inventory toward mining, while retaining Advanced Craft.
- Matching-mine page repeats missing quantities and offers a recipe return route.
- Empty material/product lists offer Mines and recipe selection.
- Overview uses compact one-line tile summaries with matching button rows; image, code-block and fake aligned-column renderers were removed after phone review.
- Missing/locked Pickaxe states provide Backpack, Tool Shop and Mining Hub actions instead of requiring /equip typing.
- Energy-shortage state updates the current panel with configured recovery information and crafting/selling alternatives; results use shorter button rows.
- Journey navigation retains a fresh source return; craft-result sale confirmation and duplicate settlement were exercised in temporary-database UI tests.

Still to verify before declaring the broad goal achieved:
- Actual phone legibility of the replacement text grid.
- Fresh-player end-to-end navigation including mining result → recipe → confirmed craft → confirmed sale and all reverse paths.
- Whether result/cooldown/locked states consistently expose an actionable next step across the player surfaces, not only Overview.
- Mobile screenshots for main menu, missing-material recipe, quantity preview and result screens; offline component tests are not visual acceptance.
- Mobile screenshots for Play, active Memory Match, completion/Collection and Casino review remain required.

No production bot or live assets are used in these tests. Each verified release is pushed to GitHub; the phone still needs git pull --ff-only and restart of the existing bot.
