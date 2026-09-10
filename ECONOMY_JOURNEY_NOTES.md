# Connected Economy update

## Economy 2.0 and Admin navigation follow-up

- Economy overview now shows affordable target batches, finished jobs and
  sellable crafted stock. Goals & Activity opens a focused work page with
  products, collection, queue, workshop and mining shortcuts.
- Choose Craft for profit (default), Earn XC or Prepare War support. The last
  choice is saved in existing economy_logs as journey_goal; changing focus
  grants no rewards and changes no assets or economy settings.
- Batch production offers 1 / 5 / 10 where affordable, Max, and custom quantity.
  Max respects materials, wallet and the existing 1,000-batch cap. All options
  open review, including output quantity, fee and current resale comparison.
  Queue availability and current prices are rechecked on confirmation.
- Sell Max reviews up to the existing 1,000-item UI limit. It does not sell
  immediately. Ready production can be collected from Goals & Activity using
  the existing atomic collection service.
- Admin Home is organized as Needs attention, Manage players, Manage server,
  Access & rewards, and System. Category buttons expose the existing 12 command
  tools. Section switching retains selected records and list positions.
- Tool pages show required-field progress and selected values. Back to tools
  returns to the originating category. Maintenance requires explicit review;
  opening, cancelling, or losing permission does not execute it.
- English UI, existing role policies, Dashboard-only economy-rule editing,
  recipe prices, rewards, Casino odds and VIP benefits remain unchanged.

Follow-up acceptance: test the three goals across a refresh, Max review with
insufficient/stale balance, queue collection, all Admin categories, tool return
navigation and maintenance cancellation. Use only mocked maintenance services
in automated tests. Phone screenshots remain pending; desktop/offline tests do
not establish actual mobile visual acceptance.

## Player flow

Economy now shows one next step and current material quantities. Mining results
open recipes using the latest material, review material sales, or mine again.
Craftable recipes appear first. Recipe details compare raw-material resale with
product resale after the crafting fee, and show missing materials and cash.

Craft 1 opens confirmation for instant crafting. Batch opens the existing
production queue workflow; Advanced Craft remains available. Completed crafting
opens the product page. Production offers View Products after collection.
Product pages expose only supported sale/use actions, with a fresh confirmation.
Recipe selection, item selection, and local Back navigation are retained.
Missing-material routes list appropriate mines and enforce mining-level access.

Backpack and command sales share the sale service and update journey progress.
Progress uses actual craft/production-claim and subsequent sale records, without
an additional reward. Existing inventory is usable; selling old stock before a
new craft does not count as completing that later craft.

## Resource Pack and administrator settings

- One pack requires 4 Stone, 2 Coal and 2 XC.
- Initial resale: 2 + round-half-up(1.20 × current ingredient resale total).
- With Stone = 2 XC and Coal = 4 XC, raw materials sell for 16 XC; the pack
  sells for 21 XC. After its 2 XC fee, the extra income is 3 XC (18.75%).
- No direct shop purchase, player-market trading or item-use effect.
- Existing items, recipes, ingredients and economy settings remain the source
  of truth. Only missing seed records are inserted. Existing administrator
  prices, disabled recipes and custom ingredients are not overwritten.
- Recommendations use current prices and require a 15–25% uplift. A changed,
  losing or unsuitable recipe is not promoted as beginner profit.
- Confirmation rechecks the quote; changed pricing requires another review.
- XC Voucher, Casino odds, VIP benefits and War damage rules are unchanged.

## Optional War branch

Existing War Supply Crate and Capital Repair Kit recipes remain available.
The product page explains the configured effect before confirming consumption.
War Supply Crate grants War Credits, **not combat Supply**. Repair is capped at
the existing 100 HP limit. Missing Nation, unsupported effects, unavailable
inventory and a fully repaired Capital do not consume the item.
Warfront navigation is optional; civilian profit does not require combat.

## Transaction safety

The shared craft, sale, use, queue and collection operations perform their
checks and writes in one SQLite transaction. Existing caller transactions use
a savepoint. Confirmations use single-use receipts in existing economy_logs;
independent database connections cannot replay a settled confirmation.
Production collection serializes the inventory credit and queue completion.
No new settings store or inventory schema is introduced.

## Verification / page checklist

Automated tests use disposable database copies and independent connections;
the source database is opened read-only when making a fixture. No production
Bot, live player transaction, maintenance task or production restart is run.

- Economy next-step section and existing navigation/component limits.
- Mining result actions and existing mission-reward shortcut.
- Recipe list, detail, material/product list, mine-area selection.
- Craft/sell/use/queue confirmations and result pages; owner rejection and Back.
- Normal cycle; insufficient materials/cash; disabled and unsellable items.
- Repricing, loss recommendations, custom administrator values and old stock.
- Receipt failure rollback, database busy and cross-connection replay.
- Full production queue, repeated and concurrent collection.
- Concurrent duplicate craft, optional War consumption and full-HP rejection.
- Existing admin, staff, Tier 6/7/8 player UI and transaction regressions.

Run:

```text
python -m unittest test_admin_dashboard test_staff_tools test_tier6 test_tier7 test_tier8 test_economy_journey -q
```

Phone Discord visual acceptance remains **pending**. After pulling and restarting
the existing phone process, open a fresh Economy panel and capture the mining,
recipe confirmation, product result, production and optional War-use pages.
Offline component checks do not replace actual phone screenshots.

## Phone update

Pull the latest main branch in the existing X-War-Bot checkout, then restart
the existing Bot process once. Do not start a second Bot. Open a fresh panel;
messages already sent before the update do not automatically change.
