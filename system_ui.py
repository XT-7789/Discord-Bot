"""Compact player navigation. Existing game callbacks retain their rules."""
import discord
import tier5
import war_tier
import os
import functools
import time
import re
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
                await i.edit_original_response(view=page(self.owner,destination),attachments=attachments)
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

    def footer(view,owner,key,include_back=True):
        row=discord.ui.ActionRow()
        if key!='menu':
            if include_back:
                row.add_item(Nav(owner,'‹ Back','back'))
            row.add_item(Nav(owner,'⌂ Menu','menu'))
        if key=='menu':
            row.add_item(Nav(owner,'? Help','help'))
        view.add_item(row)
        view.add_item(discord.ui.ActionRow(Nav(owner,'× Close','close')))

    def page(owner,key='menu',notice=''):
        history=navigation.get()
        history=('menu',) if key=='menu' else history if history and history[-1]==key else (*history,key)
        token=navigation.set(history[-40:])
        try:
            view=render_page(owner,key,notice)
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

    def render_page(owner,key='menu',notice=''):
        player=db.execute('SELECT * FROM players WHERE user_id=?',(owner,)).fetchone()
        if key=='mission_weekly' and tier5.profile_summary(db,owner)['level']<2:
            return page(owner,'missions','Weekly missions unlock at Nation Level 2.')
        if key=='close':
            view=Shell(owner,'CLOSED','Panel closed. /menu opens a new menu.')
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
            view=Shell(owner,'💳 Finance','Manage your XC in one place.',notice)
            view.box.add_item(discord.ui.TextDisplay(f"### Wallet\n## {player['xc']:,} XC\nAvailable to spend."))
            view.box.add_item(discord.ui.Separator())
            view.box.add_item(discord.ui.TextDisplay(f"### Bank\n## {player['bank_xc']:,} XC\nWithdraw to use these savings."))
            view.box.add_item(discord.ui.ActionRow(bot.xbot_finance_button_builder(owner,'deposit'),bot.xbot_finance_button_builder(owner,'withdraw')))
            view.box.add_item(discord.ui.TextDisplay(f"**Total XC: {player['xc']+player['bank_xc']:,}** · Transfers do not change your total."))
            view.box.add_item(discord.ui.ActionRow(Nav(owner,'View Assets','assets'),Nav(owner,'Exchange','exchange')))
            footer(view,owner,key)
            return prepare(view,owner,key,force=True)
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
                view=Shell(owner,'PLAYER HUB',f"**{discord.utils.escape_markdown(player['nation_name'])}** · Lv {profile['level']}\n{xp_text}",notice)
                def block(text,buttons):
                    view.box.add_item(discord.ui.Separator())
                    view.box.add_item(discord.ui.TextDisplay(text))
                    view.box.add_item(discord.ui.ActionRow(*buttons))
                block(f"### 💰 Economy\nWallet **{player['xc']:,} XC** · Bank **{player['bank_xc']:,} XC**",[bot.xbot_finance_button_builder(owner,'deposit'),bot.xbot_finance_button_builder(owner,'withdraw'),Nav(owner,'Economy','economy')])
                cities=db.execute('SELECT COUNT(*) FROM player_cities WHERE user_id=?',(owner,)).fetchone()[0]
                lands=db.execute('SELECT COUNT(*) FROM map_territories WHERE owner_user_id=?',(owner,)).fetchone()[0]
                units=db.execute('SELECT COALESCE(SUM(quantity),0) FROM player_war_units WHERE user_id=?',(owner,)).fetchone()[0]
                state=db.execute('SELECT last_collect FROM player_city_state WHERE user_id=?',(owner,)).fetchone()
                ready_at=(int(state['last_collect']) if state else 0)+war_tier.setting(db,'city_collect_cooldown')
                production='Production ready' if ready_at<=int(time.time()) else f'Production <t:{ready_at}:R>'
                block(f"### 🏙️ Nation\nCities **{cities}** · Land **{lands}** · Units **{units}**\nWar Credits **{player['money']:,}** · {production}",[Nav(owner,'Cities','city'),Nav(owner,'Army','army'),Nav(owner,'Warfront','war')])
                pending=[(category,m) for category in ('daily','starter') for m in tier5.missions_for(db,owner,category)[1] if not m['claimed']]
                ready=next(((c,m) for c,m in pending if m['progress']>=m['target']),None)
                chosen=ready or next(((c,m) for c,m in pending if m['destination'] in ('mining','economy','city')),None)
                if chosen:
                    category,m=chosen
                    target='mission_'+category if ready else {'economy':'daily'}.get(m['destination'],m['destination'])
                    text=f"### 🎯 {'Reward Ready' if ready else 'Current Mission'}\n**{m['title']}** · {m['progress']} / {m['target']}\n{progress_bar(m['progress'],m['target'])}\n+{m['xc']} XC · +{m['credits']} WC · +{m['xp']} XP"
                    actions=[Nav(owner,'View Rewards' if ready else 'Continue',target),Nav(owner,'Missions','missions')]
                else:
                    text='### 🎯 Missions\nAll featured goals complete. Explore at your own pace.'
                    actions=[Nav(owner,'Missions','missions')]
                block(text,actions)
                block('### 🎰 Casino\nChoose a game · XC stakes can be lost.',[Nav(owner,'Casino','casino'),Nav(owner,'Daily Reward','daily'),Nav(owner,'Profile','profile')])
                footer(view,owner,key)
                return prepare(view,owner,key,force=True)
            if key=='economy':
                view.box.add_item(discord.ui.TextDisplay(f"### Wallet · {player['xc']:,} XC\nBank **{player['bank_xc']:,} XC**"))
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
    class Output:
        def __init__(self,target,owner,key):
            self.target,self.owner,self.key=target,owner,key
        def __getattr__(self,name):
            return getattr(self.target,name)
        def options(self,kwargs):
            if isinstance(kwargs.get('view'),discord.ui.LayoutView):
                kwargs['view']=prepare(kwargs['view'],self.owner,self.key)
            return kwargs
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
                    token=navigation.set(history)
                    try:
                        await original(Interaction(i,self.owner,self.key))
                    finally:
                        navigation.reset(token)
                modal.on_submit=submit
                modal.system_wrapped=True
            return await self.target.send_modal(modal)

    class Message:
        def __init__(self,actual,owner,key):
            self.actual,self.owner,self.key=actual,owner,key
        def __getattr__(self,name):
            return getattr(self.actual,name)
        async def edit(self,**kwargs):
            if isinstance(kwargs.get('view'),discord.ui.LayoutView):
                kwargs['view']=prepare(kwargs['view'],self.owner,self.key)
            return await self.actual.edit(**kwargs)

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
            if isinstance(kwargs.get('view'),discord.ui.LayoutView):
                kwargs['view']=prepare(kwargs['view'],self.owner,self.key)
            return await self.actual.edit_original_response(**kwargs)

    def prepare(view,owner,key,force=False):
        if isinstance(view,discord.ui.LayoutView) and not hasattr(view,'system_history'):
            view.system_history=navigation.get() or (key,)
        if not isinstance(view,discord.ui.LayoutView) or (isinstance(view,Shell) and not force):
            return view
        signature=tuple(id(child) for child in view.walk_children())
        if getattr(view,'system_signature',None)==signature:
            return view
        view.system_prepared=True
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
        if first_text is not None and first_text.content.startswith('## '):
            title,sep,body=first_text.content.partition('\n')
            title=re.sub(r'\s*[·—-]\s*Tier\s+\d+','',title[3:].replace('X BOT ','')).replace('`','')
            first_text.content=f'-# ✦ X SYSTEM\n## {title}'+(sep+body if sep else '')
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
    for command in commands:
        if command.name in shortcuts:
            original=command.callback
            key=shortcuts[command.name]
            def wrap(handler,destination):
                @functools.wraps(handler)
                async def entry(i,*args,**kwargs):
                    return await handler(Interaction(i,i.user.id,destination),*args,**kwargs)
                return entry
            command._callback=wrap(original,key)
