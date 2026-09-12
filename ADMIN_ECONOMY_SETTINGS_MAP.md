# Admin / Dashboard Economy Settings Map

Updated: 2026-09-12. The existing SQLite database is the only editable source. `economy_settings_admin.py` contains validation and display metadata, not a second configuration store.

## Save contract

Dashboard settings saves use one dedicated connection and one `BEGIN IMMEDIATE` transaction. Validation, all submitted changes and the existing `dashboard_audit_logs` record succeed together or roll back together. Audit records contain actor ID/name/role, endpoint, field names, and before/after values. Passwords, tokens and CSRF values are not included. Existing role policy remains unchanged. Discord Admin displays these settings read-only.

The table covers 75 unique settings keys. All rows use `economy_settings(key,value)`; the field key is the database key. Bounds are inclusive integers; “or more” is bounded by SQLite signed 64-bit maximum (9223372036854775807), not floating-point rounding. Paired transfer, market and casino minimum/maximum settings are also validated together. Casino global limits are checked against individual games that inherit them.

## War

| Dashboard editor | Field / database key | Bot reader | Unit / allowed range | When it applies |
| --- | --- | --- | --- | --- |
| Settings | `aggressive_attack_bonus` | `war_tier.setting` | %; 0 or more | Next action / refresh. |
| Settings | `attack_cooldown` | `war_tier.setting` | seconds; 0 or more | Next action / refresh. |
| Settings | `capital_damage` | `war_tier.setting` | HP; 1 or more | Next action / refresh. |
| Settings | `capital_repair_cost_per_hp` | `war_tier.setting` | War Credits / HP; 0 or more | Next action / refresh. |
| Settings | `capital_reward_percent` | `war_tier.setting` | %; 0–100 | Next action / refresh. |
| Settings | `demobilize_refund_percent` | `war_tier.setting` | %; 0–100 | Next action / refresh. |
| Settings | `fortified_defense_bonus` | `war_tier.setting` | %; 0 or more | Next action / refresh. |
| Settings | `fortify_base_cost` | `war_tier.setting` | War Credits; 0 or more | Next action / refresh. |
| Settings | `fortify_max_level` | `war_tier.setting` | count; 1 or more | Next action / refresh. |
| Settings | `fortify_power_percent` | `war_tier.setting` | %; 0–100 | Next action / refresh. |
| Settings | `scout_cooldown` | `war_tier.setting` | seconds; 0 or more | Next action / refresh. |
| Settings | `scout_cost` | `war_tier.setting` | War Credits; 0 or more | Next action / refresh. |

## Items / Market

| Dashboard editor | Field / database key | Bot reader | Unit / allowed range | When it applies |
| --- | --- | --- | --- | --- |
| System toggle | `auction_enabled` | `advanced_systems.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| System toggle | `economy_shop_enabled` | `economy.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings; Economy; System toggle | `market_enabled` | `economy_extra.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings; Economy | `market_fee_percent` | `economy_extra.setting` | %; 0–100 | Next action / refresh. |
| Settings; Economy | `market_max_price` | `economy_extra.setting` | XC; 1 or more | Next action / refresh. |
| Settings; Economy | `market_min_price` | `economy_extra.setting` | XC; 1 or more | Next action / refresh. |
| System toggle | `role_shop_enabled` | `advanced_systems.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Economy | `tier6_market_expiry_days` | `economy_extra.tier6_setting` | days; 1–365 | Next action / refresh. |
| Economy | `tier6_market_max_listings` | `economy_extra.tier6_setting` | count; 1–100 | Next action / refresh. |
| Economy | `tier6_stock_enabled` | `tier6.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Economy | `tier6_stock_fee_percent` | `tier6.setting` | %; 0–100 | Next action / refresh. |
| Economy | `tier6_stock_holding_limit` | `tier6.setting` | count; 1 or more | Next action / refresh. |
| Economy | `tier6_stock_max_change_percent` | `tier6.setting` | %; 1–50 | Next action / refresh. |
| Economy | `tier6_stock_price_impact` | `tier6.setting` | %; 0–100 | Next action / refresh. |
| Economy | `tier6_stock_update_seconds` | `tier6.setting` | seconds; 60 or more | Next action / refresh. |

## Accounts / Earnings

| Dashboard editor | Field / database key | Bot reader | Unit / allowed range | When it applies |
| --- | --- | --- | --- | --- |
| System toggle | `bills_enabled` | `advanced_systems.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings | `collect_cooldown` | `No active reader (legacy)` | seconds; 0 or more | Legacy only. Current City rules are in War. |
| Settings; Economy | `daily_cooldown` | `economy_extra.setting` | seconds; 0 or more | Next action / refresh. |
| Settings; Economy | `daily_reward` | `economy_extra.setting` | XC; 0 or more | Next action / refresh. |
| Settings | `exchange_war_to_xc_percent` | `economy.setting` | %; 0–100 | Next action / refresh. |
| Settings | `exchange_xc_to_war_percent` | `economy.setting` | %; 0–100 | Next action / refresh. |
| System toggle | `income_enabled` | `advanced_systems.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings | `land_income_per_land` | `No active reader (legacy)` | War Credits / Land; 0 or more | Legacy only. Current City rules are in War. |
| Settings | `mine_cooldown` | `No active reader (legacy)` | seconds; 0 or more | Legacy only. Current rules are in Mining Areas. |
| Settings | `mine_crystal_chance` | `No active reader (legacy)` | %; 0–100 | Legacy only. Current rules are in Mining Areas. |
| Mining | `mining_collection_xc_reward` | `economy.setting` | XC; 0 or more | Future collection completions only. |
| Mining | `mining_collection_xcrystal_reward` | `economy.setting` | XCrystals; 0 or more | Future collection completions only. |
| Mining | `mining_energy_enabled` | `economy.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Mining | `mining_energy_regen_amount` | `economy.setting` | energy / tick; 1 or more | Next action / refresh. |
| Mining | `mining_energy_regen_seconds` | `economy.setting` | seconds; 10 or more | Next action / refresh. |
| Mining | `mining_max_energy` | `economy.setting` | energy / player; 1 or more | Next action / refresh. |
| Mining | `mining_starter_pickaxe_enabled` | `economy.initialise` | 0 = off · 1 = on; 0–1 | Restart required: initialization grants starter tools to eligible players. |
| Settings | `starting_xc` | `economy.setting` | XC; 0 or more | New player accounts only. |
| Economy | `tier6_contracts_enabled` | `tier6.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Economy | `tier6_economy_enabled` | `tier6.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings | `transfer_max` | `economy_extra.setting` | XC; 0 or more | Next action / refresh. |
| Settings | `transfer_min` | `economy_extra.setting` | XC; 0 or more | Next action / refresh. |
| Settings; Economy | `transfer_tax_percent` | `economy_extra.setting` | %; 0–100 | Next action / refresh. |
| Settings | `work_cooldown` | `economy.setting` | seconds; 0 or more | Next action / refresh. |
| Settings | `work_crystal_chance` | `economy.setting` | %; 0–100 | Next action / refresh. |

## Casino / VIP

| Dashboard editor | Field / database key | Bot reader | Unit / allowed range | When it applies |
| --- | --- | --- | --- | --- |
| Casino / VIP | `casino_cooldown_seconds` | `casino.setting` | seconds; 0 or more | Fallback only; individual game cooldowns take priority. |
| Settings; System toggle | `casino_enabled` | `casino.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Settings | `casino_max_bet` | `casino.setting` | XC; 1 or more | Next action / refresh. |
| Settings | `casino_min_bet` | `casino.setting` | XC; 1 or more | Next action / refresh. |
| Casino / VIP | `casino_vip_cooldown_percent` | `casino.setting` | %; 0–95 | Next action / refresh. |
| Casino / VIP | `casino_vip_daily_cost` | `casino.setting` | XC; 0 or more | New VIP purchases; existing expiry dates stay unchanged. |
| Casino / VIP | `casino_vip_duration_seconds` | `casino.setting` | seconds; 60 or more | New VIP purchases; existing expiry dates stay unchanged. |
| Casino / VIP | `crash_daily_net_win_limit` | `casino.setting` | XC; 0 or more | Next action / refresh. |
| Casino / Free Games | `free_games_enabled` | `casual_games.setting` | 0 = off · 1 = on; 0–1 | Next game start or completion. |
| Settings | `lottery_prize_ratio` | `casino.setting` | %; 0–100 | Next action / refresh. |
| Settings | `lottery_starting_prize` | `casino.setting` | XC; 0 or more | New Lottery rounds; existing prize pools stay unchanged. |
| Settings | `lottery_ticket_price` | `casino.setting` | XC; 1 or more | Next action / refresh. |
| Casino / Free Games | `memory_daily_reward_games` | `casual_games.setting` | games / day; 0–10 | Next game start or completion. |
| Casino / Free Games | `memory_daily_xc_limit` | `casual_games.setting` | XC / day; 0–100 | Next game start or completion. |
| Casino / VIP | `server_svip_cooldown_percent` | `casino.setting` | %; 0–95 | Next action / refresh. |

## Production / Research

| Dashboard editor | Field / database key | Bot reader | Unit / allowed range | When it applies |
| --- | --- | --- | --- | --- |
| System toggle | `recipes_enabled` | `advanced_systems.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Economy | `tier6_industrial_speed_cap_percent` | `tier6.setting` | %; 0–95 | Next action / refresh. |
| Economy | `tier6_industrial_speed_percent` | `tier6.setting` | %; 0–100 | Next action / refresh. |
| Economy | `tier6_production_enabled` | `tier6.setting` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Economy | `tier6_production_queue_limit` | `tier6.setting` | count; 1–25 | Next action / refresh. |
| Economy | `tier6_production_seconds_per_item` | `tier6.setting` | seconds / item; 30 or more | Next action / refresh. |
| Research | `tier8_enabled` | `tier8.enabled` | 0 = off · 1 = on; 0–1 | Next action / refresh. |
| Research | `tier8_queue_limit` | `tier8.quote` | count; 1–6 | Next action / refresh. |

## Existing record editors with atomic validation

| Dashboard | Existing database columns | Validation | Bot use |
| --- | --- | --- | --- |
| Casino / Individual Game Control | `casino_game_settings.enabled` | 0 or 1 | Game availability on next action |
| Casino / Individual Game Control | `casino_game_settings.min_bet`, `max_bet` | Nonnegative integers; 0 inherits global limits; effective minimum must not exceed maximum | Existing Casino game validation |
| Casino / Individual Game Control | `casino_game_settings.cooldown_seconds` | Nonnegative seconds; 0 means no cooldown | Existing per-game cooldown takes priority over fallback |
| Research / Technology | `tier8_technologies.max_level` | 1–10 | `tier8.quote` / technology progression |
| Research / Technology | `tier8_technologies.base_cost` | 0–1,000,000 XC | New research quotes; existing jobs unchanged |
| Research / Technology | `tier8_technologies.seconds` | 0–86,400 seconds | New research jobs; existing finish times unchanged |
| Research / Technology | `tier8_technologies.bonus_per_level`, `bonus_cap` | 0–existing technology hard cap, from `tier8.TECHS` | Existing technology effects |
| Research / Technology | `tier8_technologies.enabled` | 0 or 1 | Research availability |

Both record editors use the same transaction-plus-audit contract. Other existing record editors (items, recipes, companies, contracts, finance records and War records) retain their existing behavior and navigation; this update does not claim to replace all their business validation.

## Important exceptions

- Reopen or refresh a Discord panel to display new values; already sent messages do not update themselves.
- Deploying the code requires restarting the existing Bot and Dashboard once. Ordinary live fields then apply on the next relevant operation.
- `mining_starter_pickaxe_enabled` is initialization-time behavior: restart required. It does not represent an immediate inventory grant from Dashboard.
- `starting_xc` affects new accounts; existing balances are untouched.
- VIP purchase price/duration affect future purchases, not existing expiry dates. VIP benefits and game odds were not rebalanced.
- Per-game Casino cooldowns override the global fallback. Existing saved Crash inheritance is now preserved through initialization; default Crash values remain unchanged.
- Four retained legacy keys have no active reader: `collect_cooldown`, `land_income_per_land`, `mine_cooldown`, `mine_crystal_chance`. Their form notes link to the existing War or Mining editor; saving them does not change current City/Mining rules.
- Existing production/research jobs retain quoted costs and finish times. Lottery starting prize affects new rounds, not existing pools.
