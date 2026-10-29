import sqlite3
import sys

def migrate(db_path="xwar.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Create table for premium music subscriptions
    c.execute("""
        CREATE TABLE IF NOT EXISTS premium_music_members (
            user_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            activated_at INTEGER NOT NULL
        )
    """)

    # Clean old name collisions safely
    c.execute("DELETE FROM items WHERE name = 'Custom Tag'")
    c.execute("DELETE FROM items WHERE name = 'Legacy Custom Tag'")

    # Update existing item names to new standardized names
    c.execute("UPDATE items SET name = 'Xustom Tag Voucher' WHERE name = 'Custom Tag Voucher'")
    c.execute("UPDATE items SET name = 'XP Boost (1h)', price = 5000, effect = 'xp_boost_1h', effect_value = 60, description = 'Grants 1 hour of 1.5x XP bonus for chatting and participating in server activities.' WHERE name = 'XP Boost (2h)'")
    c.execute("UPDATE items SET name = 'Premium Music Pass (7 Days)' WHERE name = 'Premium Music Pass'")

    # 1. Disable all items not in the approved 5 items list
    active_names = (
        'Xustom Tag Voucher',
        'XP Boost (1h)',
        'VIP Lounge Pass',
        'Cash Red Packet',
        'Premium Music Pass (7 Days)'
    )
    c.execute(f"""
        UPDATE items
        SET enabled = 0, shop_visible = 0
        WHERE name NOT IN ({','.join('?' for _ in active_names)})
    """, active_names)

    # 2. Seed / Update the 5 approved items
    new_items = [
        {
            "name": "Xustom Tag Voucher",
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
            "aliases": "Custom Tag Voucher,custom_tag,xustom_tag",
            "emoji": ""
        },
        {
            "name": "XP Boost (1h)",
            "description": "Grants 1 hour of 1.5x XP bonus for chatting and participating in server activities.",
            "category": "Perks",
            "category_id": 6824,
            "price": 5000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "xp_boost_1h",
            "effect_value": 60,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "aliases": "xp_boost,xp_boost_1h",
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
            "aliases": "vip_lounge,lounge_pass",
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
            "aliases": "red_packet,packet",
            "emoji": ""
        },
        {
            "name": "Premium Music Pass (7 Days)",
            "description": "Unlock 7 days of exclusive access to the dedicated 2nd music bot line with no queue conflicts.",
            "category": "Perks",
            "category_id": 6824,
            "price": 30000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "premium_music",
            "effect_value": 604800,  # 7 days in seconds
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "aliases": "primunem music,premium music,music_pass,primunem,premium music pass",
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
                    shop_visible = ?, sellable = ?, tradeable = ?, aliases = ?, emoji = ?
                WHERE id = ?
            """, (
                item["description"], item["category"], item["category_id"], item["price"], item["currency"],
                item["sell_price"], item["stock"], item["effect"], item["effect_value"], item["enabled"],
                item["shop_visible"], item["sellable"], item["tradeable"], item["aliases"], item["emoji"], row[0]
            ))
        else:
            c.execute("""
                INSERT INTO items (
                    name, description, category, category_id, price, currency,
                    sell_price, stock, effect, effect_value, enabled, shop_visible,
                    sellable, tradeable, aliases, emoji
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["name"], item["description"], item["category"], item["category_id"], item["price"], item["currency"],
                item["sell_price"], item["stock"], item["effect"], item["effect_value"], item["enabled"], item["shop_visible"],
                item["sellable"], item["tradeable"], item["aliases"], item["emoji"]
            ))

    conn.commit()
    conn.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "xwar.db"
    migrate(db_file)
