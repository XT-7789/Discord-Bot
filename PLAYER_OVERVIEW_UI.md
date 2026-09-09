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
