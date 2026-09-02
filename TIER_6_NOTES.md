# X BOT Tier 6 — Economy Network

Tier 6 makes the Economy quicker to understand, adds longer-term goals, and gives Administrators one place to balance every major Economy rule.

## Player Economy Centre

- `/economy` opens a five-page panel: **Overview**, **Earn**, **Trade**, **Production**, and **Stocks**.
- Pages replace the same Discord message and keep return buttons to Economy or Lobby.
- Overview shows Wallet, Bank, War Credits, XCrystals, item value, Stock value, estimated net worth, ready rewards, and recent activity.
- Existing Wallet, Daily, Shop, Mining, Backpack, Market, Craft, and City panels remain connected, so no player data or useful action was removed.
- `/stock` is the fast command for players who want to open the virtual Stock Market directly.

## Contracts

- Daily and Weekly Contracts automatically read existing Economy activity; players do not need to activate them first.
- Default goals cover Mining, selling, City/Nation collection, development, Market trading, and Army recruitment.
- **Claim All Ready** collects every completed Contract on the page in one press.
- Each period has a unique claim record, preventing duplicate rewards after refreshes or restarts.
- Administrators can create, edit, pause, and rebalance Contracts from the Dashboard.

## Production Queue

- Players can queue multiple Recipe batches instead of repeating Instant Craft one item at a time.
- Recipe materials and XC are checked and reserved when Production begins.
- Industrial City levels reduce Production time. The bonus per level and maximum bonus are Dashboard settings.
- **Collect All Ready** collects all finished Production jobs in one press.
- Unfinished jobs can be cancelled; their current Recipe materials and XC cost are returned safely.

## Virtual Stock Market

- Eight game-only virtual companies are included by default. They do not represent real companies or real investments.
- Players buy and sell shares with XC, view average cost and profit/loss, and can sell an entire holding in one press.
- Prices update automatically using each company's volatility and trend, stay inside configured minimum/maximum prices, and react to player trades.
- Trading fees, update time, maximum price change, holding limit, and price impact are all Dashboard settings.
- Company supply is reconciled against player holdings by the Tier 6 repair tool.

## Player Market upgrades

- Old listings expire automatically after the configured number of days and return unsold items exactly once.
- Each player has a configurable active-listing limit.
- Purchases now record buyer, seller, item, quantity, price, fee, and time for Dashboard history.
- Sellers receive Contract progress from completed sales.

## Dashboard and staff controls

- **Tier 6 Economy** is a new Dashboard page with master switches, Daily reward, cooldown, taxes, Production, player Market, and Stock settings.
- Administrators can create and edit virtual companies and Contracts without editing code.
- The page links to Mining, Items, Recipes, Market, and Bills/Income editors for detailed balancing.
- Live totals, Economy activity, Stock trades, player Market trades, and Production jobs are visible in one place.
- `/admin` System Status includes Tier 6 health, and **Repair Economy** safely normalises invalid balances, Stock supply, and Production records.

## Reliability

- Tier 6 uses additive database tables and keeps all old player data.
- The public global command catalogue remains below Discord's 100-command limit.
- Automated tests cover Stock supply, one-time Contract claims, Production lifecycle/refunds, and one-time Market expiry returns.

## Phone update

After this version is pulled on Termux, restart the supervised bot process. Startup automatically creates the Tier 6 tables and default content; no manual database command is required.
