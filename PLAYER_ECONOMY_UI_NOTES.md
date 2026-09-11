# Player Economy quantity-preview update

## Follow-up: clearer results and next steps

- Result pages show the completed action, current wallet and order quantity without a second prospective price quote.
- Quantity presets highlight the selected amount. Successful orders hide quantity controls until the player chooses to prepare another order.
- Craft results lead to supported product actions; purchases lead to use/equip when supported; sales lead back to earning activities. These links do not automatically buy, sell or consume items.
- Returning from a product preview restores the completed result and selected quantity with current balances. Shortcut return buttons preserve the exact source.
- Production collection opens a selector limited to the items just collected, including outputs whose recipe was subsequently removed. Collection still settles only once.
- Removed the unused legacy advanced-craft confirmation implementation.
- SVIP convenience enhancements are proposed in `SVIP_PROPOSAL.md`, not enabled.
- Phone screenshots and visual acceptance remain pending; no remote phone update or production restart was performed.

## Included

- Shared item/order preview for Shop, Backpack sales and item use, player listings, market purchases/cancellations, crafting, production orders/cancellations and stock trades.
- Applicable quantity presets, finite Max and Custom. Quantity and price forms only update the preview; settlement requires the action button.
- Current costs, balances, fees and supported item effects. Changed quotes require another confirmation.
- In-place results and live return-page factories. Production job/recipe pagination, stock company pagination and My Listings pagination.
- Shared atomic settlement and durable confirmation receipts. Market expiry, cancellation and purchase serialize against each other.
- Stock price refresh respects an existing database transaction. Production cancellation uses recorded inputs and fees for new orders, with compatibility fallback for legacy orders.
- Existing command names and configured prices, rewards, fees and entitlements retained. No production Bot was started and no live assets were used for testing.

## Verification

91 tests passed on 2026-09-11 (including the result-page follow-up):

```text
python -m unittest test_admin_dashboard test_staff_tools test_tier6 test_tier7 test_tier8 test_economy_journey test_economy_transactions -q
```

Coverage includes temporary-database trades, quantity/Max boundaries, changed prices, feature closure, insufficient assets, competing purchases/sales, duplicate confirmation, expiry/cancellation refunds, failed-write rollback, database contention, owner rejection and selected return paths.

| Page | Offline checks | Phone Discord acceptance |
| --- | --- | --- |
| Backpack | Supported actions, sale preview, owner checks, live return | Pending screenshots |
| Craft | Materials/cost/resale preview, batch route, confirmation | Pending screenshots |
| Production | Queue/cancel preview, job paging, recipe selection, duplicate collection | Pending screenshots |
| Shop | Quantity preview, stock/balance checks, price changes, atomic purchase | Pending screenshots |
| Player Market / My Listings | Fee preview, pagination, cancellation, concurrency and expiry | Pending screenshots |
| Stocks | Fee/Max calculation, holding checks, price change, transaction rollback | Pending screenshots |

Offline component serialization is not a substitute for mobile screenshots. Final visual review and any remaining navigation polish are not claimed complete by this upload.

## Phone update

In the existing Termux repository, run `git pull --ff-only`, then restart the existing Bot using your normal process. Do not launch a second copy. Reopen `/menu` for fresh components; messages posted before the update are not automatically rebuilt.

Send screenshots of an item detail, a quantity preview, a completed transaction and a second list page for mobile acceptance.
