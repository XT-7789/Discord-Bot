import time
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import xbot_ui

_bot: Optional[discord.Client] = None
_db: Optional[sqlite3.Connection] = None


def initialise(db):
    """Ensure database table for autoclear channels exists."""
    db.execute("""
    CREATE TABLE IF NOT EXISTS autoclear_channels (
        channel_id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL DEFAULT 0,
        idle_minutes INTEGER NOT NULL DEFAULT 5,
        last_message_at INTEGER NOT NULL DEFAULT 0,
        is_cleared INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL DEFAULT 0
    )
    """)
    db.commit()


async def handle_message(bot: discord.Client, db, message: discord.Message):
    """Track message activity in monitored auto-clear channels."""
    if not message.guild or message.author.bot:
        return
    now = int(time.time())
    row = db.execute("SELECT channel_id FROM autoclear_channels WHERE channel_id=?", (message.channel.id,)).fetchone()
    if row:
        db.execute(
            "UPDATE autoclear_channels SET last_message_at=?, is_cleared=0 WHERE channel_id=?",
            (now, message.channel.id),
        )
        db.commit()


@tasks.loop(seconds=15)
async def autoclear_loop():
    """Background loop running every 15s to check inactivity and purge ghost chat channels."""
    global _bot, _db
    if not _bot or not _db:
        return

    now = int(time.time())
    try:
        rows = _db.execute("SELECT * FROM autoclear_channels WHERE is_cleared=0").fetchall()
    except Exception:
        return

    for row in rows:
        r = dict(row)
        ch_id = r["channel_id"]
        idle_mins = max(1, int(r.get("idle_minutes", 5)))
        idle_limit_secs = idle_mins * 60
        last_msg = int(r.get("last_message_at", 0))

        ch = _bot.get_channel(ch_id)
        if not ch or not hasattr(ch, "purge"):
            continue

        # If last_message_at is uninitialized (0), detect from channel's actual latest message
        if last_msg == 0:
            try:
                latest_m = None
                async for m in ch.history(limit=1):
                    latest_m = m
                    break
                if latest_m:
                    last_msg = int(latest_m.created_at.timestamp())
                    _db.execute("UPDATE autoclear_channels SET last_message_at=? WHERE channel_id=?", (last_msg, ch_id))
                    _db.commit()
                else:
                    # Channel has zero messages, mark as cleared
                    _db.execute("UPDATE autoclear_channels SET is_cleared=1 WHERE channel_id=?", (ch_id,))
                    _db.commit()
                    continue
            except Exception:
                continue

        # Check if idle threshold reached
        if (now - last_msg) >= idle_limit_secs:
            try:
                # Purge channel messages
                await ch.purge(limit=300)
                _db.execute("UPDATE autoclear_channels SET is_cleared=1 WHERE channel_id=?", (ch_id,))
                _db.commit()
            except Exception:
                pass


def start_autoclear_loop(bot: discord.Client, db):
    """Start the autoclear background task loop."""
    global _bot, _db
    _bot = bot
    _db = db
    if not autoclear_loop.is_running():
        autoclear_loop.start()


def register_commands(bot: discord.Client, db, is_council_or_admin, STAFF_COMMAND_KWARGS):
    """Register `/autoclear` slash command group."""
    autoclear_group = app_commands.Group(
        name="autoclear",
        description="Ghost Chat: Automatically purge channels after inactivity",
    )

    @autoclear_group.command(name="add", description="Add a channel to auto-clear mode (default 5m inactivity)")
    @app_commands.describe(
        channel="Text channel to auto-clear (defaults to current channel)",
        minutes="Inactivity minutes before clearing (e.g. 5)",
    )
    async def autoclear_add(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        minutes: app_commands.Range[int, 1, 1440] = 5,
    ):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can configure Ghost Chat.", ephemeral=True)
            return

        target_ch = channel or interaction.channel
        if not isinstance(target_ch, discord.TextChannel):
            await interaction.response.send_message("⚠️ Auto-clear can only be configured on standard text channels.", ephemeral=True)
            return

        now = int(time.time())
        db.execute(
            """INSERT INTO autoclear_channels(channel_id, guild_id, idle_minutes, last_message_at, is_cleared, created_by, created_at)
            VALUES(?,?,?,?,0,?,?)
            ON CONFLICT(channel_id) DO UPDATE SET idle_minutes=excluded.idle_minutes, is_cleared=0""",
            (target_ch.id, interaction.guild.id, minutes, now, interaction.user.id, now),
        )
        db.commit()

        embed = discord.Embed(
            title="👻 [GHOST CHAT AUTO-CLEAR ENABLED]",
            description=(
                f"✅ **{target_ch.mention}** is now monitored in **Ghost Chat Mode**!\n\n"
                f"⏱️ **Inactivity Threshold:** `{minutes} minutes`\n"
                f"🧹 **Behavior:** When nobody talks for **{minutes} minutes**, the bot will automatically purge all messages, keeping the channel completely clean and private.\n\n"
                f"-# You can remove this mode anytime using `/autoclear remove`."
            ),
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoclear_group.command(name="remove", description="Remove a channel from auto-clear mode")
    @app_commands.describe(channel="Text channel to remove (defaults to current channel)")
    async def autoclear_remove(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can configure Ghost Chat.", ephemeral=True)
            return

        target_ch = channel or interaction.channel
        row = db.execute("SELECT channel_id FROM autoclear_channels WHERE channel_id=?", (target_ch.id,)).fetchone()
        if not row:
            await interaction.response.send_message(f"⚠️ {target_ch.mention} is not currently in auto-clear mode.", ephemeral=True)
            return

        db.execute("DELETE FROM autoclear_channels WHERE channel_id=?", (target_ch.id,))
        db.commit()

        await interaction.response.send_message(
            f"✅ **Ghost Chat Mode disabled** for {target_ch.mention}. Messages will no longer be auto-purged.",
            ephemeral=True,
        )

    @autoclear_group.command(name="list", description="List all channels currently in Ghost Chat auto-clear mode")
    async def autoclear_list(interaction: discord.Interaction):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can view Ghost Chat settings.", ephemeral=True)
            return

        rows = db.execute("SELECT * FROM autoclear_channels WHERE guild_id=?", (interaction.guild.id,)).fetchall()
        if not rows:
            await interaction.response.send_message("ℹ️ No channels are currently configured for Ghost Chat auto-clear.", ephemeral=True)
            return

        now = int(time.time())
        lines = []
        for r in rows:
            ch = interaction.guild.get_channel(r["channel_id"])
            ch_mention = ch.mention if ch else f"`<#{r['channel_id']}>`"
            idle_mins = r["idle_minutes"]
            is_cleared = r["is_cleared"]
            last_msg = r["last_message_at"]

            if is_cleared or last_msg == 0:
                status = "🟢 Clean & Idle"
            else:
                elapsed = max(0, (now - last_msg) // 60)
                rem = max(0, idle_mins - elapsed)
                status = f"⏳ Active (Purge in ~{rem}m)"

            lines.append(f"• {ch_mention} — **{idle_mins}m inactivity** [{status}]")

        embed = discord.Embed(
            title="👻 [GHOST CHAT MONITORED CHANNELS]",
            description="\n".join(lines),
            color=0x9B59B6,
        )
        embed.set_footer(text=f"Total: {len(rows)} channel(s) monitored")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoclear_group.command(name="wipe", description="Emergency manual wipe: Purge all messages in a channel immediately")
    @app_commands.describe(channel="Channel to wipe immediately (defaults to current channel)")
    async def autoclear_wipe(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not is_council_or_admin(interaction):
            await interaction.response.send_message("🔒 Only Administrators can wipe channels.", ephemeral=True)
            return

        target_ch = channel or interaction.channel
        if not isinstance(target_ch, discord.TextChannel):
            await interaction.response.send_message("⚠️ Can only wipe text channels.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await target_ch.purge(limit=300)
            now = int(time.time())
            db.execute("UPDATE autoclear_channels SET is_cleared=1, last_message_at=? WHERE channel_id=?", (now, target_ch.id))
            db.commit()
            await interaction.followup.send(f"🧹 **{target_ch.mention} wiped clean!** ({len(deleted)} messages deleted)", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Failed to wipe {target_ch.mention}: {e}", ephemeral=True)

    bot.tree.add_command(autoclear_group, **STAFF_COMMAND_KWARGS)
