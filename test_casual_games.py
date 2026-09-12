"""Free-game rewards, collections and mobile panels using disposable data."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
import unittest

import casual_games as games
import economy_settings_admin as settings_admin
import test_economy_journey as fixtures


BOARD = ["⭐", "💎", "🍒", "⭐", "💎", "🍒"]


class CasualGameTests(unittest.TestCase):
    def setUp(self):
        fixtures.JourneyTests.setUp(self)
        games.initialise(self.db)
        self.db.execute("""CREATE TABLE IF NOT EXISTS dashboard_audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id TEXT,actor_name TEXT,access_level TEXT,
            endpoint TEXT,method TEXT,detail TEXT,status_code INTEGER,created_at INTEGER)""")
        self.db.commit()

    def wallet(self):
        return self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.uid,)).fetchone()[0]

    def complete(self, now=1700000000, board=BOARD):
        row = games.start_memory(self.db, self.uid, now, board)
        result = None
        for first, second in ((0, 3), (1, 4), (2, 5)):
            row = games.flip_memory(self.db, self.uid, row["id"], first, row["version"], now)["session"]
            result = games.flip_memory(self.db, self.uid, row["id"], second, row["version"], now)
            row = result["session"]
        return row, result

    def test_reward_schedule_practice_and_idempotency(self):
        before = self.wallet()
        rewards = []
        last = None
        for number in range(4):
            row, result = self.complete(1700000000 + number)
            rewards.append(result["reward"]); last = row
        self.assertEqual([9, 8, 8, 0], rewards)
        self.assertEqual(before + 25, self.wallet())
        with self.assertRaises(ValueError):
            games.flip_memory(self.db, self.uid, last["id"], 0, last["version"])
        self.assertEqual(before + 25, self.wallet())
        self.assertEqual((4, 25), tuple(self.db.execute("SELECT COUNT(*),SUM(reward_xc) FROM casual_game_completions WHERE user_id=?", (self.uid,)).fetchone()))

    def test_mismatch_requires_continue_and_expired_game_is_safe(self):
        row = games.start_memory(self.db, self.uid, 1000, BOARD)
        row = games.flip_memory(self.db, self.uid, row["id"], 0, row["version"], 1001)["session"]
        row = games.flip_memory(self.db, self.uid, row["id"], 1, row["version"], 1002)["session"]
        self.assertIsNotNone(row["second_pick"])
        with self.assertRaises(ValueError):
            games.flip_memory(self.db, self.uid, row["id"], 2, row["version"], 1003)
        row = games.continue_memory(self.db, self.uid, row["id"], row["version"], 1004)
        self.assertIsNone(row["first_pick"]); self.assertIsNone(row["second_pick"])
        self.assertIsNone(games.active_session(self.db, self.uid, 1004 + games.SESSION_TTL))
        self.assertEqual(0, self.db.execute("SELECT COUNT(*) FROM casual_game_completions").fetchone()[0])

    def test_two_connections_share_one_active_session_and_one_settlement(self):
        def begin():
            with closing(sqlite3.connect(self.path, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                return games.start_memory(db, self.uid, 1700000000, BOARD)["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: begin(), range(2)))
        self.assertEqual(1, len(set(ids)))
        row = self.db.execute("SELECT * FROM casual_game_sessions WHERE id=?", (ids[0],)).fetchone()
        for first, second in ((0, 3), (1, 4)):
            row = games.flip_memory(self.db, self.uid, row["id"], first, row["version"], 1700000001)["session"]
            row = games.flip_memory(self.db, self.uid, row["id"], second, row["version"], 1700000001)["session"]
        row = games.flip_memory(self.db, self.uid, row["id"], 2, row["version"], 1700000002)["session"]
        version = row["version"]
        def finish():
            with closing(sqlite3.connect(self.path, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                try:
                    return games.flip_memory(db, self.uid, row["id"], 5, version, 1700000003)["reward"]
                except ValueError:
                    return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            rewards = list(pool.map(lambda _: finish(), range(2)))
        self.assertEqual(1, sum(value is not None for value in rewards))
        self.assertEqual(1, self.db.execute("SELECT COUNT(*) FROM casual_game_completions WHERE session_id=?", (row["id"],)).fetchone()[0])

    def test_collections_titles_and_panels(self):
        bot = SimpleNamespace(xbot_player_panel_builders={})
        games.register(bot, self.db, lambda user: None)
        row, result = self.complete()
        data = games.progress(self.db, self.uid)
        self.assertEqual({"first_match", "sharp_memory", "perfect_recall"}, data["unlocked"])
        self.assertEqual("First Match", data["title"])
        games.equip_title(self.db, self.uid, "Perfect Recall")
        self.assertIn("Perfect Recall", games.profile_line(self.db, self.uid))
        with self.assertRaises(ValueError): games.equip_title(self.db, self.uid, "Memory Regular")
        for view in (games.PlayHubView(bot, self.db, self.uid), games.MemoryView(bot, self.db, self.uid, row["id"], result), games.CollectionView(bot, self.db, self.uid)):
            view.to_components()
            self.assertLessEqual(view.total_children_count, 40)

    def test_dashboard_validation_and_atomic_failure(self):
        actor = ("1", "Tester", "admin")
        changes = settings_admin.save(self.db, {"memory_daily_reward_games": "5", "memory_daily_xc_limit": "40"}, settings_admin.FREE_GAME_KEYS, actor, "casino_control")
        self.assertEqual("5", changes["memory_daily_reward_games"]["after"])
        with self.assertRaises(ValueError):
            settings_admin.save(self.db, {"memory_daily_reward_games": "11", "memory_daily_xc_limit": "30"}, settings_admin.FREE_GAME_KEYS, actor, "casino_control")
        self.assertEqual(40, games.setting(self.db, "memory_daily_xc_limit"))


if __name__ == "__main__":
    unittest.main()
