# Player UI acceptance record

## Scope

Player interfaces only. Existing posted Discord messages are immutable until interacted with or replaced; restart the updated bot and open a fresh /menu. Staff/admin/dashboard redesign is not part of this pass.

## Verified offline

- All entries in PUBLIC_PLAYER_COMMANDS resolve to current registered commands.
- Menu/Profile/Warfront/Economy render directly through the current system entry points; every other public player command has the shared output wrapper.
- The 35 tested panel routes start with the X SYSTEM identity, serialize within Discord component limits and retain navigation.
- Overview/Earn/Trade/Production/Stocks share tab order and active colours.
- Prepared button output, stored-message edits, modal result output and command followups use the same presentation path.
- Button/modal ownership and private error guidance remain enforced.
- Gameplay notices are not reduced to small footnotes.
- Trading, recruitment, production, city selection, Army details, market paging and map-cache regression tests remain passing.

## Intentional native presentation

Discord input modals use Discord's native form layout. Map responses retain image embeds and attachments, now with X SYSTEM identity. Plain validation/error messages remain brief private messages. Active Blackjack retains its game controls and result colours.

## Not claimed

Offline coverage does not certify every data-dependent branch, every old message callback, or visual layout on every phone. No live Discord login or production transactions were performed for this audit. Mobile visual acceptance remains pending. This document must not be used to claim exhaustive visual completion.
