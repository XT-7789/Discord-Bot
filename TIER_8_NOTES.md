# Tier 8 — Research and Easier Play

## Easy Lobby

- `/lobby` opens a compact home screen by default for both new and existing players.
- One recommended next step, a short How to Play page, and direct Daily and Mining buttons reduce the number of menus beginners must learn.
- Earn, Cities and Research are the primary development choices. War remains available without being required for Economy play.
- All Activities exposes the major centres, including Backpack, Stocks, Craft, Missions and Diplomacy.
- Detailed Lobby is still available; the Easy/Detailed choice is saved per player.
- Navigation edits the current message and respects panel ownership.

## Research

`/research` opens Economy, Industry, Military and Queue pages. Research is also linked from Economy and both Lobby modes.

| Branch | Technology | Benefit per level | Default maximum (5 levels) |
|---|---|---|---|
| Economy | Mining Methods | +5% expected material yield | +25% |
| Economy | Trade Agreements | 5% reduction of stock trading fees | 25% fee reduction |
| Industry | Assembly Lines | 5% reduction of queued crafting time | 25% reduction |
| Industry | Construction Planning | 3% reduction of City build/upgrade cost | 15% reduction |
| Military | Supply Logistics | 3% reduction of attack Supply cost | 15% reduction |
| Military | Defence Engineering | +2 defence percentage points | +10 points |

- Defaults: each level costs `50 × level` XC and takes `5 × level` minutes.
- Select one or both projects on a branch page, review the combined price and completion times, then confirm once.
- Up to six projects can be queued across branches; they run sequentially. A technology can have only one unfinished level at a time.
- Research does not need a manual claim. Completed jobs grant benefits immediately, including after a restart.
- Cancel Unfinished requires confirmation and refunds the exact saved cost of unfinished jobs. Completed benefits remain.
- Mining uses whole extra drops plus a proportional chance for fractional yield, so small drops still benefit.
- Discounts round costs upward to whole currency units; queued crafting keeps its existing 30-second minimum.
- Daily base reward is unchanged at 50 XC.

## Dashboard

Research · T8 controls the master switch, queue limit, each technology's enabled status, maximum level, base cost, duration, per-level bonus and cap. The latest 100 orders show payment and completion state.

Hard benefit limits remain 30% for mining/trade/production, 20% for construction/logistics and 10 defence points. Disabling Research pauses benefits as well as new orders; existing finish timestamps do not move. Configuration changes affect future quotes and active benefit strength, but never rewrite prepaid costs or finish times.

## Data and verification

New tables are additive. Research confirmations use an immediate database transaction, saved order tokens and unique active-level constraints. Repeat clicks and concurrent attempts cannot spend twice. Outdated prices require a new review.

Tests cover queue timing, automatic benefits, restart persistence, saved-cost refunds, stale quotes, insufficient funds, effect caps, two concurrent connections, real Discord component callbacks, full offline Bot registration, Dashboard rendering/settings and access checks. T6 and T7 regression tests also remain part of the release check.

## Phone deployment

Stop the existing Bot and Dashboard supervisor first, pull this revision in `~/X-War-Bot`, then start `bash phone_start.sh` in your existing tmux session. Startup adds the tables. Open a fresh `/lobby` and `/research` after restart; old messages retain old callbacks until replaced.
