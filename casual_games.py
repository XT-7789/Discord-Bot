"""Free casual games, collection titles and their Discord panels."""
import json
import random
import sqlite3
import time

import discord


DEFAULTS = {
    "free_games_enabled": "1",
    "memory_daily_reward_games": "3",
}
COLLECTIBLES = (
    ("first_match", "First Match", "Complete your first Memory Match."),
    ("sharp_memory", "Sharp Memory", "Complete a game in 4 attempts or fewer."),
    ("perfect_recall", "Perfect Recall", "Complete a game in exactly 3 attempts."),
    ("memory_regular", "Memory Regular", "Complete 10 Memory Match games."),
)
SYMBOLS = ("⭐", "💎", "🍒")
SESSION_TTL = 1800


def initialise(db):
    db.execute("""CREATE TABLE IF NOT EXISTS casual_game_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        game TEXT NOT NULL DEFAULT 'memory',
        board TEXT NOT NULL,
        matched TEXT NOT NULL DEFAULT '[]',
        first_pick INTEGER,
        second_pick INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        started_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        completed_at INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casual_game_completions(
        session_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        game TEXT NOT NULL,
        day TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        reward_xc INTEGER NOT NULL DEFAULT 0,
        completed_at INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casual_game_progress(
        user_id INTEGER PRIMARY KEY,
        memory_completed INTEGER NOT NULL DEFAULT 0,
        memory_best INTEGER NOT NULL DEFAULT 0,
        equipped_title TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS casual_collectibles(
        user_id INTEGER NOT NULL,
        collectible_key TEXT NOT NULL,
        unlocked_at INTEGER NOT NULL,
        PRIMARY KEY(user_id,collectible_key)
    )""")
    db.executemany("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", DEFAULTS.items())
    reward = db.execute("SELECT value FROM economy_settings WHERE key='daily_reward'").fetchone()
    initial_limit = max(0, int(reward[0]) // 2) if reward else 25
    db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES('memory_daily_xc_limit',?)", (str(initial_limit),))
    db.execute("CREATE INDEX IF NOT EXISTS casual_sessions_user_status ON casual_game_sessions(user_id,status,updated_at)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS casual_one_active_session ON casual_game_sessions(user_id,game) WHERE status='active'")
    db.execute("CREATE INDEX IF NOT EXISTS casual_completions_user_day ON casual_game_completions(user_id,day)")
    db.commit()


def setting(db, key):
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    if row:
        return int(row[0])
    if key == "memory_daily_xc_limit":
        reward = db.execute("SELECT value FROM economy_settings WHERE key='daily_reward'").fetchone()
        return max(0, int(reward[0]) // 2) if reward else 25
    return int(DEFAULTS[key])


def game_day(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(int(now or time.time())))


def _transaction(db, operation):
    name = "casual_game_action"
    own = not db.in_transaction
    db.execute("BEGIN IMMEDIATE" if own else f"SAVEPOINT {name}")
    try:
        result = operation()
        if own:
            db.commit()
        else:
            db.execute(f"RELEASE {name}")
        return result
    except Exception:
        if own:
            db.rollback()
        else:
            db.execute(f"ROLLBACK TO {name}")
            db.execute(f"RELEASE {name}")
        raise


def active_session(db, user_id, now=None, *, commit=True):
    now = int(now or time.time())
    row = db.execute("SELECT * FROM casual_game_sessions WHERE user_id=? AND game='memory' AND status='active' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if row and int(row["updated_at"]) + SESSION_TTL <= now:
        db.execute("UPDATE casual_game_sessions SET status='expired',updated_at=? WHERE id=? AND status='active'", (now, row["id"]))
        if commit:
            db.commit()
        return None
    return row


def start_memory(db, user_id, now=None, board=None):
    now = int(now or time.time())
    if not setting(db, "free_games_enabled"):
        raise ValueError("Free games are currently closed.")

    def operation():
        current = active_session(db, user_id, now, commit=False)
        if current:
            return current
        values = list(board) if board is not None else list(SYMBOLS) * 2
        if len(values) != 6 or sorted(values) != sorted(list(SYMBOLS) * 2):
            raise ValueError("Invalid Memory Match board.")
        if board is None:
            random.SystemRandom().shuffle(values)
        cursor = db.execute("""INSERT INTO casual_game_sessions
            (user_id,board,started_at,updated_at) VALUES(?,?,?,?)""", (user_id, json.dumps(values), now, now))
        return db.execute("SELECT * FROM casual_game_sessions WHERE id=?", (cursor.lastrowid,)).fetchone()

    return _transaction(db, operation)


def reward_status(db, user_id, now=None):
    current = int(now or time.time())
    day = game_day(current)
    local = time.localtime(current)
    reset_at = int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday + 1, 0, 0, 0, 0, 0, -1)))
    row = db.execute("SELECT COUNT(*) games,COALESCE(SUM(reward_xc),0) paid FROM casual_game_completions WHERE user_id=? AND day=?", (user_id, day)).fetchone()
    games = max(0, setting(db, "memory_daily_reward_games"))
    limit = max(0, setting(db, "memory_daily_xc_limit"))
    return {"day": day, "completed": int(row["games"]), "paid": int(row["paid"]),
            "games": games, "limit": limit, "rewarded_left": max(0, games - int(row["games"])),
            "xc_left": max(0, limit - int(row["paid"])), "reset_at": reset_at}


def _next_reward(db, user_id, now):
    status = reward_status(db, user_id, now)
    if not setting(db, "free_games_enabled") or status["completed"] >= status["games"] or status["games"] <= 0:
        return 0
    base, extra = divmod(status["limit"], status["games"])
    scheduled = base + (1 if status["completed"] < extra else 0)
    return min(scheduled, status["xc_left"])


def _unlock(db, user_id, attempts, completed, now):
    keys = ["first_match"]
    if attempts <= 4:
        keys.append("sharp_memory")
    if attempts == 3:
        keys.append("perfect_recall")
    if completed >= 10:
        keys.append("memory_regular")
    unlocked = []
    for key in keys:
        cursor = db.execute("INSERT OR IGNORE INTO casual_collectibles(user_id,collectible_key,unlocked_at) VALUES(?,?,?)", (user_id, key, now))
        if cursor.rowcount:
            unlocked.append(key)
    return unlocked


def _complete(db, row, now):
    existing = db.execute("SELECT * FROM casual_game_completions WHERE session_id=?", (row["id"],)).fetchone()
    if existing:
        return {"reward": int(existing["reward_xc"]), "unlocked": [], "duplicate": True}
    reward = _next_reward(db, row["user_id"], now)
    day = game_day(now)
    db.execute("""INSERT INTO casual_game_completions(session_id,user_id,game,day,attempts,reward_xc,completed_at)
        VALUES(?,?,?,?,?,?,?)""", (row["id"], row["user_id"], "memory", day, row["attempts"], reward, now))
    db.execute("INSERT OR IGNORE INTO casual_game_progress(user_id) VALUES(?)", (row["user_id"],))
    db.execute("""UPDATE casual_game_progress SET memory_completed=memory_completed+1,
        memory_best=CASE WHEN memory_best=0 OR ?<memory_best THEN ? ELSE memory_best END WHERE user_id=?""",
        (row["attempts"], row["attempts"], row["user_id"]))
    progress = db.execute("SELECT * FROM casual_game_progress WHERE user_id=?", (row["user_id"],)).fetchone()
    unlocked = _unlock(db, row["user_id"], row["attempts"], progress["memory_completed"], now)
    if unlocked and not progress["equipped_title"]:
        title = next(name for key, name, _ in COLLECTIBLES if key == unlocked[0])
        db.execute("UPDATE casual_game_progress SET equipped_title=? WHERE user_id=?", (title, row["user_id"]))
    if reward:
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (reward, row["user_id"]))
    detail = f"Memory Match · {row['attempts']} attempts · +{reward} XC"
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (row["user_id"], "casual_memory_complete", detail, now))
    return {"reward": reward, "unlocked": unlocked, "duplicate": False}


def flip_memory(db, user_id, session_id, position, expected_version, now=None):
    now = int(now or time.time())
    if not isinstance(position, int) or not 0 <= position < 6:
        raise ValueError("Choose a valid card.")

    def operation():
        row = db.execute("SELECT * FROM casual_game_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not row or row["status"] != "active":
            raise ValueError("This game is no longer active. Open Play to refresh.")
        if row["updated_at"] + SESSION_TTL <= now:
            db.execute("UPDATE casual_game_sessions SET status='expired',updated_at=? WHERE id=?", (now, session_id))
            raise ValueError("This game expired. Start a new game from Play.")
        if row["version"] != expected_version:
            raise ValueError("The board changed. Refresh the game before choosing another card.")
        matched = set(json.loads(row["matched"]))
        if position in matched or position == row["first_pick"] or row["second_pick"] is not None:
            raise ValueError("Choose another hidden card.")
        first, second, attempts = row["first_pick"], None, row["attempts"]
        board = json.loads(row["board"])
        if first is None:
            first = position
        else:
            second = position
            attempts += 1
            if board[first] == board[second]:
                matched.update((first, second))
                first, second = None, None
        status = "completed" if len(matched) == 6 else "active"
        completed_at = now if status == "completed" else 0
        cursor = db.execute("""UPDATE casual_game_sessions SET matched=?,first_pick=?,second_pick=?,attempts=?,
            version=version+1,status=?,updated_at=?,completed_at=? WHERE id=? AND version=?""",
            (json.dumps(sorted(matched)), first, second, attempts, status, now, completed_at, session_id, expected_version))
        if cursor.rowcount != 1:
            raise ValueError("The board changed. Refresh the game before choosing another card.")
        updated = db.execute("SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)).fetchone()
        result = {"session": updated, "reward": 0, "unlocked": [], "duplicate": False}
        if status == "completed":
            result.update(_complete(db, updated, now))
        return result

    return _transaction(db, operation)


def continue_memory(db, user_id, session_id, expected_version, now=None):
    now = int(now or time.time())

    def operation():
        row = db.execute("SELECT * FROM casual_game_sessions WHERE id=? AND user_id=? AND status='active'", (session_id, user_id)).fetchone()
        if not row or row["version"] != expected_version:
            raise ValueError("The board changed. Refresh the game.")
        if row["updated_at"] + SESSION_TTL <= now:
            raise ValueError("This game expired. Start a new game from Play.")
        if row["second_pick"] is None:
            raise ValueError("There are no cards to hide.")
        db.execute("UPDATE casual_game_sessions SET first_pick=NULL,second_pick=NULL,version=version+1,updated_at=? WHERE id=?", (now, session_id))
        return db.execute("SELECT * FROM casual_game_sessions WHERE id=?", (session_id,)).fetchone()

    return _transaction(db, operation)


def abandon_memory(db, user_id, session_id):
    db.execute("UPDATE casual_game_sessions SET status='abandoned',updated_at=? WHERE id=? AND user_id=? AND status='active'", (int(time.time()), session_id, user_id))
    db.commit()


def progress(db, user_id):
    row = db.execute("SELECT * FROM casual_game_progress WHERE user_id=?", (user_id,)).fetchone()
    unlocked = {r[0] for r in db.execute("SELECT collectible_key FROM casual_collectibles WHERE user_id=?", (user_id,))}
    return {"completed": int(row["memory_completed"]) if row else 0,
            "best": int(row["memory_best"]) if row else 0,
            "title": row["equipped_title"] if row else "", "unlocked": unlocked}


def equip_title(db, user_id, title):
    allowed = {name for key, name, _ in COLLECTIBLES if db.execute("SELECT 1 FROM casual_collectibles WHERE user_id=? AND collectible_key=?", (user_id, key)).fetchone()}
    if title and title not in allowed:
        raise ValueError("Unlock this title before equipping it.")
    db.execute("INSERT OR IGNORE INTO casual_game_progress(user_id) VALUES(?)", (user_id,))
    db.execute("UPDATE casual_game_progress SET equipped_title=? WHERE user_id=?", (title, user_id))
    db.commit()


def profile_line(db, user_id):
    data = progress(db, user_id)
    return f"Title **{data['title'] or 'None'}** · Collection **{len(data['unlocked'])}/{len(COLLECTIBLES)}**"


def _page(bot, owner, key, member=None):
    builder = getattr(bot, "xbot_system_page_builder", None)
    if builder:
        return builder(owner, key, member=member)
    factory = getattr(bot, "xbot_player_panel_builders", {}).get(key)
    return factory(owner) if factory else PlayHubView(bot, None, owner)


class CasualButton(discord.ui.Button):
    def __init__(self, view, label, action, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        super().__init__(label=label, style=style, emoji=emoji, disabled=disabled)
        self.panel, self.action = view, action

    async def callback(self, interaction):
        if interaction.user.id != self.panel.owner:
            await interaction.response.send_message("Open /menu for your own game.", ephemeral=True)
            return
        await self.panel.act(interaction, self.action)


class PlayHubView(discord.ui.LayoutView):
    def __init__(self, bot, db, owner, notice=""):
        super().__init__(timeout=900)
        self.bot, self.db, self.owner = bot, db, owner
        box = discord.ui.Container(accent_color=discord.Color(0x41D9D0))
        player = db.execute("SELECT xc FROM players WHERE user_id=?", (owner,)).fetchone()
        status, data = reward_status(db, owner), progress(db, owner)
        active = active_session(db, owner)
        box.add_item(discord.ui.TextDisplay("-# ✦ X SYSTEM / PLAY\n## PLAY\nFree games, Casino and your collection." + (f"\n{notice}" if notice else "")))
        box.add_item(discord.ui.Separator())
        reward_text = (f"Reward games left **{status['rewarded_left']}** · Up to **{status['xc_left']} XC** today\nResets <t:{status['reset_at']}:R>"
                       if setting(db, "free_games_enabled") else "Free games are currently closed.")
        box.add_item(discord.ui.TextDisplay(f"### 🧠 Free Games\n**Memory Match** · {reward_text}"))
        memory = CasualButton(self, "Resume Memory" if active else "Play Memory", ("memory",), discord.ButtonStyle.success, "🧠", disabled=not setting(db, "free_games_enabled"))
        box.add_item(discord.ui.ActionRow(memory))
        box.add_item(discord.ui.Separator())
        box.add_item(discord.ui.TextDisplay(f"### 🎰 Casino\nWallet **{player['xc'] if player else 0:,} XC** · Stakes can be lost."))
        box.add_item(discord.ui.ActionRow(CasualButton(self, "Casino Games", ("nav", "casino"), discord.ButtonStyle.primary, "🎰"), CasualButton(self, "Earn XC", ("nav", "earn_menu"), emoji="💰")))
        box.add_item(discord.ui.Separator())
        box.add_item(discord.ui.TextDisplay(f"### 🏆 Your Collection\nUnlocked **{len(data['unlocked'])}/{len(COLLECTIBLES)}** · Title **{data['title'] or 'None'}**\nMemory games completed **{data['completed']}**"))
        box.add_item(discord.ui.ActionRow(CasualButton(self, "Collection & Titles", ("collection",), emoji="🏆")))
        self.add_item(box)

    async def act(self, interaction, action):
        await interaction.response.defer()
        if action[0] == "memory":
            try:
                row = active_session(self.db, self.owner) or start_memory(self.db, self.owner)
            except (ValueError, sqlite3.Error) as error:
                await interaction.followup.send(str(error) if isinstance(error, ValueError) else 'Games are busy. Please try again.', ephemeral=True)
                return
            view = MemoryView(self.bot, self.db, self.owner, row["id"])
        elif action[0] == "collection":
            view = CollectionView(self.bot, self.db, self.owner)
        else:
            view = _page(self.bot, self.owner, action[1], interaction.user)
        await interaction.edit_original_response(view=view, attachments=[])


class MemoryView(discord.ui.LayoutView):
    xbot_managed_navigation = True

    def __init__(self, bot, db, owner, session_id=None, result=None, notice=""):
        super().__init__(timeout=SESSION_TTL)
        self.bot, self.db, self.owner = bot, db, owner
        row = db.execute("SELECT * FROM casual_game_sessions WHERE id=? AND user_id=?", (session_id, owner)).fetchone() if session_id else active_session(db, owner)
        self.session_id = row["id"] if row else None
        box = discord.ui.Container(accent_color=discord.Color.green() if row and row["status"] == "completed" else discord.Color(0x41D9D0))
        if not row:
            box.add_item(discord.ui.TextDisplay("-# ✦ X SYSTEM / PLAY\n## 🧠 MEMORY MATCH\nMatch all three pairs. No bet is required." + (f"\n{notice}" if notice else "")))
            box.add_item(discord.ui.ActionRow(CasualButton(self, "Start Game", ("start",), discord.ButtonStyle.success), CasualButton(self, "Games", ("games",))))
        elif row["status"] == "completed":
            completion = db.execute("SELECT * FROM casual_game_completions WHERE session_id=?", (row["id"],)).fetchone()
            reward = int(completion["reward_xc"]) if completion else int((result or {}).get("reward", 0))
            unlocked = (result or {}).get("unlocked", [])
            names = [name for key, name, _ in COLLECTIBLES if key in unlocked]
            reward_line = f"Reward **+{reward} XC**" if reward else "Practice complete · No XC reward"
            unlock_line = "\nNew: **" + ", ".join(names) + "**" if names else ""
            box.add_item(discord.ui.TextDisplay(f"-# ✦ X SYSTEM / PLAY\n## ✅ MEMORY COMPLETE\nAttempts **{row['attempts']}** · {reward_line}{unlock_line}"))
            box.add_item(discord.ui.ActionRow(CasualButton(self, "Play Again", ("restart",), discord.ButtonStyle.success), CasualButton(self, "Games", ("games",)), CasualButton(self, "Collection", ("collection",))))
            box.add_item(discord.ui.ActionRow(CasualButton(self, "Menu", ("menu",)), CasualButton(self, "Close", ("close",))))
        elif row["status"] != "active" or row["updated_at"] + SESSION_TTL <= int(time.time()):
            box.add_item(discord.ui.TextDisplay("## MEMORY MATCH\nThis game has ended. Open Games to start again."))
            box.add_item(discord.ui.ActionRow(CasualButton(self, "Games", ("games",)), CasualButton(self, "Menu", ("menu",))))
        else:
            board, matched = json.loads(row["board"]), set(json.loads(row["matched"]))
            status = reward_status(db, owner)
            mode = f"Reward available · up to {status['xc_left']} XC left" if status["rewarded_left"] else f"Practice · No XC reward · Resets <t:{status['reset_at']}:R>"
            instruction = "Press Continue to hide the unmatched cards." if row["second_pick"] is not None else "Choose two cards to find a matching pair."
            box.add_item(discord.ui.TextDisplay(f"-# ✦ X SYSTEM / PLAY\n## 🧠 MEMORY MATCH\nAttempts **{row['attempts']}** · Pairs **{len(matched)//2}/3**\n{mode}\n{instruction}" + (f"\n{notice}" if notice else "")))
            for start in (0, 3):
                buttons = []
                for position in range(start, start + 3):
                    shown = position in matched or position in {row["first_pick"], row["second_pick"]}
                    buttons.append(CasualButton(self, str(position + 1), ("flip", position, row["version"]), discord.ButtonStyle.success if position in matched else discord.ButtonStyle.primary if shown else discord.ButtonStyle.secondary, board[position] if shown else None, disabled=position in matched or row["second_pick"] is not None))
                box.add_item(discord.ui.ActionRow(*buttons))
            if row["second_pick"] is not None:
                box.add_item(discord.ui.ActionRow(CasualButton(self, "Continue", ("continue", row["version"]), discord.ButtonStyle.primary)))
            box.add_item(discord.ui.ActionRow(CasualButton(self, "Exit Game", ("exit",))))
        self.add_item(box)

    async def act(self, interaction, action):
        try:
            await interaction.response.defer()
            if action[0] == "start":
                row = start_memory(self.db, self.owner)
                view = MemoryView(self.bot, self.db, self.owner, row["id"])
            elif action[0] == "restart":
                row = start_memory(self.db, self.owner)
                view = MemoryView(self.bot, self.db, self.owner, row["id"])
            elif action[0] == "flip":
                result = flip_memory(self.db, self.owner, self.session_id, action[1], action[2])
                view = MemoryView(self.bot, self.db, self.owner, self.session_id, result=result)
            elif action[0] == "continue":
                row = continue_memory(self.db, self.owner, self.session_id, action[1])
                view = MemoryView(self.bot, self.db, self.owner, row["id"])
            elif action[0] == "exit":
                view = ExitMemoryView(self.bot, self.db, self.owner, self.session_id)
            elif action[0] == "collection":
                view = CollectionView(self.bot, self.db, self.owner)
            elif action[0] == "games":
                view = _page(self.bot, self.owner, "play", interaction.user)
            elif action[0] == "menu":
                view = _page(self.bot, self.owner, "menu", interaction.user)
            else:
                view = discord.ui.LayoutView()
                view.add_item(discord.ui.TextDisplay("Game closed. Open /menu to play again."))
            await interaction.edit_original_response(view=view, attachments=[])
        except (ValueError, sqlite3.Error) as error:
            view = MemoryView(self.bot, self.db, self.owner, self.session_id, notice=str(error))
            if interaction.response.is_done():
                await interaction.edit_original_response(view=view)
            else:
                await interaction.response.edit_message(view=view)


class ExitMemoryView(discord.ui.LayoutView):
    xbot_managed_navigation = True

    def __init__(self, bot, db, owner, session_id):
        super().__init__(timeout=300)
        self.bot, self.db, self.owner, self.session_id = bot, db, owner, session_id
        box = discord.ui.Container(accent_color=discord.Color(0xFFB020))
        box.add_item(discord.ui.TextDisplay("## Exit Memory Match?\nThis unfinished game gives no XC reward or collection progress."))
        box.add_item(discord.ui.ActionRow(CasualButton(self, "Keep Playing", ("keep",), discord.ButtonStyle.primary), CasualButton(self, "Exit Game", ("confirm",), discord.ButtonStyle.danger)))
        self.add_item(box)

    async def act(self, interaction, action):
        await interaction.response.defer()
        if action[0] == "confirm":
            abandon_memory(self.db, self.owner, self.session_id)
            view = _page(self.bot, self.owner, "play", interaction.user)
        else:
            view = MemoryView(self.bot, self.db, self.owner, self.session_id)
        await interaction.edit_original_response(view=view)


class TitleSelect(discord.ui.Select):
    def __init__(self, view, data):
        options = [discord.SelectOption(label="No title", value="none", default=not data["title"])]
        options += [discord.SelectOption(label=name, value=name, description=description[:100], default=data["title"] == name) for key, name, description in COLLECTIBLES if key in data["unlocked"]]
        super().__init__(placeholder="Choose a title to display", options=options)
        self.panel = view

    async def callback(self, interaction):
        if interaction.user.id != self.panel.owner:
            await interaction.response.send_message("Open your own Collection panel.", ephemeral=True)
            return
        equip_title(self.panel.db, self.panel.owner, "" if self.values[0] == "none" else self.values[0])
        await interaction.response.edit_message(view=CollectionView(self.panel.bot, self.panel.db, self.panel.owner, "Title updated."))


class CollectionView(discord.ui.LayoutView):
    xbot_managed_navigation = True

    def __init__(self, bot, db, owner, notice=""):
        super().__init__(timeout=900)
        self.bot, self.db, self.owner = bot, db, owner
        data = progress(db, owner)
        lines = []
        for key, name, description in COLLECTIBLES:
            lines.append(f"{'✅' if key in data['unlocked'] else '▫️'} **{name}**\n{description}")
        best = str(data["best"]) if data["best"] else "—"
        box = discord.ui.Container(accent_color=discord.Color(0x41D9D0))
        box.add_item(discord.ui.TextDisplay(f"-# ✦ X SYSTEM / PROFILE\n## 🏆 COLLECTION & TITLES\nUnlocked **{len(data['unlocked'])}/{len(COLLECTIBLES)}** · Equipped **{data['title'] or 'None'}**\nMemory games **{data['completed']}** · Best **{best} attempts**" + (f"\n{notice}" if notice else "")))
        box.add_item(discord.ui.Separator())
        box.add_item(discord.ui.TextDisplay("\n\n".join(lines)))
        if data["unlocked"]:
            box.add_item(discord.ui.ActionRow(TitleSelect(self, data)))
        box.add_item(discord.ui.ActionRow(CasualButton(self, "Play Memory", ("memory",), discord.ButtonStyle.success, disabled=not setting(db, 'free_games_enabled')), CasualButton(self, "Profile", ("profile",))))
        box.add_item(discord.ui.ActionRow(CasualButton(self, "Games", ("play",)), CasualButton(self, "Menu", ("menu",)), CasualButton(self, "Close", ("close",))))
        self.add_item(box)

    async def act(self, interaction, action):
        await interaction.response.defer()
        if action[0] == 'close':
            await interaction.edit_original_response(content='Panel closed.', view=None, attachments=[])
            return
        if action[0] == "memory":
            try:
                row = active_session(self.db, self.owner) or start_memory(self.db, self.owner)
            except (ValueError, sqlite3.Error) as error:
                await interaction.followup.send(str(error) if isinstance(error, ValueError) else 'Games are busy. Please try again.', ephemeral=True)
                return
            view = MemoryView(self.bot, self.db, self.owner, row["id"])
        else:
            view = _page(self.bot, self.owner, action[0], interaction.user)
        await interaction.edit_original_response(view=view)


def register(bot, db, create_player):
    initialise(db)
    builders = bot.xbot_player_panel_builders
    builders["play"] = lambda owner: PlayHubView(bot, db, owner)
    builders["memory"] = lambda owner: MemoryView(bot, db, owner)
    builders["collection"] = lambda owner: CollectionView(bot, db, owner)
