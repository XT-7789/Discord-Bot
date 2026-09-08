"""Nation research: bounded benefits, prepaid serial queues and simple entry UI."""
import json
import math
import os
import secrets
import sqlite3
import time

import discord


TECHS = (
    ('mining', 'economy', 'Mining Methods', 'Chance of extra mined materials', 5, 30),
    ('trade', 'economy', 'Trade Agreements', 'Lower virtual stock trading fees', 5, 30),
    ('production', 'industry', 'Assembly Lines', 'Faster queued crafting', 5, 30),
    ('construction', 'industry', 'Construction Planning', 'Lower City build and upgrade costs', 3, 20),
    ('logistics', 'military', 'Supply Logistics', 'Lower attacking Supply cost', 3, 20),
    ('defence', 'military', 'Defence Engineering', 'Extra territory defence percentage points', 2, 10),
)
BRANCHES = {'economy': '💰 Economy', 'industry': '🏭 Industry', 'military': '🛡️ Military'}


def initialise(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS tier8_technologies (
            code TEXT PRIMARY KEY, branch TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL, max_level INTEGER NOT NULL,
            base_cost INTEGER NOT NULL, seconds INTEGER NOT NULL,
            bonus_per_level INTEGER NOT NULL, bonus_cap INTEGER NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tier8_research_jobs (
            id INTEGER PRIMARY KEY, token TEXT NOT NULL, user_id INTEGER NOT NULL,
            code TEXT NOT NULL, level INTEGER NOT NULL, paid INTEGER NOT NULL,
            starts_at INTEGER NOT NULL, ready_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', UNIQUE(token,code));
        CREATE UNIQUE INDEX IF NOT EXISTS tier8_one_active_level
            ON tier8_research_jobs(user_id,code,level) WHERE status IN ('queued','complete');
        CREATE TABLE IF NOT EXISTS tier8_levels (
            user_id INTEGER NOT NULL, code TEXT NOT NULL, level INTEGER NOT NULL,
            PRIMARY KEY(user_id,code));
        CREATE TABLE IF NOT EXISTS tier8_preferences (
            user_id INTEGER PRIMARY KEY, simple_mode INTEGER NOT NULL DEFAULT 1);
    ''')
    db.executemany('INSERT OR IGNORE INTO tier8_technologies VALUES(?,?,?,?,?,?,?,?,?,1)',
                   [(c,b,n,d,5,50,300,p,cap) for c,b,n,d,p,cap in TECHS])
    db.executemany('INSERT OR IGNORE INTO economy_settings(key,value) VALUES(?,?)',
                   [('tier8_enabled','1'), ('tier8_queue_limit','6')])
    db.commit()


def enabled(db):
    try:
        row = db.execute("SELECT value FROM economy_settings WHERE key='tier8_enabled'").fetchone()
        return bool(row and str(row[0]) == '1')
    except sqlite3.OperationalError:
        return False


def bonus(db, user_id, code, now=None):
    """Ready jobs apply immediately, including after restart; no claim clicks needed."""
    if not enabled(db):
        return 0
    try:
        row = db.execute('''SELECT t.bonus_per_level,t.bonus_cap,t.max_level,
            MAX(COALESCE((SELECT level FROM tier8_levels WHERE user_id=? AND code=t.code),0),
                COALESCE((SELECT MAX(level) FROM tier8_research_jobs WHERE user_id=? AND code=t.code
                    AND status IN ('queued','complete') AND ready_at<=?),0)) level
            FROM tier8_technologies t WHERE code=? AND enabled=1''',
            (user_id,user_id,int(time.time()) if now is None else now,code)).fetchone()
    except sqlite3.OperationalError:
        return 0  # Older DBs work before the additive migration runs.
    hard_cap = next((t[5] for t in TECHS if t[0] == code), 0)
    return max(0, min(hard_cap, int(row['bonus_cap']), min(row['level'],row['max_level'])*row['bonus_per_level'])) if row else 0


def discounted(db, user_id, code, amount):
    return max(0, math.ceil(int(amount) * (100-bonus(db,user_id,code))/100))


def catalog(db, user_id, now=None):
    now = int(time.time()) if now is None else now
    return db.execute('''SELECT t.*,
        MAX(COALESCE(l.level,0),COALESCE((SELECT MAX(j.level) FROM tier8_research_jobs j
            WHERE j.user_id=? AND j.code=t.code AND j.status IN ('queued','complete') AND j.ready_at<=?),0)) level,
        COALESCE((SELECT MAX(j.level) FROM tier8_research_jobs j
            WHERE j.user_id=? AND j.code=t.code AND j.status='queued' AND j.ready_at>?),0) pending
        FROM tier8_technologies t LEFT JOIN tier8_levels l ON l.user_id=? AND l.code=t.code
        ORDER BY t.branch,t.code''', (user_id,now,user_id,now,user_id)).fetchall()


def quote(db, user_id, codes, now=None):
    now = int(time.time()) if now is None else now
    if not enabled(db):
        raise ValueError('Research is temporarily closed.')
    codes = list(dict.fromkeys(codes))
    if not codes or len(codes)>6:
        raise ValueError('Choose between 1 and 6 projects.')
    rows = {r['code']:r for r in catalog(db,user_id,now)}
    active = db.execute("SELECT COUNT(*),MAX(ready_at) FROM tier8_research_jobs WHERE user_id=? AND status='queued' AND ready_at>?",(user_id,now)).fetchone()
    limit = int(db.execute("SELECT value FROM economy_settings WHERE key='tier8_queue_limit'").fetchone()[0])
    if active[0]+len(codes)>max(1,min(6,limit)):
        raise ValueError('Research queue is full. Finished projects apply automatically; refresh when ready.')
    end = max(now,int(active[1] or now))
    entries=[]
    for code in codes:
        row=rows.get(code)
        if not row or not row['enabled']:
            raise ValueError('One selected project is unavailable. Choose again.')
        if row['pending']:
            raise ValueError(f"{row['name']} is already in your queue.")
        level=int(row['level'])+1
        if level>row['max_level']:
            raise ValueError(f"{row['name']} is already at maximum level.")
        cost=int(row['base_cost'])*level
        duration=int(row['seconds'])*level
        hard_cap=next(t[5] for t in TECHS if t[0]==code)
        current_bonus=bonus(db,user_id,code,now)
        next_bonus=min(hard_cap,row['bonus_cap'],level*row['bonus_per_level'])
        entries.append(dict(code=code,name=row['name'],level=level,paid=cost,starts_at=end,ready_at=end+duration,duration=duration,
                            current_bonus=current_bonus,next_bonus=next_bonus))
        end+=duration
    return entries


def start(db,user_id,entries,token,now=None):
    """Recheck the displayed quote under a write lock; retry never spends twice."""
    now=int(time.time()) if now is None else now
    if db.in_transaction:
        raise ValueError('Please finish the current action and try again.')
    try:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT id FROM tier8_research_jobs WHERE token=? AND user_id=?',(token,user_id)).fetchone()
        if old:
            db.rollback()
            return 'This research order was already saved. No extra XC was spent.'
        fresh=quote(db,user_id,[e['code'] for e in entries],now)
        signature=lambda es:[(e['code'],e['level'],e['paid'],e['duration'],e['next_bonus']) for e in es]
        if signature(entries)!=signature(fresh):
            raise ValueError('Research costs or levels changed. Review a new quote before confirming.')
        total=sum(e['paid'] for e in fresh)
        if not db.execute('UPDATE players SET xc=xc-? WHERE user_id=? AND xc>=?',(total,user_id,total)).rowcount:
            raise ValueError(f'You need {total:,} XC. Open Economy to earn more.')
        db.executemany('''INSERT INTO tier8_research_jobs(token,user_id,code,level,paid,starts_at,ready_at)
            VALUES(?,?,?,?,?,?,?)''',[(token,user_id,e['code'],e['level'],e['paid'],e['starts_at'],e['ready_at']) for e in fresh])
        db.execute('INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)',
                   (user_id,'research_start',json.dumps({'token':token,'codes':[e['code'] for e in fresh],'paid':total}),now))
        db.commit()
        return f"Queued {len(fresh)} project(s) for {total:,} XC. Benefits apply automatically when finished."
    except Exception:
        db.rollback()
        raise


def cancel(db,user_id,now=None):
    """Cancel remaining jobs with their original paid-cost refunds, exactly once."""
    now=int(time.time()) if now is None else now
    if db.in_transaction:
        raise ValueError('Please finish the current action and try again.')
    try:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute("SELECT id,paid FROM tier8_research_jobs WHERE user_id=? AND status='queued' AND ready_at>?",(user_id,now)).fetchall()
        refund=sum(r['paid'] for r in rows)
        db.executemany("UPDATE tier8_research_jobs SET status='cancelled' WHERE id=?",[(r['id'],) for r in rows])
        db.execute('UPDATE players SET xc=xc+? WHERE user_id=?',(refund,user_id))
        db.execute('INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)',(user_id,'research_cancel',f'{len(rows)} projects; refund {refund} XC',now))
        db.commit()
        return f'Cancelled {len(rows)} unfinished project(s). Refunded {refund:,} XC; completed benefits remain.'
    except Exception:
        db.rollback()
        raise


def register_commands(bot,db,create_player):
    builders=bot.xbot_player_panel_builders
    old_lobby=bot.xbot_player_lobby_builder

    class Owned(discord.ui.LayoutView):
        def __init__(self,owner):
            super().__init__(timeout=900)
            self.owner=owner
        async def interaction_check(self,i):
            if i.user.id==self.owner:
                return True
            await i.response.send_message('Open /lobby for your own panel.',ephemeral=True)
            return False

    class Go(discord.ui.Button):
        def __init__(self,label,destination,style=discord.ButtonStyle.secondary):
            super().__init__(label=label,style=style)
            self.destination=destination
        async def callback(self,i):
            await i.response.defer()
            if self.destination=='lobby':
                view=lobby(i.user.id)
            elif self.destination=='all':
                view=AllPanels(i.user.id)
            elif self.destination=='guide':
                view=Guide(i.user.id)
            elif self.destination.startswith('research:'):
                view=Research(i.user.id,self.destination.split(':')[1])
            else:
                factory=builders.get(self.destination)
                if not factory:
                    await i.followup.send('That panel is unavailable. Return to Lobby.',ephemeral=True)
                    return
                view=factory(i.user.id)
            await i.edit_original_response(view=view)

    class Pick(discord.ui.Select):
        def __init__(self,rows,branch):
            self.branch=branch
            options=[discord.SelectOption(label=f"{r['name']} → Lv {r['level']+1}",value=r['code'],
                description=f"{r['base_cost']*(r['level']+1):,} XC · {r['seconds']*(r['level']+1)} sec · review benefit next") for r in rows]
            super().__init__(placeholder='Choose one or several projects',options=options,min_values=1,max_values=len(options))
        async def callback(self,i):
            await i.response.defer()
            try:
                view=Confirm(i.user.id,quote(db,i.user.id,self.values),self.branch)
            except ValueError as e:
                view=Research(i.user.id,self.branch,str(e))
            await i.edit_original_response(view=view)

    class ConfirmButton(discord.ui.Button):
        def __init__(self,entries,token,branch):
            super().__init__(label='Confirm & Start',style=discord.ButtonStyle.success)
            self.entries,self.token,self.branch=entries,token,branch
        async def callback(self,i):
            await i.response.defer()
            try:
                notice=start(db,i.user.id,self.entries,self.token)
            except (ValueError,sqlite3.OperationalError) as e:
                notice=str(e) if isinstance(e,ValueError) else 'Research is busy. Try again in a moment.'
            await i.edit_original_response(view=Research(i.user.id,'queue',notice))

    class Confirm(Owned):
        def __init__(self,owner,entries,branch):
            super().__init__(owner)
            wallet=db.execute('SELECT xc FROM players WHERE user_id=?',(owner,)).fetchone()[0]
            total=sum(e['paid'] for e in entries)
            c=discord.ui.Container(accent_color=discord.Color.green())
            c.add_item(discord.ui.TextDisplay('## Review Research\n'+'\n'.join(
                f"**{e['name']} Lv {e['level']}** · {e['current_bonus']}% → **{e['next_bonus']}%**\n{e['paid']:,} XC · ready <t:{e['ready_at']}:R>" for e in entries)+
                f'\n\n**Total: {total:,} XC** · Wallet: {wallet:,} XC\nProjects run one after another. Benefits activate automatically.'))
            button=ConfirmButton(entries,secrets.token_urlsafe(16),branch)
            button.disabled=wallet<total
            c.add_item(discord.ui.ActionRow(button,Go('Back',f'research:{branch}'),Go('Earn XC','economy')))
            self.add_item(c)

    class CancelButton(discord.ui.Button):
        def __init__(self,confirm=False):
            super().__init__(label='Confirm Cancel & Refund' if confirm else 'Cancel Unfinished',style=discord.ButtonStyle.danger)
            self.confirm=confirm
        async def callback(self,i):
            await i.response.defer()
            if self.confirm:
                try:
                    notice=cancel(db,i.user.id)
                except (ValueError,sqlite3.OperationalError):
                    notice='Research is busy. Try again in a moment.'
                view=Research(i.user.id,'queue',notice)
            else:
                view=Owned(i.user.id)
                c=discord.ui.Container()
                c.add_item(discord.ui.TextDisplay('## Cancel unfinished research?\nEvery unfinished project is removed and its original XC cost refunded. Finished projects keep their benefits.'))
                c.add_item(discord.ui.ActionRow(CancelButton(True),Go('Keep Researching','research:queue')))
                view.add_item(c)
            await i.edit_original_response(view=view)

    class Research(Owned):
        def __init__(self,owner,branch='economy',notice=''):
            super().__init__(owner)
            c=discord.ui.Container(accent_color=discord.Color.blurple())
            wallet=db.execute('SELECT xc FROM players WHERE user_id=?',(owner,)).fetchone()[0]
            c.add_item(discord.ui.TextDisplay(f'## 🔬 Research Centre\nWallet: **{wallet:,} XC**\nChoose projects → review cost → confirm once.\n'+notice))
            c.add_item(discord.ui.ActionRow(*(Go(label,f'research:{key}',discord.ButtonStyle.primary if branch==key else discord.ButtonStyle.secondary) for key,label in {**BRANCHES,'queue':'⏳ Queue'}.items())))
            if branch=='queue':
                jobs=db.execute("SELECT j.*,t.name FROM tier8_research_jobs j JOIN tier8_technologies t ON t.code=j.code WHERE user_id=? AND status='queued' AND ready_at>? ORDER BY ready_at",(owner,int(time.time()))).fetchall()
                c.add_item(discord.ui.TextDisplay('\n'.join(f"**{r['name']} Lv {r['level']}** · ready <t:{r['ready_at']}:R>" for r in jobs) or '✅ No unfinished projects. Finished benefits are already active.'))
                if jobs:
                    c.add_item(discord.ui.ActionRow(CancelButton()))
            else:
                rows=[r for r in catalog(db,owner) if r['branch']==branch]
                c.add_item(discord.ui.TextDisplay('\n\n'.join(f"**{r['name']} · Lv {r['level']}/{r['max_level']}**\n{r['description']}: **{bonus(db,owner,r['code'])}% active**"+(' · ⏳ Queued' if r['pending'] else '')+(' · Paused' if not r['enabled'] else '') for r in rows)))
                available=[r for r in rows if r['enabled'] and not r['pending'] and r['level']<r['max_level']]
                if available and enabled(db):
                    c.add_item(discord.ui.ActionRow(Pick(available,branch)))
                elif not enabled(db):
                    c.add_item(discord.ui.TextDisplay('Research and its benefits are paused by the administrator.'))
            c.add_item(discord.ui.ActionRow(Go('Refresh',f'research:{branch}'),Go('Economy','economy'),Go('Lobby','lobby')))
            self.add_item(c)

    class Guide(Owned):
        def __init__(self,owner):
            super().__init__(owner)
            c=discord.ui.Container(accent_color=discord.Color.green())
            c.add_item(discord.ui.TextDisplay('## 🌱 Start here\n**1. Earn XC:** claim Daily in Economy, then mine and sell materials.\n**2. Grow:** build Cities for War Credits; choose several Cities to upgrade together.\n**3. Improve:** research permanent benefits when you can afford them.\n\nWar is optional. You can enjoy Economy and grow your Nation first.\n**XC** buys items/research. **War Credits** build Cities and recruit forces. **Supply** powers battles.'))
            c.add_item(discord.ui.ActionRow(Go('Earn XC','economy'),Go('Build Cities','city'),Go('Research','research:economy'),Go('Lobby','lobby')))
            self.add_item(c)

    class AllPanels(Owned):
        def __init__(self,owner):
            super().__init__(owner)
            c=discord.ui.Container()
            c.add_item(discord.ui.TextDisplay('## All Activities\nOpen any centre directly. Your progress is saved automatically.'))
            groups=[ [('Economy','economy'),('Mining','mining'),('Shop','shop'),('Backpack','inventory')],
                     [('Cities','city'),('Craft','craft'),('Stocks','stock'),('Market','market')],
                     [('War','war'),('Army','army'),('Recruit','recruit'),('Diplomacy','diplomacy')],
                     [('Missions','missions'),('Casino','casino'),('Research','research:economy'),('Lobby','lobby')] ]
            for group in groups:
                c.add_item(discord.ui.ActionRow(*(Go(label,dest) for label,dest in group if dest in builders or ':' in dest or dest=='lobby')))
            self.add_item(c)

    class SimpleLobby(Owned):
        def __init__(self,owner):
            super().__init__(owner)
            import tier5
            player=db.execute('SELECT * FROM players WHERE user_id=?',(owner,)).fetchone()
            objective=tier5.next_objective(db,owner)
            c=discord.ui.Container(accent_color=discord.Color.teal())
            c.add_item(discord.ui.TextDisplay(f"## 🏠 {player['nation_name']}\n💰 **{player['xc']:,} XC** · 🏙️ **{player['money']:,} War Credits**\n\n### Your next step\n{objective['label']}\nStart with earning and Cities. Explore other systems when ready."))
            c.add_item(discord.ui.ActionRow(Go('▶ Continue',objective['destination'],discord.ButtonStyle.success),Go('🌱 How to Play','guide')))
            if hasattr(bot,'xbot_daily_button_builder'):
                cooldown=int(db.execute("SELECT value FROM economy_settings WHERE key='daily_cooldown'").fetchone()[0])
                reward=int(db.execute("SELECT value FROM economy_settings WHERE key='daily_reward'").fetchone()[0])
                ready=int(player['last_daily'])+cooldown<=int(time.time())
                daily=bot.xbot_daily_button_builder(owner)
                daily.label=f'Claim Daily +{reward} XC' if ready else 'Daily Collected'
                daily.disabled=not ready
                c.add_item(discord.ui.ActionRow(daily,Go('⛏️ Mine','mining')))
            c.add_item(discord.ui.ActionRow(Go('💰 Earn','economy'),Go('🏙️ Cities','city'),Go('🔬 Research','research:economy')))
            c.add_item(discord.ui.ActionRow(Go('All Activities','all'),Go('⚔️ War','war'),ModeButton(False)))
            self.add_item(c)

    class ModeButton(discord.ui.Button):
        def __init__(self,simple):
            super().__init__(label='Easy Lobby' if simple else 'Detailed Lobby')
            self.simple=simple
        async def callback(self,i):
            await i.response.defer()
            db.execute('INSERT INTO tier8_preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET simple_mode=excluded.simple_mode',(i.user.id,int(self.simple)))
            db.commit()
            await i.edit_original_response(view=lobby(i.user.id))

    def lobby(owner):
        pref=db.execute('SELECT simple_mode FROM tier8_preferences WHERE user_id=?',(owner,)).fetchone()
        if not pref or pref[0]:
            return SimpleLobby(owner)
        view=old_lobby(owner)
        view.add_item(discord.ui.ActionRow(ModeButton(True),Go('Research','research:economy')))
        return view

    builders['research']=lambda owner:Research(owner)
    bot.xbot_player_lobby_builder=lobby
    bot.xbot_tier8_research_builder=lambda owner:Research(owner)
    guild_id=int(os.getenv('DISCORD_GUILD_ID','0') or 0)
    kwargs={'guild':discord.Object(id=guild_id)} if guild_id else {}
    for name in ('lobby','research'):
        bot.tree.remove_command(name)
        if guild_id:
            bot.tree.remove_command(name,**kwargs)

    @bot.tree.command(name='lobby',description='Open the easy Lobby and your next step',**kwargs)
    async def lobby_command(i:discord.Interaction):
        await i.response.defer()
        create_player(i.user)
        await i.edit_original_response(view=lobby(i.user.id))

    @bot.tree.command(name='research',description='Research upgrades for Economy, Industry and Defence',**kwargs)
    async def research_command(i:discord.Interaction):
        await i.response.defer()
        create_player(i.user)
        await i.edit_original_response(view=Research(i.user.id))
