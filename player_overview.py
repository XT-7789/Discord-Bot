"""Personal, persisted overview. Opening/customizing never settles assets."""
import json
import sqlite3
import time
import discord
import economy_journey as journey
import economy_progress
import svip

BLOCKS={'wallet':'Wallet & Bank','mining':'Mines','production':'Craft & Production','market':'Market & Stocks','nation':'Nation','missions':'Missions','vip':'VIP'}
DEFAULT=('wallet','production','missions')

def compact_number(value):
    value=int(value)
    if abs(value)<100000:return f'{value:,}'
    for scale,suffix in ((10**12,'T'),(10**9,'B'),(10**6,'M'),(1000,'K')):
        if abs(value)>=scale:return f'{value/scale:.1f}{suffix}'
    return str(value)

def tile_text(tiles):
    """Bounded ASCII columns; real action buttons are rendered separately."""
    lines=[' |'.join((str(tile[row]).upper() if row==0 else str(tile[row]))[:12].ljust(12) for tile in tiles).rstrip() for row in range(3)]
    return '```\n'+'\n'.join(lines)+'\n```'

def layout(db,uid):
    row=db.execute("SELECT detail FROM economy_logs WHERE user_id=? AND action='overview_layout' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    if row:
        try:
            value=json.loads(row[0])
            if isinstance(value,list) and all(isinstance(k,str) and k in BLOCKS for k in value):return tuple(dict.fromkeys(value))
        except (ValueError,TypeError):pass
    return DEFAULT

@journey.atomic_action
def save_layout(db,uid,keys):
    if not isinstance(keys,(list,tuple)) or len(keys)>len(BLOCKS) or any(not isinstance(k,str) or k not in BLOCKS for k in keys):raise ValueError('Choose valid overview sections.')
    if not db.execute('SELECT 1 FROM players WHERE user_id=?',(uid,)).fetchone():raise ValueError('Open /menu first.')
    chosen=[k for k in BLOCKS if k in keys]
    journey.audit(db,uid,'overview_layout',json.dumps(chosen))

class OverviewView(discord.ui.LayoutView):
    xbot_managed_navigation=True
    def __init__(self,bot,db,owner,page=0,*,editing=False,draft=None,notice=''):
        super().__init__(timeout=600)
        self.bot,self.db,self.owner=bot,db,owner;self.editing=editing
        self.selected=tuple(draft) if draft is not None else layout(db,owner)
        self.pages=1;self.page=0
        self.box=discord.ui.Container(accent_colour=0x41D9D0);self.add_item(self.box)
        self.text('-# ✦ X SYSTEM\n# '+('CUSTOMIZE OVERVIEW' if editing else 'MY OVERVIEW'))
        if notice:self.text('-# '+notice)
        if editing:
            self.text('Choose your stats and shortcuts. All selected sections fit on one overview. Changes apply only after Save.')
            self.box.add_item(discord.ui.ActionRow(LayoutSelect(self)))
            self.row(('Save',('save',)),('Use Defaults',('defaults',)),('Cancel',('cancel',)),primary=True)
        else:
            text,self.route,_=journey.next_step(db,owner)
            self.text('### Current goal\n'+text[:850])
            growth,self.upgrade=economy_progress.growth(db,owner)
            p=db.execute('SELECT * FROM players WHERE user_id=?',(owner,)).fetchone()
            if self.upgrade and p:
                item=self.upgrade;name=discord.utils.escape_markdown(item['name'])[:100]
                gap=max(0,item['price']-p['xc'])
                status='Owned · Equip in Backpack' if item['owned'] else f"{item['price']:,} XC · "+(f'Need {gap:,} XC' if gap else 'Budget ready')
                if p['mining_level']<item['pickaxe_required_level']:status+=f" · Mining Lv {p['mining_level']}/{item['pickaxe_required_level']}"
                self.text(f'**Next upgrade · {name}**\n{status}')
            else:self.text('**Next upgrade**\n'+growth)
            self.row(('Continue',('continue',)),('Choose Goal',('goal',)),('Review Upgrade' if self.upgrade else 'Research',('upgrade',)),primary=True)
            if p:
                tiles=self.tiles(p)
                if tiles:
                    self.box.add_item(discord.ui.Separator())
                    for start in range(0,len(tiles),3):
                        group=tiles[start:start+3]
                        self.text(tile_text(group))
                        self.row(*[(tile[3],('nav',tile[4])) for tile in group])
                    self.text('-# Energy is last recorded. Open a panel for exact totals and details.')
            if not self.selected:self.text('Goal-only overview. Use Customize to add sections.')
        footer=[] if editing else [OverviewButton(self,'Customize',('customize',)),OverviewButton(self,'Refresh',('refresh',))]
        self.add_item(discord.ui.ActionRow(*footer,OverviewButton(self,'Menu',('menu',)),OverviewButton(self,'Close',('close',))))

    def text(self,text):
        remaining=3800-sum(len(x.content) for x in self.walk_children() if isinstance(x,discord.ui.TextDisplay))
        if remaining>0:self.box.add_item(discord.ui.TextDisplay(text[:min(1200,remaining)]))
    def row(self,*items,primary=False):
        self.box.add_item(discord.ui.ActionRow(*[OverviewButton(self,label,action,discord.ButtonStyle.success if primary and n==0 else discord.ButtonStyle.secondary) for n,(label,action) in enumerate(items)]))
    def fresh(self):return OverviewView(self.bot,self.db,self.owner,self.page)
    async def interaction_check(self,i):
        if i.user.id==self.owner:return True
        await i.response.send_message('Open /overview for your own personal panel.',ephemeral=True);return False
    async def on_error(self,i,error,item):
        from system_ui import report_panel_error
        await report_panel_error(i,error)

    def tiles(self,p):
        db,uid=self.db,self.owner;result=[];extra=[];n=compact_number
        if 'wallet' in self.selected:result.append(('Wallet',n(p['xc'])+' XC','Bank '+n(p['bank_xc']),'Finance','finance'))
        if 'mining' in self.selected:result.append(('Mines','Energy '+n(p['mining_energy']),'Level '+n(p['mining_level']),'Mine','mining'))
        if 'production' in self.selected:
            state=journey.activity(db,uid)
            result.append(('Production','Ready '+n(state['ready']),'Queue '+n(state['active'])+'/'+n(svip.production_limit(db,uid)),'Production','production'))
            extra.append(('Craft','Products '+n(state['products']),'View recipes','Craft','craft'))
        if 'market' in self.selected:
            cutoff=int(time.time())-max(1,svip.setting(db,'tier6_market_expiry_days',7))*86400
            count=db.execute('SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1 AND quantity>0 AND created_at>?',(uid,cutoff)).fetchone()[0]
            shares=db.execute('SELECT COALESCE(SUM(quantity),0) FROM tier6_stock_holdings WHERE user_id=?',(uid,)).fetchone()[0]
            result.append(('Market','Listings '+n(count),'Limit '+n(svip.market_limit(db,uid)),'My Listings','market_mine'))
            extra.append(('Stocks','Shares '+n(shares),'Virtual only','Stocks','stock'))
        if 'nation' in self.selected:
            cities=db.execute('SELECT COUNT(*) FROM player_cities WHERE user_id=?',(uid,)).fetchone()[0]
            result.append(('Nation','Cities '+n(cities),n(p['money'])+' WC','Warfront','war'))
        if 'missions' in self.selected:
            import tier5
            rows=[m for category in ('starter','daily','weekly') for m in tier5.missions_for(db,uid,category)[1] if not m['claimed']]
            ready=sum(m['progress']>=m['target'] for m in rows)
            result.append(('Missions','Ready '+n(ready),'Pending '+n(len(rows)-ready),'Missions','missions'))
        if 'vip' in self.selected:
            paid=db.execute('SELECT expires_at FROM casino_vip_members WHERE user_id=?',(uid,)).fetchone()
            status='SVIP active' if svip.summary(db,uid) else 'VIP active' if paid and paid[0]>int(time.time()) else 'Check status'
            extra.append(('VIP',status,'View perks','VIP Status','vip'))
        return result+extra

    def section(self,key,p):
        db,uid=self.db,self.owner
        if key=='wallet':return f"Wallet **{p['xc']:,} XC** · Bank **{p['bank_xc']:,} XC**",[('Finance','finance')]
        if key=='mining':return f"Lv **{p['mining_level']}** · Energy **{p['mining_energy']}** (last recorded)",[('Mines','mining')]
        if key=='production':
            state=journey.activity(db,uid)
            return f"Ready **{state['ready']}** · Queue **{state['active']}/{svip.production_limit(db,uid)}**\nCrafted products **{state['products']}**",[('Craft','craft'),('Production','production')]
        if key=='market':
            cutoff=int(time.time())-max(1,svip.setting(db,'tier6_market_expiry_days',7))*86400
            listings=db.execute('SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND active=1 AND quantity>0 AND created_at>?',(uid,cutoff)).fetchone()[0]
            shares=db.execute('SELECT COALESCE(SUM(quantity),0) FROM tier6_stock_holdings WHERE user_id=?',(uid,)).fetchone()[0]
            return f"Listings **{listings}/{svip.market_limit(db,uid)}** · Virtual shares **{shares:,}**",[('My Listings','market_mine'),('Stocks','stock')]
        if key=='nation':
            cities=db.execute('SELECT COUNT(*) FROM player_cities WHERE user_id=?',(uid,)).fetchone()[0]
            return f"{discord.utils.escape_markdown(p['nation_name'] or 'No Nation')}\nCities **{cities}** · War Credits **{p['money']:,}**",[('Warfront','war')]
        if key=='missions':
            import tier5
            rows=[m for category in ('starter','daily','weekly') for m in tier5.missions_for(db,uid,category)[1] if not m['claimed']]
            ready=sum(m['progress']>=m['target'] for m in rows)
            return f"Ready to claim **{ready}** · Unfinished **{len(rows)-ready}**",[('Missions','missions')]
        status=svip.summary(db,uid)
        if not status:
            vip=db.execute('SELECT expires_at FROM casino_vip_members WHERE user_id=?',(uid,)).fetchone()
            status=f"Casino VIP until <t:{vip[0]}:R>" if vip and vip[0]>int(time.time()) else 'SVIP unverified · Check in server'
        return status,[('VIP Status','vip')]

    async def act(self,i,action):
        key=action[0]
        if key=='close':
            self.stop();v=discord.ui.LayoutView();v.add_item(discord.ui.TextDisplay('Overview closed. Open /overview to return.'))
        elif key=='customize':v=OverviewView(self.bot,self.db,self.owner,self.page,editing=True)
        elif key=='defaults':v=OverviewView(self.bot,self.db,self.owner,self.page,editing=True,draft=DEFAULT)
        elif key=='save':
            try:save_layout(self.db,self.owner,self.selected)
            except (ValueError,sqlite3.Error):
                await i.response.send_message('Preferences were not saved. Refresh and try again.',ephemeral=True);return
            v=OverviewView(self.bot,self.db,self.owner,self.page,notice='Overview saved. No assets changed.')
        elif key in {'cancel','refresh'}:v=self.fresh()
        elif key=='page':v=OverviewView(self.bot,self.db,self.owner,action[1])
        elif key=='upgrade':
            _,item=economy_progress.growth(self.db,self.owner)
            if item and not item['owned']:
                from economy_trade_ui import TradeView
                v=TradeView(self.bot,self.db,self.owner,'shop',item['id'],back=self.fresh)
            else:
                await self.act(i,('nav','inventory' if item else 'research'));return
        elif key in {'continue','goal'}:
            route=journey.next_step(self.db,self.owner)[1] if key=='continue' else {'page':'activity'}
            if key=='continue' and route['page']=='activity' and journey.selected_goal(self.db,self.owner)=='earn' and not journey.activity(self.db,self.owner)['ready']:
                await self.act(i,('nav','mining'));return
            v=journey.JourneyView(self.bot,self.db,self.owner,**route,back=self.fresh)
        elif key in {'nav','menu'}:
            dest='menu' if key=='menu' else action[1]
            v=self.bot.xbot_system_page_builder(self.owner,dest,member=i.user)
            if key=='nav':
                button=next((x for x in v.walk_children() if getattr(x,'key',None)=='back'),None)
                if button:button.callback=OverviewButton(self,'Back',('refresh',)).callback
                elif v.total_children_count<=38:v.add_item(discord.ui.ActionRow(OverviewButton(self,'Back to Overview',('refresh',))))
        else:return
        await i.response.edit_message(view=v)

class OverviewButton(discord.ui.Button):
    def __init__(self,v,label,action,style=discord.ButtonStyle.secondary):
        super().__init__(label=label,style=style,disabled=action[0]=='page' and not 0<=action[1]<v.pages)
        self.v,self.action=v,action
    @svip.interaction_context
    async def callback(self,i):
        if await self.v.interaction_check(i):await self.v.act(i,self.action)

class LayoutSelect(discord.ui.Select):
    def __init__(self,v):
        self.v=v
        super().__init__(placeholder='Choose sections to display',min_values=0,max_values=len(BLOCKS),options=[discord.SelectOption(label=label,value=key,default=key in v.selected) for key,label in BLOCKS.items()])
    @svip.interaction_context
    async def callback(self,i):
        if not await self.v.interaction_check(i):return
        if any(k not in BLOCKS for k in self.values):return
        await i.response.edit_message(view=OverviewView(self.v.bot,self.v.db,self.v.owner,self.v.page,editing=True,draft=self.values))
