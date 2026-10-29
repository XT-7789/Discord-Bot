import sqlite3
import sys

def migrate(db_path="xwar.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Clean old name collisions safely
    c.execute("DELETE FROM items WHERE name = 'Custom Tag'")
    c.execute("DELETE FROM items WHERE name = 'Legacy Custom Tag'")

    # Update existing items if needed
    c.execute("UPDATE items SET name = 'Xustom Tag Voucher' WHERE name = 'Custom Tag Voucher'")
    c.execute("UPDATE items SET name = 'XP Boost (1h)', price = 5000, effect = 'xp_boost_1h', effect_value = 60, description = 'Grants 1 hour of 1.5x XP bonus for chatting and participating in server activities.' WHERE name = 'XP Boost (2h)'")

    # 1. Disable all items not in the approved 5 items list
    active_names = (
        'Xustom Tag Voucher',
        'XP Boost (1h)',
        'VIP Lounge Pass',
        'Cash Red Packet',
        'Premium Music Pass'
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
            "name": "Premium Music Pass",
            "description": "Grants the exclusive Premium Music role with dedicated 24/7 high-quality bot service.",
            "category": "Perks",
            "category_id": 6824,
            "price": 50000,
            "currency": "cash",
            "sell_price": 0,
            "stock": -1,
            "effect": "premium_music",
            "effect_value": 1526237128093339848,
            "enabled": 1,
            "shop_visible": 1,
            "sellable": 0,
            "tradeable": 0,
            "aliases": "primunem music,premium music,music_pass,primunem",
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
