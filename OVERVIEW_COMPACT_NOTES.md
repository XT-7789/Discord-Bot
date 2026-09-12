# Compact personal overview

- All selected overview sections now appear on one page as compact status lines.
- Shortcuts are grouped into rows of up to three buttons (nine shortcuts when all seven sections are selected).
- Goal actions share one row; Customize, Refresh, Menu and Close share the footer.
- Saved selections, live reads, ownership checks and preview-only navigation are unchanged.
- No prices, rewards, VIP benefits or assets changed.
- Discord controls button width. Status columns use bounded monospace text so they remain aligned on phones.
- The image dashboard was removed after actual phone review showed its text was too small.

This supersedes the earlier four-sections-per-page overview layout.

## Three-column stats revision

- Each group now shows three fixed-width ASCII text columns, followed immediately by the matching real button row.
- With all sections selected: Wallet / Mines / Production, Market / Nation / Missions, Craft / Stocks / VIP.
- Saved section choices still apply. Production includes Craft; Market includes Stocks.
- Each column shows a title and two short status lines. Large totals are abbreviated; destination panels retain full details.
- These are monospace text columns, not native cards. Each three-column status row is immediately followed by its matching three-button row.
- Actual phone screenshots confirmed both the image alternative and code-block styling were undesirable. The supported overview now uses normal Discord text with preserved figure-space columns.

## Readability polish

- Each tile is one compact line (`NAME · primary value · status`). This keeps three tiles per group while avoiding fake columns that drift in Discord's proportional mobile font.
- Primary values are bold, supporting values stay lighter, and each group has a native separator so rows do not visually run together.
- Craft and VIP supporting lines now report useful state rather than repeating button instructions.
- Upgrade name and affordability/level status now use two separate lines instead of a long paragraph. Budget readiness does not imply the mining level requirement is met.
- Saved-layout notice uses subdued text. One primary Continue action remains; shortcuts stay neutral.
- Button widths are controlled by Discord, but their order exactly follows the text columns.
