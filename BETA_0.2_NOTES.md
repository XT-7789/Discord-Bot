# X BOT V2 — Beta 0.2

## Included

- Three independent currencies: XC, War Credits, and XCrystals
- Configurable jobs and `/work` rewards
- Rare XCrystal drops from work
- Dashboard-configurable Items and Shop
- Player inventory, buying, selling, and safe item effects
- Economy logs and leaderboards
- Existing Nation, Army, Capital, Alliance, and Alliance War systems
- Admin Dashboard pages for users, inventory, jobs, items, war, settings, and logs
- Automatic SQLite schema migration without resetting player data

## Player commands

- `/balance`, `/profile`, `/leaderboard`
- `/job_list`, `/job_apply`, `/work`
- `/shop`, `/buy`, `/inventory`, `/sell_item`, `/use`
- `/army_shop`, `/recruit`, `/collect`
- Existing Nation, Capital, Alliance, and War commands remain available

## Deferred to the next beta

- Reworked weighted Mine and Pickaxe system
- Crafting and Recipes
- Secure player-to-player Market and Trades
- Casino games (Wabbit remains the casino bot)

## Start

Bot:

```powershell
C:\Users\wongy\X-War-Bot\.venv\Scripts\python.exe bot.py
```

Dashboard, in a second terminal:

```powershell
C:\Users\wongy\X-War-Bot\.venv\Scripts\python.exe dashboard.py
```

Open `http://127.0.0.1:5000` and use `DASHBOARD_PASSWORD` from `.env`.
