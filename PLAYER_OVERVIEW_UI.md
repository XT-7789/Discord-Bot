# Player overview design

Player Hub, Economy, Profile, Warfront, Missions, Earn, Market, Finance and Casino share a heading, short status summary, separated functional groups, nearby controls and one footer row. Help remains exclusive to the hub; Back follows each open panel's navigation history.

- Economy: accounts/transfers, daily readiness and calculated energy, markets, Casino membership and routes to creation/assets.
- Profile: level-relative XP progress, accounts, currency balances and inventory count.
- Warfront: city/land/unit counts, development, military operations and reports.
- Missions: claimed progress, ready rewards and next unclaimed objective for each category; weekly unlock remains enforced.
- Earn: daily/mining/contracts, workshop and development.
- Market: shopping, player trading, investments and inventory.
- Finance: accounts/transfers and other assets.
- Casino: wallet, recorded rounds/net result, game selection and records/membership. Bets remain optional and can lose XC.

Existing detailed selectors, confirmation flows and game results retain their information and rules. The shared presentation layer adds title consistency and separates content from controls where component capacity permits; it does not replace active Blackjack controls or remove confirmation steps.

Daily claims stay in Economy/Earn and cannot pay twice on retry. Economy transfers refresh Economy in place. No balance or membership purchase occurs simply by opening a page. Values are derived from game data; unavailable Discord role context is not represented as a verified membership tier.

Verification uses temporary-database integration tests without Discord login. Mobile layout still requires checking in Discord after deployment.

## City Centre

Production, Development and Your Cities replace the long city list. Collect is green when ready and disabled during cooldown; Refresh rechecks readiness. Build/Upgrade retain the existing selection and cost-confirmation flows. Land/Slots remains available when building space is exhausted. City inspection is read-only, paginated in groups of 25, and shows the selected City's location, level, production and discounted next-level cost (or Max level). Integration coverage includes 26-city pagination, selected details, cooldown/max-level disabled states and Discord component limits.

## Army, recruitment and remaining detail pages

Army shows total/service power and War Credit balance, service switching and paginated unit inspection (25 models per page), with direct Recruit access. Recruit shows three compact model cards per page, owned quantities and per-unit cost/power. Quantity submission opens a total-cost confirmation; no payment occurs until confirmation. Confirmation rechecks enabled status, price and funds, guards retries on the same confirmation, and refreshes the recruit panel with the result. An empty service retains service-switch controls.

Legacy player detail views are passed through the shared presentation layer: consistent titles/colours, heading groups separated visually where component capacity permits, and existing owner-checked controls and navigation. This includes economic detail pages, crafting/production/research, missions and military operations. Existing active-game controls and transaction results retain their behaviour; staff/dashboard UI is outside this player-UI pass.

Offline coverage includes the 35 player routes, component limits, recruitment confirmation before payment, repeated confirmation, changed prices, insufficient funds, and Army detail selection. This is not a claim of live visual verification on every Discord device.
