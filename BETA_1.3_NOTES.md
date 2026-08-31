# X BOT Beta 1.3

Beta 1.3 keeps the war upgrade focused on three strategic systems: Division Templates, Supply, and Readiness.

## Beta 1.3A — Mining Rework

- Four configurable mining areas with level requirements, energy costs, cooldowns, experience, and XCrystal rewards.
- Configurable material drop pools with weights, quantities, and rare-drop flags.
- Pickaxe power, luck, yield bonus, cooldown reduction, and required mining level.
- Mining energy regeneration, mining levels, experience, statistics, profile, area selection, and leaderboard.
- Dashboard Mining page for global settings, areas, drop pools, and pickaxe statistics.
- Commands: `/mine`, `/mine_area`, `/mining_profile`, `/mining_leaderboard`.

## Beta 1.3C — Access and Logging

- Command enable/disable control.
- Access modes for everyone, selected roles, selected users, or administrators.
- Multiple allowed and blocked roles.
- Allowed and blocked channels.
- Required Discord permissions.
- User, server, or global command cooldowns with bypass roles.
- Command usage logging and a Dashboard Command Access log viewer.
- Configurable Discord log channels, success/failure filters, and optional role mentions.
- Dashboard audit logging remains enabled for all data-changing actions.

## Focused War Upgrade

- Players can create named Division Templates and add owned unit models to them.
- Supply is purchased with War Credits and consumed by attacks and preparation.
- Readiness affects effective combat power and falls after battle.
- `/prepare` consumes Supply to restore Readiness.
- Dashboard War page displays and configures logistics and saved templates.
- Commands: `/division_create`, `/division_add`, `/divisions`, `/supply_buy`, `/prepare`, `/war_readiness`.

## Data Safety

- Existing data is migrated in place.
- A pre-upgrade backup is stored at `backups/xwar-before-beta-1.3.db`.
