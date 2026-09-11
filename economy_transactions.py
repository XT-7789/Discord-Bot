"""Shared live quotes and atomic settlement for existing player Economy actions."""
import json
import time

import economy_journey as journey
import svip


def number(db,key,default):
    row=db.execute('SELECT value FROM economy_settings WHERE key=?',(key,)).fetchone()
    return int(row[0]) if row else default


def balance(db,uid):
    row=db.execute('SELECT * FROM players WHERE user_id=?',(uid,)).fetchone()
    if not row:raise ValueError('Open /menu to create your player account.')
    return dict(row)


def quote(db,uid,kind,target,quantity=1,price=None):
    if type(quantity) is not int or quantity<1:
        raise ValueError('Choose a positive whole number.')
    if kind in {'use','market_cancel','production_cancel'} and quantity!=1:
        raise ValueError('This action handles one item or order at a time.')
    p=balance(db,uid)
    q=dict(kind=kind,target=target,quantity=quantity,wallet=p['xc'],currency='XC',fee=0,total=0,
           maximum=None,reason=None,details='',name='',verb='Confirm',credit=False)
    fingerprint=[]
    if kind in {'craft','queue'}:
        import tier6
        r=journey.quote(db,uid,target,quantity);one=journey.quote(db,uid,target)
        q.update(name=r['recipe']['name'],total=r['cost'],maximum=journey.capacity(one),verb='Craft' if kind=='craft' else 'Queue')
        q['output_item_id']=r['recipe']['output_item_id']
        q['details']='\n'.join(f"{i['name']}: **{i['owned']} owned / {i['quantity']*quantity} needed**" for i in r['ingredients'])
        q['details']+=f"\nOutput **{r['recipe']['output_quantity']*quantity}× {r['recipe']['output_name']}**"
        if r['proceeds'] is not None:
            q['details']+=f"\nProduct resale **{r['proceeds']} XC** · After fee **{r['proceeds']-r['cost']} XC**"
        if r['gain'] is not None:q['details']+=f"\nRaw material resale **{r['materials']} XC** · Extra after crafting fee **{r['gain']:+} XC**"
        if not r['available']:q['reason']='Crafting or this recipe is closed.'
        elif r['missing']:q['reason']='Missing materials. Return to the recipe to find mining areas.'
        elif r['wallet']<r['cost']:q['reason']='Not enough XC for the fee.'
        if kind=='queue':
            active=journey.activity(db,uid)['active'];limit=svip.production_limit(db,uid)
            q['details']+=f'\nQueue **{active}/{limit}** · One slot per order; collect when ready.'
            if svip.summary(db,uid):q['details']+='\n'+svip.summary(db,uid)
            if not tier6.setting(db,'tier6_production_enabled'):q['reason']='Production is closed.'
            elif active>=limit:q.update(reason='Production queue is full. Collect ready jobs first.',maximum=0)
        fingerprint=[r['fingerprint']]
        if kind=='queue':fingerprint.extend([svip.production_limit(db,uid),svip.benefits(db,uid)['percent']])
    elif kind.startswith('stock_'):
        import tier6
        side=kind[6:];s=tier6.stock_quote(db,uid,target,side,quantity);c=s['company']
        h=db.execute('SELECT quantity,average_cost FROM tier6_stock_holdings WHERE user_id=? AND company_id=?',(uid,target)).fetchone()
        owned=h['quantity'] if h else 0
        maximum=owned
        if side=='buy':
            lo,hi=0,max(0,min(c['available_shares'],tier6.setting(db,'tier6_stock_holding_limit')-owned))
            while lo<hi:
                mid=(lo+hi+1)//2
                if tier6.stock_quote(db,uid,target,side,mid)['total']<=p['xc']:lo=mid
                else:hi=mid-1
            maximum=lo
        q.update(name=f"{c['symbol']} · {c['name']}",fee=s['fee'],total=s['total'],maximum=maximum,verb=side.title(),credit=side=='sell')
        q['details']=f"Game-only stocks · Fictional XC, not real investments.\nPrice **{c['price']} XC/share** · Owned **{owned}**\nAverage cost **{h['average_cost'] if h else 0} XC** · Available **{c['available_shares']}**\nGross **{s['gross']} XC** · Fee **{s['fee']} XC**"
        if not c['enabled'] or not tier6.setting(db,'tier6_economy_enabled') or not tier6.setting(db,'tier6_stock_enabled'):q['reason']='Stock market or this company is closed.'
        elif quantity>maximum:q['reason']='Quantity exceeds your balance, holding limit or available shares.'
        fingerprint=[c['price'],s['fee'],s['total']]
    elif kind in {'market_buy','market_cancel'}:
        r=db.execute('SELECT l.*,i.name FROM market_listings l JOIN items i ON i.id=l.item_id WHERE l.id=?',(target,)).fetchone()
        if not r:raise ValueError('Listing is no longer available.')
        r=dict(r);q['record']=r;q['name']=r['name']
        expired=r['created_at']<=int(time.time())-max(1,number(db,'tier6_market_expiry_days',7))*86400
        if not r['active'] or r['quantity']<=0 or expired:q['reason']='Listing sold, cancelled or expired. Refresh the market.'
        if kind=='market_cancel':
            q.update(verb='Cancel listing',maximum=1)
            q['details']=f"Listing #{target} · Return **{r['quantity']}× {r['name']}** to your Backpack."
            if r['seller_id']!=uid:q['reason']='This listing belongs to another player.'
            fingerprint=[r['quantity'],r['item_id'],r['seller_id']]
        else:
            total=r['price_each']*quantity;fee=total*number(db,'market_fee_percent',5)//100
            q.update(verb='Buy',total=total,fee=fee,maximum=min(r['quantity'],p['xc']//r['price_each']) if r['price_each']>0 else 0)
            q['details']=f"Listing #{target} · Available **{r['quantity']}** · **{r['price_each']} XC each**\nSeller pays **{fee} XC** fee; receives **{total-fee} XC**. Buyer pays **{total} XC**, no extra fee."
            if not journey.enabled(db,'market_enabled'):q['reason']='Player Market is closed.'
            elif not db.execute('SELECT 1 FROM players WHERE user_id=?',(r['seller_id'],)).fetchone():q['reason']='Seller account is unavailable. Choose another listing.'
            elif r['seller_id']==uid:q['reason']='You cannot buy your own listing.'
            elif quantity>q['maximum']:q['reason']='Not enough XC or listing quantity.'
            fingerprint=[r['item_id'],r['seller_id'],r['price_each'],fee,total]
    elif kind=='production_cancel':
        import tier6
        cancel=tier6.production_cancel_quote(db,uid,target);r=cancel['job'];refund=cancel['refund']
        inputs=[(x['item_id'],x['quantity']) for x in cancel['inputs']]
        names={x['id']:x['name'] for x in db.execute('SELECT id,name FROM items')}
        q.update(name=r['name'],total=refund,credit=True,maximum=1,verb='Cancel job',details=f"Return recorded materials and **{refund} XC**.\n"+'\n'.join(f"{names.get(iid,'Archived item')} ×{n}" for iid,n in inputs))
        if r['status']!='working':q['reason']='This job is no longer active.'
        elif r['ready_at']<=int(time.time()):q['reason']='This job is ready. Collect it instead.'
        fingerprint=[refund,inputs,r['status'],r['ready_at']]
    else:
        item=journey.owned(db,uid,target);q['record']=item;q['name']=item['name'];q['owned']=item['quantity']
        currency='xc' if item['currency']=='xc' else 'xcrystals'
        q.update(currency='XC' if currency=='xc' else 'XCrystals',wallet=p[currency])
        q['details']=f"Owned **{item['quantity']}**\n{item['description'] or 'No description.'}"
        if kind=='shop':
            if item['effect']=='mine_tool':
                from economy_progress import tool_comparison
                q['details']+='\n'+tool_comparison(db,uid,item)
            limits=[]
            if item['stock']>=0:limits.append(item['stock'])
            if item['price']>0:limits.append(p[currency]//item['price'])
            q.update(verb='Buy',total=item['price']*quantity,maximum=min(limits) if limits else None)
            q['details']+=f"\nUnit price **{item['price']} {q['currency']}** · Stock **{item['stock'] if item['stock']>=0 else 'Unlimited'}**"
            if not journey.enabled(db,'economy_shop_enabled'):q['reason']='System Shop is closed. Return to Economy or try later.'
            elif not item['enabled'] or not item['shop_visible'] or item['stock']==0:q['reason']='Item is unavailable or sold out.'
            elif item['price']<0:q['reason']='Invalid item price. Contact staff.'
            elif q['maximum'] is not None and quantity>q['maximum']:q['reason']='Not enough balance or shop stock.'
            fingerprint=[item['price'],item['currency']]
        elif kind=='sell':
            q.update(verb='Sell to system',total=item['sell_price']*quantity,credit=True,maximum=item['quantity'])
            q['details']+=f"\nSystem resale **{item['sell_price']} {q['currency']} per item**. Not a player-market listing."
            if not item['enabled'] or not item['sellable'] or item['sell_price']<=0:q['reason']='This item cannot be sold to the system.'
            elif quantity>item['quantity']:q['reason']='Not enough items.'
            fingerprint=[item['sell_price'],item['currency']]
        elif kind=='use':
            q.update(verb='Use item',maximum=1,reason=journey.use_reason(db,uid,target))
            q['details']+='\n'+journey.effect_text(item)
            fingerprint=[item['effect'],item['effect_value']]
        elif kind=='list':
            minimum=number(db,'market_min_price',1);maximum=number(db,'market_max_price',1000000)
            unit=minimum if price is None else price
            if type(unit) is not int:raise ValueError('Enter a whole-number XC price.')
            gross=unit*quantity;fee=gross*number(db,'market_fee_percent',5)//100
            q.update(verb='List for players',currency='XC',wallet=p['xc'],maximum=item['quantity'],total=0,fee=fee,price=unit)
            q['details']+=f"\nList **{quantity}** at **{unit} XC each** · Price range **{minimum}–{maximum} XC**\nIf sold together: gross **{gross} XC**, seller fee **{fee} XC**, net **{gross-fee} XC**. Partial-sale fee rounding may differ.\nItems leave your Backpack now; payment arrives only when bought."
            active=db.execute('SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1',(uid,)).fetchone()[0]
            if not journey.enabled(db,'market_enabled'):q['reason']='Player Market is closed.'
            elif not item['tradeable']:q['reason']='This item cannot be listed on the player market.'
            elif quantity>item['quantity']:q['reason']='Not enough items.'
            elif not minimum<=unit<=maximum:q['reason']=f'Price must be {minimum}–{maximum} XC.'
            elif active>=svip.market_limit(db,uid):q['reason']='Listing limit reached. Open My Listings.'
            q['details']+=f"\nActive listings **{active}/{svip.market_limit(db,uid)}**"
            if svip.summary(db,uid):q['details']+='\n'+svip.summary(db,uid)
            fingerprint=[unit,fee,minimum,maximum,svip.market_limit(db,uid)]
        else:raise ValueError('Unsupported action.')
    q['fingerprint']=json.dumps([kind,target,quantity,fingerprint],sort_keys=True)
    return q


@journey.atomic_action
def settle(db,uid,kind,target,quantity=1,price=None,expected=None):
    if kind.startswith('stock_'):
        import tier6
        tier6.update_stock_prices(db)
    q=quote(db,uid,kind,target,quantity,price)
    if expected is not None and expected!=q['fingerprint']:
        raise ValueError('Price, fee or effect changed. Review the updated preview and confirm again.')
    if q['reason']:raise ValueError(q['reason'])
    if kind in {'craft','queue'}:
        if kind=='craft':return journey.craft(db,uid,target,quantity)
        import tier6
        ok,message=tier6.start_production(db,uid,target,quantity)
        if not ok:raise ValueError(message)
        return message
    if kind=='sell':return journey.sell(db,uid,target,quantity,action='backpack_sell')
    if kind=='use':return journey.use(db,uid,target)
    if kind.startswith('stock_'):
        import tier6
        ok,message=tier6.stock_trade(db,uid,target,kind[6:],quantity)
        if not ok:raise ValueError(message)
        return message
    if kind=='production_cancel':
        import tier6
        return tier6.cancel_production(db,uid,target)
    now=int(time.time())
    if kind=='shop':
        item=q['record'];column='xc' if item['currency']=='xc' else 'xcrystals'
        db.execute(f'UPDATE players SET {column}={column}-? WHERE user_id=?',(q['total'],uid))
        db.execute('INSERT INTO inventories VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity',(uid,target,quantity))
        if item['stock']>=0:db.execute('UPDATE items SET stock=stock-? WHERE id=?',(quantity,target))
        journey.audit(db,uid,'buy',f"Bought {quantity}x {q['name']} for {q['total']} {q['currency']}")
    elif kind=='list':
        db.execute('UPDATE inventories SET quantity=quantity-? WHERE user_id=? AND item_id=?',(quantity,uid,target))
        result=db.execute('INSERT INTO market_listings(seller_id,item_id,quantity,price_each,created_at) VALUES(?,?,?,?,?)',(uid,target,quantity,q['price'],now))
        journey.audit(db,uid,'market_sell',f"listing {result.lastrowid}: {quantity}x {q['name']} at {q['price']}")
        return f"Listed {quantity}× {q['name']} at {q['price']} XC each. Payment arrives only after a buyer purchases."
    elif kind=='market_buy':
        r=q['record'];seller_payment=q['total']-q['fee']
        db.execute('UPDATE players SET xc=xc-? WHERE user_id=?',(q['total'],uid))
        db.execute('UPDATE players SET xc=xc+? WHERE user_id=?',(seller_payment,r['seller_id']))
        db.execute('INSERT INTO inventories VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity',(uid,r['item_id'],quantity))
        db.execute('UPDATE market_listings SET quantity=quantity-?,active=CASE WHEN quantity-?<=0 THEN 0 ELSE 1 END WHERE id=?',(quantity,quantity,target))
        journey.audit(db,uid,'market_buy',f'listing {target}: {quantity} for {q["total"]} XC')
        journey.audit(db,r['seller_id'],'market_sale',f'listing {target}: {quantity} for {seller_payment} XC')
        db.execute('INSERT INTO tier6_market_trades(listing_id,buyer_id,seller_id,item_id,quantity,price_each,fee,total,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(target,uid,r['seller_id'],r['item_id'],quantity,r['price_each'],q['fee'],q['total'],now))
    elif kind=='market_cancel':
        r=q['record']
        db.execute('UPDATE market_listings SET active=0 WHERE id=? AND active=1',(target,))
        db.execute('INSERT INTO inventories VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity',(uid,r['item_id'],r['quantity']))
        journey.audit(db,uid,'market_cancel',f"listing {target}: returned {r['quantity']}")
        return f"Cancelled listing #{target}. Returned {r['quantity']}× {q['name']} to your Backpack."
    return f"Bought {quantity}× {q['name']} · Paid {q['total']} {q['currency']}."


@journey.atomic_action
def equip(db,uid,item_id):
    item=journey.owned(db,uid,item_id)
    if item['quantity']<1 or item['effect']!='mine_tool':raise ValueError('Only an owned mining tool can be equipped.')
    db.execute('UPDATE players SET equipped_pickaxe_id=? WHERE user_id=?',(item_id,uid))
    journey.audit(db,uid,'equip',item['name'])
    return f"Equipped {item['name']}."
