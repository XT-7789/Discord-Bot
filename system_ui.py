"""Compact player navigation. Existing game callbacks retain their rules."""
import discord
import tier5
import os
import functools
import time
import re


def register(bot, db, create_player):
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
        'menu':('MAIN MENU','Select a system.', [('👤 Profile','profile'),('💰 Economy','economy'),('⚔️ War','war'),('🎯 Missions','missions')]),
        'economy':('ECONOMY','Earn, trade and play.', [('💳 Finance','finance'),('💼 Earn','earn_menu'),('📊 Market','market_menu'),('⛏️ Mines','mining'),('🎰 Casino','casino'),('🏆 Rankings','rankings')]),
        'finance':('FINANCE','Your wallet and savings.', [('💰 Wallet','wallet'),('🏦 Bank','bank'),('📋 Assets','assets'),('💱 Exchange','exchange')]),
        'earn_menu':('EARN','Collect rewards or make something.', [('🎁 Daily','daily'),('💼 Contracts','contracts'),('⛏️ Mines','mining'),('🛠️ Craft','craft'),('🏭 Production','production'),('🔬 Research','research')]),
        'market_menu':('MARKET','Browse before you spend.', [('🛒 Shop','shop'),('🤝 Player Market','market'),('📈 Stocks','stock'),('🎒 Backpack','inventory')]),
        'war':('WAR','Your nation and armed forces.', [('🏙️ Cities','city'),('🪖 Army','army'),('➕ Recruit','recruit'),('🕊️ Diplomacy','diplomacy'),('⚔️ Attack','attack'),('🛡️ Defence','defence'),('📋 Reports','reports'),('🗺️ Overview','war_overview')]),
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

    class Nav(discord.ui.Button):
        def __init__(self,owner,label,key):
            super().__init__(label=label,style=discord.ButtonStyle.secondary)
            self.owner,self.key=owner,key
        async def callback(self,i):
            if i.user.id!=self.owner:
                await i.response.send_message('Open /lobby for your own menu.',ephemeral=True)
                return
            await i.response.defer()
            await i.edit_original_response(view=page(self.owner,self.key))

    class Shell(discord.ui.LayoutView):
        def __init__(self,owner,title,body='',notice=''):
            super().__init__(timeout=900)
            self.owner=owner
            self.system_prepared=True
            self.box=discord.ui.Container(accent_color=discord.Color(0x41D9D0))
            self.box.add_item(discord.ui.TextDisplay(f'### ✦ X SYSTEM\n`{title}`'+ ('\n'+body if body else '')+ ('\n\n'+notice if notice else '')))
            self.add_item(self.box)
        async def interaction_check(self,i):
            if i.user.id==self.owner:
                return True
            await i.response.send_message('Open /lobby for your own menu.',ephemeral=True)
            return False

    def footer(view,owner,key,include_back=True):
        row=discord.ui.ActionRow()
        if key!='menu':
            if include_back:
                row.add_item(Nav(owner,'‹ Back',parents.get(key,'menu')))
            row.add_item(Nav(owner,'⌂ Menu','menu'))
        row.add_item(Nav(owner,'× Close','close'))
        view.add_item(row)

    def page(owner,key='menu',notice=''):
        player=db.execute('SELECT * FROM players WHERE user_id=?',(owner,)).fetchone()
        if key=='mission_weekly' and tier5.profile_summary(db,owner)['level']<2:
            return page(owner,'missions','Weekly missions unlock at Nation Level 2.')
        if key=='close':
            view=Shell(owner,'CLOSED','Panel closed. /lobby opens a new menu.')
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
                async def submit(i):
                    await original(Interaction(i,self.owner,self.key))
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
                await i.response.send_message('Open /lobby for your own panel.',ephemeral=True)
                return False
            return await check(i)
        view.interaction_check=owner_check
        can_footer=view.total_children_count<=35 and getattr(view,'finished',None) is not False
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
            first_text.content=f'### ✦ X SYSTEM\n`{title}`'+(sep+body if sep else '')
        for child in list(view.walk_children()):
            if isinstance(child,discord.ui.Container) and child.accent_colour not in (discord.Color.red(),discord.Color.green(),discord.Color(0xFF5470)):
                child.accent_colour=discord.Color(0x41D9D0)
            if isinstance(child,(discord.ui.Button,discord.ui.Select)) and not isinstance(child,Nav) and not getattr(child,'system_wrapped',False):
                original=child.callback
                async def callback(i,handler=original):
                    await handler(Interaction(i,owner,key))
                child.callback=callback
                child.system_wrapped=True
        # Discord allows 40 components including containers/action rows.
        if not force and can_footer and not any(isinstance(child,Nav) for child in view.walk_children()):
            has_back=any(isinstance(child,discord.ui.Button) and (child.label or '').lower().startswith('back to ') for child in view.walk_children())
            footer(view,owner,key,include_back=not has_back)
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
