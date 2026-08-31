"""Shared Discord Components V2 panels for X BOT."""
import discord


def panel(title: str, body: str, *, colour=discord.Color.blurple(), footer: str | None = None):
    """Build the shared X BOT information card.

    Keeping the title and content in separate components gives every command
    the same easy-to-scan hierarchy on Discord desktop and mobile.
    """
    view = discord.ui.LayoutView(timeout=180)
    container = discord.ui.Container(accent_color=colour)
    container.add_item(discord.ui.TextDisplay(f"## {title.strip()}"))
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(discord.ui.TextDisplay(body.strip() or "No information is available yet."))
    if footer:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
    view.add_item(container)
    return view


def success(title: str, body: str, footer: str | None = None):
    return panel(title, body, colour=discord.Color.green(), footer=footer)


def warning(title: str, body: str, footer: str | None = None):
    return panel(title, body, colour=discord.Color.orange(), footer=footer)


def danger(title: str, body: str, footer: str | None = None):
    return panel(title, body, colour=discord.Color.red(), footer=footer)
