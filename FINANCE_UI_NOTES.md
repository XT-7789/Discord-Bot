# Readable navigation and Finance

Main Menu is now a Player Hub: player level and XP bar, Wallet/Bank with transfer buttons, Nation city/land/unit counts and production readiness, a current or claimable mission with progress and rewards, and Casino/VIP Status/Profile shortcuts. VIP Status is read-only, checks the interaction member's SVIP role and existing paid VIP expiry, and never purchases membership. Daily remains under Economy > Earn. These are live database summaries, not decorative sample numbers. Native Discord controls retain their platform-defined size.

Finance shows Wallet and Bank separately, with Deposit and Withdraw directly below. Transfers reuse the existing banking rules and refresh the original Finance message with the updated balances and a success notice. Assets and Exchange remain accessible.

Help appears only in the Player Hub footer. Shared Back uses the current panel's route history rather than a fixed parent category; Menu resets the route to the hub. Histories belong to individual views, not a global per-player stack. Directly opened pages return to the hub when no previous route exists. Footer buttons share one row: Help / Close on the hub, Back / Menu / Close elsewhere. Active Blackjack retains its existing explicit exit flow. Component counts remain within Discord's 40-component limit.

Offline integration coverage checks all player pages, transfers in both directions, insufficient funds, in-place results, actual Back routes, isolated open-panel histories and Help placement. No live Discord login is used in these tests.
