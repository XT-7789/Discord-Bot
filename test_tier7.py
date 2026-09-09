"""Regression tests for Tier 7 Warfront 2.0 using a disposable database."""

import shutil
import sqlite3
import tempfile
import time
import unittest
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import tier7
import war_system
import war_tier


class Tier7WarfrontTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "tier7-test.db"
        shutil.copy2(Path(__file__).with_name("xwar.db"), self.database_path)
        self.db = sqlite3.connect(self.database_path)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        war_system.initialise(self.db)
        war_tier.initialise(self.db)
        tier7.initialise(self.db)
        self.attacker = 990000000000000071
        self.defender = 990000000000000072
        for user_id in (self.attacker, self.defender):
            self.db.execute("DELETE FROM players WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM player_war_settings WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM player_war_units WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM map_territories WHERE owner_user_id=?", (user_id,))
            self.db.execute("DELETE FROM tier7_defence_profiles WHERE user_id=?", (user_id,))
        self.db.execute("UPDATE war_seasons SET status='ended' WHERE status='active'")
        now = int(time.time())
        self.db.execute(
            """INSERT INTO players
               (user_id,nation_name,display_name,money,land,capital_health,nation_created_at,last_attack)
               VALUES(?,?,?,?,?,?,0,0)""",
            (self.attacker, "Tier 7 Attacker", "T7 Attacker", 100_000, 1, 100),
        )
        self.db.execute(
            """INSERT INTO players
               (user_id,nation_name,display_name,money,land,capital_health,nation_created_at,last_attack)
               VALUES(?,?,?,?,?,?,0,0)""",
            (self.defender, "Tier 7 Defender", "T7 Defender", 50_000, 1, 100),
        )
        self.db.executemany(
            """INSERT INTO map_territories
               (territory_code,territory_name,owner_user_id,is_capital,acquired_at,level)
               VALUES(?,?,?,?,?,1)""",
            (
                ("T7-ATT-CAP", "Attacker Capital", self.attacker, 1, now),
                ("T7-DEF-LAND", "Defender Front Land", self.defender, 0, now),
                ("T7-DEF-CAP", "Defender Capital", self.defender, 1, now),
            ),
        )
        infantry = self.db.execute("SELECT id FROM war_unit_types WHERE code='infantry'").fetchone()["id"]
        fighter = self.db.execute("SELECT id FROM war_unit_types WHERE code='fighter'").fetchone()["id"]
        self.db.executemany(
            "INSERT OR REPLACE INTO player_war_units(user_id,unit_type_id,quantity) VALUES(?,?,?)",
            (
                (self.attacker, infantry, 500), (self.attacker, fighter, 30),
                (self.defender, infantry, 20), (self.defender, fighter, 2),
            ),
        )
        self.db.execute(
            "INSERT INTO nation_wars(attacker_id,defender_id,started_at,ends_at,active) VALUES(?,?,?,?,1)",
            (self.attacker, self.defender, now, now + 86400),
        )
        for key in ("tier7_attack_cooldown_seconds", "tier7_pair_cooldown_seconds"):
            self.db.execute("UPDATE economy_settings SET value='0' WHERE key=?", (key,))
        self.db.commit()
        tier7.ensure_player_state(self.db, self.attacker)
        tier7.ensure_player_state(self.db, self.defender)

    @staticmethod
    def no_alliance(_user_id):
        return None

    @staticmethod
    def no_alliance_war():
        return None

    def test_unrelated_alliance_war_does_not_hide_nation_war_target(self):
        unrelated_alliance_war = {
            "id": 7000,
            "attacker_alliance_id": 7001,
            "defender_alliance_id": 7002,
        }

        def alliance_for(user_id):
            return {"id": 7003} if user_id == self.attacker else None

        targets = tier7.eligible_targets(
            self.db, self.attacker, lambda: unrelated_alliance_war, alliance_for,
        )
        self.assertEqual([self.defender], [int(row["user_id"]) for row in targets])
        conflict, error = tier7._conflict_for(
            self.db, self.attacker, self.defender, lambda: unrelated_alliance_war, alliance_for,
        )
        self.assertIsNone(error)
        self.assertEqual("nation", conflict["kind"])

    def test_plan_preview_resolve_and_double_click_are_safe(self):
        plan = tier7.create_plan(self.db, self.attacker, self.defender, "standard")
        self.assertEqual("T7-DEF-LAND", plan["territory_code"])
        preview, errors = tier7.preview_plan(
            self.db, plan["id"], self.no_alliance_war, self.no_alliance
        )
        self.assertFalse(errors)
        self.assertGreater(preview["attacker_win_chance"], 50)
        success, report, errors = tier7.resolve_plan(
            self.db, plan["id"], self.no_alliance_war, self.no_alliance, seed="tier7-win"
        )
        self.assertTrue(success, errors)
        self.assertEqual(1, report["land_captured"])
        self.assertEqual(
            self.attacker,
            self.db.execute(
                "SELECT owner_user_id FROM map_territories WHERE territory_code='T7-DEF-LAND'"
            ).fetchone()["owner_user_id"],
        )
        def result_snapshot():
            return {
                "players": tuple(tuple(row) for row in self.db.execute(
                    """SELECT user_id,money,land,capital_health,last_attack FROM players
                       WHERE user_id IN (?,?) ORDER BY user_id""",
                    (self.attacker, self.defender),
                ).fetchall()),
                "states": tuple(tuple(row) for row in self.db.execute(
                    """SELECT user_id,supply,readiness,morale FROM player_war_settings
                       WHERE user_id IN (?,?) ORDER BY user_id""",
                    (self.attacker, self.defender),
                ).fetchall()),
                "units": tuple(tuple(row) for row in self.db.execute(
                    """SELECT user_id,unit_type_id,quantity FROM player_war_units
                       WHERE user_id IN (?,?) ORDER BY user_id,unit_type_id""",
                    (self.attacker, self.defender),
                ).fetchall()),
                "territories": tuple(tuple(row) for row in self.db.execute(
                    """SELECT territory_code,owner_user_id,is_capital FROM map_territories
                       WHERE owner_user_id IN (?,?) ORDER BY territory_code""",
                    (self.attacker, self.defender),
                ).fetchall()),
                "battles": self.db.execute("SELECT COUNT(*) amount FROM battle_history").fetchone()["amount"],
                "reports": self.db.execute("SELECT COUNT(*) amount FROM tier7_battle_reports").fetchone()["amount"],
            }

        before_retry = result_snapshot()
        success_again, report_again, errors_again = tier7.resolve_plan(
            self.db, plan["id"], self.no_alliance_war, self.no_alliance, seed="different"
        )
        self.assertTrue(success_again, errors_again)
        self.assertEqual(report["id"], report_again["id"])
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) amount FROM tier7_battle_reports WHERE plan_id=?", (plan["id"],)
        ).fetchone()["amount"])
        self.assertEqual(before_retry, result_snapshot())

    def test_capital_cannot_be_selected_before_ordinary_land(self):
        plan = tier7.create_plan(self.db, self.attacker, self.defender)
        tier7.update_plan_territory(self.db, plan["id"], "T7-DEF-CAP")
        plan = self.db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan["id"],)).fetchone()
        errors, _metadata = tier7.validate_plan(
            self.db, plan, self.no_alliance_war, self.no_alliance
        )
        self.assertTrue(any("ordinary Land" in error for error in errors))

    def test_remote_capital_is_not_attackable_after_ordinary_land_is_gone(self):
        self.db.execute(
            "DELETE FROM map_territories WHERE territory_code IN ('T7-ATT-CAP','T7-DEF-LAND','T7-DEF-CAP','CAP-USA','CAP-JPN')"
        )
        self.db.executemany(
            """INSERT INTO map_territories
               (territory_code,territory_name,owner_user_id,is_capital,acquired_at,level)
               VALUES(?,?,?,?,?,1)""",
            (
                ("CAP-USA", "Washington, D.C. — Capital City, United States of America", self.attacker, 1, int(time.time())),
                ("CAP-JPN", "Tokyo — Capital City, Japan", self.defender, 1, int(time.time())),
            ),
        )
        self.db.commit()
        tier7.ensure_defence_rows(self.db)
        self.assertEqual([], tier7.attackable_territories(self.db, self.attacker, self.defender))
        plan = tier7.create_plan(self.db, self.attacker, self.defender)
        tier7.update_plan_territory(self.db, plan["id"], "CAP-JPN")
        plan = self.db.execute("SELECT * FROM tier7_battle_plans WHERE id=?", (plan["id"],)).fetchone()
        errors, _metadata = tier7.validate_plan(self.db, plan, self.no_alliance_war, self.no_alliance)
        self.assertTrue(any("active front" in error for error in errors))

    def test_mode_balance_change_recalculates_selected_deployment(self):
        plan = tier7.create_plan(self.db, self.attacker, self.defender, "standard")
        infantry_id = self.db.execute("SELECT id FROM war_unit_types WHERE code='infantry'").fetchone()["id"]
        before = self.db.execute(
            "SELECT quantity FROM tier7_plan_units WHERE plan_id=? AND unit_type_id=?",
            (plan["id"], infantry_id),
        ).fetchone()["quantity"]
        self.assertEqual(350, before)
        self.db.execute(
            "UPDATE economy_settings SET value='35' WHERE key='tier7_standard_force_percent'"
        )
        self.db.commit()
        preview, errors = tier7.preview_plan(self.db, plan["id"], self.no_alliance_war, self.no_alliance)
        self.assertIsNotNone(preview, errors)
        after = self.db.execute(
            "SELECT quantity FROM tier7_plan_units WHERE plan_id=? AND unit_type_id=?",
            (plan["id"], infantry_id),
        ).fetchone()["quantity"]
        self.assertEqual(175, after)

    def test_two_database_connections_cannot_resolve_two_stale_plans_together(self):
        self.db.execute(
            "UPDATE economy_settings SET value='60' WHERE key='tier7_attack_cooldown_seconds'"
        )
        first = tier7.create_plan(self.db, self.attacker, self.defender)
        second = tier7.create_plan(self.db, self.attacker, self.defender)
        self.db.commit()
        barrier = threading.Barrier(2)

        def resolve_from_separate_process(plan_id):
            connection = sqlite3.connect(self.database_path, timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                barrier.wait(timeout=5)
                return tier7.resolve_plan(
                    connection, plan_id, self.no_alliance_war, self.no_alliance,
                    seed=f"concurrent-{plan_id}",
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resolve_from_separate_process, (first["id"], second["id"])))
        self.assertEqual(1, sum(1 for success, _report, _errors in results if success))
        report_count = self.db.execute(
            "SELECT COUNT(*) amount FROM tier7_battle_reports WHERE plan_id IN (?,?)",
            (first["id"], second["id"]),
        ).fetchone()["amount"]
        self.assertEqual(1, report_count)

    def test_loss_summary_stays_inside_discord_limit(self):
        losses = {}
        for index in range(20):
            cursor = self.db.execute(
                """INSERT INTO war_unit_types(code,name,emoji,branch,cost,power,description,enabled,position)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"t7-long-{index}", f"Very Long Tier Seven Unit Model {index} " + "X" * 80,
                 "⚔️", "land", 1, 1, "", 1, 500 + index),
            )
            losses[int(cursor.lastrowid)] = index + 1
        summary = tier7._loss_text(self.db, losses)
        self.assertLessEqual(len(summary), 700)
        self.assertIn("more model", summary)

    def test_fortification_single_and_batch_charge_once(self):
        before = self.db.execute("SELECT money FROM players WHERE user_id=?", (self.attacker,)).fetchone()["money"]
        success, _message = tier7.fortify_territory(self.db, self.attacker, "T7-ATT-CAP")
        self.assertTrue(success)
        level = self.db.execute(
            "SELECT fortification_level FROM tier7_territory_defence WHERE territory_code='T7-ATT-CAP'"
        ).fetchone()["fortification_level"]
        after = self.db.execute("SELECT money FROM players WHERE user_id=?", (self.attacker,)).fetchone()["money"]
        self.assertEqual(1, level)
        self.assertEqual(tier7.setting(self.db, "tier7_fortification_base_cost"), before - after)
        success, message = tier7.batch_fortify(self.db, self.attacker)
        self.assertTrue(success, message)
        self.assertIn("Fortified", message)

    def test_health_repair_expires_old_draft(self):
        plan = tier7.create_plan(self.db, self.attacker, self.defender)
        interrupted = tier7.create_plan(self.db, self.attacker, self.defender)
        missing_report = tier7.create_plan(self.db, self.attacker, self.defender)
        self.db.execute("UPDATE tier7_battle_plans SET expires_at=1 WHERE id=?", (plan["id"],))
        self.db.execute(
            "UPDATE tier7_battle_plans SET status='resolving',expires_at=1 WHERE id=?", (interrupted["id"],)
        )
        self.db.execute(
            "UPDATE tier7_battle_plans SET status='resolved',battle_id=999999 WHERE id=?", (missing_report["id"],)
        )
        self.db.commit()
        before = tier7.health_report(self.db)
        self.assertGreaterEqual(before["expired_drafts"], 1)
        self.assertGreaterEqual(before["stuck_resolving"], 1)
        self.assertGreaterEqual(before["missing_reports"], 1)
        tier7.repair(self.db)
        statuses = {
            row["id"]: row["status"] for row in self.db.execute(
                "SELECT id,status FROM tier7_battle_plans WHERE id IN (?,?,?)",
                (plan["id"], interrupted["id"], missing_report["id"]),
            ).fetchall()
        }
        self.assertEqual("expired", statuses[plan["id"]])
        self.assertEqual("failed", statuses[interrupted["id"]])
        self.assertEqual("failed", statuses[missing_report["id"]])

    def test_all_tier7_discord_pages_construct_offline(self):
        class FakeTree:
            def __init__(self):
                self.commands = {}

            def remove_command(self, name, **_kwargs):
                return self.commands.pop(name, None)

            def command(self, name, **_kwargs):
                def decorator(function):
                    self.commands[name] = function
                    return function
                return decorator

        class FakeBot:
            def __init__(self):
                self.tree = FakeTree()
                self.xbot_player_panel_builders = {
                    "war": lambda _owner_id: None,
                    "city": lambda _owner_id: None,
                    "diplomacy": lambda _owner_id: None,
                }
                self.xbot_player_lobby_builder = lambda _owner_id: None
                self.xbot_army_recruit_builder = lambda _owner_id: None

        fake = FakeBot()
        tier7.register_commands(
            fake, self.db, lambda user: self.db.execute(
                "SELECT * FROM players WHERE user_id=?", (user.id,)
            ).fetchone(), self.no_alliance_war, self.no_alliance,
        )
        attack_page = fake.xbot_tier7_attack_builder(self.attacker, self.defender)
        pages = (
            fake.xbot_player_panel_builders["war"](self.attacker),
            attack_page,
            fake.xbot_tier7_defence_builder(self.attacker),
            fake.xbot_tier7_army_builder(self.attacker),
            fake.xbot_tier7_reports_builder(self.attacker),
        )
        self.assertTrue(all(page.children for page in pages))
        attack_payload = str(attack_page.to_components())
        self.assertIn("Choose an active enemy Nation", attack_payload)
        self.assertIn("Tier 7 Defender", attack_payload)
        for heading in ('Target & Objective','Deployment','Launch Cost'):
            self.assertIn(heading,attack_payload)
        self.assertIn('Defensive Posture',str(pages[2].to_components()))
        self.assertIn('BATTLE HISTORY',str(pages[4].to_components()))
        self.assertTrue(all(page.total_children_count<=40 for page in pages))
        self.assertEqual({"war", "attack"}, set(fake.tree.commands))

    def test_large_land_and_unit_libraries_have_selectable_pages(self):
        now = int(time.time())
        extra_enemy_ids = [990000000000000100 + index for index in range(26)]
        self.db.executemany(
            """INSERT INTO players
               (user_id,nation_name,display_name,money,land,capital_health,nation_created_at,last_attack)
               VALUES(?,?,?,?,?,?,0,0)""",
            ((user_id, f"ZZ Enemy {index:02d}", f"Enemy {index:02d}", 1000, 1, 100)
             for index, user_id in enumerate(extra_enemy_ids)),
        )
        self.db.executemany(
            "INSERT INTO nation_wars(attacker_id,defender_id,started_at,ends_at,active) VALUES(?,?,?,?,1)",
            ((self.attacker, user_id, now, now + 86400) for user_id in extra_enemy_ids),
        )
        self.db.executemany(
            """INSERT INTO map_territories
               (territory_code,territory_name,owner_user_id,is_capital,acquired_at,level)
               VALUES(?,?,?,?,?,1)""",
            ((f"T7-PAGE-{index:02d}", f"Defender Land {index:02d}", self.defender, 0, now + index)
             for index in range(30)),
        )
        for index in range(30):
            cursor = self.db.execute(
                """INSERT INTO war_unit_types(code,name,emoji,branch,cost,power,description,enabled,position)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"t7-page-unit-{index}", f"Tier 7 Page Unit {index}", "🪖", "land", 1, 1, "", 1, 600 + index),
            )
            self.db.execute(
                "INSERT INTO player_war_units(user_id,unit_type_id,quantity) VALUES(?,?,10)",
                (self.attacker, int(cursor.lastrowid)),
            )
        self.db.commit()
        tier7.ensure_defence_rows(self.db, self.defender)

        class FakeTree:
            def __init__(self):
                self.commands = {}

            def remove_command(self, name, **_kwargs):
                return self.commands.pop(name, None)

            def command(self, name, **_kwargs):
                def decorator(function):
                    self.commands[name] = function
                    return function
                return decorator

        class FakeBot:
            def __init__(self):
                self.tree = FakeTree()
                self.xbot_player_panel_builders = {
                    "war": lambda _owner_id: None,
                    "city": lambda _owner_id: None,
                    "diplomacy": lambda _owner_id: None,
                }
                self.xbot_player_lobby_builder = lambda _owner_id: None
                self.xbot_army_recruit_builder = lambda _owner_id: None

        fake = FakeBot()
        tier7.register_commands(
            fake, self.db, lambda user: self.db.execute(
                "SELECT * FROM players WHERE user_id=?", (user.id,)
            ).fetchone(), self.no_alliance_war, self.no_alliance,
        )
        attack_payload = str(fake.xbot_tier7_attack_builder(self.attacker, self.defender).to_components())
        defence_payload = str(fake.xbot_tier7_defence_builder(self.defender).to_components())
        self.assertIn("Next Enemies", attack_payload)
        self.assertIn("Next Land", attack_payload)
        self.assertIn("Next Units", attack_payload)
        self.assertIn("Next Land", defence_payload)

    def test_guild_registration_removes_legacy_global_war_commands(self):
        guild_id = 123456789012345678

        class ScopedTree:
            def __init__(self):
                self.commands = {
                    (None, "war"): object(), (None, "attack"): object(),
                    (guild_id, "war"): object(), (guild_id, "attack"): object(),
                }

            @staticmethod
            def scope(guild):
                return int(guild.id) if guild is not None else None

            def remove_command(self, name, guild=None, **_kwargs):
                return self.commands.pop((self.scope(guild), name), None)

            def command(self, name, guild=None, **_kwargs):
                def decorator(function):
                    self.commands[(self.scope(guild), name)] = function
                    return function
                return decorator

        class FakeBot:
            def __init__(self):
                self.tree = ScopedTree()
                self.xbot_player_panel_builders = {"war": lambda _owner_id: None}

        fake = FakeBot()
        with mock.patch.dict(os.environ, {"DISCORD_GUILD_ID": str(guild_id)}):
            tier7.register_commands(
                fake, self.db, lambda _user: None, self.no_alliance_war, self.no_alliance,
            )
        self.assertNotIn((None, "war"), fake.tree.commands)
        self.assertNotIn((None, "attack"), fake.tree.commands)
        self.assertEqual("tier7", fake.tree.commands[(guild_id, "war")].__module__)
        self.assertEqual("tier7", fake.tree.commands[(guild_id, "attack")].__module__)


if __name__ == "__main__":
    unittest.main()
