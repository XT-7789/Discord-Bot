# X BOT V2 Beta 1.1

## Economy Administration

- Added `/spawn user item quantity reason` for audited item grants.
- Added `/remove_item user item quantity reason` for audited corrections.
- Added `/economy_adjust user currency amount reason` for audited currency adjustments.
- X Council role `1523954152701431848` can use `/spawn` by default.
- `/remove_item` and `/economy_adjust` are Administrator-only by default.
- Permissions remain editable from Dashboard > Command Access.

## Item Shop Dashboard

- Added a dedicated Item Shop page modeled after Role Shop.
- Add existing Item Library entries to the shop.
- Edit price, currency, stock, sell-back price, and sellable status.
- Remove an item from the shop without deleting it or player inventories.
- Open or close the complete Economy Shop.

## Cleanup

- Disabled the obsolete `Letter of Recommendation`.
- Removed obsolete Letter copies from player inventories.
- Work Pass remains the Recommendation, Apology, and Vacation item.
- Historical economy logs and item records were preserved.

The pre-upgrade database is stored at `backups/xwar-before-beta-1.1.db`.
