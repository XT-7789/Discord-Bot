# SVIP convenience proposal — not enabled

## Existing behavior inspected

- `casino.cooldown_info` checks the configured server SVIP role at interaction time.
- Code defaults: server SVIP reduces Casino cooldown by 75%; paid Casino VIP by 50%. The stronger applicable reduction is used, not their sum. Dashboard values can differ from defaults.
- SVIP does not require the daily Casino VIP subscription payment for its role benefit.
- Existing SVIP status is displayed in the VIP panel. No production-slot or market-listing SVIP bonus was found in the current transaction paths.

## Proposed package requiring owner approval

| Benefit | Proposed addition | Default-setting example |
| --- | --- | --- |
| Production capacity | +2 active queue slots | 5 → 7 slots |
| Player-market capacity | +5 active listings | 20 → 25 listings |
| Production speed | 10% shorter remaining calculated production duration for new orders | Apply after current industrial/research reductions; retain the existing 30-second floor |
| Profile presentation | SVIP badge and a concise active-benefits section | Cosmetic only |

Keep configured costs, output quantities, resale prices, Casino odds, payouts and war damage unchanged. No passive currency grant or automatic trading. More slots improve convenience; shorter production also increases throughput, so capacity and speed need economy-balance review before activation.

## Required implementation decisions before enabling

- Confirm the numbers above, especially the 10% time reduction.
- Use existing Dashboard/settings conventions, with shared validation and shared enforcement across commands and panels; do not create a second configuration source.
- Verify current server role at order confirmation. A DM or unavailable role lookup must not silently grant perks.
- On role loss, do not confiscate items or cancel existing jobs/listings. Block new orders above the player's then-current limit; existing jobs retain their saved completion time.
- Do not retroactively shorten existing jobs. Preserve research discounts and the duration floor, without double-applying a benefit.
- Add isolated tests for role changes, expired paid VIP, combined benefits, capacity boundaries and bypasses through old entry points.

This document is a proposal only. This UI update changes no SVIP settings or entitlements.
