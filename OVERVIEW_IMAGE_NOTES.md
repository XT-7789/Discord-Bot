# Personal overview image dashboard

The overview now renders one 960px-wide PNG with three columns and up to three rows. Real Discord buttons stay below the image, followed by the existing footer. Saved section preferences, ownership checks, and preview-only navigation are unchanged. No game rules or assets are modified.

The renderer receives a plain snapshot; database reads stay on the interaction thread. Pillow runs via asyncio.to_thread after component acknowledgement. The bounded cache stores at most 32 player/content-specific PNGs for 60 seconds; views always read fresh state first. Font or rendering failures retain the functional text overview. Font is bundled IBM Plex Sans, unmodified, from https://github.com/google/fonts/tree/main/ofl/ibmplexsans with its SIL OFL license in assets/fonts/OFL.txt. No runtime downloads.

Verification: temporary-database regressions plus image/cache/fallback tests; desktop 960px and simulated 390px previews inspected. Representative nine-card render approximately 0.07 seconds / 80 KB on the development PC; cached retrieval below 0.001 seconds. These are not Termux performance measurements.

Phone rollout: git pull --ff-only, restart the existing bot, and open a fresh /overview. Do not start a second bot. Confirm all sections, Customize/Save, Refresh, opening a detail and Back, and Close. Check that old images disappear when leaving. Actual Discord mobile screenshots and Termux cold/cache timing remain PENDING acceptance.

Discord controls button sizing and image display scale. Tap the image to read full-size details. Abbreviated totals are explained in the image; full values remain available in detail panels. Existing compact-layout documentation is superseded for the successful image path and remains relevant only to fallback.
