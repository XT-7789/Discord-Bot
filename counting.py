"""Counting channel system with milestones, rewards, and anti-spam."""
import re
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands


def initialise(db):
    """Initialise counting system database table."""
    db.execute("""
    CREATE TABLE IF NOT EXISTS counting_state (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL DEFAULT 0,
        current_count INTEGER NOT NULL DEFAULT 0,
        last_user_id INTEGER NOT NULL DEFAULT 0,
        high_score INTEGER NOT NULL DEFAULT 0
    )
    """)
    db.commit()


def get_counting_state(db, guild_id: int):
    """Get counting state for a guild."""
    row = db.execute("SELECT * FROM counting_state WHERE guild_id=?", (guild_id,)).fetchone()
    if not row:
        db.execute(
            "INSERT OR IGNORE INTO counting_state(guild_id, channel_id, current_count, last_user_id, high_score) VALUES(?, 0, 0, 0, 0)",
            (guild_id,),
        )
        db.commit()
        row = db.execute("SELECT * FROM counting_state WHERE guild_id=?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_counting_channel(db, guild_id: int, channel_id: int):
    """Set the designated counting channel for a guild."""
    db.execute(
        """INSERT INTO counting_state(guild_id, channel_id, current_count, last_user_id, high_score)
        VALUES(?, ?, 0, 0, 0)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id""",
        (guild_id, channel_id),
    )
    db.commit()


def reset_counting_count(db, guild_id: int):
    """Reset current counting score back to 0."""
    db.execute("UPDATE counting_state SET current_count=0, last_user_id=0 WHERE guild_id=?", (guild_id,))
    db.commit()


async def handle_message(bot: discord.Client, db, message: discord.Message):
    """Handle incoming messages in the counting channel."""
    if not message.guild or message.author.bot:
        return

    state = get_counting_state(db, message.guild.id)
    if not state or state["channel_id"] == 0:
        return

    # Check if message is in the designated counting channel
    if message.channel.id != state["channel_id"]:
        return

    content = message.content.strip()
    # Check if the message is purely an integer
    if not re.match(r"^\d+$", content):
        return

    num = int(content)
    current = state["current_count"]
    last_user = state["last_user_id"]
    high_score = state["high_score"]
    expected = current + 1

    # Rule 1: Cannot count twice in a row (unless count is 0)
    if current > 0 and message.author.id == last_user:
        new_high = max(high_score, current)
        db.execute(
            "UPDATE counting_state SET current_count=0, last_user_id=0, high_score=? WHERE guild_id=?",
            (new_high, message.guild.id),
        )
        db.commit()

        embed = discord.Embed(
            title="[COUNT RUINED]",
            description=(
                f"{message.author.mention} **ruined the count at `{current}`!**\n"
                f"• Reason: You cannot count twice in a row.\n"
                f"• Current High Score: `{new_high}`\n"
                f"• Next number: `1`"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="X BOT · Counting System")
        try:
            await message.channel.send(embed=embed)
        except Exception:
            pass
        return

    # Rule 2: Must be the exact next integer
    if num != expected:
        new_high = max(high_score, current)
        db.execute(
            "UPDATE counting_state SET current_count=0, last_user_id=0, high_score=? WHERE guild_id=?",
            (new_high, message.guild.id),
        )
        db.commit()

        embed = discord.Embed(
            title="[COUNT RUINED]",
            description=(
                f"{message.author.mention} **ruined the count!**\n"
                f"• Counted `{num}` instead of expected `{expected}`.\n"
                f"• Ruined at: `{current}`\n"
                f"• Current High Score: `{new_high}`\n"
                f"• Next number: `1`"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="X BOT · Counting System")
        try:
            await message.channel.send(embed=embed)
        except Exception:
            pass
        return

    # Valid next number!
    new_count = num
    new_high = max(high_score, new_count)
    db.execute(
        "UPDATE counting_state SET current_count=?, last_user_id=?, high_score=? WHERE guild_id=?",
        (new_count, message.author.id, new_high, message.guild.id),
    )
    db.commit()

    # Milestone checking: 50, 100, 200, 300, 400, 500, 750, 1000 or every 100
    is_milestone = (new_count in (50, 100, 200, 300, 400, 500, 750, 1000)) or (new_count >= 100 and new_count % 100 == 0)

    if is_milestone:
        reward_cash = 5000
        reward_xp = 100
        import leveling

        leveling._ensure_economy_player(db, message.author)
        db.execute(
            "UPDATE players SET money=money+? WHERE user_id=?",
            (reward_cash, message.author.id),
        )
        now_ts = int(time.time())
        db.execute(
            "INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
            (message.author.id, "counting_milestone", f"Reached Counting Milestone {new_count} (+${reward_cash:,} Cash, +{reward_xp} XP)", now_ts),
        )
        db.commit()
        await leveling.grant_xp(bot, db, message.author, reward_xp, "counting_milestone")

        embed = discord.Embed(
            title=f"[COUNTING MILESTONE: {new_count}]",
            description=(
                f"Outstanding teamwork! {message.author.mention} **reached milestone `{new_count}`!**\n\n"
                f"• Reward Awarded: **+${reward_cash:,} Cash** · **+{reward_xp} XP**\n"
                f"• Current High Score: `{new_high}`\n"
                f"• Next number: `{new_count + 1}`"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="X BOT · Counting System")
        try:
            await message.channel.send(embed=embed)
        except Exception:
            pass


def register_commands(bot: discord.Client, db, is_council_or_admin, STAFF_COMMAND_KWARGS):
    """Register counting management commands."""
    counting_group = app_commands.Group(name="counting", description="Manage server counting channel and view records")

    @counting_group.command(name="setup", description="Admin: Set the channel designated for counting")
    @app_commands.describe(channel="Text channel to designate as the official counting channel")
    async def counting_setup(interaction: discord.Interaction, channel: discord.TextChannel):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("[Restricted] Only Administrators can configure the counting channel.", ephemeral=True)
            return

        set_counting_channel(db, interaction.guild_id, channel.id)
        embed = discord.Embed(
            title="[COUNTING SYSTEM CONFIGURED]",
            description=(
                f"Official counting channel set to {channel.mention}.\n\n"
                f"**Rules:**\n"
                f"• Start from `1` and count up incrementally.\n"
                f"• The same member cannot count twice consecutively.\n"
                f"• Milestones (50, 100, 200, 500, etc.) award **+$5,000 Cash** and **+100 XP**.\n"
                f"• Wrong numbers or consecutive counting resets the count to 0."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="X BOT · Counting System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @counting_group.command(name="status", description="View current counting progress and server high score")
    async def counting_status(interaction: discord.Interaction):
        state = get_counting_state(db, interaction.guild_id)
        if not state or state["channel_id"] == 0:
            await interaction.response.send_message("[Notice] Counting channel is not yet configured for this server.", ephemeral=True)
            return

        ch = interaction.guild.get_channel(state["channel_id"])
        ch_mention = ch.mention if ch else f"ID: {state['channel_id']}"
        last_u = f"<@{state['last_user_id']}>" if state["last_user_id"] else "None"

        embed = discord.Embed(
            title="[COUNTING STATUS]",
            description=(
                f"• **Designated Channel:** {ch_mention}\n"
                f"• **Current Count:** `{state['current_count']}`\n"
                f"• **Next Expected:** `{state['current_count'] + 1}`\n"
                f"• **Last Counter:** {last_u}\n"
                f"• **All-Time High Score:** `{state['high_score']}`"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="X BOT · Counting System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @counting_group.command(name="reset", description="Admin: Reset current count back to 0")
    async def counting_reset(interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("[Restricted] Only Administrators can reset the counting channel.", ephemeral=True)
            return

        reset_counting_count(db, interaction.guild_id)
        await interaction.response.send_message("[Success] Counting has been reset. Next number is `1`.", ephemeral=True)

    bot.tree.add_command(counting_group, **STAFF_COMMAND_KWARGS)
