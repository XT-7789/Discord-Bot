"""Regression tests for Tier 6 Economy.  All changes use a temporary database copy."""

import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import economy_extra
import tier5
import tier6


class Tier6EconomyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "tier6-test.db"
        shutil.copy2(Path(__file__).with_name("xwar.db"), self.database_path)
        self.db = sqlite3.connect(self.database_path)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        tier5.initialise(self.db)
        tier6.initialise(self.db)
        self.user_id = 990000000000000006
        self.db.execute("DELETE FROM players WHERE user_id=?", (self.user_id,))
        self.db.execute(
            "INSERT INTO players(user_id,nation_name,display_name,xc) VALUES(?,?,?,?)",
            (self.user_id, "Tier 6 Test Nation", "Tier 6 Test", 2_000_000),
        )
        self.db.execute(
            """INSERT OR REPLACE INTO tier5_profiles
               (user_id,nation_level,nation_xp,last_rewarded_level,created_at,updated_at)
               VALUES(?,?,?,?,?,?)""",
            (self.user_id, 5, 0, 1, int(time.time()), int(time.time())),
        )
        self.db.commit()

    def test_stock_buy_and_sell_preserves_share_supply(self):
        company = self.db.execute(
            "SELECT * FROM tier6_stock_companies WHERE enabled=1 AND available_shares>=10 ORDER BY id LIMIT 1"
        ).fetchone()
        before_available = int(company["available_shares"])
        success, _ = tier6.stock_trade(self.db, self.user_id, company["id"], "buy", 10)
        self.assertTrue(success)
        holding = self.db.execute(
            "SELECT quantity FROM tier6_stock_holdings WHERE user_id=? AND company_id=?",
            (self.user_id, company["id"]),
        ).fetchone()
        self.assertEqual(10, holding["quantity"])
        success, _ = tier6.stock_trade(self.db, self.user_id, company["id"], "sell", 10)
        self.assertTrue(success)
        self.assertIsNone(self.db.execute(
            "SELECT 1 FROM tier6_stock_holdings WHERE user_id=? AND company_id=?",
            (self.user_id, company["id"]),
        ).fetchone())
        after_available = self.db.execute(
            "SELECT available_shares FROM tier6_stock_companies WHERE id=?", (company["id"],)
        ).fetchone()[0]
        self.assertEqual(before_available, after_available)

    def test_contract_reward_can_only_be_claimed_once_per_period(self):
        now = int(time.time())
        self.db.execute("DELETE FROM tier6_contract_claims WHERE user_id=?", (self.user_id,))
        self.db.executemany(
            "INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
            [(self.user_id, "mine", "tier6 test", now) for _ in range(3)],
        )
        before = int(self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.user_id,)).fetchone()[0])
        first = tier6.claim_contracts(self.db, self.user_id)
        middle = int(self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.user_id,)).fetchone()[0])
        second = tier6.claim_contracts(self.db, self.user_id)
        after = int(self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.user_id,)).fetchone()[0])
        self.assertIn("Claimed", first)
        self.assertGreater(middle, before)
        self.assertIn("No completed", second)
        self.assertEqual(middle, after)

    def test_production_start_claim_and_cancel(self):
        recipe = self.db.execute("SELECT * FROM recipes WHERE enabled=1 ORDER BY id LIMIT 1").fetchone()
        self.assertIsNotNone(recipe)
        ingredients = self.db.execute(
            "SELECT item_id,quantity FROM recipe_ingredients WHERE recipe_id=?", (recipe["id"],)
        ).fetchall()
        for row in ingredients:
            self.db.execute(
                "INSERT OR REPLACE INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)",
                (self.user_id, row["item_id"], int(row["quantity"]) * 20),
            )
        self.db.commit()
        success, _ = tier6.start_production(self.db, self.user_id, recipe["id"], 2)
        self.assertTrue(success)
        before_output = self.db.execute(
            "SELECT quantity FROM inventories WHERE user_id=? AND item_id=?",
            (self.user_id, recipe["output_item_id"]),
        ).fetchone()
        before_output_quantity = int(before_output["quantity"]) if before_output else 0
        job = self.db.execute(
            "SELECT * FROM tier6_production_queue WHERE user_id=? AND status='working' ORDER BY id DESC LIMIT 1",
            (self.user_id,),
        ).fetchone()
        self.db.execute(
            "UPDATE recipes SET output_quantity=output_quantity+100 WHERE id=?",
            (recipe["id"],),
        )
        self.db.execute("UPDATE tier6_production_queue SET ready_at=? WHERE id=?", (int(time.time()) - 1, job["id"]))
        self.db.commit()
        result = tier6.claim_production(self.db, self.user_id)
        self.assertIn("Collected", result)
        output = self.db.execute(
            "SELECT quantity FROM inventories WHERE user_id=? AND item_id=?",
            (self.user_id, recipe["output_item_id"]),
        ).fetchone()
        self.assertEqual(before_output_quantity + int(recipe["output_quantity"]) * 2, int(output["quantity"]))

        self.db.execute(
            "UPDATE recipes SET output_quantity=? WHERE id=?",
            (recipe["output_quantity"], recipe["id"]),
        )
        before_cancel_xc = int(self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.user_id,)).fetchone()[0])

        success, _ = tier6.start_production(self.db, self.user_id, recipe["id"], 1)
        self.assertTrue(success)
        job = self.db.execute(
            "SELECT id FROM tier6_production_queue WHERE user_id=? AND status='working' ORDER BY id DESC LIMIT 1",
            (self.user_id,),
        ).fetchone()
        self.db.execute("UPDATE recipes SET xc_cost=xc_cost+99999 WHERE id=?", (recipe["id"],))
        self.db.execute("UPDATE recipe_ingredients SET quantity=quantity+999 WHERE recipe_id=?", (recipe["id"],))
        result = tier6.cancel_production(self.db, self.user_id, job["id"])
        self.assertIn("Cancelled", result)
        after_cancel_xc = int(self.db.execute("SELECT xc FROM players WHERE user_id=?", (self.user_id,)).fetchone()[0])
        self.assertEqual(before_cancel_xc, after_cancel_xc)

    def test_expired_market_listing_is_returned_once(self):
        item = self.db.execute("SELECT id FROM items WHERE enabled=1 ORDER BY id LIMIT 1").fetchone()
        before = self.db.execute(
            "SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (self.user_id, item["id"])
        ).fetchone()
        before_quantity = int(before["quantity"]) if before else 0
        old = int(time.time()) - 10 * 86400
        cursor = self.db.execute(
            "INSERT INTO market_listings(seller_id,item_id,quantity,price_each,created_at) VALUES(?,?,?,?,?)",
            (self.user_id, item["id"], 3, 10, old),
        )
        self.db.commit()
        self.assertEqual(1, economy_extra.expire_market_listings(self.db))
        self.assertEqual(0, economy_extra.expire_market_listings(self.db))
        listing = self.db.execute("SELECT active FROM market_listings WHERE id=?", (cursor.lastrowid,)).fetchone()
        quantity = self.db.execute(
            "SELECT quantity FROM inventories WHERE user_id=? AND item_id=?", (self.user_id, item["id"])
        ).fetchone()[0]
        self.assertEqual(0, listing["active"])
        self.assertEqual(before_quantity + 3, quantity)


if __name__ == "__main__":
    unittest.main()
