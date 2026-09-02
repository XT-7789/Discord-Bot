# X BOT V2 — Tier 4

Tier 4 turns diplomacy into a button-driven system inside the existing War panel.

## Tier 4 hotfixes

- Fixed **War Centre → Operations → Recruit** failing with `ArmyShopView is not defined`.
- War and Economy now share one registered Army Recruit panel, keeping all three military branches and the return-to-War button connected.
- Fixed **War Centre → City Centre** returning Discord `10062 Unknown interaction` on slower Termux phones.
- Database/map-heavy City and Recruit pages now acknowledge the button immediately, then replace the original panel after loading.
- Added short direct commands for every major player panel: `/shop`, `/backpack`, `/market`, `/mining`, `/city`, `/army`, `/recruit`, and `/diplomacy`.
- Expanded `/lobby` with a second shortcut row for City, Army, Mining, Shop, and Market.
- City collection and City building now show their result inside the original City panel instead of posting extra result messages.
- Added **Build Many**: create up to 50 Civilian/Industrial Cities in one transaction, with automatic Land allocation and generated names.
- Added **Upgrade Many**: multi-select up to 25 Cities, review one combined cost, then upgrade the selected Cities together.
- Added **Upgrade Every City** inside the multi-upgrade page for Nations that want to raise all eligible Cities, including more than 25.
- Simplified City Centre: one **Build** button now offers one-City or several-City building, and one **Upgrade** button accepts one or multiple selected Cities.
- City Centre skips full world-map processing after a Nation already has mapped Land, making repeated City actions much faster on Termux phones.

## Player features

- Open `/war`, then press **Diplomacy**.
- **Overview** shows Alliance, war, relation, and inbox status.
- **Alliance 2.0** uses private invitations. Leaders can invite or remove members; members can leave with confirmation.
- **Relations** supports Friendly relations and seven-day Non-Aggression Pacts.
- **Declare War** now shows a confirmation page and still requires a real shared Land border.
- **Peace Offers** require the enemy Nation to accept. Accepted peace creates a 24-hour truce.
- **Nation Trade** securely exchanges XC, War Credits, and Supply after both sides still own the promised assets.
- **Inbox** handles Alliance invitations, relation requests, peace offers, and trade offers in one place.
- **Alliance Rankings** ranks Alliances by total military power.

All pages replace the same Discord message and include buttons back to the War Centre and Lobby.

## Administration and phone hosting

- `/admin` Home now includes **System Status** and **Backup Now**.
- The Dashboard includes a **Diplomacy** audit page for Alliances, relations, pending requests, and Nation trades.
- The database is backed up safely every six hours; the newest 30 backups are retained.
- `phone_start.sh` supervises both the bot and Dashboard, restarts either after a crash, requests a Termux wake lock, and writes phone logs into `logs/`.

Recommended Termux start command:

```bash
cd ~/shared/DiscordBot
chmod +x phone_start.sh
tmux new -s xbot './phone_start.sh'
```

Detach from tmux with `Ctrl+B`, then `D`. Reopen later with `tmux attach -t xbot`.
