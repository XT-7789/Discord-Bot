"""Consistent browse / preview / action sections for private Admin subpages."""
import discord


def build(panel, content):
    from staff_panel import PAGES, TOOL_GROUPS, AdminPageSelect, ApplicationFormSelect, PendingApplicationSelect, RewardCodeSelect, TesterFeedbackSelect, AdminUserSelect, AdminChannelSelect
    import staff_tools
    controls=list(panel._controls)
    for control in controls:
        control.row=None
    parts=[discord.ui.TextDisplay(f'-# ✦ X SYSTEM · ADMIN\n# {PAGES[panel.page].upper()}\n{content.description or ""}')]
    if panel.notice:
        parts.append(discord.ui.TextDisplay('### Latest result\n'+panel.notice[:1800]))

    def take(predicate):
        found=[c for c in controls if predicate(c)]
        for c in found:controls.remove(c)
        return found

    def action(*names):
        return take(lambda c:getattr(c,'action',None) in names)

    def add_rows(items):
        # A select occupies a full row; buttons stay in short pairs on mobile.
        pending=[]
        for item in items:
            if isinstance(item,discord.ui.Select):
                if pending:parts.append(discord.ui.ActionRow(*pending));pending=[]
                parts.append(discord.ui.ActionRow(item))
            else:
                pending.append(item)
                if len(pending)==2:parts.append(discord.ui.ActionRow(*pending));pending=[]
        if pending:parts.append(discord.ui.ActionRow(*pending))

    def section(title,body,items=()):
        parts.extend((discord.ui.Separator(),discord.ui.TextDisplay(f'## {title}\n{body}'[:3900])))
        add_rows(items)

    def listing(cls,kind):
        return take(lambda c:isinstance(c,cls))+take(lambda c:getattr(c,'action','').startswith('list:'+kind+':'))

    fields=[f for f in content.fields if f.name!='Latest action']
    if panel.page=='applications':
        form=panel.selected_form()
        form_text=f"**{form['name']}**\n{form['description'] or 'No description.'}" if form else 'No open form is available. Open or configure forms in Dashboard.'
        post=action('post_application','toggle_applications')
        for c in post:
            if c.action=='post_application':c.disabled=form is None
        section('Post a form',form_text[:850]+'\n'+panel.list_pages.get('forms',''),listing(ApplicationFormSelect,'forms')+post)
        selected=panel.selected_application()
        preview=next((f for f in fields if f.name.startswith('#')),None)
        text=(f'**{preview.name}**\n{preview.value[:450]}\nRead the full answers before deciding.' if preview else 'Choose a pending application below to review its answers.')
        items=listing(PendingApplicationSelect,'applications')
        if selected:items.append(DetailsButton(panel))
        items+=action('review:accepted','review:hold','review:denied')
        section('Review an application',text+'\n'+panel.list_pages.get('applications','No pending applications.'),items)
    elif panel.page=='tester':
        selected=panel.selected_feedback()
        section('Choose a report',panel.list_pages.get('reports','No pending reports.'),listing(TesterFeedbackSelect,'reports'))
        if selected:
            section('Selected report',f"**#{selected['id']} · {selected['user_name']}**\n{selected['kind'].title()} · **{selected['title']}**\n{selected['details'][:450]}\nRead the full report before deciding.",[DetailsButton(panel),*action('accept_feedback','reject_feedback')])
        else:section('Review','Select a report to see its details and available actions.')
        section('Collect feedback','Post the feedback entry in this channel.',action('post_tester_feedback'))
    elif panel.page=='codes':
        filters=take(lambda c:getattr(c,'action','').startswith('filter_codes:'))
        section('Find a code',panel.list_pages.get('codes','No codes match this filter.')+'\nSelecting only previews a code; no reward is issued.')
        parts.append(discord.ui.ActionRow(*filters))
        add_rows(listing(RewardCodeSelect,'codes'))
        selected=panel.selected_code()
        if selected:
            field=fields[0]
            outside=not any(r['id']==selected['id'] for r in panel.reward_codes()[panel.offsets.get('codes',0):panel.offsets.get('codes',0)+25])
            section('Selected code',f'### {field.name}\n{field.value}'+('\nSelected code is outside the current list page/filter.' if outside else ''),action('toggle_code'))
        else:section('Selected code','No code selected. Change the filter or create a new code.')
        section('Create a code','Set rewards and usage limits in the form.',action('create_code'))
    elif panel.page=='verification':
        roles='\n'.join(f"**{f.name}** · {f.value if f.value!='<@&0>' else 'Not configured'}" for f in fields)
        section('Role mapping',roles or 'No roles configured.')
        section('Manage verification','Post the entry here or change whether verification is open.',action('post_verification','toggle_verification'))
    elif panel.page=='maintenance':
        if panel.pending_action:
            summary={'backup_now':'Create a database backup. No player assets are changed.','tier5_repair':'Repair stored mission data.','tier6_repair':'Repair stored economy data.'}[panel.pending_action]
            section('Review maintenance',summary+'\nConfirm only if you intend to run this operation.',action('confirm_maintenance:'+panel.pending_action,'cancel_maintenance'))
        else:
            section('Inspect system','Read-only health checks.',action('system_status'))
            section('Create a backup','Review first; the backup starts only after confirmation.',action('backup_now'))
            section('Repair data','These operations can change stored records. Use only when needed.',action('tier5_repair','tier6_repair'))
    elif panel.page=='assets':
        user_select=take(lambda c:isinstance(c,AdminUserSelect))
        asset_actions=action('asset_edit_money','asset_spawn_item','asset_remove_item','refresh')
        legacy_tools=take(lambda c:isinstance(c,staff_tools.ToolButton))
        for field in fields:section(field.name,field.value)
        section('Player Economy & Inventory Manager','Select target player, then edit money or spawn/remove backpack items directly.',user_select+asset_actions)
        if legacy_tools:section('Advanced Tool Wizards','Multi-step guided wizards.',legacy_tools)
    elif panel.page=='war_tools':
        user_select=take(lambda c:isinstance(c,AdminUserSelect))
        war_actions=action('war_edit_troops','war_edit_capital','war_edit_resources','war_toggle_status','refresh')
        legacy_tools=take(lambda c:isinstance(c,staff_tools.ToolButton))
        for field in fields:section(field.name,field.value)
        section('Armed Forces & Military Manager','Select a commander to inspect or edit army, air, navy, capital health and resources.',user_select+war_actions)
        if legacy_tools:section('Alliance War Tools','Guided war controls.',legacy_tools)
    elif panel.page=='server':
        channel_select=take(lambda c:isinstance(c,AdminChannelSelect))
        server_actions=action('server_toggle_level','server_set_level_channel','server_edit_template','server_post_gaming_roles','server_say','server_lottery_draw')
        legacy_tools=take(lambda c:isinstance(c,staff_tools.ToolButton))
        for field in fields:section(field.name,field.value)
        section('Server Channel & Action Controls','Select a channel above, then choose a setting or panel action below.',channel_select+server_actions)
        if legacy_tools:section('Other Server Tools','Guided server tools.',legacy_tools)
    elif panel.page in TOOL_GROUPS:
        for field in fields:section(field.name,field.value)
        inspections=take(lambda c:isinstance(c,staff_tools.ToolButton) and c.name in staff_tools.READ_ONLY)
        changes=take(lambda c:isinstance(c,staff_tools.ToolButton))
        if inspections:section('Inspect','Read-only player information.',inspections)
        if changes:section('Manage','Choose targets and review details before confirming changes.',changes)
    else:
        for field in fields:section(field.name,field.value)
    # Section navigation never sits between a record picker and its actions.
    switches=take(lambda c:isinstance(c,AdminPageSelect))
    if controls:add_rows(controls)
    add_rows(switches)
    return parts


class DetailsButton(discord.ui.Button):
    def __init__(self,panel):
        super().__init__(label='Read full answers' if panel.page=='applications' else 'Read full report',style=discord.ButtonStyle.primary)
        self.panel=panel
    async def callback(self,i):
        if await self.panel.interaction_check(i):
            await i.response.edit_message(embed=None,view=DetailsView(self.panel))


class DetailsView(discord.ui.LayoutView):
    def __init__(self,panel,page=0):
        super().__init__(timeout=900)
        self.panel=panel
        if panel.page=='applications':
            record=panel.selected_application()
            title=f"Application #{record['id']} · {record['user_name']}" if record else 'Application unavailable'
            rows=panel.db.execute('SELECT question,answer FROM application_answers WHERE submission_id=? ORDER BY question_id',(record['id'],)).fetchall() if record else []
            text='\n\n'.join(f"{r['question']}\n{r['answer']}" for r in rows) or 'No saved answers, or this application has already been reviewed.'
        else:
            record=panel.selected_feedback()
            title=f"Report #{record['id']} · {record['user_name']}" if record else 'Report unavailable'
            text=f"{record['title']}\n\n{record['details']}" if record else 'This report has already been reviewed or removed.'
        text=discord.utils.escape_markdown(text)
        pages=[text[n:n+3000] for n in range(0,len(text),3000)] or ['No content.']
        self.page=min(max(0,page),len(pages)-1)
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(f'-# ✦ X SYSTEM · ADMIN / READ ONLY\n# {discord.utils.escape_markdown(title)}\nPage {self.page+1} / {len(pages)}'),discord.ui.TextDisplay(pages[self.page]),accent_colour=0x36CFC9))
        if len(pages)>1:
            self.add_item(discord.ui.ActionRow(DetailNav(self,'‹ Previous',-1,self.page==0),DetailNav(self,'Next ›',1,self.page==len(pages)-1)))
        self.add_item(discord.ui.ActionRow(DetailNav(self,'‹ Back to review',0),DetailNav(self,'⌂ Home','page:home'),DetailNav(self,'Refresh','refresh'),DetailNav(self,'× Close','close')))
    async def interaction_check(self,i):return await self.panel.interaction_check(i)
    async def on_error(self,i,error,item):await self.panel.on_error(i,error,item)


class DetailNav(discord.ui.Button):
    def __init__(self,v,label,direction,disabled=False):
        super().__init__(label=label,disabled=disabled);self.v,self.direction=v,direction
    async def callback(self,i):
        if await self.v.panel.interaction_check(i):
            if isinstance(self.direction,str):
                if self.direction=='refresh':
                    await i.response.edit_message(embed=None,view=DetailsView(self.v.panel,self.v.page))
                else:await self.v.panel.handle_action(i,self.direction)
                return
            target=DetailsView(self.v.panel,self.v.page+self.direction) if self.direction else self.v.panel.clone()
            await i.response.edit_message(embed=None,view=target)
