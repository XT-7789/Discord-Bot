"""Compact player navigation. Existing game callbacks retain their rules."""
import discord
import tier5
import tier6
import war_tier
import casino
import economy
import os
import functools
import time
import re
import logging
from pathlib import Path
from contextvars import ContextVar

GUIDE_IMAGE=Path(__file__).parent/'assets'/'ui'/'navigation-guide.png'
HELP_TOPICS={
    'earn':('How do I earn XC?','Economy > Earn','Collect Daily rewards and complete Contracts. Mining materials can also be sold for XC.','earn_menu'),
    'casino':('Where can I play Casino games?','Economy > Casino','Choose a game, review its bet range, then submit one round. XC is fictional currency; you can lose your stake.','casino'),
    'items':('Where are my items?','Profile > Backpack','Select an item to see its quantity and available Use, Equip or Sell actions.','inventory'),
    'cities':('How do I build or upgrade Cities?','Warfront > Cities','Open Build or Upgrade. Choose one or several Cities or available locations and review the cost before confirming.','city'),
    'rewards':('Where do I claim mission rewards?','Missions > Starter / Daily / Weekly','Choose a category and press Claim Ready after completing its goals. Weekly missions unlock at Nation Level 2.','missions'),
    'bank':('How do I deposit or withdraw XC?','Economy > Finance','Press Deposit or Withdraw below your balances. Neither action creates extra XC.','finance'),
    'trade':('Where are Shop, Market and Stocks?','Economy > Market','Shop sells bot items; Player Market contains player listings. Stocks shows the fictional market and your holdings.','market_menu'),
    'army':('Where do I recruit an army?','Warfront > Recruit','Select a unit, enter a quantity and review the War Credit cost. Army shows your existing forces.','recruit'),
    'craft':('Where are crafting and research?','Economy > Earn','Craft makes items; Production manages queued jobs; Research improves existing activities.','earn_menu'),
    'cooldown':('Why do I still wait with SVIP?','Economy > Casino','SVIP reduces the configured cooldown; it does not normally remove it. Results show your actual tier, cooldown and next-round time.','casino'),
}


async def report_panel_error(interaction,error):
    """Best-effort private guidance; never retry a game or financial action."""
    logger=logging.getLogger('xbot.player_ui')
    logger.error('Player panel failed: %s',type(error).__name__,exc_info=(type(error),error,error.__traceback__))
    if isinstance(error,discord.HTTPException) and error.code==10062:
        # The interaction token itself has expired; another reply cannot fix it.
        return
    message=('⚠️ This action could not finish normally. Open /menu to refresh your panel. '
             'If this involved a payment or reward, check your balance and items before trying again. '
             'The issue has been logged for the administrator.')
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message,ephemeral=True)
        else:
            await interaction.response.send_message(message,ephemeral=True)
    except discord.HTTPException:
        logger.warning('Could not deliver player panel error guidance.')


def register(bot, db, create_player):
    navigation=ContextVar('player_panel_history',default=())
    builders=bot.xbot_player_panel_builders
    legacy=dict(builders)
    bot.xbot_legacy_easy_lobby_builder=bot.xbot_player_lobby_builder
    legacy.update({
        'war_overview':legacy['war'], 'economy_overview':legacy['economy'],
        'attack':bot.xbot_tier7_attack_builder,
        'defence':bot.xbot_tier7_defence_builder,
        'reports':bot.xbot_tier7_reports_builder,
    })
    sections={
        'menu':('MAIN MENU','Select a system.', [('👤 Profile','profile'),('💰 Economy','economy'),('⚔️ Warfront','war'),('🎯 Missions','missions')]),
        'economy':('ECONOMY','Earn, trade and play.', [('💳 Finance','finance'),('💼 Earn','earn_menu'),('📊 Market','market_menu'),('⛏️ Mines','mining'),('🎰 Casino','casino'),('🏆 Rankings','rankings')]),
        'finance':('FINANCE','Your wallet and savings.', [('💰 Wallet','wallet'),('🏦 Bank','bank'),('📋 Assets','assets'),('💱 Exchange','exchange')]),
        'earn_menu':('EARN','Collect rewards or make something.', [('🎁 Daily','daily'),('💼 Contracts','contracts'),('⛏️ Mines','mining'),('🛠️ Craft','craft'),('🏭 Production','production'),('🔬 Research','research')]),
        'market_menu':('MARKET','Browse before you spend.', [('🛒 Shop','shop'),('🤝 Player Market','market'),('📈 Stocks','stock'),('🎒 Backpack','inventory')]),
        'war':('WARFRONT','Your nation and armed forces.', [('🏙️ Cities','city'),('🪖 Army','army'),('➕ Recruit','recruit'),('🕊️ Diplomacy','diplomacy'),('⚔️ Attack','attack'),('🛡️ Defence','defence'),('📋 Reports','reports'),('🗺️ Overview','war_overview')]),
        'profile':('PROFILE','', [('🎒 Backpack','inventory'),('📋 Assets','assets'),('🎯 Missions','missions')]),
        'missions':('MISSIONS','Choose your goals.', [('🌱 Starter','mission_starter'),('☀️ Daily','mission_daily'),('📅 Weekly','mission_weekly')]),
    }
    parents={'menu':'menu','profile':'menu','economy':'menu','war':'menu','missions':'menu',
             'finance':'economy','earn_menu':'economy','market_menu':'economy','rankings':'economy',
             'daily':'earn_menu','wallet':'finance','bank':'finance','assets':'profile','exchange':'finance',
             'inventory':'profile','contracts':'earn_menu','mining':'economy','craft':'earn_menu',
             'production':'earn_menu','research':'earn_menu','shop':'market_menu','market':'market_menu',
             'stock':'market_menu','casino':'economy','city':'war','army':'war','recruit':'war',
             'diplomacy':'war','attack':'war','defence':'war','reports':'war','war_overview':'war',
             'mission_starter':'missions','mission_daily':'missions','mission_weekly':'missions'}
    parents.update({'help':'menu','help_map':'help',**{'help:'+key:'help' for key in HELP_TOPICS}})

    class Nav(discord.ui.Button):
        def __init__(self,owner,label,key):
            super().__init__(label=label,style=discord.ButtonStyle.secondary)
            self.owner,self.key=owner,key
        async def callback(self,i):
            if i.user.id!=self.owner:
                await i.response.send_message('Open /menu for your own menu.',ephemeral=True)
                return
            await i.response.defer()
            history=getattr(self,'history',('menu',))
            destination=self.key
            if destination=='back':
                history=history[:-1] or ('menu',)
                destination=history[-1]
            token=navigation.set(history)
            try:
                attachments=[discord.File(GUIDE_IMAGE,filename='navigation-guide.png')] if destination=='help_map' and GUIDE_IMAGE.is_file() else []
                await i.edit_original_response(view=page(self.owner,destination,member=i.user),attachments=attachments)
            finally:
                navigation.reset(token)

    class HelpSelect(discord.ui.Select):
        def __init__(self,owner):
            super().__init__(placeholder='What would you like to do?',options=[discord.SelectOption(label=topic[0],value=key) for key,topic in HELP_TOPICS.items()])
            self.owner=owner
        async def callback(self,i):
            await i.response.defer()
            token=navigation.set(getattr(self,'history',('help',)))
            try:
                await i.edit_original_response(view=page(self.owner,'help:'+self.values[0]),attachments=[])
            finally:
                navigation.reset(token)

    class Shell(discord.ui.LayoutView):
        def __init__(self,owner,title,body='',notice=''):
            super().__init__(timeout=900)
            self.owner=owner
            self.system_prepared=True
            self.box=discord.ui.Container(accent_color=discord.Color(0x41D9D0))
            self.box.add_item(discord.ui.TextDisplay(f'-# ✦ X SYSTEM\n## {title}'+ ('\n'+body if body else '')+ ('\n\n'+notice if notice else '')))
            self.add_item(self.box)
        async def interaction_check(self,i):
            if i.user.id==self.owner:
                return True
            await i.response.send_message('Open /menu for your own menu.',ephemeral=True)
            return False
        async def on_error(self,i,error,item):
            await report_panel_error(i,error)

    def footer(view,owner,key,include_back=True):
        row=discord.ui.ActionRow()
        if key!='menu':
            if include_back:
                row.add_item(Nav(owner,'‹ Back','back'))
            row.add_item(Nav(owner,'⌂ Menu','menu'))
        if key=='menu':
            row.add_item(Nav(owner,'? Help','help'))
        row.add_item(Nav(owner,'× Close','close'))
        view.add_item(row)

    def page(owner,key='menu',notice='',member=None):
        history=navigation.get()
        history=('menu',) if key=='menu' else history if history and history[-1]==key else (*history,key)
        token=navigation.set(history[-40:])
        try:
            view=render_page(owner,key,notice,member)
            align_economy_tabs(view,owner,key)
            if key in {'menu','economy'}:
                # Overview colour emphasises system entry points, not banking.
                main_routes={'profile','economy','war','missions'} if key=='menu' else {'finance','market_menu','casino','earn_menu'}
                for child in view.walk_children():
                    if isinstance(child,discord.ui.Button):
                        if getattr(child,'action',None) in {'deposit','withdraw'}:
                            child.style=discord.ButtonStyle.secondary
                        elif isinstance(child,Nav) and child.key in main_routes and not getattr(child,'economy_tab',False):
                            child.style=discord.ButtonStyle.primary
                        if isinstance(child,Nav) and child.label in {'Continue','View Rewards'}:
                            child.style=discord.ButtonStyle.success
            view.system_history=navigation.get()
            for child in view.walk_children():
                if isinstance(child,(Nav,HelpSelect)):
                    child.history=view.system_history
            return view
        finally:
            navigation.reset(token)

    def progress_bar(value,total):
        filled=min(10,max(0,int(10*value/max(1,total))))
        return '▰'*filled+'▱'*(10-filled)

    def overview_block(view,title,body,buttons):
        view.box.add_item(discord.ui.Separator())
        view.box.add_item(discord.ui.TextDisplay(f'### {title}\n{body}'))
        if buttons:
            view.box.add_item(discord.ui.ActionRow(*buttons))

    def align_economy_tabs(view,owner,key):
        tabs=[('Overview','economy'),('Earn','contracts'),('Trade','market_menu'),('Production','production'),('Stocks','stock')]
        if key not in {dest for _,dest in tabs} or getattr(view,'economy_tabs_aligned',False):
            return
        box=next((x for x in view.children if isinstance(x,discord.ui.Container)),None)
        if box is None:
            return
        # Remove the old five-tab strip before inserting the shared one.
        old_actions={'economy_v2','earn','trade','production','stock'}
        for row in list(box.children):
            if isinstance(row,discord.ui.ActionRow) and len(row.children)==5 and {getattr(x,'action',None) for x in row.children}==old_actions:
                box.remove_item(row)
        # This shortcut duplicates the Earn tab; keep Assets and Refresh.
        for row in list(box.children):
            if isinstance(row,discord.ui.ActionRow):
                for child in list(row.children):
                    if isinstance(child,Nav) and child.label=='Earn & Create':
                        row.remove_item(child)
        items=list(box.children)
        if not items or not isinstance(items[0],discord.ui.TextDisplay):
            return
        header=items[0]
        # All five screens share the same brand/title hierarchy; their data
        # blocks remain untouched below the navigation.
        lines=header.content.split('\n')
        title_index=next((n for n,line in enumerate(lines) if line.startswith('## ')),None)
        if title_index is None:
            return
        rest='\n'.join(lines[title_index+1:]).strip()
        if rest=='OVERVIEW':
            rest=''
        extra=6+(1 if rest else 0)
        if view.total_children_count+extra>40:
            return
        header.content='-# ✦ X SYSTEM / ECONOMY\n'+lines[title_index]
        row=discord.ui.ActionRow()
        for label,dest in tabs:
            button=Nav(owner,label,dest)
            button.style=discord.ButtonStyle.primary if dest==key else discord.ButtonStyle.secondary
            button.economy_tab=True
            row.add_item(button)
        for child in items:
            box.remove_item(child)
        box.add_item(header)
        box.add_item(row)
        if rest:
            box.add_item(discord.ui.TextDisplay(rest))
        for child in items[1:]:
            box.add_item(child)
        view.economy_tabs_aligned=True

    def render_page(owner,key='menu',notice='',member=None):
        player=db.execute('SELECT * FROM players WHERE user_id=?',(owner,)).fetchone()
        if key=='mission_weekly' and tier5.profile_summary(db,owner)['level']<2:
            return page(owner,'missions','Weekly missions unlock at Nation Level 2.')
        if key=='close':
            view=Shell(owner,'CLOSED','Panel closed. /menu opens a new menu.')
            return view
        if key=='vip':
            active=db.execute('SELECT expires_at FROM casino_vip_members WHERE user_id=?',(owner,)).fetchone()
            expires=int(active['expires_at']) if active else 0
            paid_active=expires>int(time.time())
            if member is not None and hasattr(member,'roles'):
                info=casino.cooldown_info(db,member,'blackjack')
                body=f"### {info['tier']}\nCasino cooldown reduction: **{info['reduction']}%**"
            else:
                body=f"### {'VIP' if paid_active else 'No active Casino VIP'}\nServer SVIP role cannot be checked here. Open this panel inside your server."
            body+=f'\nCasino VIP expires <t:{expires}:R>.' if paid_active else '\nCasino VIP subscription: inactive.'
            view=Shell(owner,'💎 VIP Status',body+'\n-# Status check only · No XC charged.')
            footer(view,owner,key)
            return view
        if key=='help':
            view=Shell(owner,'HELP / QUICK FIND','What would you like to do?\nChoose a question for directions and a direct link.')
            view.box.add_item(discord.ui.ActionRow(HelpSelect(owner)))
            view.box.add_item(discord.ui.ActionRow(Nav(owner,'▧ Visual Guide','help_map')))
            footer(view,owner,key)
            return view
        if key.startswith('help:') and key[5:] in HELP_TOPICS:
            question,path,answer,destination=HELP_TOPICS[key[5:]]
            view=Shell(owner,'HELP',f'**{question}**\n`Menu > {path}`\n\n{answer}')
            view.box.add_item(discord.ui.ActionRow(Nav(owner,'Open Panel',destination)))
            footer(view,owner,key)
            return view
        if key=='help_map':
            view=Shell(owner,'VISUAL GUIDE','Tap the image to zoom. Use Help questions for direct links.')
            if GUIDE_IMAGE.is_file():
                view.box.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem('attachment://navigation-guide.png',description='X SYSTEM navigation: Profile, Economy, Warfront and Missions.')))
            else:
                view.box.add_item(discord.ui.TextDisplay('The image is unavailable. All directions remain available in Help.'))
            footer(view,owner,key)
            return view
        if key=='finance':
            view=Shell(owner,'💳 Finance','OVERVIEW',notice)
            overview_block(view,'💰 Wallet',f"## {player['xc']:,} XC\nAvailable to spend",[bot.xbot_finance_button_builder(owner,'deposit')])
            overview_block(view,'🏦 Bank',f"## {player['bank_xc']:,} XC\nWithdraw to your Wallet",[bot.xbot_finance_button_builder(owner,'withdraw')])
            overview_block(view,'📋 Assets',f"Total XC **{player['xc']+player['bank_xc']:,}**\nWar Credits **{player['money']:,}**\nXCrystals **{player['xcrystals']:,}**",[Nav(owner,'View Assets','assets'),Nav(owner,'Exchange','exchange')])
            footer(view,owner,key)
            return prepare(view,owner,key,force=True)
        if key=='assets':
            data=tier6.economy_summary(db,owner)
            view=Shell(owner,'💼 Assets','OVERVIEW',notice)
            overview_block(view,'Estimated Net Worth',f"## {data['net_worth']:,} XC\nIncludes item sell-back and current stock values.",[])
            overview_block(view,'💳 Accounts',f"Wallet **{player['xc']:,} XC**\nBank **{player['bank_xc']:,} XC**",[Nav(owner,'Finance','finance')])
            overview_block(view,'🎒 Items',f"## {data['inventory_value']:,} XC\nEstimated sell-back value",[Nav(owner,'Backpack','inventory')])
            overview_block(view,'📈 Investments',f"Stock value **{data['stock_value']:,} XC**\nCost **{data['stock_cost']:,} XC**\nUnrealised P/L **{data['stock_value']-data['stock_cost']:+,} XC**",[Nav(owner,'Stock Market','stock')])
            holdings=data['holdings']
            if holdings:
                lines=[f"**{discord.utils.escape_markdown(r['symbol'])}** ×{r['quantity']:,} · {int(r['quantity'])*int(r['price']):,} XC" for r in holdings[:12]]
                overview_block(view,'Portfolio','\n'.join(lines)+ ('\nOpen Stock Market for all holdings.' if len(holdings)>12 else ''),[])
            else:
                view.box.add_item(discord.ui.TextDisplay('No stock holdings yet.'))
            footer(view,owner,key)
            return view
        if key in sections:
            title,body,links=sections[key]
            if key in {'menu','profile'}:
                profile=tier5.profile_summary(db,owner)
                body=f"**{discord.utils.escape_markdown(player['nation_name'])}** · Lv {profile['level']}\n{player['xc']:,} XC"
                if key=='profile':
                    body+=f" · {player['money']:,} WC\n{profile['rank']} · {profile['xp']:,} XP"
            if key=='missions':
                lines=[]
                for category in ('starter','daily','weekly'):
                    _,missions=tier5.missions_for(db,owner,category)
                    ready=sum(not m['claimed'] and m['progress']>=m['target'] for m in missions)
                    lines.append(f'{category.title()} · {ready} ready')
                body='\n'.join(lines)
            view=Shell(owner,title,body,notice)
            if key=='menu':
                xp=profile['xp']-profile['current_floor']
                span=(profile['next_threshold'] or profile['xp'])-profile['current_floor']
                xp_text=f"{progress_bar(xp,span)} {xp:,} / {span:,} XP" if profile['next_threshold'] else 'MAX LEVEL'
                view=Shell(owner,'MAIN MENU','OVERVIEW',notice)
                def block(text,buttons):
                    view.box.add_item(discord.ui.Separator())
                    view.box.add_item(discord.ui.TextDisplay(text))
                    view.box.add_item(discord.ui.ActionRow(*buttons))
                block(f"### 👤 Profile\n**{discord.utils.escape_markdown(player['nation_name'])}** · Lv **{profile['level']}**\n{xp_text}",[Nav(owner,'Profile','profile'),Nav(owner,'VIP Status','vip')])
                block(f"### 💰 Economy\nWallet **{player['xc']:,} XC** · Bank **{player['bank_xc']:,} XC**",[Nav(owner,'Economy','economy'),Nav(owner,'Finance','finance'),Nav(owner,'Casino','casino')])
                cities=db.execute('SELECT COUNT(*) FROM player_cities WHERE user_id=?',(owner,)).fetchone()[0]
                lands=db.execute('SELECT COUNT(*) FROM map_territories WHERE owner_user_id=?',(owner,)).fetchone()[0]
                units=db.execute('SELECT COALESCE(SUM(quantity),0) FROM player_war_units WHERE user_id=?',(owner,)).fetchone()[0]
                state=db.execute('SELECT last_collect FROM player_city_state WHERE user_id=?',(owner,)).fetchone()
                ready_at=(int(state['last_collect']) if state else 0)+war_tier.setting(db,'city_collect_cooldown')
                production='Production ready' if ready_at<=int(time.time()) else f'Production <t:{ready_at}:R>'
                block(f"### ⚔️ Warfront\nCities **{cities}** · Land **{lands}** · Units **{units}**\nWar Credits **{player['money']:,}** · {production}",[Nav(owner,'Warfront','war'),Nav(owner,'Cities','city'),Nav(owner,'Army','army')])
                pending=[(category,m) for category in ('daily','starter') for m in tier5.missions_for(db,owner,category)[1] if not m['claimed']]
                ready=next(((c,m) for c,m in pending if m['progress']>=m['target']),None)
                chosen=ready or next(((c,m) for c,m in pending if m['destination'] in ('mining','economy','city')),None)
                if chosen:
                    category,m=chosen
                    target='mission_'+category if ready else {'economy':'daily'}.get(m['destination'],m['destination'])
                    text=f"### 🎯 Missions\n**{m['title']}** · {m['progress']} / {m['target']}\n{'Reward ready · ' if ready else ''}+{m['xc']} XC · +{m['credits']} WC · +{m['xp']} XP"
                    actions=[Nav(owner,'Missions','missions'),Nav(owner,'View Rewards' if ready else 'Continue',target)]
                else:
                    text='### 🎯 Missions\nAll featured goals complete. Explore at your own pace.'
                    actions=[Nav(owner,'Missions','missions')]
                block(text,actions)
                footer(view,owner,key)
                return prepare(view,owner,key,force=True)
            if key in {'economy','profile','war','missions','earn_menu','market_menu'}:
                view=Shell(owner,title,'OVERVIEW',notice)
                def navs(*entries):
                    return [Nav(owner,label,dest) for label,dest in entries]
                def block(title,body,buttons):
                    overview_block(view,title,body,buttons)
                def funds():
                    return f"Wallet **{player['xc']:,} XC** · Bank **{player['bank_xc']:,} XC**"
                def earning():
                    ready_at=int(player['last_daily'])+economy.setting(db,'daily_cooldown')
                    ready=ready_at<=int(time.time())
                    daily=bot.xbot_overview_daily_button_builder(owner,key)
                    daily.disabled=not ready
                    daily.label='Claim Daily' if ready else 'Daily Collected'
                    maximum=economy.setting(db,'mining_max_energy')
                    elapsed=max(0,int(time.time())-int(player['mining_energy_updated']))
                    energy=min(maximum,int(player['mining_energy'])+(elapsed//max(1,economy.setting(db,'mining_energy_regen_seconds')))*economy.setting(db,'mining_energy_regen_amount'))
                    status='Daily **Ready**' if ready else f'Daily <t:{ready_at}:R>'
                    energy_text=f'Energy **{energy}/{maximum}**' if economy.setting(db,'mining_energy_enabled') else 'Energy costs off'
                    block('💼 Earn',f'{status} · {energy_text}',[daily,*navs(('Mines','mining'),('Contracts','contracts'))])
                if key=='economy':
                    import economy_journey
                    for component in economy_journey.goal_block(bot,db,owner):
                        if isinstance(component,discord.ui.ActionRow):
                            component.add_item(Nav(owner,'Refresh','economy'))
                        view.box.add_item(component)
                    # Keep the overview under Discord's 40-component limit while retaining every shortcut.
                    view.box.add_item(discord.ui.TextDisplay('### 💳 Finance\n'+funds()))
                    view.box.add_item(discord.ui.ActionRow(bot.xbot_finance_button_builder(owner,'deposit',key),bot.xbot_finance_button_builder(owner,'withdraw',key),*navs(('Finance','finance'))))
                    earning()
                    block('📊 Market','Items · Player trading · Stocks',navs(('Market','market_menu'),('Shop','shop'),('Stocks','stock')))
                    membership=casino.cooldown_info(db,member,'blackjack')['tier'] if member is not None and hasattr(member,'roles') else 'Check VIP Status'
                    block('🎰 Casino',f'Membership **{membership}** · XC stakes can be lost.',navs(('Casino','casino'),('VIP Status','vip'),('Rankings','rankings')))
                elif key=='profile':
                    span=(profile['next_threshold'] or profile['xp'])-profile['current_floor']
                    xp=profile['xp']-profile['current_floor']
                    status=f'{progress_bar(xp,span)} {xp}/{span} XP' if profile['next_threshold'] else 'MAX LEVEL'
                    block('👤 Player',f"**{discord.utils.escape_markdown(player['nation_name'])}** · Lv {profile['level']}\n{profile['rank']}\n{status}",navs(('Missions','missions')))
                    block('💰 Accounts',funds()+f"\nWar Credits **{player['money']:,}** · XCrystals **{player['xcrystals']:,}**",navs(('Finance','finance'),('VIP Status','vip')))
                    count=db.execute('SELECT COALESCE(SUM(quantity),0) FROM inventories WHERE user_id=?',(owner,)).fetchone()[0]
                    block('🎒 Collection',f'Backpack **{count:,} items**',navs(('Backpack','inventory'),('Assets','assets')))
                elif key=='war':
                    cities=db.execute('SELECT COUNT(*) FROM player_cities WHERE user_id=?',(owner,)).fetchone()[0]
                    lands=db.execute('SELECT COUNT(*) FROM map_territories WHERE owner_user_id=?',(owner,)).fetchone()[0]
                    units=db.execute('SELECT COALESCE(SUM(quantity),0) FROM player_war_units WHERE user_id=?',(owner,)).fetchone()[0]
                    block('🏙️ Nation',f"Cities **{cities}** · Land **{lands}**\nWar Credits **{player['money']:,}**",navs(('Cities','city'),('Overview','war_overview')))
                    block('🪖 Forces',f'Units **{units:,}**',navs(('Army','army'),('Recruit','recruit')))
                    block('⚔️ Operations','Choose a target and review before launching.',navs(('Diplomacy','diplomacy'),('Attack','attack'),('Defence','defence')))
                    block('📋 Intelligence','Review previous operations.',navs(('Reports','reports')))
                elif key=='missions':
                    level=tier5.profile_summary(db,owner)['level']
                    for category in ('starter','daily','weekly'):
                        _,items=tier5.missions_for(db,owner,category)
                        claimed=sum(m['claimed'] for m in items)
                        ready=sum(not m['claimed'] and m['progress']>=m['target'] for m in items)
                        pending=next((m for m in items if not m['claimed']),None)
                        body=f'{progress_bar(claimed,len(items))} {claimed}/{len(items)} claimed · **{ready} ready**'
                        if pending:
                            body+=f"\n{pending['title']} · {pending['progress']}/{pending['target']}"
                        button=Nav(owner,'View '+category.title(),'mission_'+category)
                        if category=='weekly' and level<2:
                            body='Unlocks at Nation Level 2.'
                            button.disabled=True
                        block(category.title(),body,[button])
                elif key=='earn_menu':
                    earning()
                    block('🛠️ Workshop','Craft items and manage production.',navs(('Craft','craft'),('Production','production')))
                    block('🔬 Development',f"War Credits **{player['money']:,}**",navs(('Research','research'),('Cities','city')))
                elif key=='market_menu':
                    block('🛒 Shopping',funds(),navs(('Shop','shop'),('Player Market','market')))
                    block('📈 Investments','Review quotes and holdings before trading.',navs(('Stocks','stock'),('Assets','assets')))
                    count=db.execute('SELECT COALESCE(SUM(quantity),0) FROM inventories WHERE user_id=?',(owner,)).fetchone()[0]
                    block('🎒 Inventory',f'Backpack **{count:,} items**',navs(('Backpack','inventory')))
                footer(view,owner,key)
                return prepare(view,owner,key,force=True)
            for n in range(0,len(links),2):
                row=discord.ui.ActionRow()
                for label,dest in links[n:n+2]:
                    button=Nav(owner,label,dest)
                    if dest=='mission_weekly' and tier5.profile_summary(db,owner)['level']<2:
                        button.disabled=True
                        button.label='📅 Weekly · Lv 2'
                    row.add_item(button)
                view.box.add_item(row)
            footer(view,owner,key)
            return view
        if key=='daily':
            settings={r['key']:int(r['value']) for r in db.execute("SELECT key,value FROM economy_settings WHERE key IN ('daily_reward','daily_cooldown')")}
            ready_at=int(player['last_daily'])+settings['daily_cooldown']
            ready=ready_at<=int(time.time())
            status='Ready to collect.' if ready else f'Next reward <t:{ready_at}:R>.'
            view=Shell(owner,'DAILY',f"Wallet · {player['xc']:,} XC\nReward · {settings['daily_reward']} XC\n{status}",notice)
            daily=bot.xbot_daily_button_builder(owner)
            daily.disabled=not ready
            view.box.add_item(discord.ui.ActionRow(daily))
            footer(view,owner,key)
            return prepare(view,owner,key,force=True)
        if key=='rankings':
            rows=db.execute('SELECT p.nation_name,s.total_won-s.total_wagered net FROM casino_stats s JOIN players p ON p.user_id=s.user_id ORDER BY net DESC LIMIT 10').fetchall()
            body='Casino · net results\n'+('\n'.join(f"{n}. {discord.utils.escape_markdown(r['nation_name'])} · {r['net']:+,} XC" for n,r in enumerate(rows,1)) or 'No games recorded yet.')
            view=Shell(owner,'RANKINGS',body)
            footer(view,owner,key)
            return view
        factory=legacy.get(key)
        if factory:
            return prepare(factory(owner),owner,key)
        return page(owner,parents.get(key,'menu'),'This panel is unavailable right now.')

    # Wrap only player UI output, leaving native Discord interactions and game
    # state intact. Subpages and modal results inherit navigation from the page
    # that opened them; text-only and legacy map attachments pass through.
    def style_output(kwargs,owner,key):
        if isinstance(kwargs.get('view'),discord.ui.LayoutView):
            kwargs['view']=prepare(kwargs['view'],owner,key)
        # Maps need image embeds. Keep attachments/fields intact, but use the
        # same system identity rather than a separate old-looking card.
        if isinstance(kwargs.get('embed'),discord.Embed):
            embed=kwargs['embed'].copy()
            embed.set_author(name='✦ X SYSTEM')
            kwargs['embed']=embed
        return kwargs

    class Output:
        def __init__(self,target,owner,key):
            self.target,self.owner,self.key=target,owner,key
        def __getattr__(self,name):
            return getattr(self.target,name)
        def options(self,kwargs):
            return style_output(kwargs,self.owner,self.key)
        async def send_message(self,*args,**kwargs):
            return await self.target.send_message(*args,**self.options(kwargs))
        async def edit_message(self,*args,**kwargs):
            return await self.target.edit_message(*args,**self.options(kwargs))
        async def send(self,*args,**kwargs):
            return await self.target.send(*args,**self.options(kwargs))
        async def send_modal(self,modal):
            if not getattr(modal,'system_wrapped',False):
                original=modal.on_submit
                history=navigation.get()
                async def submit(i):
                    if i.user.id!=self.owner:
                        await i.response.send_message('Open /menu for your own panel.',ephemeral=True)
                        return
                    token=navigation.set(history)
                    try:
                        await original(Interaction(i,self.owner,self.key))
                    finally:
                        navigation.reset(token)
                modal.on_submit=submit
                modal.on_error=report_panel_error
                modal.system_wrapped=True
            return await self.target.send_modal(modal)

    class Message:
        def __init__(self,actual,owner,key):
            self.actual,self.owner,self.key=actual,owner,key
        def __getattr__(self,name):
            return getattr(self.actual,name)
        async def edit(self,**kwargs):
            return await self.actual.edit(**style_output(kwargs,self.owner,self.key))

    class Interaction:
        def __init__(self,actual,owner,key):
            self.actual,self.owner,self.key=actual,owner,key
            self.response=Output(actual.response,owner,key)
        @property
        def followup(self):
            return Output(self.actual.followup,self.owner,self.key)
        @property
        def message(self):
            actual=self.actual.message
            return Message(actual,self.owner,self.key) if actual is not None else None
        async def original_response(self):
            return Message(await self.actual.original_response(),self.owner,self.key)
        def __getattr__(self,name):
            return getattr(self.actual,name)
        async def edit_original_response(self,**kwargs):
            return await self.actual.edit_original_response(**style_output(kwargs,self.owner,self.key))

    def prepare(view,owner,key,force=False):
        if isinstance(view,discord.ui.LayoutView) and not hasattr(view,'system_history'):
            view.system_history=navigation.get() or (key,)
        if not isinstance(view,discord.ui.LayoutView) or (isinstance(view,Shell) and not force):
            return view
        signature=tuple(id(child) for child in view.walk_children())
        if getattr(view,'system_signature',None)==signature:
            return view
        view.system_prepared=True
        async def panel_error(i,error,item):
            await report_panel_error(i,error)
        view.on_error=panel_error
        check=getattr(view,'system_original_check',view.interaction_check)
        view.system_original_check=check
        async def owner_check(i):
            if i.user.id!=owner:
                await i.response.send_message('Open /menu for your own panel.',ephemeral=True)
                return False
            return await check(i)
        view.interaction_check=owner_check
        can_footer=view.total_children_count<=34 and getattr(view,'finished',None) is not False
        if can_footer:
            for child in list(view.walk_children()):
                if isinstance(child,discord.ui.Button) and not isinstance(child,Nav) and (child.label or '').strip() in {'Lobby','✨ Lobby','Menu'}:
                    row=child.parent
                    if isinstance(row,discord.ui.ActionRow):
                        row.remove_item(child)
                        if not row.children:
                            (row.parent or view).remove_item(row)
        first_text=next((child for child in view.walk_children() if isinstance(child,discord.ui.TextDisplay)),None)
        if first_text is not None:
            lines=first_text.content.split('\n')
            title_line=next((n for n,line in enumerate(lines) if line.startswith('## ')),None)
            if title_line is not None:
                title=re.sub(r'\s*[·—-]\s*Tier\s+\d+','',lines[title_line][3:].replace('X BOT ','')).replace('`','')
                body='\n'.join(lines[title_line+1:])
                first_text.content=f'-# ✦ X SYSTEM\n## {title}'+ ('\n'+body if body else '')
        # Gameplay notices and instructions must not become tiny footnotes.
        for child in view.walk_children():
            if isinstance(child,discord.ui.TextDisplay):
                child.content='\n'.join(line if line.startswith('-# ✦ X SYSTEM') else line.removeprefix('-# ') for line in child.content.split('\n'))
        # Older detail pages retain their data and controls, but their heading
        # groups become real visual blocks like the new overview screens.
        if not isinstance(view,Shell) and getattr(view,'finished',None) is not False:
            for box in list(view.children):
                if not isinstance(box,discord.ui.Container):
                    continue
                original=list(box.children)
                replacement=[]
                extra=0
                for child in original:
                    parts=child.content.split('\n### ') if isinstance(child,discord.ui.TextDisplay) else []
                    needed=2*(len(parts)-1)
                    if len(parts)>1 and view.total_children_count+extra+needed<=34:
                        replacement.append(discord.ui.TextDisplay(parts[0].rstrip()))
                        for part in parts[1:]:
                            replacement.extend([discord.ui.Separator(),discord.ui.TextDisplay('### '+part.strip())])
                        extra+=needed
                    else:
                        replacement.append(child)
                if extra:
                    for child in original:
                        box.remove_item(child)
                    for child in replacement:
                        box.add_item(child)
        # Preserve specialised forms/results, while clearly separating their
        # readable content from controls. Never change active-game actions.
        if not isinstance(view,Shell) and getattr(view,'finished',None) is not False and view.total_children_count<=32:
            box=next((x for x in view.children if isinstance(x,discord.ui.Container)),None)
            if box is not None:
                items=list(box.children)
                first_row=next((n for n,x in enumerate(items) if isinstance(x,discord.ui.ActionRow)),None)
                if first_row is not None and first_row>0 and not isinstance(items[first_row-1],discord.ui.Separator):
                    box.add_item(discord.ui.Separator())
                    # Public remove/add APIs keep component parentage intact.
                    for item in items[first_row:]:
                        box.remove_item(item)
                        box.add_item(item)
        for child in list(view.walk_children()):
            if isinstance(child,discord.ui.Container) and child.accent_colour not in (discord.Color.red(),discord.Color.green(),discord.Color(0xFF5470)):
                child.accent_colour=discord.Color(0x41D9D0)
            if isinstance(child,(discord.ui.Button,discord.ui.Select)) and not isinstance(child,Nav) and not getattr(child,'system_wrapped',False):
                original=child.callback
                async def callback(i,handler=original):
                    token=navigation.set(view.system_history)
                    try:
                        await handler(Interaction(i,owner,key))
                    finally:
                        navigation.reset(token)
                child.callback=callback
                child.system_wrapped=True
        # Discord allows 40 components including containers/action rows.
        if not force and can_footer and not any(isinstance(child,Nav) for child in view.walk_children()):
            footer(view,owner,key)
        align_economy_tabs(view,owner,key)
        for child in view.walk_children():
            if isinstance(child,Nav):
                child.history=view.system_history
        view.system_signature=tuple(id(child) for child in view.walk_children())
        return view

    for key,factory in legacy.items():
        builders[key]=lambda owner,k=key:page(owner,k)
    for key in sections:
        if key!='menu':
            builders[key]=lambda owner,k=key:page(owner,k)
    bot.xbot_player_lobby_builder=lambda owner,notice='':page(owner,'menu',notice)
    bot.xbot_tier6_economy_builder=lambda owner,notice='':page(owner,'economy',notice)
    bot.xbot_system_page_builder=page
    # Keep the same shortcut catalogue; replace entry presentation only.
    for name,key in [('lobby','menu'),('economy','economy'),('war','war')]:
        commands=list(bot.tree.get_commands())
        guild_id=int(os.getenv('DISCORD_GUILD_ID','0') or 0)
        if guild_id:
            commands+=list(bot.tree.get_commands(guild=discord.Object(id=guild_id)))
        for command in commands:
            if command.name==name:
                async def entry(i:discord.Interaction,dest=key):
                    await i.response.defer()
                    create_player(i.user)
                    await i.edit_original_response(view=page(i.user.id,dest))
                command._callback=entry
    # Keep old names internally for existing panel callbacks; startup's public
    # allowlist hides the old names and publishes their replacements.
    def add_entry(name,destination,description):
        async def entry(i:discord.Interaction):
            await i.response.defer()
            create_player(i.user)
            await i.edit_original_response(view=page(i.user.id,destination),attachments=[])
        kwargs={'guild':discord.Object(id=guild_id)} if guild_id else {}
        bot.tree.remove_command(name,**kwargs)
        bot.tree.command(name=name,description=description,**kwargs)(entry)
    add_entry('menu','menu','Open X SYSTEM: Profile, Economy, Warfront and Missions')
    add_entry('warfront','war','Open your Cities, Army, Diplomacy and Battles')
    add_entry('profile','profile','View your profile, progress, Backpack and assets')
    shortcuts={'casino':'casino','city':'city','army':'army','recruit':'recruit','diplomacy':'diplomacy',
               'craft':'craft','stock':'stock','research':'research','missions':'missions','mining':'mining',
               'mine':'mining','sell_item':'economy','shop':'shop','backpack':'inventory','market':'market',
               'attack':'attack','daily':'daily','balance':'wallet','collect':'city'}
    shortcuts.update({'claim_land':'city','declare_war':'diplomacy','code_redeem':'economy',
                      'map':'war','map_detail':'war'})
    for command in commands:
        if command.name in shortcuts:
            original=command.callback
            key=shortcuts[command.name]
            def wrap(handler,destination):
                @functools.wraps(handler)
                async def entry(i,*args,**kwargs):
                    return await handler(Interaction(i,i.user.id,destination),*args,**kwargs)
                entry.system_ui_wrapped=True
                return entry
            command._callback=wrap(original,key)
