"""Tier 5 player progression, missions, and guided next actions.

The mission system derives progress from existing game tables.  This keeps it
compatible with old players and means panel callbacks do not need fragile,
duplicated mission-update hooks.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord


LEVEL_THRESHOLDS = (0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200)


def initialise(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tier5_profiles (
            user_id INTEGER PRIMARY KEY,
            nation_xp INTEGER NOT NULL DEFAULT 0,
            nation_level INTEGER NOT NULL DEFAULT 1,
            last_rewarded_level INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tier5_mission_claims (
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            period_key TEXT NOT NULL,
            mission_key TEXT NOT NULL,
            claimed_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, category, period_key, mission_key)
        );
        CREATE INDEX IF NOT EXISTS idx_tier5_claims_user
            ON tier5_mission_claims(user_id, category, period_key);
        """
    )
    db.commit()


def level_for_xp(xp: int) -> int:
    level = 1
    for index, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if xp < threshold:
            break
        level = index
    return level


def ensure_profile(db, user_id: int):
    now = int(time.time())
    cursor = db.execute(
        "INSERT OR IGNORE INTO tier5_profiles(user_id,created_at,updated_at) VALUES(?,?,?)",
        (user_id, now, now),
    )
    if cursor.rowcount:
        db.commit()
    row = db.execute("SELECT * FROM tier5_profiles WHERE user_id=?", (user_id,)).fetchone()
    expected = level_for_xp(int(row["nation_xp"]))
    if int(row["nation_level"]) != expected:
        db.execute(
            "UPDATE tier5_profiles SET nation_level=?,updated_at=? WHERE user_id=?",
            (expected, now, user_id),
        )
        db.commit()
        row = db.execute("SELECT * FROM tier5_profiles WHERE user_id=?", (user_id,)).fetchone()
    return row


def _day_key(now: int) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


def _week_key(now: int) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%G-W%V")


def _day_start(now: int) -> int:
    current = datetime.fromtimestamp(now, timezone.utc)
    return int(current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _week_start(now: int) -> int:
    current = datetime.fromtimestamp(now, timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()) - current.weekday() * 86400


def _log_count(db, user_id: int, actions, since: int = 0) -> int:
    placeholders = ",".join("?" for _ in actions)
    return int(db.execute(
        f"SELECT COUNT(*) FROM economy_logs WHERE user_id=? AND action IN ({placeholders}) AND created_at>=?",
        (user_id, *actions, since),
    ).fetchone()[0])


def _claimed_keys(db, user_id: int, category: str, period_key: str):
    return {row[0] for row in db.execute(
        "SELECT mission_key FROM tier5_mission_claims WHERE user_id=? AND category=? AND period_key=?",
        (user_id, category, period_key),
    ).fetchall()}


def _mission(key, title, description, progress, target, xp, xc, credits, destination, claimed):
    return {
        "key": key,
        "title": title,
        "description": description,
        "progress": min(max(0, int(progress)), int(target)),
        "target": int(target),
        "xp": int(xp),
        "xc": int(xc),
        "credits": int(credits),
        "destination": destination,
        "claimed": key in claimed,
    }


def missions_for(db, user_id: int, category: str, now: int | None = None):
    now = int(now or time.time())
    ensure_profile(db, user_id)
    if category == "starter":
        period_key = "once"
        claimed = _claimed_keys(db, user_id, category, period_key)
        city_count = int(db.execute("SELECT COUNT(*) FROM player_cities WHERE user_id=?", (user_id,)).fetchone()[0])
        army_quantity = int(db.execute("SELECT COALESCE(SUM(quantity),0) FROM player_war_units WHERE user_id=?", (user_id,)).fetchone()[0])
        land_count = int(db.execute("SELECT COUNT(*) FROM map_territories WHERE owner_user_id=?", (user_id,)).fetchone()[0])
        player = db.execute("SELECT total_mines FROM players WHERE user_id=?", (user_id,)).fetchone()
        total_mines = int(player["total_mines"] or 0) if player else 0
        return period_key, [
            _mission("nation", "Found Your Nation", "Open the Lobby and begin your Nation.", bool(player), 1, 25, 10, 50, "lobby", claimed),
            _mission("daily", "Claim a Daily Reward", "Collect the free Economy daily reward.", _log_count(db, user_id, ("daily",)), 1, 35, 25, 50, "economy", claimed),
            _mission("mine", "Start Mining", "Complete your first mining action.", max(total_mines, _log_count(db, user_id, ("mine",))), 1, 40, 25, 75, "mining", claimed),
            _mission("city", "Build a City", "Build your first Civilian or Industrial City.", city_count, 1, 50, 35, 100, "city", claimed),
            _mission("army", "Recruit an Army", "Recruit any military unit.", max(_log_count(db, user_id, ("recruit",)), 1 if army_quantity > 10 else 0), 1, 50, 35, 100, "recruit", claimed),
            _mission("land", "Expand Your Nation", "Claim one free Land beyond your Capital.", max(_log_count(db, user_id, ("free_land_claim",)), max(0, land_count - 1)), 1, 75, 50, 150, "city", claimed),
        ]
    if category == "daily":
        period_key = _day_key(now)
        claimed = _claimed_keys(db, user_id, category, period_key)
        since = _day_start(now)
        return period_key, [
            _mission("daily_reward", "Daily Check-in", "Collect today's Economy reward.", _log_count(db, user_id, ("daily",), since), 1, 20, 15, 30, "economy", claimed),
            _mission("mine_three", "Mining Shift", "Mine three times today.", _log_count(db, user_id, ("mine",), since), 3, 25, 20, 40, "mining", claimed),
            _mission("collect", "Collect Production", "Collect Nation or City production once.", _log_count(db, user_id, ("collect", "city_collect"), since), 1, 25, 20, 50, "city", claimed),
        ]
    if category == "weekly":
        period_key = _week_key(now)
        claimed = _claimed_keys(db, user_id, category, period_key)
        since = _week_start(now)
        battles = int(db.execute(
            "SELECT COUNT(*) FROM battle_history WHERE (attacker_id=? OR defender_id=?) AND created_at>=?",
            (user_id, user_id, since),
        ).fetchone()[0])
        return period_key, [
            _mission("mine_ten", "Resource Drive", "Mine ten times this week.", _log_count(db, user_id, ("mine",), since), 10, 70, 50, 150, "mining", claimed),
            _mission("develop_three", "Develop the Nation", "Build or upgrade Cities three times.", _log_count(db, user_id, ("city_build", "city_bulk_build", "city_upgrade", "city_bulk_upgrade", "land_upgrade"), since), 3, 90, 60, 250, "city", claimed),
            _mission("recruit_three", "Mobilisation", "Complete three recruitment orders.", _log_count(db, user_id, ("recruit",), since), 3, 80, 50, 200, "recruit", claimed),
            _mission("battle", "Battle Experience", "Take part in one Nation battle.", battles, 1, 100, 75, 300, "war", claimed),
        ]
    raise ValueError(f"Unknown Tier 5 mission category: {category}")


def _rank_name(level: int) -> str:
    names = ("Settlement", "Province", "Developing Nation", "Regional Power", "Major Power", "Great Power", "World Power", "Superpower", "Global Empire", "Legendary Empire")
    return names[max(0, min(level - 1, len(names) - 1))]


def profile_summary(db, user_id: int):
    profile = ensure_profile(db, user_id)
    xp = int(profile["nation_xp"])
    level = int(profile["nation_level"])
    current_floor = LEVEL_THRESHOLDS[level - 1]
    next_threshold = LEVEL_THRESHOLDS[level] if level < len(LEVEL_THRESHOLDS) else None
    return {
        "xp": xp,
        "level": level,
        "rank": _rank_name(level),
        "current_floor": current_floor,
        "next_threshold": next_threshold,
    }


def claim_ready(db, user_id: int, category: str, now: int | None = None):
    now = int(now or time.time())
    period_key, missions = missions_for(db, user_id, category, now)
    ready = [mission for mission in missions if not mission["claimed"] and mission["progress"] >= mission["target"]]
    if not ready:
        return "No completed unclaimed missions on this page."
    profile = ensure_profile(db, user_id)
    old_level = int(profile["nation_level"])
    totals = {"xp": 0, "xc": 0, "credits": 0}
    claimed_count = 0
    for mission in ready:
        cursor = db.execute(
            "INSERT OR IGNORE INTO tier5_mission_claims(user_id,category,period_key,mission_key,claimed_at) VALUES(?,?,?,?,?)",
            (user_id, category, period_key, mission["key"], now),
        )
        if cursor.rowcount:
            claimed_count += 1
            for key in totals:
                totals[key] += mission[key]
    if not claimed_count:
        return "Those mission rewards were already claimed."
    new_xp = int(profile["nation_xp"]) + totals["xp"]
    new_level = level_for_xp(new_xp)
    level_xc = sum(level * 20 for level in range(old_level + 1, new_level + 1))
    level_credits = sum(level * 100 for level in range(old_level + 1, new_level + 1))
    totals["xc"] += level_xc
    totals["credits"] += level_credits
    db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?", (totals["xc"], totals["credits"], user_id))
    db.execute(
        "UPDATE tier5_profiles SET nation_xp=?,nation_level=?,last_rewarded_level=MAX(last_rewarded_level,?),updated_at=? WHERE user_id=?",
        (new_xp, new_level, new_level, now, user_id),
    )
    detail = f"{category}: {claimed_count} mission(s), +{totals['xp']} Nation XP, +{totals['xc']} XC, +{totals['credits']} War Credits"
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, "tier5_mission_reward", detail, now))
    db.commit()
    level_text = f" Nation Level increased to **{new_level} — {_rank_name(new_level)}**!" if new_level > old_level else ""
    return (
        f"✅ Claimed **{claimed_count}** mission reward(s): **+{totals['xp']} Nation XP**, "
        f"**+{totals['xc']} XC**, **+{totals['credits']} War Credits**.{level_text}"
    )


def next_objective(db, user_id: int):
    for category in ("starter", "daily", "weekly"):
        _, missions = missions_for(db, user_id, category)
        for mission in missions:
            if not mission["claimed"] and mission["progress"] >= mission["target"]:
                return {"label": f"Claim: {mission['title']}", "destination": "missions", "category": category}
        for mission in missions:
            if not mission["claimed"]:
                return {"label": mission["title"], "destination": mission["destination"], "category": category}
    return {"label": "All current missions complete", "destination": "missions", "category": "home"}


def lobby_snapshot(db, user_id: int):
    summary = profile_summary(db, user_id)
    objective = next_objective(db, user_id)
    return {**summary, **objective}


def health_report(db):
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    players = int(db.execute("SELECT COUNT(*) FROM players").fetchone()[0])
    profiles = int(db.execute("SELECT COUNT(*) FROM tier5_profiles").fetchone()[0])
    claims = int(db.execute("SELECT COUNT(*) FROM tier5_mission_claims").fetchone()[0])
    invalid = int(db.execute("SELECT COUNT(*) FROM tier5_profiles WHERE nation_xp<0 OR nation_level<1 OR nation_level>10").fetchone()[0])
    status = "✅ Healthy" if integrity == "ok" and invalid == 0 else "⚠️ Needs repair"
    return f"Tier 5: {status} · Profiles {profiles}/{players} · Claims {claims} · Invalid {invalid} · DB {integrity}"


def repair(db):
    now = int(time.time())
    before = int(db.execute("SELECT COUNT(*) FROM tier5_profiles").fetchone()[0])
    db.execute(
        """INSERT OR IGNORE INTO tier5_profiles(user_id,created_at,updated_at)
           SELECT user_id,?,? FROM players""",
        (now, now),
    )
    for row in db.execute("SELECT user_id,nation_xp FROM tier5_profiles").fetchall():
        xp = max(0, int(row["nation_xp"] or 0))
        db.execute(
            "UPDATE tier5_profiles SET nation_xp=?,nation_level=?,last_rewarded_level=MIN(MAX(last_rewarded_level,1),10),updated_at=? WHERE user_id=?",
            (xp, level_for_xp(xp), now, row["user_id"]),
        )
    db.commit()
    after = int(db.execute("SELECT COUNT(*) FROM tier5_profiles").fetchone()[0])
    return f"✅ Tier 5 repair completed. Added {after - before} missing profile(s); checked {after} total."


def _bar(progress: int, target: int) -> str:
    filled = min(8, int(8 * min(progress, target) / max(1, target)))
    return "▰" * filled + "▱" * (8 - filled)


def register_commands(bot, db, create_player):
    class MissionButton(discord.ui.Button):
        def __init__(self, owner_id: int, action: str, label: str, emoji: str, *, style=discord.ButtonStyle.secondary, disabled=False):
            super().__init__(label=label, emoji=emoji, style=style, disabled=disabled)
            self.owner_id, self.action = owner_id, action

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/menu` for your own Missions.", ephemeral=True)
                return
            if self.action.startswith("page:"):
                if self.action=='page:home':
                    await interaction.response.edit_message(view=bot.xbot_player_panel_builders['missions'](self.owner_id))
                    return
                await interaction.response.edit_message(view=MissionView(self.owner_id, self.action.split(":", 1)[1]))
                return
            if self.action.startswith("claim:"):
                category = self.action.split(":", 1)[1]
                notice = claim_ready(db, self.owner_id, category)
                await interaction.response.edit_message(view=MissionView(self.owner_id, category, notice))
                return
            if self.action == "lobby":
                builder = getattr(bot, "xbot_player_lobby_builder", None)
            else:
                builder = getattr(bot, "xbot_player_panel_builders", {}).get(self.action)
            if builder is None:
                await interaction.response.send_message("That panel is still loading. Try again in a moment.", ephemeral=True)
                return
            await interaction.response.defer()
            await interaction.edit_original_response(view=builder(self.owner_id))

    class MissionView(discord.ui.LayoutView):
        def __init__(self, owner_id: int, page: str = "home", notice: str = ""):
            super().__init__(timeout=300)
            self.owner_id = owner_id
            create_player_from_id = db.execute("SELECT * FROM players WHERE user_id=?", (owner_id,)).fetchone()
            summary = profile_summary(db, owner_id)
            objective = next_objective(db, owner_id)
            container = discord.ui.Container(accent_color=discord.Color.blurple())
            next_text = "MAX" if summary["next_threshold"] is None else f"{summary['xp']}/{summary['next_threshold']} XP"
            container.add_item(discord.ui.TextDisplay(
                f"## 🎯 Nation Missions\n"
                f"🏳️ **{create_player_from_id['nation_name']}** · Level **{summary['level']}** — **{summary['rank']}**\n"
                f"⭐ Nation Progress: **{next_text}**\n"
                f"🧭 Next action: **{objective['label']}**"
            ))
            container.add_item(discord.ui.ActionRow(
                MissionButton(owner_id, "page:home", "Overview", "🏠", style=discord.ButtonStyle.primary if page == "home" else discord.ButtonStyle.secondary),
                MissionButton(owner_id, "page:starter", "Starter", "🌱", style=discord.ButtonStyle.primary if page == "starter" else discord.ButtonStyle.secondary),
                MissionButton(owner_id, "page:daily", "Daily", "☀️", style=discord.ButtonStyle.primary if page == "daily" else discord.ButtonStyle.secondary),
                MissionButton(owner_id, "page:weekly", "Weekly", "📅", style=discord.ButtonStyle.primary if page == "weekly" else discord.ButtonStyle.secondary, disabled=summary["level"] < 2),
            ))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            if page == "home":
                sections = []
                for category, label in (("starter", "Starter Journey"), ("daily", "Daily Missions"), ("weekly", "Weekly Missions")):
                    _, missions = missions_for(db, owner_id, category)
                    complete = sum(mission["claimed"] for mission in missions)
                    ready = sum(not mission["claimed"] and mission["progress"] >= mission["target"] for mission in missions)
                    sections.append(f"**{label}:** {complete}/{len(missions)} claimed" + (f" · 🎁 **{ready} ready**" if ready else ""))
                container.add_item(discord.ui.TextDisplay("### Progress\n" + "\n".join(sections)))
                container.add_item(discord.ui.TextDisplay(
                    "### Nation Level Unlocks\n"
                    "**Level 1:** Starter Journey + Daily Missions\n"
                    "**Level 2:** Weekly Missions\n"
                    "**Level 3+:** higher rank and automatic level-up reward packages\n"
                    "-# Gameplay panels remain open; levels guide progress without removing old access."
                ))
            else:
                period_key, missions = missions_for(db, owner_id, page)
                lines = []
                for mission in missions:
                    state = "🎁 Claimed" if mission["claimed"] else "✅ Ready" if mission["progress"] >= mission["target"] else "⬜ In progress"
                    lines.append(
                        f"**{mission['title']}** · {mission['progress']}/{mission['target']}  {_bar(mission['progress'], mission['target'])}\n"
                        f"{mission['description']}\n{state} · **{mission['xp']} XP + {mission['xc']} XC + {mission['credits']} WC**"
                    )
                ready = any(not mission["claimed"] and mission["progress"] >= mission["target"] for mission in missions)
                container.add_item(discord.ui.TextDisplay(f"### {page.title()} · `{period_key}`\n\n" + "\n\n".join(lines)))
                container.add_item(discord.ui.ActionRow(
                    MissionButton(owner_id, f"claim:{page}", "Claim Ready", "🎁", style=discord.ButtonStyle.success, disabled=not ready),
                    MissionButton(owner_id, objective["destination"], "Continue", "▶️", style=discord.ButtonStyle.primary),
                    MissionButton(owner_id, "lobby", "Lobby", "✨"),
                ))
            if page == "home":
                container.add_item(discord.ui.ActionRow(
                    MissionButton(owner_id, objective["destination"], "Continue Next Mission", "▶️", style=discord.ButtonStyle.success),
                    MissionButton(owner_id, "lobby", "Lobby", "✨"),
                ))
            container.add_item(discord.ui.TextDisplay("-# Mission progress updates automatically from normal panel actions. Rewards can only be claimed once."))
            self.add_item(container)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/menu` for your own Missions.", ephemeral=True)
            return False

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders["missions"] = lambda owner_id: MissionView(owner_id)
    for category in ('starter','daily','weekly'):
        bot.xbot_player_panel_builders['mission_'+category] = lambda owner_id,cat=category: MissionView(owner_id,cat)
    bot.xbot_tier5_health = lambda: health_report(db)
    bot.xbot_tier5_repair = lambda: repair(db)
