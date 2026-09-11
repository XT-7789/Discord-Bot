"""Read-only growth targets and current-to-next equipment comparisons."""
import discord

STATS=(('pickaxe_power','Power'),('pickaxe_luck','Luck %'),('pickaxe_yield_bonus','Yield bonus %'),('pickaxe_cooldown_reduction','Cooldown reduction %'))

def tool_comparison(db,uid,item):
    if item['effect']!='mine_tool':return ''
    p=db.execute('SELECT equipped_pickaxe_id,mining_level FROM players WHERE user_id=?',(uid,)).fetchone()
    current=db.execute('SELECT * FROM items WHERE id=?',(p['equipped_pickaxe_id'],)).fetchone() if p else None
    lines=[f"{label}: **{current[key] if current else (1 if key=='pickaxe_power' else 0)} → {item[key]}**" for key,label in STATS]
    lines.append(f"Mining Level **{p['mining_level'] if p else 1}** · Required **{item['pickaxe_required_level']}**")
    return '### Equipped → Selected\n'+'\n'.join(lines)

def growth(db,uid):
    p=db.execute('SELECT xc,mining_level,equipped_pickaxe_id FROM players WHERE user_id=?',(uid,)).fetchone()
    if not p:return 'Open /menu to start your progression.',None
    current=db.execute('SELECT * FROM items WHERE id=?',(p['equipped_pickaxe_id'],)).fetchone()
    rows=db.execute("""SELECT i.*,COALESCE(v.quantity,0) owned FROM items i LEFT JOIN inventories v ON v.item_id=i.id AND v.user_id=?
        WHERE i.enabled=1 AND i.effect='mine_tool' AND i.currency='xc' AND i.price>=0
        AND (COALESCE(v.quantity,0)>0 OR (i.shop_visible=1 AND i.stock<>0)) ORDER BY i.price,i.id""",(uid,)).fetchall()
    # Recommend only an improvement with no lower displayed equipment stats.
    item=next((r for r in rows if r['id']!=p['equipped_pickaxe_id'] and
        all(r[k]>=(current[k] if current else (1 if k=='pickaxe_power' else 0)) for k,_ in STATS) and
        any(r[k]>(current[k] if current else (1 if k=='pickaxe_power' else 0)) for k,_ in STATS)),None)
    if not item:return 'No further shop tool upgrade available. Explore Research for your next improvement.',None
    cost=0 if item['owned'] else item['price'];gap=max(0,cost-p['xc'])
    name=discord.utils.escape_markdown(item['name'])
    status='Already owned · Open Backpack to equip' if item['owned'] else f"Wallet **{p['xc']:,}/{cost:,} XC** · "+(f'Need **{gap:,} XC** more' if gap else 'Affordable now · Review before buying')
    level=f"\nMining Level **{p['mining_level']}/{item['pickaxe_required_level']}**" if p['mining_level']<item['pickaxe_required_level'] else ''
    return f"Next tool: **{name}**\n{status}{level}",dict(item)
