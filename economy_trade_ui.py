"""Same-message amount preview and explicit settlement, shared by Economy leaves."""
import secrets
import sqlite3

import discord
import economy_transactions as transactions


class TradeView(discord.ui.LayoutView):
    xbot_managed_navigation=True

    def __init__(self,bot,db,owner,kind,target,quantity=1,*,price=None,back=None,notice='',complete=False,advanced=False):
        super().__init__(timeout=600)
        self.bot,self.db,self.owner=bot,db,owner
        self.kind,self.target,self.quantity,self.price=kind,target,quantity,price
        self.back,self.advanced=back,advanced
        self.used=complete;self.token=secrets.token_hex(16);self.quote=None
        self.box=discord.ui.Container(accent_colour=0x36CFC9);self.add_item(self.box)
        self.text('-# ✦ X SYSTEM · ECONOMY\n# '+{'shop':'SHOP ITEM','sell':'SELL TO SYSTEM','use':'USE ITEM','list':'SELL TO PLAYERS','market_buy':'PLAYER MARKET','market_cancel':'MY LISTING','stock_buy':'BUY SHARES','stock_sell':'SELL SHARES','craft':'CRAFT & EARN','queue':'PRODUCTION ORDER','production_cancel':'CANCEL PRODUCTION'}.get(kind,'DETAILS'))
        if notice:self.text('## '+('Completed' if complete else 'Update')+'\n'+notice[:600])
        try:
            if advanced and kind=='craft' and quantity>100:raise ValueError('Advanced instant crafting supports 1–100 batches. Use Production for larger orders.')
            if kind.startswith('stock_'):
                import tier6
                tier6.update_stock_prices(db)
            q=transactions.quote(db,owner,kind,target,quantity,price);self.quote=q
            if kind=='list':self.price=q['price']
            self.text(f"## {discord.utils.escape_markdown(q['name'])}\nWallet **{q['wallet']:,} {q['currency']}**\n{q['details']}")
            label=(f"Receive {q['total']:,}" if q['credit'] else f"Pay {q['total']:,}")+f" {q['currency']}"
            if kind in {'use','market_cancel','list'}:label='Review the effect above'
            self.text(f"## {'Next order reference' if complete else 'Order preview'}\nQuantity **{quantity:,}** · **{label}**\n"+('Previous order settled. Prepare another to trade again.' if complete else 'Nothing is spent until you press the action button.'))
            if q['reason'] and not complete:self.text('⚠️ '+q['reason'])
            fixed=kind in {'use','market_cancel','production_cancel'}
            if not fixed and not complete:
                if kind=='craft' and not advanced:
                    self.row(('Craft 1',('quantity',1)),('Batch production',('mode','queue')))
                else:
                    maximum=q['maximum'];buttons=[]
                    for n in (1,5,10):
                        if maximum is None or n<=maximum:buttons.append((str(n),('quantity',n)))
                    if maximum is not None and maximum>0:buttons.append(('Max',('quantity',min(maximum,100 if advanced and kind=='craft' else 1000 if kind in {'queue','craft'} else maximum))))
                    buttons.append(('Custom',('custom',)))
                    self.row(*buttons)
            if kind=='list' and not complete:self.row(('Set unit price',('price',)))
            if kind.startswith('stock_') and not complete:self.row(('Buy / Sell',('mode','stock_sell' if kind=='stock_buy' else 'stock_buy')))
            if complete:self.row(({'craft':'Craft Again','queue':'Queue Again','shop':'Buy Again','sell':'Sell Again'}.get(kind,'Prepare another'),('refresh',)))
            else:
                text=f"{q['verb']} {quantity:,} · "+(f"Receive {q['total']:,}" if q['credit'] else f"{q['total']:,}")+f" {q['currency']}"
                if kind=='use':text='Confirm Use · 1 item'
                elif kind=='market_cancel':text='Confirm Cancel & Return'
                elif kind=='list':text=f'List {quantity:,} · {self.price:,} XC each'
                button=TradeButton(self,text,('execute',),discord.ButtonStyle.success)
                button.disabled=bool(q['reason']);self.box.add_item(discord.ui.ActionRow(button))
            if not complete:self.row(('Refresh preview',('refresh',)))
            if complete and kind=='craft':
                from economy_journey import owned
                product=owned(db,owner,q['output_item_id']);actions=[]
                if product['quantity']>0 and product['enabled']:
                    if product['sellable'] and product['sell_price']>0:actions.append(('Sell Product',('product','sell',product['id'])))
                    if product['effect'] in {'war_credits','capital_repair','xc_reward'}:actions.append(('Use Product',('product','use',product['id'])))
                if actions:self.row(*actions)
            if kind in {'shop','sell','use'}:self.row(('Backpack',('nav','inventory')),('Craft',('nav','craft')))
            if kind in {'craft','queue','production_cancel'}:self.row(('Production queue',('nav','production')),('Products',('products',)))
            if kind in {'craft','queue'}:self.row(('Find materials',('materials',)),('Sell materials',('nav','inventory')),('Advanced Craft',('advanced',)))
            if kind in {'list','market_buy','market_cancel'}:self.row(('My Listings',('nav','market_mine')),('Player Market',('nav','market')))
        except (ValueError,sqlite3.Error) as error:
            self.text(str(error) if isinstance(error,ValueError) else 'Database busy. Refresh before retrying.')
            self.row(('Refresh preview',('refresh',)))
        self.add_item(discord.ui.ActionRow(TradeNav(self,'‹ Back','back'),TradeNav(self,'⌂ Menu','menu'),TradeNav(self,'× Close','close')))

    def text(self,text):
        remaining=3900-sum(len(c.content) for c in self.walk_children() if isinstance(c,discord.ui.TextDisplay))
        if remaining>0:self.box.add_item(discord.ui.TextDisplay(text[:min(2500,remaining)]))
    def row(self,*buttons):self.box.add_item(discord.ui.ActionRow(*[TradeButton(self,label,action) for label,action in buttons]))
    def rebuild(self,**changes):
        data=dict(price=self.price,back=self.back,advanced=self.advanced)
        data.update(changes)
        kind=data.pop('kind',self.kind);quantity=data.pop('quantity',self.quantity)
        return TradeView(self.bot,self.db,self.owner,kind,self.target,quantity,**data)
    async def interaction_check(self,i):
        if i.user.id==self.owner:return True
        await i.response.send_message('Open your own Economy panel.',ephemeral=True);return False
    async def on_error(self,i,error,item):
        from system_ui import report_panel_error
        await report_panel_error(i,error)
    async def act(self,i,action):
        key=action[0]
        if key in {'custom','price'}:
            await i.response.send_modal(AmountModal(self,key));return
        if key=='execute':
            if self.used or self.quote is None:
                await i.response.send_message('This order was already handled. Check the result or prepare another.',ephemeral=True);return
            self.used=True
            await i.response.defer()
            try:
                message=transactions.settle(self.db,self.owner,self.kind,self.target,self.quantity,self.price,expected=self.quote['fingerprint'],token=self.token)
            except ValueError as e:
                target=self.rebuild(notice=str(e))
            except sqlite3.Error:
                target=self.rebuild(notice='Database busy or unavailable. No order settled; refresh before retrying.')
            else:target=self.rebuild(notice=message,complete=True)
            await i.edit_original_response(view=target);return
        if key=='quantity':target=self.rebuild(quantity=action[1])
        elif key=='mode':target=self.rebuild(kind=action[1],quantity=1)
        elif key=='refresh':target=self.rebuild()
        elif key=='advanced':target=self.rebuild(kind='craft',quantity=1,advanced=True,back=self.rebuild)
        elif key=='products':
            from economy_journey import JourneyView
            target=JourneyView(self.bot,self.db,self.owner,page='products',back=lambda:self.rebuild())
        elif key=='product':
            target=TradeView(self.bot,self.db,self.owner,action[1],action[2],back=self.rebuild)
        elif key=='materials':
            from economy_journey import JourneyView
            target=JourneyView(self.bot,self.db,self.owner,page='areas',rid=self.target,back=lambda:self.rebuild())
        elif key=='nav':
            builder=getattr(self.bot,'xbot_player_panel_builders',{}).get(action[1])
            if not builder:await i.response.send_message('This panel is unavailable.',ephemeral=True);return
            target=builder(self.owner)
        else:return
        await i.response.edit_message(view=target)


class TradeButton(discord.ui.Button):
    def __init__(self,v,label,action,style=discord.ButtonStyle.secondary):
        super().__init__(label=label[:80],style=style);self.v,self.action=v,action
    async def callback(self,i):
        if await self.v.interaction_check(i):await self.v.act(i,self.action)


class TradeNav(discord.ui.Button):
    def __init__(self,v,label,key):
        super().__init__(label=label);self.v,self.key=v,key
    async def callback(self,i):
        v=self.v
        if not await v.interaction_check(i):return
        if self.key=='close':
            target=discord.ui.LayoutView();target.add_item(discord.ui.TextDisplay('Panel closed. Open /menu to return.'));v.stop()
        elif self.key=='back' and v.back:target=v.back() if callable(v.back) else v.back
        else:
            key='menu' if self.key=='menu' else 'economy'
            target=v.bot.xbot_system_page_builder(v.owner,key)
        await i.response.edit_message(view=target)


class AmountModal(discord.ui.Modal):
    def __init__(self,v,field):
        super().__init__(title='Set unit price' if field=='price' else 'Choose quantity')
        self.v,self.field=v,field
        self.maximum=9999999999 if field=='price' else 100 if v.advanced and v.kind=='craft' else 1000 if v.kind in {'craft','queue'} else 99999999 if v.kind.startswith('stock_') else 9999999
        self.value=discord.ui.TextInput(label='XC per item' if field=='price' else 'Whole-number quantity',placeholder=f'1–{self.maximum:,}; submit to preview only',default=str(v.price if field=='price' else v.quantity),max_length=len(str(self.maximum)))
        self.add_item(self.value)
    async def on_submit(self,i):
        if not await self.v.interaction_check(i):return
        try:
            value=int(self.value.value.replace(',','').strip())
            if not 1<=value<=self.maximum:raise ValueError()
        except ValueError:
            await i.response.send_message('Enter a positive whole number within the displayed limits.',ephemeral=True);return
        target=self.v.rebuild(**({'price':value} if self.field=='price' else {'quantity':value}))
        await i.response.edit_message(view=target)
    async def on_error(self,i,error):await self.v.on_error(i,error,self)
