"""Tier 4 diplomacy, Alliance 2.0, Nation trade, and phone-safe backups."""

from __future__ import annotations

import re
import sqlite3
import time
import math
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import tasks

import war_system
import war_tier


RELATION_LABELS = {
    "friendly": "🤝 Friendly",
    "nap": "🕊️ Non-Aggression Pact",
    "truce": "🏳️ Truce",
}
REQUEST_TTL = 3 * 24 * 60 * 60


def initialise(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS alliance_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alliance_id INTEGER NOT NULL,
            inviter_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            responded_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_alliance_invites_target
            ON alliance_invites(target_id,status,expires_at);

        CREATE TABLE IF NOT EXISTS nation_relation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            responded_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_relation_requests_target
            ON nation_relation_requests(target_id,status,expires_at);

        CREATE TABLE IF NOT EXISTS nation_relations (
            first_user_id INTEGER NOT NULL,
            second_user_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ends_at INTEGER,
            PRIMARY KEY(first_user_id,second_user_id)
        );

        CREATE TABLE IF NOT EXISTS nation_peace_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            war_id INTEGER NOT NULL,
            offered_by INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            responded_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_peace_offers_target
            ON nation_peace_offers(target_id,status,expires_at);

        CREATE TABLE IF NOT EXISTS nation_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposer_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            offer_xc INTEGER NOT NULL DEFAULT 0,
            offer_war_credits INTEGER NOT NULL DEFAULT 0,
            offer_supply INTEGER NOT NULL DEFAULT 0,
            request_xc INTEGER NOT NULL DEFAULT 0,
            request_war_credits INTEGER NOT NULL DEFAULT 0,
            request_supply INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            responded_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_nation_trades_target
            ON nation_trades(target_id,status,expires_at);

        CREATE TABLE IF NOT EXISTS tier4_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    db.commit()


def _pair(first, second):
    return (first, second) if first < second else (second, first)


def _player(db, user_id):
    return db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()


def _nation(db, user_id):
    row = _player(db, user_id)
    return row["nation_name"] if row else f"Nation #{user_id}"


def _alliance(db, user_id):
    return db.execute(
        """SELECT a.* FROM alliances a JOIN alliance_members m ON m.alliance_id=a.id
           WHERE m.user_id=?""", (user_id,)
    ).fetchone()


def _supply(db, user_id):
    row = db.execute("SELECT supply FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    return int(row["supply"] or 0) if row else 0


def _expire_pending(db):
    now = int(time.time())
    for table in ("alliance_invites", "nation_relation_requests", "nation_peace_offers", "nation_trades"):
        db.execute(f"UPDATE {table} SET status='expired',responded_at=? WHERE status='pending' AND expires_at<=?", (now, now))
    db.execute("DELETE FROM nation_relations WHERE ends_at IS NOT NULL AND ends_at<=?", (now,))
    db.commit()


def _pending_items(db, user_id):
    _expire_pending(db)
    items = []
    for row in db.execute(
        """SELECT i.*,a.name,a.tag FROM alliance_invites i JOIN alliances a ON a.id=i.alliance_id
           WHERE i.target_id=? AND i.status='pending' ORDER BY i.id DESC""", (user_id,)
    ).fetchall():
        items.append((f"alliance:{row['id']}", f"Alliance [{row['tag']}] {row['name']}", f"Invited by {_nation(db,row['inviter_id'])}"))
    for row in db.execute(
        "SELECT * FROM nation_relation_requests WHERE target_id=? AND status='pending' ORDER BY id DESC", (user_id,)
    ).fetchall():
        items.append((f"relation:{row['id']}", RELATION_LABELS.get(row["relation_type"], row["relation_type"]), f"From {_nation(db,row['sender_id'])}"))
    for row in db.execute(
        "SELECT * FROM nation_peace_offers WHERE target_id=? AND status='pending' ORDER BY id DESC", (user_id,)
    ).fetchall():
        items.append((f"peace:{row['id']}", "Peace offer", f"From {_nation(db,row['offered_by'])}"))
    for row in db.execute(
        "SELECT * FROM nation_trades WHERE target_id=? AND status='pending' ORDER BY id DESC", (user_id,)
    ).fetchall():
        items.append((f"trade:{row['id']}", "Nation trade", f"From {_nation(db,row['proposer_id'])}"))
    return items


def _asset_text(xc, credits, supply):
    parts = []
    if xc:
        parts.append(f"{xc:,} XC")
    if credits:
        parts.append(f"{credits:,} War Credits")
    if supply:
        parts.append(f"{supply:,} Supply")
    return " + ".join(parts) if parts else "Nothing"


async def _notify(bot, user_id, text):
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(text)
    except (discord.HTTPException, discord.Forbidden, discord.NotFound, discord.ClientException):
        pass


class PageButton(discord.ui.Button):
    def __init__(self, page, label, emoji, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, emoji=emoji, style=style)
        self.page = page

    async def callback(self, interaction):
        await interaction.response.edit_message(view=DiplomacyView(
            self.view.bot, self.view.db, self.view.create_player,
            self.view.owner_id, page=self.page,
        ))


class BackPanelButton(discord.ui.Button):
    def __init__(self, target, label, emoji):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary)
        self.target = target

    async def callback(self, interaction):
        builder = getattr(self.view.bot, "xbot_player_panel_builders", {}).get(self.target)
        if not builder:
            await interaction.response.send_message("That panel is temporarily unavailable.", ephemeral=True)
            return
        await interaction.response.edit_message(view=builder(self.view.owner_id))


class NationSelect(discord.ui.UserSelect):
    def __init__(self, selected_target=None):
        super().__init__(placeholder="Choose another Nation…", min_values=1, max_values=1)
        self.selected_target = selected_target

    async def callback(self, interaction):
        target = self.values[0]
        if target.bot or target.id == self.view.owner_id:
            await interaction.response.send_message("Choose another player Nation.", ephemeral=True)
            return
        self.view.create_player(target)
        await interaction.response.edit_message(view=DiplomacyView(
            self.view.bot, self.view.db, self.view.create_player, self.view.owner_id,
            page=self.view.page, selected_target=target.id,
            notice=f"Selected {_nation(self.view.db,target.id)}.",
        ))


class InboxSelect(discord.ui.Select):
    def __init__(self, items, selected=None):
        options = [discord.SelectOption(label=label[:100], value=value, description=desc[:100], default=value == selected)
                   for value, label, desc in items[:25]]
        super().__init__(placeholder="Choose an invitation, offer, or request…", options=options)

    async def callback(self, interaction):
        await interaction.response.edit_message(view=DiplomacyView(
            self.view.bot, self.view.db, self.view.create_player, self.view.owner_id,
            page="inbox", selected_inbox=self.values[0],
        ))


class AllianceCreateModal(discord.ui.Modal, title="Create an Alliance"):
    name = discord.ui.TextInput(label="Alliance name", min_length=3, max_length=30)
    tag = discord.ui.TextInput(label="Alliance tag", placeholder="2–5 letters or numbers", min_length=2, max_length=5)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction):
        name, tag = str(self.name).strip(), str(self.tag).strip().upper()
        if not tag.isalnum():
            await interaction.response.send_message("Alliance tag may only contain letters and numbers.", ephemeral=True)
            return
        if _alliance(self.panel.db, interaction.user.id):
            await interaction.response.send_message("You already belong to an Alliance.", ephemeral=True)
            return
        try:
            cursor = self.panel.db.execute("INSERT INTO alliances(name,tag,leader_id,created_at) VALUES(?,?,?,?)",
                                           (name, tag, interaction.user.id, int(time.time())))
            self.panel.db.execute("INSERT INTO alliance_members(user_id,alliance_id) VALUES(?,?)",
                                  (interaction.user.id, cursor.lastrowid))
            self.panel.db.commit()
        except sqlite3.IntegrityError:
            await interaction.response.send_message("That Alliance name or tag is already used.", ephemeral=True)
            return
        await interaction.response.edit_message(view=DiplomacyView(
            self.panel.bot, self.panel.db, self.panel.create_player, interaction.user.id,
            page="alliance", notice=f"Alliance [{tag}] {name} created.",
        ))


class TradeModal(discord.ui.Modal, title="Propose a Nation Trade"):
    give_xc = discord.ui.TextInput(label="You give — XC", default="0", max_length=12)
    give_credits = discord.ui.TextInput(label="You give — War Credits", default="0", max_length=12)
    give_supply = discord.ui.TextInput(label="You give — Supply", default="0", max_length=12)
    receive_xc = discord.ui.TextInput(label="You request — XC", default="0", max_length=12)
    receive_assets = discord.ui.TextInput(label="Request War Credits, Supply", placeholder="Example: 500, 100", default="0, 0", max_length=30)

    def __init__(self, panel, target_id):
        super().__init__()
        self.panel, self.target_id = panel, target_id

    async def on_submit(self, interaction):
        try:
            give = [max(0, int(str(value).replace(",", "").strip() or 0)) for value in
                    (self.give_xc, self.give_credits, self.give_supply)]
            requested = [part.strip().replace(",", "") for part in re.split(r"[,/]", str(self.receive_assets))]
            if len(requested) != 2:
                raise ValueError
            receive = [max(0, int(str(self.receive_xc).replace(",", "").strip() or 0)), int(requested[0] or 0), int(requested[1] or 0)]
        except ValueError:
            await interaction.response.send_message("Use whole numbers. Enter requested War Credits and Supply like `500, 100`.", ephemeral=True)
            return
        if not any(give + receive) or (all(v == 0 for v in give) or all(v == 0 for v in receive)):
            await interaction.response.send_message("Both Nations must give at least one asset.", ephemeral=True)
            return
        self.panel.db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (interaction.user.id,))
        self.panel.db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (self.target_id,))
        self.panel.db.commit()
        player = _player(self.panel.db, interaction.user.id)
        if give[0] > int(player["xc"] or 0) or give[1] > int(player["money"] or 0) or give[2] > _supply(self.panel.db, interaction.user.id):
            await interaction.response.send_message("You do not currently own everything in your offer.", ephemeral=True)
            return
        now = int(time.time())
        self.panel.db.execute(
            """INSERT INTO nation_trades(proposer_id,target_id,offer_xc,offer_war_credits,offer_supply,
               request_xc,request_war_credits,request_supply,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (interaction.user.id, self.target_id, *give, *receive, now, now + REQUEST_TTL),
        )
        self.panel.db.commit()
        await _notify(self.panel.bot, self.target_id, f"📦 **{_nation(self.panel.db,interaction.user.id)}** sent your Nation a trade offer. Open `/warfront` → **Diplomacy** → **Inbox**.")
        await interaction.response.edit_message(view=DiplomacyView(
            self.panel.bot, self.panel.db, self.panel.create_player, interaction.user.id,
            page="trade", selected_target=self.target_id, notice="Trade offer sent. It expires in 3 days.",
        ))


class ActionButton(discord.ui.Button):
    def __init__(self, action, label, emoji, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, emoji=emoji, style=style)
        self.action = action

    async def callback(self, interaction):
        await self.view.handle_action(interaction, self.action)


class ConfirmView(discord.ui.LayoutView):
    def __init__(self, panel, action, title, body, *, target_id=None):
        super().__init__(timeout=180)
        self.panel, self.action, self.owner_id, self.target_id = panel, action, panel.owner_id, target_id
        box = discord.ui.Container(accent_color=discord.Color.gold())
        box.add_item(discord.ui.TextDisplay(f"## {title}\n{body}"))
        box.add_item(discord.ui.ActionRow(
            ActionButton("confirm", "Confirm", "✅", discord.ButtonStyle.danger),
            ActionButton("cancel", "Cancel", "↩️"),
        ))
        self.add_item(box)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This confirmation belongs to another player.", ephemeral=True)
        return False

    async def handle_action(self, interaction, action):
        if action == "cancel":
            await interaction.response.edit_message(view=DiplomacyView(
                self.panel.bot, self.panel.db, self.panel.create_player, self.owner_id, page=self.panel.page,
                selected_target=self.target_id,
            ))
            return
        if self.action == "declare":
            ok, notice = self.panel.declare_war(self.owner_id, self.target_id)
            if ok:
                await _notify(self.panel.bot, self.target_id, f"⚔️ **{_nation(self.panel.db,self.owner_id)}** declared war on your Nation. Open `/warfront` for details.")
            await interaction.response.edit_message(view=DiplomacyView(
                self.panel.bot, self.panel.db, self.panel.create_player, self.owner_id,
                page="relations", selected_target=self.target_id, notice=notice,
            ))
        elif self.action == "leave_alliance":
            notice = self.panel.leave_alliance()
            await interaction.response.edit_message(view=DiplomacyView(
                self.panel.bot, self.panel.db, self.panel.create_player, self.owner_id,
                page="alliance", notice=notice,
            ))


class DiplomacyView(discord.ui.LayoutView):
    def __init__(self, bot, db, create_player, owner_id, *, page="overview", selected_target=None,
                 selected_inbox=None, notice=None):
        super().__init__(timeout=600)
        self.bot, self.db, self.create_player = bot, db, create_player
        self.owner_id, self.page = owner_id, page
        self.selected_target, self.selected_inbox = selected_target, selected_inbox
        if not _player(db, owner_id):
            user = bot.get_user(owner_id)
            if user is None:
                user = type("PanelUser", (), {"id": owner_id, "display_name": f"Player {owner_id}"})()
            create_player(user)
        _expire_pending(db)
        box = discord.ui.Container(accent_color=discord.Color.teal())
        box.add_item(discord.ui.TextDisplay(self._content(notice)))
        box.add_item(discord.ui.ActionRow(
            PageButton("overview", "Overview", "🌐", discord.ButtonStyle.primary if page == "overview" else discord.ButtonStyle.secondary),
            PageButton("alliance", "Alliance", "🤝", discord.ButtonStyle.primary if page == "alliance" else discord.ButtonStyle.secondary),
            PageButton("relations", "Relations", "🕊️", discord.ButtonStyle.primary if page == "relations" else discord.ButtonStyle.secondary),
            PageButton("trade", "Trade", "📦", discord.ButtonStyle.primary if page == "trade" else discord.ButtonStyle.secondary),
            PageButton("inbox", "Inbox", "📨", discord.ButtonStyle.primary if page == "inbox" else discord.ButtonStyle.secondary),
        ))
        self._page_controls(box)
        box.add_item(discord.ui.ActionRow(
            ActionButton("rankings", "Alliance Rankings", "🏆"),
            BackPanelButton("war", "War Centre", "⚔️"),
            BackPanelButton("lobby", "Lobby", "🏠"),
        ))
        self.add_item(box)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This Diplomacy panel belongs to another player. Open `/warfront` for your own panel.", ephemeral=True)
        return False

    def _content(self, notice):
        player = _player(self.db, self.owner_id)
        alliance = _alliance(self.db, self.owner_id)
        pending = len(_pending_items(self.db, self.owner_id))
        header = f"## 🌐 {player['nation_name']} — Diplomacy Centre"
        note = f"\n-# {notice}" if notice else ""
        if self.page == "overview":
            war = war_tier.active_nation_war(self.db, self.owner_id)
            relation_count = self.db.execute("SELECT COUNT(*) n FROM nation_relations WHERE first_user_id=? OR second_user_id=?",
                                             (self.owner_id, self.owner_id)).fetchone()["n"]
            alliance_text = f"**[{alliance['tag']}] {alliance['name']}**" if alliance else "Independent Nation"
            war_text = "At peace" if not war else f"At war with **{_nation(self.db, war['defender_id'] if war['attacker_id']==self.owner_id else war['attacker_id'])}**"
            return f"{header}\n\n🤝 {alliance_text}\n⚔️ {war_text}\n🕊️ **{relation_count}** active relations\n📨 **{pending}** pending inbox items{note}"
        if self.page == "alliance":
            if not alliance:
                return f"{header}\n### 🤝 Alliance 2.0\nCreate an Alliance or accept a private invitation in **Inbox**. Open membership prevents unwanted joins.{note}"
            members = self.db.execute("SELECT user_id FROM alliance_members WHERE alliance_id=? ORDER BY user_id", (alliance["id"],)).fetchall()
            power = war_system.alliance_total_power(self.db, alliance["id"])
            names = ", ".join(f"<@{row['user_id']}>" for row in members[:12])
            return f"{header}\n### 🤝 [{alliance['tag']}] {alliance['name']}\n👑 Leader <@{alliance['leader_id']}> · 👥 **{len(members)} members** · ⚡ **{power:,} Power**\n{names}{note}"
        if self.page in {"relations", "trade"}:
            target = _player(self.db, self.selected_target) if self.selected_target else None
            title = "Nation Relations" if self.page == "relations" else "Secure Nation Trade"
            body = "Choose a Discord member whose Nation you want to contact."
            if target:
                pair = _pair(self.owner_id, self.selected_target)
                relation = self.db.execute("SELECT * FROM nation_relations WHERE first_user_id=? AND second_user_id=?", pair).fetchone()
                relation_text = RELATION_LABELS.get(relation["relation_type"], relation["relation_type"]) if relation else "Neutral"
                body = f"Selected **{target['nation_name']}**\nCurrent relation: **{relation_text}**"
            return f"{header}\n### {'🕊️' if self.page=='relations' else '📦'} {title}\n{body}{note}"
        if self.page == "rankings":
            rows = self.db.execute("""SELECT a.*,COUNT(m.user_id) members FROM alliances a
                LEFT JOIN alliance_members m ON m.alliance_id=a.id GROUP BY a.id""").fetchall()
            ranked = sorted(rows, key=lambda row: war_system.alliance_total_power(self.db, row["id"]), reverse=True)[:10]
            body = "\n".join(f"**{index}. [{row['tag']}] {row['name']}** · {war_system.alliance_total_power(self.db,row['id']):,} Power · {row['members']} members"
                              for index, row in enumerate(ranked, 1)) or "No Alliances have been created."
            return f"{header}\n### 🏆 Alliance Rankings\n{body}{note}"
        items = _pending_items(self.db, self.owner_id)
        body = f"**{len(items)} pending** invitation(s), relation request(s), peace offer(s), or trade(s)."
        if self.selected_inbox:
            body += "\n\n" + self._inbox_detail(self.selected_inbox)
        return f"{header}\n### 📨 Diplomatic Inbox\n{body}{note}"

    def _page_controls(self, box):
        if self.page == "alliance":
            alliance = _alliance(self.db, self.owner_id)
            if not alliance:
                box.add_item(discord.ui.ActionRow(ActionButton("create_alliance", "Create Alliance", "➕", discord.ButtonStyle.success)))
            else:
                controls = []
                if alliance["leader_id"] == self.owner_id:
                    box.add_item(discord.ui.ActionRow(NationSelect(self.selected_target)))
                    controls.append(ActionButton("invite", "Invite Selected", "✉️", discord.ButtonStyle.success))
                    controls.append(ActionButton("kick", "Remove Selected", "🚪", discord.ButtonStyle.danger))
                controls.append(ActionButton("leave_alliance", "Leave Alliance", "↩️", discord.ButtonStyle.danger))
                box.add_item(discord.ui.ActionRow(*controls))
        elif self.page in {"relations", "trade"}:
            box.add_item(discord.ui.ActionRow(NationSelect(self.selected_target)))
            if self.selected_target:
                if self.page == "relations":
                    box.add_item(discord.ui.ActionRow(
                        ActionButton("friendly", "Friendly", "🤝", discord.ButtonStyle.success),
                        ActionButton("nap", "Non-Aggression", "🕊️", discord.ButtonStyle.primary),
                        ActionButton("declare", "Declare War", "⚔️", discord.ButtonStyle.danger),
                        ActionButton("peace", "Offer Peace", "🏳️"),
                    ))
                else:
                    box.add_item(discord.ui.ActionRow(ActionButton("trade", "Create Trade Offer", "📦", discord.ButtonStyle.success)))
        elif self.page == "inbox":
            items = _pending_items(self.db, self.owner_id)
            if items:
                box.add_item(discord.ui.ActionRow(InboxSelect(items, self.selected_inbox)))
                if self.selected_inbox:
                    box.add_item(discord.ui.ActionRow(
                        ActionButton("accept_inbox", "Accept", "✅", discord.ButtonStyle.success),
                        ActionButton("reject_inbox", "Reject", "❌", discord.ButtonStyle.danger),
                    ))

    def _inbox_detail(self, value):
        kind, row_id_text = value.split(":", 1)
        row_id = int(row_id_text)
        if kind == "alliance":
            row = self.db.execute("""SELECT i.*,a.name,a.tag FROM alliance_invites i JOIN alliances a ON a.id=i.alliance_id WHERE i.id=?""", (row_id,)).fetchone()
            return f"**Alliance invitation:** [{row['tag']}] {row['name']}\nInvited by {_nation(self.db,row['inviter_id'])}."
        if kind == "relation":
            row = self.db.execute("SELECT * FROM nation_relation_requests WHERE id=?", (row_id,)).fetchone()
            return f"**{RELATION_LABELS.get(row['relation_type'],row['relation_type'])} request** from {_nation(self.db,row['sender_id'])}."
        if kind == "peace":
            row = self.db.execute("SELECT * FROM nation_peace_offers WHERE id=?", (row_id,)).fetchone()
            return f"**Peace offer** from {_nation(self.db,row['offered_by'])}. Accepting ends the current war for both Nations."
        row = self.db.execute("SELECT * FROM nation_trades WHERE id=?", (row_id,)).fetchone()
        return (f"**Trade from {_nation(self.db,row['proposer_id'])}**\n"
                f"They give: **{_asset_text(row['offer_xc'],row['offer_war_credits'],row['offer_supply'])}**\n"
                f"You give: **{_asset_text(row['request_xc'],row['request_war_credits'],row['request_supply'])}**")

    async def handle_action(self, interaction, action):
        if action == "rankings":
            await interaction.response.edit_message(view=DiplomacyView(self.bot, self.db, self.create_player, self.owner_id, page="rankings"))
            return
        if action == "create_alliance":
            await interaction.response.send_modal(AllianceCreateModal(self)); return
        if action == "trade":
            await interaction.response.send_modal(TradeModal(self, self.selected_target)); return
        if action == "leave_alliance":
            alliance = _alliance(self.db, self.owner_id)
            await interaction.response.edit_message(view=ConfirmView(self, "leave_alliance", "Leave Alliance?",
                f"You are about to leave **[{alliance['tag']}] {alliance['name']}**. Leadership transfers automatically if needed."))
            return
        if action in {"invite", "kick"}:
            await self._alliance_member_action(interaction, action); return
        if action in {"friendly", "nap"}:
            await self._relation_request(interaction, action); return
        if action == "declare":
            target = _player(self.db, self.selected_target)
            await interaction.response.edit_message(view=ConfirmView(self, "declare", "Confirm War Declaration",
                f"Declare war on **{target['nation_name']}**? The declaration succeeds only if your Nations share a real Land border.", target_id=self.selected_target))
            return
        if action == "peace":
            await self._offer_peace(interaction); return
        if action in {"accept_inbox", "reject_inbox"}:
            await self._answer_inbox(interaction, action == "accept_inbox")

    async def _alliance_member_action(self, interaction, action):
        alliance = _alliance(self.db, self.owner_id)
        if not alliance or alliance["leader_id"] != self.owner_id or not self.selected_target:
            await interaction.response.send_message("Only the Alliance leader can do that after selecting a member.", ephemeral=True); return
        if action == "invite":
            if _alliance(self.db, self.selected_target):
                await interaction.response.send_message("That Nation already belongs to an Alliance.", ephemeral=True); return
            now = int(time.time())
            self.db.execute("UPDATE alliance_invites SET status='cancelled',responded_at=? WHERE target_id=? AND status='pending'", (now, self.selected_target))
            self.db.execute("INSERT INTO alliance_invites(alliance_id,inviter_id,target_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                            (alliance["id"], self.owner_id, self.selected_target, now, now + REQUEST_TTL))
            self.db.commit()
            await _notify(self.bot, self.selected_target, f"🤝 You were invited to **[{alliance['tag']}] {alliance['name']}**. Open `/warfront` → **Diplomacy** → **Inbox**.")
            notice = "Private Alliance invitation sent."
        else:
            if self.selected_target == self.owner_id:
                await interaction.response.send_message("Use Leave Alliance instead.", ephemeral=True); return
            cursor = self.db.execute("DELETE FROM alliance_members WHERE user_id=? AND alliance_id=?", (self.selected_target, alliance["id"]))
            self.db.commit()
            notice = "Member removed." if cursor.rowcount else "That Nation is not in your Alliance."
        await interaction.response.edit_message(view=DiplomacyView(self.bot, self.db, self.create_player, self.owner_id,
            page="alliance", selected_target=self.selected_target, notice=notice))

    async def _relation_request(self, interaction, relation_type):
        if not self.selected_target:
            await interaction.response.send_message("Choose a Nation first.", ephemeral=True); return
        own_alliance, target_alliance = _alliance(self.db, self.owner_id), _alliance(self.db, self.selected_target)
        if own_alliance and target_alliance and own_alliance["id"] == target_alliance["id"]:
            await interaction.response.send_message("Alliance members are already allies.", ephemeral=True); return
        if war_tier.active_nation_war(self.db, self.owner_id, self.selected_target):
            await interaction.response.send_message("End the active war with an accepted Peace Offer before changing relations.", ephemeral=True); return
        now = int(time.time())
        self.db.execute("""UPDATE nation_relation_requests SET status='cancelled',responded_at=?
            WHERE sender_id=? AND target_id=? AND status='pending'""", (now, self.owner_id, self.selected_target))
        self.db.execute("""INSERT INTO nation_relation_requests(sender_id,target_id,relation_type,created_at,expires_at)
            VALUES(?,?,?,?,?)""", (self.owner_id, self.selected_target, relation_type, now, now + REQUEST_TTL))
        self.db.commit()
        await _notify(self.bot, self.selected_target, f"🕊️ **{_nation(self.db,self.owner_id)}** sent a {RELATION_LABELS[relation_type]} request. Open `/warfront` → **Diplomacy** → **Inbox**.")
        await interaction.response.edit_message(view=DiplomacyView(self.bot, self.db, self.create_player, self.owner_id,
            page="relations", selected_target=self.selected_target, notice="Relation request sent. It requires the other Nation to accept."))

    async def _offer_peace(self, interaction):
        war = war_tier.active_nation_war(self.db, self.owner_id, self.selected_target)
        if not war:
            await interaction.response.send_message("You do not have an active war with that Nation.", ephemeral=True); return
        target = war["defender_id"] if war["attacker_id"] == self.owner_id else war["attacker_id"]
        now = int(time.time())
        self.db.execute("UPDATE nation_peace_offers SET status='cancelled',responded_at=? WHERE war_id=? AND status='pending'", (now, war["id"]))
        self.db.execute("INSERT INTO nation_peace_offers(war_id,offered_by,target_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                        (war["id"], self.owner_id, target, now, now + REQUEST_TTL))
        self.db.commit()
        await _notify(self.bot, target, f"🏳️ **{_nation(self.db,self.owner_id)}** offered peace. Open `/warfront` → **Diplomacy** → **Inbox**.")
        await interaction.response.edit_message(view=DiplomacyView(self.bot, self.db, self.create_player, self.owner_id,
            page="relations", selected_target=target, notice="Peace offer sent. The other Nation must accept."))

    def declare_war(self, attacker_id, defender_id):
        attacker, defender = _player(self.db, attacker_id), _player(self.db, defender_id)
        if not attacker or not defender:
            return False, "Both players must create a Nation first."
        if int(attacker["capital_health"]) <= 0 or int(defender["capital_health"]) <= 0:
            return False, "A conquered Nation cannot start or receive a declaration."
        own_alliance, target_alliance = _alliance(self.db, attacker_id), _alliance(self.db, defender_id)
        if own_alliance and target_alliance and own_alliance["id"] == target_alliance["id"]:
            return False, "You cannot declare war on an Alliance member."
        if war_tier.active_nation_war(self.db, attacker_id) or war_tier.active_nation_war(self.db, defender_id):
            return False, "One of these Nations already has an active war."
        pair = _pair(attacker_id, defender_id)
        relation = self.db.execute("SELECT * FROM nation_relations WHERE first_user_id=? AND second_user_id=?", pair).fetchone()
        if relation and relation["relation_type"] in {"nap", "truce"} and (relation["ends_at"] is None or relation["ends_at"] > int(time.time())):
            return False, f"An active {RELATION_LABELS.get(relation['relation_type'],relation['relation_type'])} prevents this declaration."
        tiles = war_tier.world_city_tiles(war_tier._province_features())
        war_tier.sync_map_ownership(self.db, tiles)
        if not war_tier.nations_are_adjacent(self.db, attacker_id, defender_id, tiles):
            return False, "War denied: these Nations do not share a real Land border."
        now = int(time.time())
        duration = max(3600, war_tier.setting(self.db, "nation_war_duration"))
        self.db.execute("INSERT INTO nation_wars(attacker_id,defender_id,started_at,ends_at) VALUES(?,?,?,?)",
                        (attacker_id, defender_id, now, now + duration))
        self.db.execute("DELETE FROM nation_relations WHERE first_user_id=? AND second_user_id=?", pair)
        self.db.commit()
        return True, f"War declared on {defender['nation_name']}. It ends <t:{now + duration}:R>."

    def leave_alliance(self):
        alliance = _alliance(self.db, self.owner_id)
        if not alliance:
            return "You are not in an Alliance."
        members = self.db.execute("SELECT user_id FROM alliance_members WHERE alliance_id=? AND user_id<>? ORDER BY user_id",
                                  (alliance["id"], self.owner_id)).fetchall()
        if alliance["leader_id"] == self.owner_id:
            if members:
                self.db.execute("UPDATE alliances SET leader_id=? WHERE id=?", (members[0]["user_id"], alliance["id"]))
            else:
                self.db.execute("DELETE FROM alliances WHERE id=?", (alliance["id"],))
                self.db.execute("UPDATE alliance_invites SET status='cancelled',responded_at=? WHERE alliance_id=? AND status='pending'",
                                (int(time.time()), alliance["id"]))
        self.db.execute("DELETE FROM alliance_members WHERE user_id=?", (self.owner_id,))
        self.db.commit()
        return f"You left [{alliance['tag']}] {alliance['name']}."

    async def _answer_inbox(self, interaction, accept):
        if not self.selected_inbox:
            await interaction.response.send_message("Choose an inbox item first.", ephemeral=True); return
        kind, row_id_text = self.selected_inbox.split(":", 1)
        row_id, now = int(row_id_text), int(time.time())
        notice, notify_id = "Request rejected.", None
        try:
            if kind == "alliance":
                row = self.db.execute("SELECT * FROM alliance_invites WHERE id=? AND target_id=? AND status='pending'", (row_id, self.owner_id)).fetchone()
                if not row: raise ValueError("This invitation is no longer available.")
                notify_id = row["inviter_id"]
                if accept:
                    if _alliance(self.db, self.owner_id): raise ValueError("Leave your current Alliance first.")
                    alliance = self.db.execute("SELECT * FROM alliances WHERE id=?", (row["alliance_id"],)).fetchone()
                    if not alliance: raise ValueError("That Alliance no longer exists.")
                    self.db.execute("INSERT INTO alliance_members(user_id,alliance_id) VALUES(?,?)", (self.owner_id, row["alliance_id"]))
                    notice = f"Joined [{alliance['tag']}] {alliance['name']}."
                self.db.execute("UPDATE alliance_invites SET status=?,responded_at=? WHERE id=?", ("accepted" if accept else "rejected", now, row_id))
            elif kind == "relation":
                row = self.db.execute("SELECT * FROM nation_relation_requests WHERE id=? AND target_id=? AND status='pending'", (row_id, self.owner_id)).fetchone()
                if not row: raise ValueError("This relation request is no longer available.")
                notify_id = row["sender_id"]
                if accept:
                    pair = _pair(row["sender_id"], self.owner_id)
                    ends_at = now + 7 * 24 * 60 * 60 if row["relation_type"] == "nap" else None
                    self.db.execute("""INSERT INTO nation_relations(first_user_id,second_user_id,relation_type,started_at,ends_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(first_user_id,second_user_id) DO UPDATE SET
                        relation_type=excluded.relation_type,started_at=excluded.started_at,ends_at=excluded.ends_at""",
                        (*pair, row["relation_type"], now, ends_at))
                    notice = f"Relation accepted: {RELATION_LABELS.get(row['relation_type'],row['relation_type'])}."
                self.db.execute("UPDATE nation_relation_requests SET status=?,responded_at=? WHERE id=?", ("accepted" if accept else "rejected", now, row_id))
            elif kind == "peace":
                row = self.db.execute("SELECT * FROM nation_peace_offers WHERE id=? AND target_id=? AND status='pending'", (row_id, self.owner_id)).fetchone()
                if not row: raise ValueError("This peace offer is no longer available.")
                notify_id = row["offered_by"]
                if accept:
                    war = self.db.execute("SELECT * FROM nation_wars WHERE id=? AND active=1", (row["war_id"],)).fetchone()
                    if not war: raise ValueError("That war has already ended.")
                    self.db.execute("UPDATE nation_wars SET active=0,ended_at=?,ended_by=? WHERE id=?", (now, self.owner_id, war["id"]))
                    pair = _pair(war["attacker_id"], war["defender_id"])
                    self.db.execute("""INSERT INTO nation_relations(first_user_id,second_user_id,relation_type,started_at,ends_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(first_user_id,second_user_id) DO UPDATE SET
                        relation_type='truce',started_at=excluded.started_at,ends_at=excluded.ends_at""",
                        (*pair, "truce", now, now + 24 * 60 * 60))
                    notice = "Peace accepted. A 24-hour truce is now active."
                self.db.execute("UPDATE nation_peace_offers SET status=?,responded_at=? WHERE id=?", ("accepted" if accept else "rejected", now, row_id))
            else:
                row = self.db.execute("SELECT * FROM nation_trades WHERE id=? AND target_id=? AND status='pending'", (row_id, self.owner_id)).fetchone()
                if not row: raise ValueError("This trade is no longer available.")
                notify_id = row["proposer_id"]
                if accept:
                    self.db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (row["proposer_id"],))
                    self.db.execute("INSERT OR IGNORE INTO player_war_settings(user_id) VALUES(?)", (self.owner_id,))
                    proposer, receiver = _player(self.db, row["proposer_id"]), _player(self.db, self.owner_id)
                    if (row["offer_xc"] > proposer["xc"] or row["offer_war_credits"] > proposer["money"] or row["offer_supply"] > _supply(self.db,row["proposer_id"])):
                        raise ValueError("The proposing Nation no longer owns its offered assets.")
                    if (row["request_xc"] > receiver["xc"] or row["request_war_credits"] > receiver["money"] or row["request_supply"] > _supply(self.db,self.owner_id)):
                        raise ValueError("Your Nation does not own all requested assets.")
                    self.db.execute("UPDATE players SET xc=xc-?+?,money=money-?+? WHERE user_id=?",
                                    (row["offer_xc"], row["request_xc"], row["offer_war_credits"], row["request_war_credits"], row["proposer_id"]))
                    self.db.execute("UPDATE players SET xc=xc-?+?,money=money-?+? WHERE user_id=?",
                                    (row["request_xc"], row["offer_xc"], row["request_war_credits"], row["offer_war_credits"], self.owner_id))
                    self.db.execute("UPDATE player_war_settings SET supply=supply-?+? WHERE user_id=?", (row["offer_supply"], row["request_supply"], row["proposer_id"]))
                    self.db.execute("UPDATE player_war_settings SET supply=supply-?+? WHERE user_id=?", (row["request_supply"], row["offer_supply"], self.owner_id))
                    notice = "Trade completed securely for both Nations."
                self.db.execute("UPDATE nation_trades SET status=?,responded_at=? WHERE id=?", ("accepted" if accept else "rejected", now, row_id))
            self.db.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.db.rollback()
            await interaction.response.send_message(str(exc), ephemeral=True); return
        if notify_id:
            await _notify(self.bot, notify_id, f"📨 **{_nation(self.db,self.owner_id)}** {'accepted' if accept else 'rejected'} your diplomatic request. Open `/warfront` → **Diplomacy**.")
        await interaction.response.edit_message(view=DiplomacyView(self.bot, self.db, self.create_player, self.owner_id,
            page="inbox", notice=notice))


def backup_now(db):
    database_row = db.execute("PRAGMA database_list").fetchone()
    database_path = Path(database_row["file"] if isinstance(database_row, sqlite3.Row) else database_row[2]).resolve()
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = backup_dir / f"xwar-{stamp}.db"
    destination = sqlite3.connect(target)
    try:
        db.backup(destination)
    finally:
        destination.close()
    backups = sorted(backup_dir.glob("xwar-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old in backups[30:]:
        if old.resolve().parent == backup_dir.resolve():
            old.unlink()
    db.execute("INSERT INTO tier4_runtime(key,value) VALUES('last_backup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
    db.commit()
    return target


def health_text(bot, db):
    last = db.execute("SELECT value FROM tier4_runtime WHERE key='last_backup'").fetchone()
    backup = f"<t:{last['value']}:R>" if last else "Not created yet"
    active_wars = db.execute("SELECT COUNT(*) n FROM nation_wars WHERE active=1").fetchone()["n"]
    pending = sum(db.execute(f"SELECT COUNT(*) n FROM {table} WHERE status='pending'").fetchone()["n"] for table in
                  ("alliance_invites", "nation_relation_requests", "nation_peace_offers", "nation_trades"))
    raw_latency = float(getattr(bot, "latency", 0) or 0)
    latency = round(raw_latency * 1000) if math.isfinite(raw_latency) else 0
    return f"Gateway **{latency} ms** · Active wars **{active_wars}** · Pending diplomacy **{pending}** · Last backup **{backup}**"


_backup_loop = None


def start_backup_task(bot, db):
    global _backup_loop
    if _backup_loop and _backup_loop.is_running():
        return

    @tasks.loop(hours=6)
    async def loop():
        target = backup_now(db)
        print(f"Tier 4 database backup created: {target}")

    @loop.before_loop
    async def before_loop():
        await bot.wait_until_ready()

    _backup_loop = loop
    loop.start()


def register_commands(bot, db, create_player):
    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["diplomacy"] = lambda owner_id: DiplomacyView(bot, db, create_player, owner_id)
    bot.xbot_declare_war_view = lambda owner_id, target_id: ConfirmView(
        DiplomacyView(bot, db, create_player, owner_id, page="relations", selected_target=target_id),
        "declare", "Confirm War Declaration",
        f"Declare war on **{_nation(db,target_id)}**? The declaration succeeds only if your Nations share a real Land border.",
        target_id=target_id,
    )
    bot.xbot_offer_peace = lambda owner_id, target_id: DiplomacyView(
        bot, db, create_player, owner_id, page="relations", selected_target=target_id,
        notice="Review the enemy Nation, then press Offer Peace.",
    )
    bot.xbot_tier4_health = lambda: health_text(bot, db)
    bot.xbot_tier4_backup = lambda: backup_now(db)
