# X SYSTEM delivery roadmap

User-approved order. Current focus: NOW / Economy Centre UI. Existing implementations are a baseline, not proof of final mobile acceptance. Do not restart completed code or treat historical tier names as permission to add new features.

## NOW

1. X SYSTEM Main UI — implemented; final visual acceptance pending.
2. Economy Centre UI — current focus: readable accounts, earnings readiness, market and Casino groups; in-place actions; consistent navigation.
3. Profile UI — implemented baseline; review after Economy.

NOW acceptance: real player values, clear primary actions, no bank-only colour emphasis, Help only on the hub, single-row footer, per-panel Back history, no accidental payments, and mobile screenshot review.

## T7

4. Warfront 2.0
5. Attack Planner
6. Defence / Army / Reports

Review existing implementations in this order. Preserve city/multi-recruit confirmations. Check target-selection persistence, ownership, stale interactions, page lengths and in-place results before adding features.

## POST-T7

7. Command Cleanup
8. Mines / Economy Integration
9. Economy Balance
10. VIP Balance

Audit public command routes before removing aliases. Balance work starts with evidence and a proposed change list; do not invent new prices, rewards, odds or paid membership benefits without review.

## SYSTEM

11. Database / Code Cleanup
12. Permission & Error UI
13. Mobile UI Testing

Preserve production data. Database cleanup requires exact scope, backup and migration checks. Test denied actions and expired interactions. Final mobile acceptance must use actual Discord evidence; offline component tests alone are insufficient. Critical safety or data-integrity bugs may be fixed earlier without expanding features.

## FUTURE

14. More Economy content
15. War expansion
16. Events / seasonal systems
17. Larger X System expansion

Backlog only until current stages are accepted and feature scope is agreed.

## Working rules

- Discuss in Chinese; player UI and Help remain English.
- Prefer easier existing gameplay over additional modes.
- Keep completed work; record implementation, offline tests and live acceptance separately.
- Do not log in to Discord or start a second bot on the PC for testing.
- No unsolicited reminders, scheduled work, or automatic balance changes.

## Current checkpoint

Player UI baseline through commit cc1a55d has offline coverage for 35 entry routes within 27 tests. Main/Economy/Profile are not awaiting first implementation; they are awaiting final refinement and mobile acceptance. Economy now also has a read-only Refresh shortcut to recheck balances and Daily readiness without navigating away.
