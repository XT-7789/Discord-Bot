"""Unit tests for X BOT Activity Streak Rework and Weekly Dividends."""

import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import leveling
import economy_extra


class LevelingStreakTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE economy_settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("""CREATE TABLE players (
            user_id INTEGER PRIMARY KEY,
            money INTEGER NOT NULL DEFAULT 0,
            xc INTEGER NOT NULL DEFAULT 0,
            xcrystals INTEGER NOT NULL DEFAULT 0,
            display_name TEXT NOT NULL DEFAULT '',
            nation_name TEXT NOT NULL DEFAULT '',
            capital_name TEXT NOT NULL DEFAULT '',
            last_daily INTEGER NOT NULL DEFAULT 0
        )""")
        self.db.execute("""CREATE TABLE economy_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            detail TEXT,
            created_at INTEGER
        )""")
        self.db.execute("""CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT,
            emoji TEXT,
            enabled INTEGER NOT NULL DEFAULT 1
        )""")
        self.db.execute("""CREATE TABLE inventories (
            user_id INTEGER,
            item_id INTEGER,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, item_id)
        )""")
        leveling.initialise(self.db)
        economy_extra.initialise(self.db)

    def tearDown(self):
        self.db.close()

    def test_streak_rewards_seeded(self):
        rows = self.db.execute("SELECT * FROM streak_rewards ORDER BY days").fetchall()
        self.assertGreaterEqual(len(rows), 6)
        day_map = {r["days"]: r for r in rows}
        self.assertIn(2, day_map)
        self.assertIn(3, day_map)
        self.assertIn(7, day_map)
        self.assertIn(30, day_map)

        day7 = day_map[7]
        self.assertEqual(day7["xc"], 100)
        self.assertEqual(day7["money"], 50000)
        self.assertEqual(day7["xp"], 250)

    def test_update_activity_streak_progression(self):
        member = SimpleNamespace(id=1001, display_name="GamerAlpha")
        today = int(time.time() // 86400)

        # Day 1: New streak
        streak, rewards = leveling._update_activity_streak(self.db, member)
        self.assertEqual(streak, 1)
        self.assertEqual(len(rewards), 0)

        # Advance to Day 2 (streak continues)
        self.db.execute("UPDATE xp_profiles SET last_active_day=? WHERE user_id=?", (today, 1001))
        with patch("time.time", return_value=(today + 1) * 86400 + 100):
            streak, rewards = leveling._update_activity_streak(self.db, member)
            self.assertEqual(streak, 2)
            self.assertGreater(len(rewards), 0)  # Day 2 reward awarded!

            # Check wallet credited
            player = self.db.execute("SELECT * FROM players WHERE user_id=1001").fetchone()
            self.assertGreaterEqual(player["money"], 5000)
            self.assertGreaterEqual(player["xc"], 10)

            # Check profile XP credited
            prof = self.db.execute("SELECT * FROM xp_profiles WHERE user_id=1001").fetchone()
            self.assertGreaterEqual(prof["total_xp"], 25)

    async def test_settle_weekly_dividends(self):
        bot = MagicMock(spec=discord.Client)
        bot.get_channel.return_value = None

        past_week = "2026-W37"
        current_week = time.strftime("%Y-W%W", time.gmtime())

        # Set initial settled week to past_week
        self.db.execute("INSERT OR REPLACE INTO economy_settings(key,value) VALUES('last_settled_dividend_week',?)", (past_week,))

        # Seed top earners for past_week
        for uid, xp in [(2001, 5000), (2002, 3500), (2003, 2000), (2004, 1000)]:
            self.db.execute("INSERT INTO players(user_id, money, xc) VALUES(?, 0, 0)", (uid,))
            self.db.execute(
                "INSERT INTO xp_profiles(user_id, total_xp, weekly_xp, last_week_key) VALUES(?,?,?,?)",
                (uid, xp, xp, past_week),
            )
        self.db.commit()

        results = await leveling.settle_weekly_dividends(bot, self.db)
        self.assertEqual(len(results), 4)

        # Rank 1: $100k, 250 XC
        r1 = self.db.execute("SELECT * FROM players WHERE user_id=2001").fetchone()
        self.assertEqual(r1["money"], 100000)
        self.assertEqual(r1["xc"], 250)

        # Rank 2: $60k, 150 XC
        r2 = self.db.execute("SELECT * FROM players WHERE user_id=2002").fetchone()
        self.assertEqual(r2["money"], 60000)
        self.assertEqual(r2["xc"], 150)

        # Rank 3: $40k, 100 XC
        r3 = self.db.execute("SELECT * FROM players WHERE user_id=2003").fetchone()
        self.assertEqual(r3["money"], 40000)
        self.assertEqual(r3["xc"], 100)

        # Rank 4: $20k, 50 XC
        r4 = self.db.execute("SELECT * FROM players WHERE user_id=2004").fetchone()
        self.assertEqual(r4["money"], 20000)
        self.assertEqual(r4["xc"], 50)

        # Setting updated to current week
        settled_setting = self.db.execute("SELECT value FROM economy_settings WHERE key='last_settled_dividend_week'").fetchone()["value"]
        self.assertEqual(settled_setting, current_week)

    async def test_daily_command_streak_bonus(self):
        # Register commands to a mock bot
        bot = MagicMock()
        bot.tree = MagicMock()
        commands_registered = {}
        def mock_command(**kwargs):
            def decorator(func):
                commands_registered[kwargs.get("name", func.__name__)] = func
                return func
            return decorator
        bot.tree.command = mock_command

        def dummy_create_player(u):
            self.db.execute("INSERT OR IGNORE INTO players(user_id, display_name) VALUES(?,?)", (u.id, u.display_name))
            self.db.commit()
            return self.db.execute("SELECT * FROM players WHERE user_id=?", (u.id,)).fetchone()

        economy_extra.register_commands(bot, self.db, dummy_create_player, lambda *a: None)
        daily_cmd = commands_registered.get("daily")
        self.assertIsNotNone(daily_cmd)

        user = MagicMock(spec=discord.Member)
        user.id = 5555
        user.display_name = "DailyStreakUser"

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = user
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        # Day 1 daily
        await daily_cmd(interaction)
        interaction.response.send_message.assert_called_once()
        player = self.db.execute("SELECT * FROM players WHERE user_id=5555").fetchone()
        self.assertGreaterEqual(player["money"], 10000)
        self.assertGreaterEqual(player["xc"], 50)

        # Check streak advanced in xp_profiles
        prof = self.db.execute("SELECT * FROM xp_profiles WHERE user_id=5555").fetchone()
        self.assertEqual(prof["current_streak"], 1)


if __name__ == "__main__":
    unittest.main()

