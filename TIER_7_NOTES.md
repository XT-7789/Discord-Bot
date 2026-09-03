# Tier 7 — Warfront 2.0

Tier 7 turns War into a fast, panel-first strategy loop while keeping the existing map, units, Diplomacy, Cities and War Seasons compatible.

## Release scope

- Five-page `/war` centre.
- Saved Attack Planner with target, paginated territory/model selection, multi-model deployment, mode, preview and confirmation.
- Quick Attack automation using only valid active conflicts and front-line territory.
- Three-front combat with limited random variance and visible battle reports.
- Per-territory terrain and fortification.
- Nation Garrison, stance, Capital Priority and Auto Reinforce.
- Dashboard configuration, history, health reporting and safe repair.
- Atomic battle settlement across multiple SQLite connections, bounded reports and connected-front Capital rules.
- No new public slash-command names; `/war` and `/attack` replace their legacy versions.

## Attack modes

| Mode | Default force | Attack modifier | Supply | Casualty scale |
|---|---:|---:|---:|---:|
| Recon Probe | 35% | -5% | 10 | 70% |
| Standard Assault | 70% | 0% | 25 | 100% |
| Breakthrough | 100% | +12% | 50 | 135% |

All values are editable under **Dashboard → War → Tier 7 · Warfront 2.0**.

## Deployment

1. Stop the phone or PC bot process.
2. Pull the same Git revision on the host.
3. Start the bot once; Tier 7 tables and settings migrate automatically.
4. Confirm the terminal shows the expected immediate guild command catalogue without duplicate global commands.
5. Open `/war`, test Preview, and only then test a real Launch Attack.

Do not run the same Discord token on the PC and phone at the same time.
