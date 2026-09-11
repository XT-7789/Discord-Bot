# Beginner usability audit

Goal: new players can understand the UI, choose an action, and complete the existing economy loop without guessing commands. Game values and entitlements stay unchanged.

Implemented and covered by offline regressions:
- Main menu prioritizes four systems, with personal overview retained. Finance/Casino and Cities/Army remain in their respective centres rather than duplicating main-menu choices.
- First-use menu guidance and English Help explain Mine → Craft → Sell, optional War/Casino, and separate XC / War Credits.
- Overview and Economy next-step buttons name their destinations instead of generic Continue.
- Recipe detail lists remaining materials and directs insufficient inventory toward mining, while retaining Advanced Craft.
- Matching-mine page repeats missing quantities and offers a recipe return route.
- Empty material/product lists offer Mines and recipe selection.
- Overview image has a no-FreeType bitmap fallback, explicit failure notice, and real numbered shortcuts.

Still to verify before declaring the broad goal achieved:
- Actual phone image legibility and Termux performance after the FreeType fix.
- Fresh-player end-to-end navigation including mining result → recipe → confirmed craft → confirmed sale and all reverse paths.
- Whether result/cooldown/locked states consistently expose an actionable next step across the player surfaces, not only Overview.
- Mobile screenshots for main menu, missing-material recipe, quantity preview and result screens; offline component tests are not visual acceptance.

No production bot or live assets are used in these tests. Each verified release is pushed to GitHub; the phone still needs git pull --ff-only and restart of the existing bot.
