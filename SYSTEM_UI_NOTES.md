# X SYSTEM navigation

The main menu is intentionally short: Profile, Economy, War and Missions. It does not display the old goal feed or detailed-mode toggle. Existing preferences and player data are not deleted.

## Where features live

| Main entry | Categories and tools |
| --- | --- |
| Profile | Personal progress, Backpack, Assets, Missions |
| Economy | Finance (Wallet, Bank, Assets, Exchange); Earn (Daily, Contracts, Mines, Craft, Production, Research); Market (Shop, Player Market, Stocks, Backpack); Mines; Casino; Rankings |
| War | Cities, Army, Recruit, Diplomacy, Attack, Defence, Reports, Overview |
| Missions | Starter, Daily, Weekly (Nation Level 2) |

Category menus use two real Discord buttons per row, a short X SYSTEM heading and cyan accent. Existing operational panels retain their gameplay controls and gain consistent navigation where component capacity permits. Casino loss/success colouring stays meaningful. Attachment-based maps keep their original layout.

Back returns to the parent category; Menu goes home; Close replaces the message with a closed notice. Close does not delete data, refund purchases or stop background jobs. In-progress Blackjack hands omit generic Close: players must use the explicit hand controls.

`system_ui.register` runs after existing player systems. It retains their builders and callbacks, replaces the three major entry screens, and forwards view output through a presentation layer. Modal and stored-message updates are covered. Staff commands are not wrapped.

Restart the phone bot after pulling and open a fresh `/lobby`, `/economy` or `/war`. Old posted panels retain their original callbacks until replaced or expired.
