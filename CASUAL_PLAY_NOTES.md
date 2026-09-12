# Casual Play release notes

## Player flow

Main Menu → Play opens Memory Match, Casino and Collection progress. Memory Match is free, uses six buttons and can be resumed for 30 minutes. A mismatch stays visible until Continue is pressed, so network speed does not affect scoring.

The first rewarded completions of each server-local day share one XC cap. Defaults are three rewarded games and 25 XC total, distributed 9 / 8 / 8. Practice remains available after the cap. Game completion, balance credit, audit entry and collectible unlocks settle once in one transaction.

Collection titles are cosmetic: First Match, Sharp Memory, Perfect Recall and Memory Regular. One unlocked title can be displayed on Profile.

Panel-launched Casino games now show a review before payment. Blackjack, Coinflip and Slots provide 1× / 2× / 5× minimum-bet shortcuts and Custom. Results and blocked previews link back to a free game.

## Dashboard settings

Dashboard → Casino → Free Games edits the existing `economy_settings` keys:

- `free_games_enabled`: 0 or 1; checked at start and completion.
- `memory_daily_reward_games`: 0–10 rewarded completions per player/day.
- `memory_daily_xc_limit`: 0–100 XC per player/day.

The fields use the shared atomic validator and Dashboard audit log. First initialization sets the XC limit to half of the current Daily reward; later initialization preserves administrator values.

## Acceptance status

Automated coverage includes reward distribution, practice, mismatch/continue, expiry, title permissions, Dashboard validation, two-connection session creation and duplicate settlement. Actual Discord phone screenshots for Play, the card grid, completion, Collection and Casino review are still required.
