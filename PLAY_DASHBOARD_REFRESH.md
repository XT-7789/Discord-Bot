# Casino / Play and Dashboard refresh

## Delivered
- Shared dark navy / teal dashboard shell, searchable grouped navigation, clear page headings, section jump links, refreshed cards, forms and scrollable tables.
- Overview workspace entries for Casino / Free Games, Economy and community review. Existing routes, login, permissions and settings remain unchanged.
- Versioned stylesheet and script URLs to avoid retaining the previous cached theme.
- Casino confirmation can only dispatch one round per confirmation view. Invalid game ranges and invalid Balloon Pop colours are rejected before review.
- Memory best score now keeps the lowest attempt count. Expired mismatches cannot be resumed, and expired boards no longer display active card controls.
- Play / Collection handles unavailable game starts, and Collection includes Games / Menu / Close navigation.

## Verification
- 116 automated regression tests passed using disposable databases (admin, player UI, economy, war, Casino and casual games).
- Added expired-continue and best-score tests; exercised repeat Casino confirmation in the integrated button test.
- Inspected local isolated Dashboard Overview at a narrow browser width and Casino at desktop width. No production Bot or live assets were used.
- Actual Termux / Discord phone interaction and phone browser acceptance remain pending. Browser screenshots are not a substitute for those checks.

## Deployment
Pull the latest main branch in the existing checkout, then restart the existing Bot and Dashboard processes using the project's normal launcher. Do not launch duplicate production processes. Refresh the Dashboard page; static URLs include a new cache version.

## Follow-up boundaries
This is a shared layout and interaction-hardening release, not an assertion that every historical Casino path is fully audited. Item-funded Spin / Balloon Pop preview wording, all command bypass permission paths and full phone navigation testing deserve a separate exhaustive check. Odds, reward amounts and VIP benefits were not changed.
