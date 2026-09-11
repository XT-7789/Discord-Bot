"""Connected Economy workflow; existing inventory, recipes and audit tables only."""
import functools
import json
import secrets
import sqlite3
import time
from decimal import Decimal, ROUND_HALF_UP

import discord


def initialise(db):
    materials = [db.execute('SELECT * FROM items WHERE name=? COLLATE NOCASE', (name,)).fetchone() for name in ('Stone', 'Coal')]
    if any(r is None for r in materials):
        return
    value = sum(int(r['sell_price']) * n for r, n in zip(materials, (4, 2)))
    price = 2 + int((Decimal(value) * Decimal('1.20')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    db.execute("""INSERT OR IGNORE INTO items(name,description,emoji,category,price,currency,sell_price,
        effect,enabled,sellable,tradeable,shop_visible,stock) VALUES
        ('Resource Pack','Craft from materials, then sell back for XC.','📦','Materials & Resources',0,'xc',?,'none',1,1,0,0,0)""", (price,))
    item = db.execute("SELECT id FROM items WHERE name='Resource Pack' COLLATE NOCASE").fetchone()
    added = db.execute("""INSERT OR IGNORE INTO recipes(name,emoji,description,output_item_id,output_quantity,xc_cost,enabled)
        VALUES('Resource Pack','📦','A civilian material pack for resale.',?,1,2,1)""", (item['id'],)).rowcount
    if added:
        rid = db.execute("SELECT id FROM recipes WHERE name='Resource Pack' COLLATE NOCASE").fetchone()[0]
        db.executemany('INSERT INTO recipe_ingredients VALUES(?,?,?)', [(rid, r['id'], n) for r, n in zip(materials, (4, 2))])
    # Caller owns initialization commit. Never recalculate existing prices/ingredients.


def enabled(db, key):
    row = db.execute('SELECT value FROM economy_settings WHERE key=?', (key,)).fetchone()
    return row is None or row[0] == '1'


def audit(db, uid, action, detail):
    db.execute('INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)', (uid, action, detail, int(time.time())))


def atomic_action(fn):
    """Serializes quote/recheck/write; optional durable single-use UI receipt."""
    @functools.wraps(fn)
    def wrapped(db, uid, *args, token=None, **kwargs):
        nested = db.in_transaction
        db.execute('SAVEPOINT journey_action' if nested else 'BEGIN IMMEDIATE')
        try:
            if token and db.execute("SELECT 1 FROM economy_logs WHERE user_id=? AND action='journey_receipt' AND detail=?", (uid, token)).fetchone():
                raise ValueError('Already processed. Check your inventory or queue before trying again.')
            result = fn(db, uid, *args, **kwargs)
            if token:
                audit(db, uid, 'journey_receipt', token)
            if nested: db.execute('RELEASE SAVEPOINT journey_action')
            else: db.commit()
            return result
        except Exception:
            if nested:
                db.execute('ROLLBACK TO SAVEPOINT journey_action')
                db.execute('RELEASE SAVEPOINT journey_action')
            else: db.rollback()
            raise
    return wrapped


def quote(db, uid, rid, amount=1):
    if not isinstance(amount, int) or not 1 <= amount <= 1000:
        raise ValueError('Choose 1 to 1,000 batches.')
    r = db.execute('''SELECT r.*,i.name output_name,i.emoji output_emoji,i.sell_price,i.sellable,
        i.currency,i.enabled output_enabled,i.effect,i.effect_value FROM recipes r
        JOIN items i ON i.id=r.output_item_id WHERE r.id=?''', (rid,)).fetchone()
    if not r:
        raise ValueError('Recipe is no longer available.')
    ingredients = [dict(x) for x in db.execute('''SELECT g.*,i.name,i.emoji,i.sell_price,i.sellable,i.currency,
        COALESCE(v.quantity,0) owned FROM recipe_ingredients g JOIN items i ON i.id=g.item_id
        LEFT JOIN inventories v ON v.item_id=i.id AND v.user_id=? WHERE g.recipe_id=? ORDER BY i.name''', (uid, rid))]
    if r['xc_cost'] < 0 or r['output_quantity'] < 1 or any(i['quantity'] < 1 for i in ingredients):
        raise ValueError('Recipe configuration is invalid. Please contact staff.')
    player = db.execute('SELECT xc FROM players WHERE user_id=?', (uid,)).fetchone()
    comparable = all(i['sellable'] and i['currency']=='xc' and i['sell_price']>0 for i in ingredients)
    material_value = sum(i['quantity'] * amount * i['sell_price'] for i in ingredients) if comparable else None
    output_value = r['sell_price'] * r['output_quantity'] * amount if r['sellable'] and r['currency']=='xc' and r['sell_price']>0 else None
    cost = r['xc_cost'] * amount
    gain = output_value - cost - material_value if output_value is not None and material_value is not None else None
    margin = gain / material_value if material_value and gain is not None else None
    missing = [i for i in ingredients if i['owned'] < i['quantity'] * amount]
    open_now = bool(r['enabled'] and r['output_enabled'] and enabled(db,'recipes_enabled') and enabled(db,'tier6_economy_enabled'))
    fingerprint = json.dumps([rid, amount, r['output_item_id'],r['output_quantity'],r['xc_cost'],r['sell_price'],r['sellable'],r['currency'],r['effect'],r['effect_value'],[(i['item_id'],i['quantity'],i['sell_price'],i['sellable'],i['currency']) for i in ingredients]])
    return dict(recipe=dict(r), ingredients=ingredients, amount=amount, cost=cost, materials=material_value,
        proceeds=output_value, gain=gain, margin=margin, recommended=open_now and margin is not None and .15 <= margin <= .25,
        missing=missing, wallet=player['xc'] if player else 0, available=open_now,
        craftable=open_now and not missing and player is not None and player['xc']>=cost, fingerprint=fingerprint)


@atomic_action
def craft(db, uid, rid, amount=1, expected=None):
    q = quote(db, uid, rid, amount)
    if expected is not None and expected != q['fingerprint']:
        raise ValueError('Recipe or prices changed. Review the new quote first.')
    if not q['available']:
        raise ValueError('Crafting or this recipe is disabled.')
    if q['missing']:
        raise ValueError('Missing materials: ' + ', '.join(i['name'] for i in q['missing']))
    if q['wallet'] < q['cost']:
        raise ValueError(f"You need {q['cost']} XC for the crafting fee.")
    for i in q['ingredients']:
        db.execute('UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?', (i['quantity']*amount,uid,i['item_id']))
    db.execute('UPDATE players SET xc=xc-? WHERE user_id=?', (q['cost'],uid))
    r=q['recipe']; count=r['output_quantity']*amount
    db.execute('INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity', (uid,r['output_item_id'],count))
    audit(db,uid,'craft',f"{amount}x {r['name']}")
    audit(db,uid,'journey_craft',json.dumps({'recipe':rid,'item':r['output_item_id'],'quantity':count}))
    return f"Created {count}× {r['output_name']} · Paid {q['cost']} XC."


def owned(db,uid,iid):
    r=db.execute('SELECT i.*,COALESCE(v.quantity,0) quantity FROM items i LEFT JOIN inventories v ON v.item_id=i.id AND v.user_id=? WHERE i.id=?',(uid,iid)).fetchone()
    if r is None: raise ValueError('Item is unavailable.')
    return dict(r)


@atomic_action
def sell(db,uid,iid,quantity=1,expected=None,action='sell_item'):
    i=owned(db,uid,iid)
    if quantity<1 or i['quantity']<quantity: raise ValueError('Not enough items.')
    if not i['enabled'] or not i['sellable'] or i['sell_price']<=0: raise ValueError('This item cannot be sold.')
    if expected is not None and expected != (i['sell_price'],i['currency']): raise ValueError('Sale price changed. Review again.')
    column='xc' if i['currency']=='xc' else 'xcrystals'
    reward=i['sell_price']*quantity
    db.execute(f'UPDATE players SET {column}={column}+? WHERE user_id=?',(reward,uid))
    db.execute('UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?',(quantity,uid,iid))
    audit(db,uid,action,f"{quantity}x {i['name']}: +{reward} {i['currency']}")
    audit(db,uid,'journey_sell',json.dumps({'item':iid,'quantity':quantity}))
    return f"Sold {quantity}× {i['name']} · Received {reward} {'XC' if column=='xc' else 'XCrystals'}."


def use_reason(db,uid,iid):
    i=owned(db,uid,iid)
    if not i['enabled'] or i['quantity']<1: return 'You do not own an available item.'
    if i['effect'] not in {'war_credits','capital_repair','xc_reward'}: return 'This item has no supported use effect.'
    p=db.execute('SELECT nation_name,capital_health FROM players WHERE user_id=?',(uid,)).fetchone()
    if not p: return 'Create your player account first.'
    if i['effect'] in {'war_credits','capital_repair'} and not p['nation_name']: return 'Found your Nation in Warfront first.'
    if i['effect']=='capital_repair' and p['capital_health']>=100: return 'Your Capital is already fully repaired.'
    if i['effect_value']<=0: return 'This item has no positive configured effect.'
    return None


@atomic_action
def use(db,uid,iid,expected=None):
    reason=use_reason(db,uid,iid)
    if reason: raise ValueError(reason)
    i=owned(db,uid,iid)
    if expected is not None and expected!=(i['effect'],i['effect_value']): raise ValueError('Item effect changed. Review again.')
    column={'war_credits':'money','xc_reward':'xc','capital_repair':'capital_health'}[i['effect']]
    expression=f'MIN(100,{column}+?)' if column=='capital_health' else f'{column}+?'
    db.execute(f'UPDATE players SET {column}={expression} WHERE user_id=?',(i['effect_value'],uid))
    db.execute('UPDATE inventories SET quantity=quantity-1 WHERE user_id=? AND item_id=?',(uid,iid))
    audit(db,uid,'use',i['name'])
    return f"Used {i['name']}. " + effect_text(i)


def effect_text(i):
    if i['effect']=='war_credits': return f"Grants {i['effect_value']} War Credits — not combat Supply."
    if i['effect']=='capital_repair': return f"Repairs up to {i['effect_value']} Capital HP (maximum 100)."
    if i['effect']=='xc_reward': return f"Redeems for {i['effect_value']} XC."
    return 'No use effect; sell this product for income.'


def quotes(db,uid,material=None):
    rows=db.execute('SELECT id FROM recipes WHERE enabled=1 ORDER BY name').fetchall()
    result=[]
    for row in rows:
        try: q=quote(db,uid,row[0])
        except ValueError: continue
        if material is None or any(i['item_id']==material for i in q['ingredients']): result.append(q)
    return sorted(result,key=lambda q:(not q['craftable'],not q['recommended'],q['recipe']['name']))


GOALS = {'craft': 'Craft for profit', 'earn': 'Earn XC', 'war': 'Prepare War support'}


def selected_goal(db, uid):
    row = db.execute("SELECT detail FROM economy_logs WHERE user_id=? AND action='journey_goal' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    return row[0] if row and row[0] in GOALS else 'craft'


@atomic_action
def set_goal(db, uid, value):
    if value not in GOALS:
        raise ValueError('Choose an available goal.')
    audit(db, uid, 'journey_goal', value)


def capacity(q):
    """Batches affordable now; Max is a quote, never an automatic purchase."""
    if not q['available']:
        return 0
    limits = [1000, *(max(0, i['owned']) // i['quantity'] for i in q['ingredients'])]
    if q['cost']:
        limits.append(max(0, q['wallet']) // q['cost'])
    return min(limits)


def activity(db, uid):
    now = int(time.time())
    queue = db.execute("SELECT COUNT(*) active, COALESCE(SUM(ready_at<=?),0) ready FROM tier6_production_queue WHERE user_id=? AND status='working'", (now, uid)).fetchone()
    products = db.execute('''SELECT COALESCE(SUM(v.quantity),0) FROM inventories v JOIN items i ON i.id=v.item_id
        WHERE v.user_id=? AND v.quantity>0 AND i.enabled=1 AND i.sellable=1 AND i.sell_price>0
        AND EXISTS(SELECT 1 FROM recipes r WHERE r.output_item_id=i.id)''', (uid,)).fetchone()[0]
    return dict(active=queue['active'], ready=queue['ready'], products=products)


def next_step(db, uid):
    mode = selected_goal(db, uid)
    state = activity(db, uid)
    if state['ready']:
        return 'Collect finished production, then review your products.', dict(page='activity'), None
    if mode == 'earn':
        if state['products']:
            return 'Sell a finished product for income.', dict(page='products'), None
        materials = db.execute("SELECT 1 FROM inventories v JOIN items i ON i.id=v.item_id WHERE v.user_id=? AND v.quantity>0 AND i.enabled=1 AND i.effect='crafting_material' AND i.sellable=1 AND i.sell_price>0 LIMIT 1", (uid,)).fetchone()
        return ('Review material sales. Crafting may earn more.', dict(page='materials'), None) if materials else ('Mine materials to earn XC. No War required.', dict(page='activity'), None)
    if mode == 'war':
        qs = [q for q in quotes(db, uid) if q['available'] and q['recipe']['effect'] in {'war_credits', 'capital_repair'}]
        for q in qs:
            iid = q['recipe']['output_item_id']
            if use_reason(db, uid, iid) is None:
                return 'Review your support item before using it. War is optional.', dict(page='product', rid=q['recipe']['id'], iid=iid), q
        if qs:
            q = qs[0]
            return 'Prepare a support item. War Credits are not combat Supply.', dict(page='detail', rid=q['recipe']['id']), q
        return 'No War-support recipe is available. You can change your goal.', dict(page='activity'), None
    text, rid, page = goal(db, uid)
    q = quote(db, uid, rid) if rid else None
    return text, dict(page=page, rid=rid, iid=q['recipe']['output_item_id'] if q else None), q


def goal(db,uid):
    qs=quotes(db,uid)
    r=next((q for q in qs if q['recipe']['name']=='Resource Pack' and q['recommended']),next((q for q in qs if q['recommended']),None))
    if r is None: return 'No profitable beginner recipe is available. Sell materials or explore optional crafting.', None, 'materials'
    iid=r['recipe']['output_item_id']; rid=r['recipe']['id']
    def first_event(action,field,value,after=0):
        for x in db.execute('SELECT id,detail FROM economy_logs WHERE user_id=? AND action=? AND id>? ORDER BY id',(uid,action,after)):
            try:
                if json.loads(x['detail']).get(field)==value:return x['id']
            except (ValueError,AttributeError):continue
        return 0
    crafted=first_event('journey_craft','recipe',rid)
    finished=crafted and first_event('journey_sell','item',iid,crafted)
    stock=owned(db,uid,iid)['quantity']
    materials=' · '.join(f"{i['name']} {i['owned']}/{i['quantity']}" for i in r['ingredients'])
    if finished: title='Loop complete · Make another pack or explore optional War support'; page='detail'
    elif crafted and stock: title='3 / 3 · Sell your crafted product'; page='product'
    elif r['craftable']: title='2 / 3 · Craft your first product'; page='detail'
    else: title='1 / 3 · Gather materials for your first product'; page='detail'
    return f"{title}\n{materials}\nFee {r['cost']} XC · Extra vs raw sale {r['gain']:+} XC", rid, page


class Button(discord.ui.Button):
    def __init__(self,view,label,action,style=discord.ButtonStyle.secondary,disabled=False):
        super().__init__(label=label[:80],style=style,disabled=disabled)
        self.owner_view=view; self.action=action

    async def callback(self,interaction):
        v=self.owner_view
        if await v.interaction_check(interaction): await v.act(interaction,self.action)


class Select(discord.ui.Select):
    def __init__(self,v,options,kind,placeholder):
        super().__init__(options=options,placeholder=placeholder)
        self.v,self.kind=v,kind
    async def callback(self,i):
        if await self.v.interaction_check(i): await self.v.act(i,(self.kind,int(self.values[0])))


class JourneyView(discord.ui.LayoutView):
    def __init__(self,bot,db,uid,page='recipes',rid=None,iid=None,material=None,offset=0,back=None,notice='',operation=None,amount=1):
        super().__init__(timeout=600)
        self.bot,self.db,self.uid=bot,db,uid
        self.page,self.rid,self.iid,self.material,self.offset=page,rid,iid,material,offset
        self.back,self.operation,self.amount=back,operation,amount
        self.token=secrets.token_hex(16); self.used=False; self.expected=None
        self.box=discord.ui.Container(accent_colour=0x36CFC9)
        self.add_item(self.box)
        self.text('-# ✦ X SYSTEM · ECONOMY\n# '+{'activity':'MY ECONOMY','batch':'BATCH PRODUCTION','products':'YOUR PRODUCTS','recipes':'WORKSHOP','detail':'CRAFT & EARN','product':'YOUR PRODUCT','materials':'SELL MATERIALS','areas':'FIND MATERIALS','confirm':'REVIEW','result':'COMPLETED'}.get(page,'ECONOMY'))
        if notice: self.text(notice)
        try: self.build()
        except ValueError as e: self.text(str(e))
        self.row(('‹ Back',('back',)),('Economy',('nav','economy')))

    def text(self,text): self.box.add_item(discord.ui.TextDisplay(text[:3900]))
    def row(self,*items): self.box.add_item(discord.ui.ActionRow(*[Button(self,label,action,discord.ButtonStyle.primary if n==0 else discord.ButtonStyle.secondary) for n,(label,action) in enumerate(items)]))
    def next(self,**kwargs):
        data=dict(page=self.page,rid=self.rid,iid=self.iid,material=self.material,offset=self.offset,back=self)
        data.update(kwargs); return JourneyView(self.bot,self.db,self.uid,**data)

    def fresh(self):
        return self.next(back=self.back,operation=self.operation,amount=self.amount)

    async def interaction_check(self,i):
        if i.user.id==self.uid: return True
        await i.response.send_message('Open your own Economy panel.',ephemeral=True); return False

    def build(self):
        db,uid=self.db,self.uid
        if self.page=='activity':
            state=activity(db,uid); mode=selected_goal(db,uid)
            self.text(f"### Your goal · {GOALS[mode]}\nChoose your focus. No extra reward or automatic spending.")
            self.box.add_item(discord.ui.ActionRow(GoalSelect(self)))
            text,route,q=next_step(db,uid)
            self.text('### Next step\n'+text)
            if route['page']!='activity':self.box.add_item(discord.ui.ActionRow(Entry(self.bot,db,uid,'Continue',back=self,**route)))
            ready=capacity(q) if q else 0
            self.text(f"### Ready now\n**{state['ready']}** jobs to collect · **{state['active']}** jobs in queue\n**{state['products']}** sellable products · **{ready}** batches of the target recipe affordable")
            if state['ready']:self.row(('Collect ready',('collect',)))
            self.row(('Products',('products',)),('Production queue',('nav','production')))
            self.row(('Workshop',('all',)),('Mines',('nav','mining')),('Refresh',('activity',)))
        elif self.page=='recipes':
            qs=quotes(db,uid,self.material)
            self.text('Choose a recipe. Ready-to-craft options appear first. War support is optional.')
            options=[discord.SelectOption(label=q['recipe']['name'][:100],value=str(q['recipe']['id']),description=('Ready · ' if q['craftable'] else 'Needs materials / XC · ')+('Profit route' if q['recommended'] else 'Optional recipe'),default=q['recipe']['id']==self.rid) for q in qs[self.offset:self.offset+25]]
            if options:self.box.add_item(discord.ui.ActionRow(Select(self,options,'recipe','Choose a recipe…')))
            else:self.text('No enabled recipe uses these materials.')
            self.pager(len(qs))
            if self.material:self.row(('All recipes',('all',)))
        elif self.page=='detail':
            q=quote(db,uid,self.rid);r=q['recipe'];self.iid=r['output_item_id']
            self.text(f"## {r['name']} → {r['output_quantity']}× {r['output_name']}\n"+'\n'.join(f"{i['name']}: **{i['owned']} / {i['quantity']}**" for i in q['ingredients']))
            self.text(f"Fee **{q['cost']} XC** · Wallet **{q['wallet']} XC**\nRaw material sale: **{q['materials'] if q['materials'] is not None else 'Not comparable'} XC**\nProduct sale: **{q['proceeds'] if q['proceeds'] is not None else 'Unavailable'} XC**\nExtra after fee: **{str(q['gain']) if q['gain'] is not None else 'Not comparable'} XC**\n"+('Profitable beginner route.' if q['recommended'] else 'Not recommended as a beginner profit route.'))
            self.text(effect_text(r)+f"\n**Can make now: {capacity(q)} batches** (materials + XC).")
            if q['available']:
                self.row(('Craft 1',('confirm','craft')),('Batch production',('batch',)),('Advanced craft',('nav','craft_advanced')))
            if q['missing']:self.row(('Find missing materials',('areas',)))
            if q['wallet']<q['cost']:self.row(('Sell Materials',('materials',)))
            if owned(db,uid,self.iid)['quantity']>0:self.row(('View product',('product',self.iid)))
        elif self.page=='batch':
            q=quote(db,uid,self.rid);maximum=capacity(q)
            import tier6
            state=activity(db,uid)
            self.text(f"## {q['recipe']['name']}\nCan queue **{maximum} batches** from your materials and XC.\nQueue **{state['active']} / {tier6.setting(db,'tier6_production_queue_limit')}** · Ready **{state['ready']}**\nOne queue slot per order. Review total fees and resale before confirming.")
            open_now=enabled(db,'tier6_production_enabled') and q['available'] and state['active']<tier6.setting(db,'tier6_production_queue_limit')
            if open_now and maximum:
                self.row(*[(str(n),('preset','queue',n)) for n in (1,5,10) if n<=maximum],('Max',('max','queue')))
                self.row(('Choose quantity',('quantity','queue')))
            else:self.text('Production is closed, the queue is full, or materials / XC are insufficient.')
            self.row(('Production queue',('nav','production')),('Recipe details',('recipe',self.rid)))
        elif self.page in {'product','result'}:
            if self.iid:
                i=owned(db,uid,self.iid)
                self.text(f"## {i['name']} · Owned {i['quantity']}\n{effect_text(i)}")
                buttons=[]
                if i['enabled'] and i['quantity']>0 and i['sellable'] and i['sell_price']>0:
                    buttons.extend((('Sell Product',('confirm','sell')),('Sell Max',('max','sell'))))
                if i['enabled'] and i['effect'] in {'war_credits','capital_repair','xc_reward'}:
                    reason=use_reason(db,uid,self.iid)
                    if reason:self.text(reason)
                    else:buttons.append(('Use',('confirm','use')))
                if buttons:self.row(*buttons)
                if i['effect'] in {'war_credits','capital_repair'}:self.row(('Warfront',('nav','war')))
            if self.rid:self.row(('Craft Again',('recipe',self.rid)))
            else:self.row(('Craft',('all',)))
        elif self.page in {'materials','products'}:
            condition = "i.sellable=1 AND i.sell_price>0 AND i.effect='crafting_material'" if self.page=='materials' else 'EXISTS (SELECT 1 FROM recipes r WHERE r.output_item_id=i.id)'
            rows=db.execute(f'''SELECT i.*,v.quantity FROM inventories v JOIN items i ON i.id=v.item_id
                WHERE v.user_id=? AND v.quantity>0 AND i.enabled=1 AND {condition} ORDER BY i.name''',(uid,)).fetchall()
            self.text('Choose an item to review. Nothing is sold or used automatically.')
            options=[discord.SelectOption(label=f"{i['name']} ×{i['quantity']}"[:100],value=str(i['id']),default=i['id']==self.iid) for i in rows[self.offset:self.offset+25]]
            if options:self.box.add_item(discord.ui.ActionRow(Select(self,options,'product','Choose item…')))
            else:self.text('No crafted products in your Backpack.' if self.page == 'products' else 'No sellable materials in your Backpack.')
            self.pager(len(rows))
        elif self.page=='areas':
            q=quote(db,uid,self.rid); missing=[i['item_id'] for i in q['missing']]
            rows=db.execute('''SELECT DISTINCT a.* FROM mining_areas a JOIN mining_area_drops d ON d.area_id=a.id WHERE a.enabled=1 AND d.weight>0 ORDER BY a.required_level,a.position''').fetchall()
            level=db.execute('SELECT mining_level FROM players WHERE user_id=?',(uid,)).fetchone()[0]
            areas=[a for a in rows if any(db.execute('SELECT 1 FROM mining_area_drops WHERE area_id=? AND item_id=? AND weight>0',(a['id'],iid)).fetchone() for iid in missing)]
            self.text('Select an unlocked area, then press Mine. Drops are random; a run does not guarantee the missing material.')
            self.text('\n'.join(f"{a['name']} · {'Unlocked' if level>=a['required_level'] else 'Requires level '+str(a['required_level'])}" for a in areas[self.offset:self.offset+25]) or 'No matching areas.')
            options=[discord.SelectOption(label=a['name'][:100],value=str(a['id'])) for a in areas[self.offset:self.offset+25] if level>=a['required_level']]
            if options:self.box.add_item(discord.ui.ActionRow(Select(self,options,'area','Choose mining area…')))
            else:self.text('No matching unlocked area. Check Mining progression or ask staff about configured drops.')
            self.pager(len(areas))
        elif self.page=='confirm':
            if self.operation in {'craft','queue'}:
                q=quote(db,uid,self.rid,self.amount);self.expected=q['fingerprint']
                self.text(f"{self.amount}× {q['recipe']['name']} · Fee {q['cost']} XC\n"+'\n'.join(f"{i['name']}: {i['quantity']*self.amount} needed / {i['owned']} owned" for i in q['ingredients']))
                self.text(f"Output: **{q['recipe']['output_quantity']*self.amount}× {q['recipe']['output_name']}**\nEstimated resale: **{q['proceeds'] if q['proceeds'] is not None else 'Unavailable'} XC**\nAfter fee: **{q['proceeds']-q['cost'] if q['proceeds'] is not None else 'Unavailable'} XC**\nExtra vs raw sale: **{q['gain'] if q['gain'] is not None else 'Not comparable'} XC**")
                if self.operation=='queue':self.text('Uses the existing production queue. Collect when ready.')
            else:
                i=owned(db,uid,self.iid)
                if self.operation=='sell':
                    self.expected=(i['sell_price'],i['currency']);self.text(f"Sell {self.amount}× {i['name']} for {i['sell_price']*self.amount} {i['currency']}. Owned {i['quantity']}.")
                else:self.expected=(i['effect'],i['effect_value']);self.text(f"Use 1× {i['name']}. {effect_text(i)}")
            self.text('Nothing changes until you confirm. Prices and availability are checked again.')
            self.row(('Confirm',('execute',)))
            if self.operation=='sell':self.row(('Choose quantity',('quantity','sell')))

    def pager(self,total):
        buttons=[]
        if self.offset:buttons.append(('‹ Previous',('offset',self.offset-25)))
        if self.offset+25<total:buttons.append(('Next ›',('offset',self.offset+25)))
        if buttons:self.row(*buttons)

    async def act(self,i,action):
        key=action[0]
        if key in {'recipe','batch','confirm','max','preset'}:
            from economy_trade_ui import TradeView
            import economy_transactions
            kind='craft' if key=='recipe' else 'queue' if key=='batch' else action[1]
            target=action[1] if key=='recipe' else self.iid if kind in {'sell','use'} else self.rid
            quantity=action[2] if key=='preset' else 1
            if key=='max':
                try:quantity=economy_transactions.quote(self.db,self.uid,kind,target)['maximum'] or 1
                except ValueError as error:
                    await i.response.send_message(str(error),ephemeral=True);return
            back=(lambda:self.next(rid=target,back=self.back,operation=self.operation,amount=self.amount)) if key=='recipe' else self.fresh
            await i.response.edit_message(view=TradeView(self.bot,self.db,self.uid,kind,target,quantity,back=back));return
        if key=='quantity':
            await i.response.send_modal(Quantity(self,action[1]));return
        if key=='collect':
            await i.response.defer()
            import tier6
            try:message=tier6.claim_production(self.db,self.uid)
            except sqlite3.Error:message='Database busy or unavailable. Refresh to check production before retrying.'
            await i.edit_original_response(view=self.next(page='activity',notice=message));return
        if key=='execute':
            if self.used:
                await i.response.send_message('Already processed. Check your latest result.',ephemeral=True);return
            self.used=True
            await i.response.defer()
            try:
                if self.operation=='craft':message=craft(self.db,self.uid,self.rid,self.amount,expected=self.expected,token=self.token)
                elif self.operation=='sell':message=sell(self.db,self.uid,self.iid,self.amount,expected=self.expected,token=self.token)
                elif self.operation=='use':message=use(self.db,self.uid,self.iid,expected=self.expected,token=self.token)
                else:
                    import tier6
                    ok,message=tier6.start_production(self.db,self.uid,self.rid,self.amount,expected=self.expected,token=self.token)
                    if not ok:raise ValueError(message)
                target=self.next(page='result',notice=message)
                if self.operation=='queue':target.row(('Production queue',('nav','production')))
            except ValueError as e:target=self.next(page='detail' if self.rid else 'product',notice=f'Not completed: {e}')
            except sqlite3.Error:target=self.next(page='detail' if self.rid else 'product',notice='Database busy or unavailable. No transaction completed; refresh before retrying.')
            await i.edit_original_response(view=target);return
        if key=='nav':
            builder=getattr(self.bot,'xbot_player_panel_builders',{}).get(action[1])
            if builder is None:await i.response.send_message('This panel is unavailable.',ephemeral=True);return
            await i.response.edit_message(view=builder(self.uid));return
        if key in {'max','preset'}:
            operation=action[1]
            try:
                maximum=min(1000,owned(self.db,self.uid,self.iid)['quantity']) if operation=='sell' else capacity(quote(self.db,self.uid,self.rid))
                amount=maximum if key=='max' else action[2]
                if not 1<=amount<=maximum:raise ValueError('No available quantity. Refresh your inventory and quote.')
                target=self.next(page='confirm',operation=operation,amount=amount)
            except ValueError as error:
                await i.response.send_message(str(error),ephemeral=True);return
        elif key=='back':target=self.back() if callable(self.back) else self.back.fresh() if isinstance(self.back,JourneyView) else self.back or self.next(page='recipes',back=None)
        elif key=='recipe':target=self.next(page='detail',rid=action[1],offset=0)
        elif key=='product':
            r=self.db.execute('SELECT id FROM recipes WHERE output_item_id=? ORDER BY enabled DESC,id LIMIT 1',(action[1],)).fetchone()
            target=self.next(page='product',iid=action[1],rid=r[0] if r else self.rid,offset=0,back=lambda:self.next(iid=action[1],back=self.back))
        elif key=='all':target=self.next(page='recipes',material=None,offset=0)
        elif key=='offset':target=self.next(offset=action[1],back=self.back)
        elif key=='confirm':target=self.next(page='confirm',operation=action[1],amount=1)
        elif key in {'areas','materials','products','activity','batch'}:target=self.next(page=key,offset=0)
        elif key=='area':
            a=self.db.execute('SELECT * FROM mining_areas WHERE id=? AND enabled=1',(action[1],)).fetchone()
            p=self.db.execute('SELECT mining_level FROM players WHERE user_id=?',(self.uid,)).fetchone()
            if not a or not p or p[0]<a['required_level']:await i.response.send_message('Area is unavailable or locked.',ephemeral=True);return
            self.db.execute('UPDATE players SET mining_area_id=? WHERE user_id=?',(a['id'],self.uid));self.db.commit()
            target=self.next(page='detail',notice=f"Selected {a['name']}. Mine, then return to this recipe.")
            target.box.add_item(discord.ui.ActionRow(MineForRecipe(target)))
        else:return
        await i.response.edit_message(view=target)


class MineForRecipe(discord.ui.Button):
    def __init__(self,v):
        super().__init__(label='Mine',style=discord.ButtonStyle.success);self.v=v
    async def callback(self,i):
        if not await self.v.interaction_check(i):return
        v=self.v
        class Response:
            def __getattr__(self,key):return getattr(i.response,key)
            async def edit_message(self,**kwargs):
                view=kwargs.get('view')
                if isinstance(view,discord.ui.LayoutView) and view.total_children_count<=37:
                    view.add_item(discord.ui.ActionRow(Entry(v.bot,v.db,v.uid,'Back to recipe',page='detail',rid=v.rid,back=v)))
                await i.response.edit_message(**kwargs)
            async def send_message(self,*args,**kwargs):
                await i.response.send_message(*args,**kwargs)
        class Interaction:
            response=Response()
            def __getattr__(self,key):return getattr(i,key)
        await v.bot.xbot_mine_button_builder().callback(Interaction())


class Quantity(discord.ui.Modal):
    def __init__(self,v,operation):
        super().__init__(title='Choose quantity');self.v,self.operation=v,operation
        self.value=discord.ui.TextInput(label='Whole number · 1 to 1,000',default='1',max_length=4);self.add_item(self.value)
    async def on_submit(self,i):
        if not await self.v.interaction_check(i):return
        try:
            n=int(self.value.value)
            if not 1<=n<=1000:raise ValueError()
        except ValueError:await i.response.send_message('Choose 1 to 1,000.',ephemeral=True);return
        from economy_trade_ui import TradeView
        target=self.v.iid if self.operation in {'sell','use'} else self.v.rid
        await i.response.edit_message(view=TradeView(self.v.bot,self.v.db,self.v.uid,self.operation,target,n,back=self.v.fresh))


class GoalSelect(discord.ui.Select):
    def __init__(self,v):
        self.v=v
        super().__init__(placeholder='Choose your economy goal',options=[discord.SelectOption(label=label,value=key,default=selected_goal(v.db,v.uid)==key) for key,label in GOALS.items()])
    async def callback(self,i):
        if not await self.v.interaction_check(i):return
        try:set_goal(self.v.db,self.v.uid,self.values[0])
        except (ValueError,sqlite3.Error):
            await i.response.send_message('Goal could not be saved. Please refresh and try again.',ephemeral=True);return
        await i.response.edit_message(view=self.v.next(page='activity',back=self.v.back,notice='Goal saved. No assets were spent.'))


class Entry(discord.ui.Button):
    def __init__(self,bot,db,uid,label='Craft',page='recipes',material=None,back=None,rid=None,iid=None):
        super().__init__(label=label,style=discord.ButtonStyle.primary)
        self.args=(bot,db,uid);self.kwargs=dict(page=page,material=material,back=back,rid=rid,iid=iid)
    async def callback(self,i):
        if i.user.id!=self.args[2]:await i.response.send_message('Open your own Economy panel.',ephemeral=True);return
        if self.kwargs['page']=='detail' and self.kwargs['rid']:
            from economy_trade_ui import TradeView
            view=JourneyView(*self.args,**{**self.kwargs,'page':'recipes'})
            await i.response.edit_message(view=TradeView(*self.args,'craft',self.kwargs['rid'],back=view.fresh));return
        await i.response.edit_message(view=JourneyView(*self.args,**self.kwargs))


def goal_block(bot,db,uid):
    text,route,q=next_step(db,uid);state=activity(db,uid)
    status=f"\n**{capacity(q) if q else 0}** target batches ready · **{state['ready']}** jobs to collect · **{state['products']}** sellable products"
    return discord.ui.TextDisplay('### Your next step · '+GOALS[selected_goal(db,uid)]+'\n'+text+status), discord.ui.ActionRow(Entry(bot,db,uid,'Continue',**route),Entry(bot,db,uid,'Goals & Activity',page='activity'))
