# X BOT V2 Beta 1.0

## Job Incentive System

- Added configurable Recommendation, Apology, and Vacation items.
- Added job tenure tracking for every player.
- Added configurable base drop chance, daily tenure multiplier, and maximum chance.
- Added a weighted incentive item pool with editable item, amount, and weight.
- Added preservation settings for Recommendation and Apology items.
- `/work` now rolls the incentive pool and grants successful drops to inventory.
- Department jobs use the hidden `Work Pass` item by default.
- Default incentive pool: `Work Pass` (weight 99) and `Clover` (weight 1).

Existing balances, inventories, nations, units, and job history are preserved. The pre-upgrade database is stored at `backups/xwar-before-beta-1.0.db`.
