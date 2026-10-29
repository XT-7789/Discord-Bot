import sqlite3
import sys

def migrate(db_path="xwar.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1. Disable legacy / unused items from shop
    c.execute("""
        UPDATE items
        SET enabled = 0, shop_visible = 0
        WHERE name NOT IN (
            'Color Role Voucher',
            'Custom Tag Voucher',
            'XP Boost (2h)',
            'VIP Lounge Pass',
            'Server Megaphone',
            'Cash Red Packet'
        )
    """)

    # If old 'Custom Tag' exists, rename to avoid collision or update it
    c.execute("UPDATE items SET name = 'Legacy Custom Tag', enabled = 0, shop_visible = 0 WHERE name = 'Custom Tag'")

    # 2. Seed / Update the 6 new core social/utility perk items
    new_items = [
        {
            "name": "Color Role Voucher",
            "description": "Redeem to pick your own custom Discord name color from color presets or HEX code.",
            "category": "Perks",
            "category_id": 6824,
            "price": 25000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "color_role",
            "effect_value": 1,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        },
        {
            "name": "Custom Tag Voucher",
            "description": "Redeem to set a personalized custom tag / title next to your profile and badge.",
            "category": "Perks",
            "category_id": 6824,
            "price": 100000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "custom_tag",
            "effect_value": 1,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        },
        {
            "name": "XP Boost (2h)",
            "description": "Grants 2 hours of 1.5x XP bonus for chatting and participating in server activities.",
            "category": "Perks",
            "category_id": 6824,
            "price": 10000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "xp_boost_2h",
            "effect_value": 120,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        },
        {
            "name": "VIP Lounge Pass",
            "description": "Create a private VIP Suite lounge with custom limits, bitrate, and management tools.",
            "category": "Perks",
            "category_id": 6824,
            "price": 50000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "vip_lounge",
            "effect_value": 1,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        },
        {
            "name": "Server Megaphone",
            "description": "Broadcast a highlighted announcement message across the server in #general.",
            "category": "Social",
            "category_id": 6824,
            "price": 30000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "server_megaphone",
            "effect_value": 1,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        },
        {
            "name": "Cash Red Packet",
            "description": "Drop a lucky red packet in chat for other members to grab and share cash.",
            "category": "Social",
            "category_id": 6824,
            "price": 5000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "red_packet",
            "effect_value": 5000,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "emoji": ""
        }
    ]

    for item in new_items:
        c.execute("SELECT id FROM items WHERE name = ?", (item["name"],))
        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE items
                SET description = ?, category = ?, category_id = ?, price = ?, currency = ?,
                    sell_price = ?, stock = ?, effect = ?, effect_value = ?, enabled = ?,
                    shop_visible = ?, sellable = ?, tradeable = ?, emoji = ?
                WHERE id = ?
            """, (
                item["description"], item["category"], item["category_id"], item["price"], item["currency"],
                item["sell_price"], item["stock"], item["effect"], item["effect_value"], item["enabled"],
                item["shop_visible"], item["sellable"], item["tradeable"], item["emoji"], row[0]
            ))
        else:
            c.execute("""
                INSERT INTO items (
                    name, description, category, category_id, price, currency,
                    sell_price, stock, effect, effect_value, enabled, shop_visible,
                    sellable, tradeable, emoji
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["name"], item["description"], item["category"], item["category_id"], item["price"], item["currency"],
                item["sell_price"], item["stock"], item["effect"], item["effect_value"], item["enabled"], item["shop_visible"],
                item["sellable"], item["tradeable"], item["emoji"]
            ))

    conn.commit()
    conn.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "xwar.db"
    migrate(db_file)
