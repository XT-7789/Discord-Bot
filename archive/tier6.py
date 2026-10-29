"""Tier 6 unified Economy, contracts, production queues, and virtual stocks."""

from __future__ import annotations

import math
import random
import time
import tier8
import economy_journey
import svip
from datetime import datetime, timezone

import discord
from discord import app_commands

import xbot_ui


DEFAULTS = {
    **svip.DEFAULTS,
    "tier6_economy_enabled": "1",
    "tier6_contracts_enabled": "1",
    "tier6_production_enabled": "1",
    "tier6_production_seconds_per_item": "300",
    "tier6_production_queue_limit": "5",
    "tier6_industrial_speed_percent": "5",
    "tier6_industrial_speed_cap_percent": "50",
    "tier6_stock_enabled": "1",
    "tier6_stock_fee_percent": "2",
    "tier6_stock_update_seconds": "3600",
    "tier6_stock_max_change_percent": "12",
    "tier6_stock_holding_limit": "100000",
    "tier6_stock_price_impact": "20",
    "tier6_market_expiry_days": "7",
    "tier6_market_max_listings": "20",
}


STOCK_SEEDS = (
    ("XMN", "X Mining Group", "⛏️", "Mining", "Resource extraction and mining equipment.", 120, 40, 800, 100000, 8),
    ("XIN", "X Industries", "🏭", "Industry", "Industrial production and city machinery.", 180, 50, 1200, 80000, 7),
    ("XDV", "X Development", "🏙️", "Development", "Civilian construction and infrastructure.", 150, 40, 1000, 90000, 6),
    ("XDF", "X Defence", "⚔️", "Defence", "Military technology and logistics.", 240, 80, 1600, 70000, 10),
    ("XLG", "X Logistics", "🚢", "Logistics", "International shipping and supply chains.", 100, 30, 700, 120000, 5),
    ("XTC", "X Technology", "🧪", "Technology", "Research, crafting, and advanced materials.", 300, 90, 2200, 60000, 12),
    ("XFN", "X Finance", "🏦", "Finance", "Banking and market infrastructure.", 200, 60, 1400, 75000, 5),
    ("XEN", "X Entertainment", "🎰", "Entertainment", "Games, events, and entertainment.", 90, 20, 650, 130000, 14),
)


CONTRACT_SEEDS = (
    ("daily_mine", "Mining Order", "⛏️", "Complete 3 mining actions.", "mine", 3, 35, 75, "daily", 1),
    ("daily_sell", "Supply Delivery", "📦", "Complete 1 item or material sale.", "sell", 1, 40, 60, "daily", 1),
    ("daily_collect", "Production Collection", "🏙️", "Collect Nation or City production once.", "collect", 1, 25, 100, "daily", 1),
    ("weekly_develop", "Development Programme", "🏗️", "Build or upgrade Cities 3 times.", "develop", 3, 120, 300, "weekly", 2),
    ("weekly_trade", "Market Participant", "🏷️", "Complete 3 player market or stock trades.", "trade", 3, 150, 250, "weekly", 2),
    ("weekly_recruit", "Defence Procurement", "🪖", "Complete 3 recruitment orders.", "recruit", 3, 100, 350, "weekly", 2),
)


def initialise(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tier6_stock_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '📈',
            industry TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price INTEGER NOT NULL,
            previous_price INTEGER NOT NULL,
            min_price INTEGER NOT NULL,
            max_price INTEGER NOT NULL,
            total_shares INTEGER NOT NULL,
            available_shares INTEGER NOT NULL,
            volatility INTEGER NOT NULL DEFAULT 8,
            trend INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_update INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tier6_stock_holdings (
            user_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            average_cost INTEGER NOT NULL,
            PRIMARY KEY(user_id,company_id)
        );
        CREATE TABLE IF NOT EXISTS tier6_stock_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price INTEGER NOT NULL,
            fee INTEGER NOT NULL,
            total INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tier6_stock_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            price INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tier6_stock_trades_user ON tier6_stock_trades(user_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_tier6_stock_prices_company ON tier6_stock_prices(company_id,created_at);

        CREATE TABLE IF NOT EXISTS tier6_market_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price_each INTEGER NOT NULL,
            fee INTEGER NOT NULL,
            total INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tier6_market_trades_buyer ON tier6_market_trades(buyer_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_tier6_market_trades_seller ON tier6_market_trades(seller_id,created_at);

        CREATE TABLE IF NOT EXISTS tier6_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_key TEXT NOT NULL UNIQUE COLLATE NOCASE,
            title TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '📋',
            description TEXT NOT NULL DEFAULT '',
            action_type TEXT NOT NULL,
            target INTEGER NOT NULL,
            reward_xc INTEGER NOT NULL DEFAULT 0,
            reward_war_credits INTEGER NOT NULL DEFAULT 0,
            reward_item_id INTEGER,
            reward_item_quantity INTEGER NOT NULL DEFAULT 0,
            period TEXT NOT NULL DEFAULT 'daily',
            minimum_level INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS tier6_contract_claims (
            user_id INTEGER NOT NULL,
            contract_id INTEGER NOT NULL,
            period_key TEXT NOT NULL,
            claimed_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,contract_id,period_key)
        );

        CREATE TABLE IF NOT EXISTS tier6_production_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipe_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            xc_paid INTEGER NOT NULL DEFAULT 0,
            output_item_id INTEGER,
            output_quantity INTEGER,
            started_at INTEGER NOT NULL,
            ready_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'working',
            claimed_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS tier6_production_inputs (
            job_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY(job_id,item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tier6_production_user ON tier6_production_queue(user_id,status,ready_at);

        CREATE TABLE IF NOT EXISTS tier6_economy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_xc INTEGER NOT NULL,
            total_bank INTEGER NOT NULL,
            total_war_credits INTEGER NOT NULL,
            stock_value INTEGER NOT NULL,
            active_listings INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    production_columns = {row["name"] for row in db.execute("PRAGMA table_info(tier6_production_queue)").fetchall()}
    for column, definition in {
        "xc_paid": "INTEGER NOT NULL DEFAULT 0",
        "output_item_id": "INTEGER",
        "output_quantity": "INTEGER",
    }.items():
        if column not in production_columns:
            db.execute(f"ALTER TABLE tier6_production_queue ADD COLUMN {column} {definition}")
    for key, value in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)", (key, value))
    if not db.execute("SELECT 1 FROM tier6_stock_companies LIMIT 1").fetchone():
        now = int(time.time())
        db.executemany(
            """INSERT INTO tier6_stock_companies
               (symbol,name,emoji,industry,description,price,previous_price,min_price,max_price,total_shares,available_shares,volatility,last_update)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [row[:6] + (row[5],) + row[6:9] + (row[8], row[9], now) for row in STOCK_SEEDS],
        )
    for row in CONTRACT_SEEDS:
        db.execute(
            """INSERT OR IGNORE INTO tier6_contracts
               (contract_key,title,emoji,description,action_type,target,reward_xc,reward_war_credits,period,minimum_level)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            row,
        )
    db.commit()


def setting(db, key: str) -> int:
    row = db.execute("SELECT value FROM economy_settings WHERE key=?", (key,)).fetchone()
    return int(row["value"] if row else DEFAULTS[key])


def _period(period: str, now: int):
    current = datetime.fromtimestamp(now, timezone.utc)
    if period == "weekly":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        since = int(start.timestamp()) - current.weekday() * 86400
        return current.strftime("%G-W%V"), since
    if period == "once":
        return "once", 0
    since = int(current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return current.strftime("%Y-%m-%d"), since


def update_stock_prices(db, now: int | None = None):
    owns_transaction = not db.in_transaction
    now = int(now or time.time())
    interval = max(60, setting(db, "tier6_stock_update_seconds"))
    global_cap = max(1, min(50, setting(db, "tier6_stock_max_change_percent")))
    changed = 0
    for company in db.execute("SELECT * FROM tier6_stock_companies WHERE enabled=1").fetchall():
        elapsed = max(0, now - int(company["last_update"] or 0))
        steps = min(24, elapsed // interval)
        if not steps:
            continue
        price = int(company["price"])
        previous = price
        for offset in range(int(steps)):
            bucket = (int(company["last_update"] or 0) // interval) + offset + 1
            generator = random.Random(f"xbot-stock:{company['id']}:{bucket}")
            cap = min(global_cap, max(1, int(company["volatility"])))
            move = generator.randint(-cap, cap) + int(company["trend"] or 0)
            move = max(-global_cap, min(global_cap, move))
            price = max(int(company["min_price"]), min(int(company["max_price"]), round(price * (100 + move) / 100)))
        updated_at = (int(company["last_update"] or now) + int(steps) * interval) if company["last_update"] else now
        db.execute("UPDATE tier6_stock_companies SET previous_price=?,price=?,last_update=? WHERE id=?", (previous, price, updated_at, company["id"]))
        db.execute("INSERT INTO tier6_stock_prices(company_id,price,created_at) VALUES(?,?,?)", (company["id"], price, now))
        changed += 1
    if changed and owns_transaction:
        db.commit()
    return changed


def stock_quote(db, user_id, company_id, side, quantity):
    if side not in {'buy','sell'} or not isinstance(quantity,int) or quantity<1:
        raise ValueError('Choose a positive whole number of shares.')
    company=db.execute('SELECT * FROM tier6_stock_companies WHERE id=?',(company_id,)).fetchone()
    if not company:raise ValueError('Company unavailable.')
    gross=int(company['price'])*quantity
    fee=tier8.discounted(db,user_id,'trade',max(0,math.ceil(gross*setting(db,'tier6_stock_fee_percent')/100)))
    return dict(company=dict(company),gross=gross,fee=fee,total=gross+fee if side=='buy' else max(0,gross-fee))


@economy_journey.atomic_action
def stock_trade(db, user_id: int, company_id: int, side: str, quantity: int, expected=None):
    if side not in {'buy','sell'}:return False,'Unknown stock trade type.'
    quantity = int(quantity)
    if not setting(db, "tier6_economy_enabled") or not setting(db, "tier6_stock_enabled"):
        return False, "The Stock Market is currently closed."
    if quantity <= 0:
        return False, "Quantity must be at least 1 share."
    update_stock_prices(db)
    company = db.execute("SELECT * FROM tier6_stock_companies WHERE id=? AND enabled=1", (company_id,)).fetchone()
    player = db.execute("SELECT xc FROM players WHERE user_id=?", (user_id,)).fetchone()
    if not company or not player:
        return False, "That company or player is unavailable."
    price = int(company["price"])
    quoted=stock_quote(db,user_id,company_id,side,quantity)
    gross,fee=quoted['gross'],quoted['fee']
    if expected is not None and expected != (price,fee,quoted['total']):
        return False,'Price or fee changed. Review the updated quote and confirm again.'
    holding = db.execute("SELECT * FROM tier6_stock_holdings WHERE user_id=? AND company_id=?", (user_id, company_id)).fetchone()
    owned = int(holding["quantity"]) if holding else 0
    if side == "buy":
        total = gross + fee
        if quantity > int(company["available_shares"]):
            return False, f"Only {company['available_shares']:,} shares are available."
        if owned + quantity > setting(db, "tier6_stock_holding_limit"):
            return False, "This trade exceeds the per-company holding limit."
        if int(player["xc"]) < total:
            return False, f"You need {total:,} XC including the {fee:,} XC fee."
        average = round(((owned * int(holding["average_cost"]) if holding else 0) + gross) / (owned + quantity))
        db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (total, user_id))
        db.execute("UPDATE tier6_stock_companies SET available_shares=available_shares-? WHERE id=?", (quantity, company_id))
        db.execute(
            """INSERT INTO tier6_stock_holdings(user_id,company_id,quantity,average_cost) VALUES(?,?,?,?)
               ON CONFLICT(user_id,company_id) DO UPDATE SET quantity=excluded.quantity,average_cost=excluded.average_cost""",
            (user_id, company_id, owned + quantity, average),
        )
        message = f"✅ Bought **{quantity:,} {company['symbol']}** at **{price:,} XC** each. Total **{total:,} XC** including fee."
    elif side == "sell":
        if owned < quantity:
            return False, f"You only own {owned:,} shares."
        total = max(0, gross - fee)
        db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (total, user_id))
        db.execute("UPDATE tier6_stock_companies SET available_shares=MIN(total_shares,available_shares+?) WHERE id=?", (quantity, company_id))
        remaining = owned - quantity
        if remaining:
            db.execute("UPDATE tier6_stock_holdings SET quantity=? WHERE user_id=? AND company_id=?", (remaining, user_id, company_id))
        else:
            db.execute("DELETE FROM tier6_stock_holdings WHERE user_id=? AND company_id=?", (user_id, company_id))
        message = f"✅ Sold **{quantity:,} {company['symbol']}** at **{price:,} XC** each. Received **{total:,} XC** after fee."
    else:
        return False, "Unknown stock trade type."
    now = int(time.time())
    impact_strength = max(0, min(100, setting(db, "tier6_stock_price_impact")))
    impact_fraction = min(
        setting(db, "tier6_stock_max_change_percent") / 100,
        quantity * impact_strength / max(1, int(company["total_shares"])),
    )
    direction = 1 if side == "buy" else -1
    impacted_price = round(price * (1 + direction * impact_fraction))
    impacted_price = max(int(company["min_price"]), min(int(company["max_price"]), impacted_price))
    if impacted_price != price:
        db.execute(
            "UPDATE tier6_stock_companies SET previous_price=?,price=? WHERE id=?",
            (price, impacted_price, company_id),
        )
        db.execute(
            "INSERT INTO tier6_stock_prices(company_id,price,created_at) VALUES(?,?,?)",
            (company_id, impacted_price, now),
        )
    db.execute(
        "INSERT INTO tier6_stock_trades(user_id,company_id,side,quantity,price,fee,total,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, company_id, side, quantity, price, fee, total, now),
    )
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, "stock_trade", message.replace("**", ""), now))
    return True, message


def portfolio(db, user_id: int):
    update_stock_prices(db)
    rows = db.execute(
        """SELECT h.*,c.symbol,c.name,c.emoji,c.price FROM tier6_stock_holdings h
           JOIN tier6_stock_companies c ON c.id=h.company_id WHERE h.user_id=? ORDER BY c.symbol""",
        (user_id,),
    ).fetchall()
    value = sum(int(row["quantity"]) * int(row["price"]) for row in rows)
    cost = sum(int(row["quantity"]) * int(row["average_cost"]) for row in rows)
    return rows, value, cost


ACTION_LOGS = {
    "mine": ("mine",),
    "sell": ("sell_item", "sell_mined", "backpack_sell"),
    "collect": ("collect", "city_collect"),
    "develop": ("city_build", "city_bulk_build", "city_upgrade", "city_bulk_upgrade", "land_upgrade"),
    "trade": ("market_buy", "market_sale", "stock_trade"),
    "recruit": ("recruit",),
    "craft": ("craft", "production_claim"),
}


def contract_rows(db, user_id: int, now: int | None = None):
    now = int(now or time.time())
    level_row = db.execute("SELECT nation_level FROM tier5_profiles WHERE user_id=?", (user_id,)).fetchone()
    level = int(level_row["nation_level"]) if level_row else 1
    output = []
    for contract in db.execute("SELECT * FROM tier6_contracts WHERE enabled=1 AND minimum_level<=? ORDER BY period,id", (level,)).fetchall():
        period_key, since = _period(contract["period"], now)
        actions = ACTION_LOGS.get(contract["action_type"], (contract["action_type"],))
        placeholders = ",".join("?" for _ in actions)
        progress = int(db.execute(
            f"SELECT COUNT(*) FROM economy_logs WHERE user_id=? AND action IN ({placeholders}) AND created_at>=?",
            (user_id, *actions, since),
        ).fetchone()[0])
        claimed = db.execute(
            "SELECT 1 FROM tier6_contract_claims WHERE user_id=? AND contract_id=? AND period_key=?",
            (user_id, contract["id"], period_key),
        ).fetchone() is not None
        output.append({**dict(contract), "period_key": period_key, "progress": min(progress, int(contract["target"])), "claimed": claimed})
    return output


def claim_contracts(db, user_id: int):
    if not setting(db, "tier6_economy_enabled") or not setting(db, "tier6_contracts_enabled"):
        return "Contracts are currently closed."
    ready = [row for row in contract_rows(db, user_id) if not row["claimed"] and row["progress"] >= row["target"]]
    if not ready:
        return "No completed Contract rewards are ready."
    xc = credits = count = 0
    items = {}
    now = int(time.time())
    for row in ready:
        cursor = db.execute(
            "INSERT OR IGNORE INTO tier6_contract_claims(user_id,contract_id,period_key,claimed_at) VALUES(?,?,?,?)",
            (user_id, row["id"], row["period_key"], now),
        )
        if not cursor.rowcount:
            continue
        count += 1
        xc += int(row["reward_xc"])
        credits += int(row["reward_war_credits"])
        if row["reward_item_id"] and row["reward_item_quantity"]:
            items[int(row["reward_item_id"])] = items.get(int(row["reward_item_id"]), 0) + int(row["reward_item_quantity"])
    db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?", (xc, credits, user_id))
    for item_id, quantity in items.items():
        db.execute(
            """INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
               ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
            (user_id, item_id, quantity),
        )
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, "contract_reward", f"{count} contracts, +{xc} XC, +{credits} War Credits", now))
    db.commit()
    return f"✅ Claimed **{count} Contracts**: **+{xc:,} XC** and **+{credits:,} War Credits**."


@economy_journey.atomic_action
def start_production(db, user_id: int, recipe_id: int, quantity: int, expected=None):
    if not setting(db, "tier6_economy_enabled") or not setting(db, "tier6_production_enabled"):
        return False, "Production is currently closed."
    quantity = int(quantity)
    if quantity <= 0 or quantity > 1000:
        return False, "Production quantity must be between 1 and 1,000."
    try:
        quote = economy_journey.quote(db,user_id,recipe_id,quantity)
    except ValueError as error:
        return False,str(error)
    if not quote['available']:
        return False,'Crafting or this recipe is disabled.'
    if expected is not None and expected != quote['fingerprint']:
        return False,'Recipe or prices changed. Review again.'
    active = int(db.execute("SELECT COUNT(*) FROM tier6_production_queue WHERE user_id=? AND status='working'", (user_id,)).fetchone()[0])
    if active >= svip.production_limit(db,user_id):
        return False, "Your Production Queue is full."
    recipe = db.execute("SELECT * FROM recipes WHERE id=? AND enabled=1", (recipe_id,)).fetchone()
    player = db.execute("SELECT xc FROM players WHERE user_id=?", (user_id,)).fetchone()
    if not recipe or not player:
        return False, "That Recipe is unavailable."
    total_xc = int(recipe["xc_cost"]) * quantity
    if int(player["xc"]) < total_xc:
        return False, f"You need {total_xc:,} XC."
    ingredients = db.execute(
        """SELECT ri.item_id,ri.quantity,i.name,i.emoji,COALESCE(inv.quantity,0) owned
           FROM recipe_ingredients ri JOIN items i ON i.id=ri.item_id
           LEFT JOIN inventories inv ON inv.item_id=ri.item_id AND inv.user_id=? WHERE ri.recipe_id=?""",
        (user_id, recipe_id),
    ).fetchall()
    missing = [f"{row['emoji']} {row['name']} ×{int(row['quantity']) * quantity}" for row in ingredients if int(row["owned"]) < int(row["quantity"]) * quantity]
    if missing:
        return False, "Missing materials: " + ", ".join(missing)
    for row in ingredients:
        db.execute("UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?", (int(row["quantity"]) * quantity, user_id, row["item_id"]))
    db.execute("DELETE FROM inventories WHERE user_id=? AND quantity<=0", (user_id,))
    db.execute("UPDATE players SET xc=xc-? WHERE user_id=?", (total_xc, user_id))
    industrial_levels = int(db.execute("SELECT COALESCE(SUM(level),0) FROM player_cities WHERE user_id=? AND city_type='industrial'", (user_id,)).fetchone()[0])
    speed = min(setting(db, "tier6_industrial_speed_cap_percent"), industrial_levels * setting(db, "tier6_industrial_speed_percent"))
    seconds = max(30, setting(db, "tier6_production_seconds_per_item") * quantity * (100 - speed) // 100)
    seconds = max(30, tier8.discounted(db, user_id, 'production', seconds))
    # Apply once after existing discounts; never alter a saved job's ready_at.
    seconds=max(30,(seconds*(100-svip.benefits(db,user_id)['percent'])+99)//100)
    now = int(time.time())
    cursor = db.execute(
        """INSERT INTO tier6_production_queue
           (user_id,recipe_id,quantity,xc_paid,output_item_id,output_quantity,started_at,ready_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (user_id, recipe_id, quantity, total_xc, recipe["output_item_id"], recipe["output_quantity"], now, now + seconds),
    )
    for row in ingredients:
        db.execute(
            "INSERT INTO tier6_production_inputs(job_id,item_id,quantity) VALUES(?,?,?)",
            (cursor.lastrowid, row["item_id"], int(row["quantity"]) * quantity),
        )
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, "production_start", f"{recipe['name']} x{quantity}, ready {now + seconds}", now))
    return True, f"✅ Started **{recipe['emoji']} {recipe['name']} ×{quantity:,}**. Ready <t:{now + seconds}:R>. Industrial speed bonus: **{speed}%**."


@economy_journey.atomic_action
def claim_production(db, user_id: int, *, return_items=False):
    now = int(time.time())
    rows = db.execute(
        """SELECT q.*,COALESCE(r.name,'Archived Recipe') name,COALESCE(r.emoji,'🏭') emoji,
           COALESCE(q.output_item_id,r.output_item_id) resolved_output_item_id,
           COALESCE(q.output_quantity,r.output_quantity) resolved_output_quantity,
           i.name item_name,i.emoji item_emoji
           FROM tier6_production_queue q LEFT JOIN recipes r ON r.id=q.recipe_id
           JOIN items i ON i.id=COALESCE(q.output_item_id,r.output_item_id)
           WHERE q.user_id=? AND q.status='working' AND q.ready_at<=? ORDER BY q.id""",
        (user_id, now),
    ).fetchall()
    if not rows:
        notice="No finished Production is ready to collect."
        return (notice,()) if return_items else notice
    totals = {}
    for row in rows:
        amount = int(row["quantity"]) * int(row["resolved_output_quantity"])
        output_item_id = int(row["resolved_output_item_id"])
        totals[output_item_id] = totals.get(output_item_id, 0) + amount
        db.execute("UPDATE tier6_production_queue SET status='claimed',claimed_at=? WHERE id=? AND status='working'", (now, row["id"]))
        economy_journey.audit(db,user_id,'journey_craft',__import__('json').dumps({'recipe':row['recipe_id'],'item':output_item_id,'quantity':amount}))
    for item_id, amount in totals.items():
        db.execute(
            """INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
               ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
            (user_id, item_id, amount),
        )
    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)", (user_id, "production_claim", f"Claimed {len(rows)} production jobs", now))
    notice=f"✅ Collected **{len(rows)}** completed Production job(s)."
    return (notice,tuple(totals)) if return_items else notice


def production_cancel_quote(db, user_id: int, job_id: int):
    """Use the saved order inputs; only legacy jobs fall back to the recipe."""
    row = db.execute(
        """SELECT q.*,COALESCE(r.name,'Archived Recipe') name,COALESCE(r.xc_cost,0) recipe_xc_cost
           FROM tier6_production_queue q LEFT JOIN recipes r ON r.id=q.recipe_id
           WHERE q.id=? AND q.user_id=?""",
        (job_id, user_id),
    ).fetchone()
    if row is None:raise ValueError('Production job unavailable.')
    quantity = int(row["quantity"])
    ingredients = db.execute(
        "SELECT item_id,quantity FROM tier6_production_inputs WHERE job_id=?",
        (job_id,),
    ).fetchall()
    if not ingredients and row['output_item_id'] is None:
        ingredients = [
            {"item_id": item["item_id"], "quantity": int(item["quantity"]) * quantity}
            for item in db.execute(
                "SELECT item_id,quantity FROM recipe_ingredients WHERE recipe_id=?",
                (row["recipe_id"],),
            ).fetchall()
        ]
    refund=int(row['xc_paid'] or 0) if row['output_item_id'] is not None else int(row['xc_paid'] or 0) or int(row['recipe_xc_cost'])*quantity
    return dict(job=dict(row),inputs=[dict(i) for i in ingredients],refund=refund)


@economy_journey.atomic_action
def cancel_production(db, user_id: int, job_id: int):
    """Cancel an unfinished job and safely return its recorded inputs."""
    now=int(time.time())
    try:q=production_cancel_quote(db,user_id,job_id)
    except ValueError:return 'That Production job is no longer active.'
    row=q['job'];quantity=int(row['quantity'])
    if row['status']!='working':return 'That Production job is no longer active.'
    if int(row['ready_at'])<=now:return 'This job is already ready. Collect it instead of cancelling it.'
    for ingredient in q['inputs']:
        db.execute(
            """INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
               ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
            (user_id, ingredient["item_id"], int(ingredient["quantity"])),
        )
    refund=q['refund']
    db.execute("UPDATE players SET xc=xc+? WHERE user_id=?", (refund, user_id))
    db.execute("UPDATE tier6_production_queue SET status='cancelled',claimed_at=? WHERE id=?", (now, job_id))
    db.execute(
        "INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
        (user_id, "production_cancel", f"Cancelled {row['name']} x{quantity}; refunded {refund} XC", now),
    )
    return f"↩️ Cancelled **{row['name']} ×{quantity:,}** and returned its materials plus **{refund:,} XC**."


def economy_summary(db, user_id: int):
    player = db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    holdings, stock_value, stock_cost = portfolio(db, user_id)
    inventory_value = int(db.execute(
        """SELECT COALESCE(SUM(inv.quantity*MAX(i.sell_price,0)),0) FROM inventories inv
           JOIN items i ON i.id=inv.item_id WHERE inv.user_id=?""", (user_id,)
    ).fetchone()[0])
    since = int(time.time()) - 86400
    activity = int(db.execute("SELECT COUNT(*) FROM economy_logs WHERE user_id=? AND created_at>=?", (user_id, since)).fetchone()[0])
    ready_contracts = 0
    if setting(db, "tier6_contracts_enabled"):
        ready_contracts = sum(not row["claimed"] and row["progress"] >= row["target"] for row in contract_rows(db, user_id))
    ready_production = int(db.execute("SELECT COUNT(*) FROM tier6_production_queue WHERE user_id=? AND status='working' AND ready_at<=?", (user_id, int(time.time()))).fetchone()[0])
    active_production = int(db.execute("SELECT COUNT(*) FROM tier6_production_queue WHERE user_id=? AND status='working'", (user_id,)).fetchone()[0])
    net_worth = int(player["xc"]) + int(player["bank_xc"]) + inventory_value + stock_value
    return {
        "player": player, "holdings": holdings, "stock_value": stock_value, "stock_cost": stock_cost,
        "inventory_value": inventory_value, "net_worth": net_worth, "activity": activity,
        "ready_contracts": ready_contracts, "ready_production": ready_production, "active_production": active_production,
    }


def health_report(db):
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    negative_players = int(db.execute("SELECT COUNT(*) FROM players WHERE xc<0 OR bank_xc<0 OR money<0").fetchone()[0])
    invalid_holdings = int(db.execute("SELECT COUNT(*) FROM tier6_stock_holdings WHERE quantity<=0 OR average_cost<0").fetchone()[0])
    invalid_companies = int(db.execute("""SELECT COUNT(*) FROM tier6_stock_companies c
        WHERE price<min_price OR price>max_price OR available_shares<0 OR available_shares>total_shares
        OR available_shares != total_shares-COALESCE((SELECT SUM(quantity) FROM tier6_stock_holdings h WHERE h.company_id=c.id),0)""").fetchone()[0])
    broken_queue = int(db.execute("SELECT COUNT(*) FROM tier6_production_queue WHERE quantity<=0 OR status NOT IN ('working','claimed','cancelled')").fetchone()[0])
    status = "✅ Healthy" if integrity == "ok" and not (negative_players or invalid_holdings or invalid_companies or broken_queue) else "⚠️ Needs repair"
    return f"Economy: {status} · Negative balances {negative_players} · Invalid stocks {invalid_holdings + invalid_companies} · Invalid production {broken_queue} · DB {integrity}"


def repair(db):
    db.execute("UPDATE players SET xc=MAX(0,xc),bank_xc=MAX(0,bank_xc),money=MAX(0,money)")
    db.execute("DELETE FROM tier6_stock_holdings WHERE quantity<=0")
    db.execute("UPDATE tier6_stock_holdings SET average_cost=MAX(0,average_cost)")
    db.execute("UPDATE tier6_stock_companies SET price=MIN(max_price,MAX(min_price,price))")
    for company in db.execute("SELECT id,total_shares FROM tier6_stock_companies").fetchall():
        held = int(db.execute("SELECT COALESCE(SUM(quantity),0) FROM tier6_stock_holdings WHERE company_id=?", (company["id"],)).fetchone()[0])
        total = max(held, int(company["total_shares"]))
        db.execute("UPDATE tier6_stock_companies SET total_shares=?,available_shares=? WHERE id=?", (total, total - held, company["id"]))
    db.execute("UPDATE tier6_production_queue SET status='cancelled' WHERE quantity<=0 OR status NOT IN ('working','claimed','cancelled')")
    db.commit()
    return "✅ Economy repair completed. Balances, Stocks, and Production Queue were normalised."


def register_commands(bot, db, create_player):
    builders = getattr(bot, "xbot_player_panel_builders", {})
    legacy_economy_builder = builders.get("economy")

    async def replace_panel(interaction, builder):
        await interaction.response.defer()
        await interaction.edit_original_response(view=builder())

    class EconomyNavButton(discord.ui.Button):
        def __init__(self, owner_id: int, action: str, label: str, emoji: str, *, style=discord.ButtonStyle.secondary):
            super().__init__(label=label, emoji=emoji, style=style)
            self.owner_id, self.action = owner_id, action

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Open `/economy` for your own Economy Centre.", ephemeral=True)
                return
            # Resolve old embedded buttons through the current UI, not captured
            # pre-redesign factories. Old Discord messages remain safe to use.
            modern=getattr(bot,'xbot_system_page_builder',None)
            routes={'economy_v2':'economy','legacy_economy':'economy','assets':'assets','earn':'contracts','trade':'market_menu','production':'production','stock':'stock'}
            if modern and self.action in routes:
                await replace_panel(interaction,lambda:modern(self.owner_id,routes[self.action]))
                return
            local = {
                "economy_v2": lambda: bot.xbot_player_panel_builders['economy'](self.owner_id),
                "earn": lambda: ContractView(self.owner_id),
                "trade": lambda: TradeHubView(self.owner_id),
                "production": lambda: ProductionView(self.owner_id),
                "stock": lambda: StockMarketView(self.owner_id),
                "assets": lambda: AssetsView(self.owner_id),
            }
            if self.action in local:
                await replace_panel(interaction, local[self.action])
                return
            if self.action == "lobby":
                builder = getattr(bot, "xbot_player_lobby_builder", None)
            elif self.action == "legacy_economy":
                builder = legacy_economy_builder
            else:
                builder = getattr(bot, "xbot_player_panel_builders", {}).get(self.action)
            if builder is None:
                await interaction.response.send_message("That Economy page is still loading. Try again shortly.", ephemeral=True)
                return
            await replace_panel(interaction, lambda: builder(self.owner_id))

    def economy_navigation(owner_id: int, active: str):
        entries = (
            ("economy_v2", "Overview", "💰"),
            ("earn", "Earn", "🛠️"),
            ("trade", "Trade", "🏪"),
            ("production", "Production", "🏭"),
            ("stock", "Stocks", "📈"),
        )
        return discord.ui.ActionRow(*(
            EconomyNavButton(owner_id, action, label, emoji, style=discord.ButtonStyle.primary if action == active else discord.ButtonStyle.secondary)
            for action, label, emoji in entries
        ))

    class OwnedEconomyView(discord.ui.LayoutView):
        def __init__(self, owner_id: int):
            super().__init__(timeout=300)
            self.owner_id = owner_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message("Open `/economy` for your own Economy Centre.", ephemeral=True)
            return False

    class EconomyV2View(OwnedEconomyView):
        def __init__(self, owner_id: int, notice: str = ""):
            super().__init__(owner_id)
            if not setting(db, "tier6_economy_enabled"):
                container = discord.ui.Container(accent_color=discord.Color.orange())
                container.add_item(discord.ui.TextDisplay(
                    "## 💰 Economy Centre Temporarily Closed\n"
                    "Economy maintenance is in progress. Daily rewards and your existing Wallet remain available."
                ))
                container.add_item(discord.ui.ActionRow(
                    EconomyNavButton(owner_id, "legacy_economy", "Wallet & Daily", "🎁", style=discord.ButtonStyle.primary),
                    EconomyNavButton(owner_id, "lobby", "Lobby", "✨"),
                ))
                self.add_item(container)
                return
            data = economy_summary(db, owner_id)
            player = data["player"]
            container = discord.ui.Container(accent_color=discord.Color.gold())
            container.add_item(discord.ui.TextDisplay(
                f"## 💰 X BOT Economy Centre · V3.0\n"
                f"🪙 Wallet **{player['xc']:,} XC** · 🏦 Bank **{player['bank_xc']:,} XC**\n"
                f"⚔️ **{player['money']:,} War Credits** · 💎 **{player['xcrystals']:,} XCrystals**\n"
                f"📈 Stocks **{data['stock_value']:,} XC** · 🎒 Items **{data['inventory_value']:,} XC**\n"
                f"💼 Estimated net worth **{data['net_worth']:,} XC**"
            ))
            container.add_item(economy_navigation(owner_id, "economy_v2"))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            alerts = []
            if data["ready_contracts"]:
                alerts.append(f"🎁 **{data['ready_contracts']} Contract reward(s)** ready")
            if data["ready_production"]:
                alerts.append(f"📦 **{data['ready_production']} Production job(s)** ready")
            if not alerts:
                alerts.append("✅ No rewards are waiting. Open Earn to continue building your economy.")
            container.add_item(discord.ui.TextDisplay(
                "### Ready now\n" + "\n".join(alerts) +
                f"\n\n-# {data['activity']} Economy action(s) recorded in the last 24 hours."
            ))
            container.add_item(discord.ui.ActionRow(
                EconomyNavButton(owner_id, "assets", "My Assets", "🎒", style=discord.ButtonStyle.success),
                EconomyNavButton(owner_id, "legacy_economy", "Wallet & Daily", "🎁", style=discord.ButtonStyle.primary),
                EconomyNavButton(owner_id, "research", "Research", "🔬"),
                EconomyNavButton(owner_id, "lobby", "Lobby", "✨"),
            ))
            self.add_item(container)

    class ContractClaimButton(discord.ui.Button):
        def __init__(self, owner_id: int, enabled: bool):
            super().__init__(label="Claim All Ready", emoji="🎁", style=discord.ButtonStyle.success, disabled=not enabled)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            notice = claim_contracts(db, self.owner_id)
            await interaction.response.edit_message(view=ContractView(self.owner_id, notice))

    class ContractView(OwnedEconomyView):
        def __init__(self, owner_id: int, notice: str = ""):
            super().__init__(owner_id)
            rows = contract_rows(db, owner_id) if setting(db, "tier6_economy_enabled") and setting(db, "tier6_contracts_enabled") else []
            container = discord.ui.Container(accent_color=discord.Color.green())
            ready_count=sum(not row['claimed'] and row['progress']>=row['target'] for row in rows)
            container.add_item(discord.ui.TextDisplay(f"## 🛠️ Contracts\n### Ready to Claim\n## {ready_count} rewards\nProgress updates as you play."))
            container.add_item(economy_navigation(owner_id, "earn"))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            if rows:
                lines = []
                for row in rows:
                    state = "🎁 Claimed" if row["claimed"] else "✅ Ready" if row["progress"] >= row["target"] else "⬜ In progress"
                    lines.append(
                        f"### {row['emoji']} {row['title']}\nProgress **{row['progress']}/{row['target']}**\n"
                        f"{row['description']}\n{state} · **{row['reward_xc']} XC + {row['reward_war_credits']} WC**"
                    )
                container.add_item(discord.ui.TextDisplay("\n\n".join(lines)))
                ready = any(not row["claimed"] and row["progress"] >= row["target"] for row in rows)
                container.add_item(discord.ui.ActionRow(
                    ContractClaimButton(owner_id, ready),
                    EconomyNavButton(owner_id, "mining", "Mining", "⛏️", style=discord.ButtonStyle.primary),
                    EconomyNavButton(owner_id, "city", "City", "🏙️"),
                    EconomyNavButton(owner_id, "missions", "Missions", "🎯"),
                    EconomyNavButton(owner_id, "earn_menu", "Workshop", "🛠️"),
                ))
            else:
                container.add_item(discord.ui.TextDisplay("Contracts are currently closed or no Contracts are available at your Nation Level."))
            self.add_item(container)

    class TradeHubView(OwnedEconomyView):
        def __init__(self, owner_id: int):
            super().__init__(owner_id)
            active = int(db.execute("SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1", (owner_id,)).fetchone()[0])
            recent = int(db.execute("SELECT COUNT(*) FROM tier6_stock_trades WHERE user_id=? AND created_at>=?", (owner_id, int(time.time()) - 86400)).fetchone()[0])
            container = discord.ui.Container(accent_color=discord.Color.teal())
            container.add_item(discord.ui.TextDisplay(
                f"## 🏪 Trade Centre\n"
                f"🏷️ Active player listings: **{active}**\n📈 Stock trades in 24 hours: **{recent}**\n"
                "Buy, sell, or manage assets without leaving the main Economy message."
            ))
            container.add_item(economy_navigation(owner_id, "trade"))
            container.add_item(discord.ui.ActionRow(
                EconomyNavButton(owner_id, "shop", "System Shop", "🏪", style=discord.ButtonStyle.primary),
                EconomyNavButton(owner_id, "market", "Player Market", "🏷️", style=discord.ButtonStyle.success),
                EconomyNavButton(owner_id, "inventory", "Backpack", "🎒"),
                EconomyNavButton(owner_id, "stock", "Stock Market", "📈", style=discord.ButtonStyle.primary),
            ))
            self.add_item(container)

    class StockCompanySelect(discord.ui.Select):
        def __init__(self, owner_id: int, companies):
            options = []
            for row in companies[:25]:
                change = int(row["price"]) - int(row["previous_price"])
                options.append(discord.SelectOption(
                    label=f"{row['symbol']} · {row['name']}"[:100], value=str(row["id"]), emoji=row["emoji"],
                    description=f"{row['price']:,} XC · {'+' if change >= 0 else ''}{change:,} · {row['industry']}"[:100],
                ))
            super().__init__(placeholder="Choose a company", options=options, disabled=not options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message('Open your own Stocks panel.', ephemeral=True); return
            await interaction.response.edit_message(view=StockCompanyView(self.owner_id, int(self.values[0]), market_page=self.view.page))

    class StockTradeButton(discord.ui.Button):
        def __init__(self,owner_id,company_id,side):
            super().__init__(label=side.title(),style=discord.ButtonStyle.primary)
            self.owner_id,self.company_id,self.side=owner_id,company_id,side
        async def callback(self,interaction):
            if interaction.user.id!=self.owner_id:
                await interaction.response.send_message('Open your own Stocks panel.',ephemeral=True);return
            from economy_trade_ui import TradeView
            page=self.view.market_page
            await interaction.response.edit_message(view=TradeView(bot,db,self.owner_id,'stock_'+self.side,self.company_id,back=lambda:StockCompanyView(self.owner_id,self.company_id,market_page=page)))

    class StockSellAllButton(discord.ui.Button):
        def __init__(self,owner_id,company_id,quantity):
            super().__init__(label='Review Sell All',style=discord.ButtonStyle.secondary,disabled=quantity<=0)
            self.owner_id,self.company_id,self.quantity=owner_id,company_id,quantity
        async def callback(self,interaction):
            if interaction.user.id!=self.owner_id:
                await interaction.response.send_message('Open your own Stocks panel.',ephemeral=True);return
            from economy_trade_ui import TradeView
            page=self.view.market_page
            await interaction.response.edit_message(view=TradeView(bot,db,self.owner_id,'stock_sell',self.company_id,max(1,self.quantity),back=lambda:StockCompanyView(self.owner_id,self.company_id,market_page=page)))

    class StockCompanyView(OwnedEconomyView):
        def __init__(self, owner_id: int, company_id: int, notice: str = "", market_page=0):
            super().__init__(owner_id)
            self.market_page=market_page
            update_stock_prices(db)
            company = db.execute("SELECT * FROM tier6_stock_companies WHERE id=?", (company_id,)).fetchone()
            holding = db.execute("SELECT * FROM tier6_stock_holdings WHERE user_id=? AND company_id=?", (owner_id, company_id)).fetchone()
            change = int(company["price"]) - int(company["previous_price"])
            percent = change * 100 / max(1, int(company["previous_price"]))
            owned = int(holding["quantity"]) if holding else 0
            average = int(holding["average_cost"]) if holding else 0
            profit = owned * (int(company["price"]) - average)
            container = discord.ui.Container(accent_color=discord.Color.green() if change >= 0 else discord.Color.red())
            container.add_item(discord.ui.TextDisplay(
                f"## {company['emoji']} {company['symbol']} · {company['name']}\n"
                f"{company['industry']}\n"
                f"### 💹 Share Price\n## {company['price']:,} XC\nChange **{change:+,} ({percent:+.1f}%)**\n"
                f"### 💼 Your Holding\nShares **{owned:,}**\nAverage cost **{average:,} XC**\nUnrealised P/L **{profit:+,} XC**\n"
                f"### 📊 Availability\n**{company['available_shares']:,}** shares available\nGame virtual stocks · Fictional XC, not real investments."
            ))
            if notice:
                container.add_item(discord.ui.TextDisplay(notice))
            container.add_item(discord.ui.ActionRow(
                StockTradeButton(owner_id, company_id, "buy"),
                StockTradeButton(owner_id, company_id, "sell"),
                StockSellAllButton(owner_id, company_id, owned),
                EconomyListPage(owner_id, 'stock', market_page, 'All Companies'),
                EconomyNavButton(owner_id, "assets", "Portfolio", "💼"),
            ))
            container.add_item(discord.ui.ActionRow(EconomyNavButton(owner_id, "economy_v2", "Economy", "💰")))
            self.add_item(container)

    class StockMarketView(OwnedEconomyView):
        def __init__(self, owner_id: int, page=0):
            super().__init__(owner_id)
            update_stock_prices(db)
            count=db.execute('SELECT COUNT(*) FROM tier6_stock_companies WHERE enabled=1').fetchone()[0]
            pages=max(1,(count+24)//25);self.page=min(max(0,page),pages-1)
            companies = db.execute("SELECT * FROM tier6_stock_companies WHERE enabled=1 ORDER BY symbol LIMIT 25 OFFSET ?",(self.page*25,)).fetchall()
            summary=economy_summary(db,owner_id)
            container = discord.ui.Container(accent_color=discord.Color.blue())
            container.add_item(discord.ui.TextDisplay(
                f"## 📈 Stock Market\nOVERVIEW\n"
                f"### 💰 Wallet\n## {summary['player']['xc']:,} XC\n"
                f"### 💼 Portfolio\nValue **{summary['stock_value']:,} XC**\nUnrealised P/L **{summary['stock_value']-summary['stock_cost']:+,} XC**\n"
                "Game-only companies · Fictional XC, not real investments."
            ))
            container.add_item(economy_navigation(owner_id, "stock"))
            if setting(db, "tier6_economy_enabled") and setting(db, "tier6_stock_enabled") and companies:
                container.add_item(discord.ui.TextDisplay(f"### Companies\n{count} available · Page {self.page+1}/{pages} · Select to inspect."))
                container.add_item(discord.ui.ActionRow(StockCompanySelect(owner_id, companies)))
                if pages>1:
                    container.add_item(discord.ui.ActionRow(EconomyListPage(owner_id,'stock',self.page-1,'Previous',self.page==0),EconomyListPage(owner_id,'stock',self.page+1,'Next',self.page==pages-1)))
                container.add_item(discord.ui.ActionRow(
                    EconomyNavButton(owner_id, "assets", "My Portfolio", "💼", style=discord.ButtonStyle.success),
                    EconomyNavButton(owner_id, "economy_v2", "Economy", "💰"),
                ))
            else:
                container.add_item(discord.ui.TextDisplay("No companies available. Return to Economy or try refreshing later." if not companies else "The Stock Market is currently closed. You can still visit your Backpack."))
            self.add_item(container)

    class EconomyListPage(discord.ui.Button):
        def __init__(self,owner,kind,page,label,disabled=False,recipe_page=0,selected=None):
            super().__init__(label=label,disabled=disabled)
            self.owner,self.kind,self.page,self.recipe_page,self.selected=owner,kind,page,recipe_page,selected
        async def callback(self,i):
            if i.user.id!=self.owner:
                await i.response.send_message('Open your own Economy panel.',ephemeral=True);return
            view=StockMarketView(self.owner,self.page) if self.kind=='stock' else ProductionView(self.owner,page=self.page,recipe_page=self.recipe_page,selected=self.selected)
            await i.response.edit_message(view=view)

    class ProductionRecipeSelect(discord.ui.Select):
        def __init__(self, owner_id: int, recipes, selected=None):
            options = [discord.SelectOption(
                label=row["name"][:100], value=str(row["id"]), emoji=row["emoji"],
                description=f"Output {row['output_quantity']}x {row['output_name']} · {row['xc_cost']} XC each"[:100], default=row['id']==selected,
            ) for row in recipes[:25]]
            super().__init__(placeholder="Choose a Recipe to queue", options=options, disabled=not options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id!=self.owner_id:
                await interaction.response.send_message('Open your own Production panel.',ephemeral=True);return
            from economy_trade_ui import TradeView
            rid=int(self.values[0]);page=self.view.page;recipe_page=self.view.recipe_page
            await interaction.response.edit_message(view=TradeView(bot,db,self.owner_id,'queue',rid,back=lambda:ProductionView(self.owner_id,page=page,recipe_page=recipe_page,selected=rid)))



    class ProductionClaimButton(discord.ui.Button):
        def __init__(self, owner_id: int, ready: bool):
            super().__init__(label="Collect All Ready", emoji="📦", style=discord.ButtonStyle.success, disabled=not ready)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message('Open your own Production panel.',ephemeral=True);return
            await interaction.response.defer()
            page,recipe_page,selected=self.view.page,self.view.recipe_page,self.view.selected
            notice,items = claim_production(db, self.owner_id,return_items=True)
            back=lambda:ProductionView(self.owner_id,page=page,recipe_page=recipe_page,selected=selected)
            result=economy_journey.JourneyView(bot,db,self.owner_id,page='products',product_ids=items,notice=notice,back=back) if items else ProductionView(self.owner_id,notice,page,recipe_page,selected)
            await interaction.edit_original_response(view=result)

    class ProductionCancelSelect(discord.ui.Select):
        def __init__(self, owner_id: int, jobs):
            options = [discord.SelectOption(
                label=f"Cancel #{row['id']} · {row['name']} ×{row['quantity']}"[:100],
                value=str(row["id"]),
                emoji="↩️",
                description="Preview returned materials and XC before cancelling"[:100],
            ) for row in jobs[:25]]
            super().__init__(placeholder="Cancel an unfinished Production job", options=options)
            self.owner_id = owner_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id!=self.owner_id:
                await interaction.response.send_message('Open your own Production panel.',ephemeral=True);return
            from economy_trade_ui import TradeView
            page=self.view.page;recipe_page=self.view.recipe_page;selected=self.view.selected
            await interaction.response.edit_message(view=TradeView(bot,db,self.owner_id,'production_cancel',int(self.values[0]),back=lambda:ProductionView(self.owner_id,page=page,recipe_page=recipe_page,selected=selected)))

    class ProductionView(OwnedEconomyView):
        def __init__(self, owner_id: int, notice: str = "", page=0, recipe_page=0, selected=None):
            super().__init__(owner_id)
            now = int(time.time())
            totals=db.execute("SELECT COUNT(*) total,COALESCE(SUM(ready_at<=?),0) ready FROM tier6_production_queue WHERE user_id=? AND status='working'",(now,owner_id)).fetchone()
            recipe_count=db.execute('SELECT COUNT(*) FROM recipes r JOIN items i ON i.id=r.output_item_id WHERE r.enabled=1').fetchone()[0]
            pages=max(1,(totals['total']+9)//10);recipe_pages=max(1,(recipe_count+24)//25)
            self.page=min(max(0,page),pages-1);self.recipe_page=min(max(0,recipe_page),recipe_pages-1);self.selected=selected
            recipes = db.execute(
                """SELECT r.*,i.name output_name FROM recipes r JOIN items i ON i.id=r.output_item_id
                   WHERE r.enabled=1 ORDER BY r.name LIMIT 25 OFFSET ?""",(self.recipe_page*25,)
            ).fetchall()
            queue = db.execute(
                """SELECT q.*,COALESCE(r.name,'Archived Recipe') name,COALESCE(r.emoji,'🏭') emoji
                   FROM tier6_production_queue q LEFT JOIN recipes r ON r.id=q.recipe_id
                   WHERE q.user_id=? AND q.status='working' ORDER BY q.ready_at,q.id LIMIT 10 OFFSET ?""", (owner_id,self.page*10)
            ).fetchall()
            container = discord.ui.Container(accent_color=discord.Color.orange())
            container.add_item(discord.ui.TextDisplay(f"## 🏭 Production Centre\n### Ready to Collect\n## {totals['ready']} jobs\nActive queue **{totals['total']}/{svip.production_limit(db,owner_id)}** · Collect finished products below.\n"+svip.summary(db,owner_id)))
            container.add_item(economy_navigation(owner_id, "production"))
            if notice:
                container.add_item(discord.ui.TextDisplay(f"-# {notice}"))
            if queue:
                queue_lines = []
                for row in queue:
                    state = "✅ Ready" if int(row["ready_at"]) <= now else f"<t:{row['ready_at']}:R>"
                    queue_lines.append(f"{row['emoji']} **{row['name'][:100]} ×{row['quantity']}** · {state}")
                container.add_item(discord.ui.TextDisplay(f"### Queue · Page {self.page+1}/{pages}\n" + "\n\n".join(queue_lines)))
            else:
                container.add_item(discord.ui.TextDisplay("### Queue\nNo active Production jobs."))
            if recipes and setting(db, "tier6_production_enabled"):
                container.add_item(discord.ui.ActionRow(ProductionRecipeSelect(owner_id, recipes, selected)))
            elif not setting(db,'tier6_production_enabled'):
                container.add_item(discord.ui.TextDisplay('New Production jobs are closed. Existing jobs can still be collected.'))
            else:
                container.add_item(discord.ui.TextDisplay('No enabled recipes. Try Mines or return to Economy.'))
            paging=[]
            if pages>1:
                paging.extend([EconomyListPage(owner_id,'production',self.page-1,'Previous Jobs',self.page==0,self.recipe_page,selected),EconomyListPage(owner_id,'production',self.page+1,'Next Jobs',self.page==pages-1,self.recipe_page,selected)])
            if recipe_pages>1:
                paging.extend([EconomyListPage(owner_id,'production',self.page,'Previous Recipes',self.recipe_page==0,self.recipe_page-1,selected),EconomyListPage(owner_id,'production',self.page,'Next Recipes',self.recipe_page==recipe_pages-1,self.recipe_page+1,selected)])
            if paging:container.add_item(discord.ui.ActionRow(*paging))
            cancellable = [row for row in queue if int(row["ready_at"]) > now]
            if cancellable:
                container.add_item(discord.ui.ActionRow(ProductionCancelSelect(owner_id, cancellable)))
            ready = totals['ready']>0
            container.add_item(discord.ui.ActionRow(
                ProductionClaimButton(owner_id, ready),
                economy_journey.Entry(bot,db,owner_id,'View Products',page='products'),
                EconomyNavButton(owner_id, "craft", "Instant Craft", "🧪", style=discord.ButtonStyle.primary),
                EconomyNavButton(owner_id, "economy_v2", "Economy", "💰"),
            ))
            self.add_item(container)

    class AssetsView(OwnedEconomyView):
        def __init__(self, owner_id: int):
            super().__init__(owner_id)
            data = economy_summary(db, owner_id)
            player = data["player"]
            lines = []
            for row in data["holdings"][:12]:
                value = int(row["quantity"]) * int(row["price"])
                profit = int(row["quantity"]) * (int(row["price"]) - int(row["average_cost"]))
                lines.append(f"{row['emoji']} **{row['symbol']}** ×{row['quantity']:,} · {value:,} XC · P/L {profit:+,}")
            container = discord.ui.Container(accent_color=discord.Color.purple())
            container.add_item(discord.ui.TextDisplay(
                f"## 💼 My Economy Assets\n"
                f"🪙 Wallet **{player['xc']:,}** · 🏦 Bank **{player['bank_xc']:,} XC**\n"
                f"🎒 Sell-back item value **{data['inventory_value']:,} XC**\n"
                f"📈 Stock value **{data['stock_value']:,} XC** · Cost **{data['stock_cost']:,} XC**\n"
                f"💰 Estimated net worth **{data['net_worth']:,} XC**\n\n"
                f"### Portfolio\n" + ("\n".join(lines) if lines else "No Stock holdings yet.")
            ))
            container.add_item(discord.ui.ActionRow(
                EconomyNavButton(owner_id, "inventory", "Backpack", "🎒"),
                EconomyNavButton(owner_id, "stock", "Stock Market", "📈", style=discord.ButtonStyle.primary),
                EconomyNavButton(owner_id, "legacy_economy", "Wallet & Bank", "🏦"),
                EconomyNavButton(owner_id, "economy_v2", "Economy", "💰"),
            ))
            self.add_item(container)

    bot.xbot_player_panel_builders = getattr(bot, "xbot_player_panel_builders", {})
    bot.xbot_player_panel_builders.update({
        "economy": lambda owner_id: EconomyV2View(owner_id),
        "contracts": lambda owner_id: ContractView(owner_id),
        "production": lambda owner_id: ProductionView(owner_id),
        "stock": lambda owner_id: StockMarketView(owner_id),
        "assets": lambda owner_id: AssetsView(owner_id),
    })
    bot.xbot_tier6_economy_builder = lambda owner_id, notice="": EconomyV2View(owner_id, notice)
    bot.xbot_tier6_health = lambda: health_report(db)
    bot.xbot_tier6_repair = lambda: repair(db)

    @bot.tree.command(name="stock", description="Open the X BOT virtual Stock Market")
    async def stock(interaction: discord.Interaction):
        await interaction.response.defer()
        create_player(interaction.user)
        await interaction.edit_original_response(view=StockMarketView(interaction.user.id))
