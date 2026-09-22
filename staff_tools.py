"""Guided staff commands. Explicit catalogue; no dynamic command execution input."""
import asyncio
import inspect

import discord
from discord import app_commands

import advanced_systems


TOOLS = {
    'role': ('Members', 'Give / remove role'),
    'inrole': ('Members', 'Role members'),
    'inventory_check': ('Members', 'Inspect backpack'),
    'forces_check': ('Members', 'Inspect armed forces'),
    'setlevel': ('Members', 'Set activity level'),
    'deadzone_send': ('Members', 'Send to Deadzone'),
    'deadzone_restore': ('Members', 'Revive from Deadzone'),
    'deadzone_scan': ('Members', 'Scan inactive (7d)'),
    'deadzone_post': ('Members', 'Post Deadzone Board'),
    'level': ('Members', 'Check user level'),
    'spawn': ('Assets', 'Give items'),
    'remove_item': ('Assets', 'Remove items'),
    'economy_adjust': ('Assets', 'Adjust currency'),
    'say': ('Server', 'Post announcement'),
    'lottery_draw': ('Server', 'Draw lottery'),
    'war_start': ('War', 'Start alliance war'),
    'war_end': ('War', 'End alliance war'),
}
READ_ONLY = {'inrole', 'inventory_check', 'forces_check', 'level'}


def lookup(root, name, guild=None):
    if name not in TOOLS:
        return None
    tree = root.bot.tree
    command = tree.get_command(name, guild=guild) if guild else None
    return command or tree.get_command(name)


class Context:
    def __init__(self, interaction, command, user=None, response=None):
        self.original = interaction
        self.command = command
        self.user = user or interaction.user
        self.response = response or interaction.response

    def __getattr__(self, key):
        return getattr(self.original, key)


class ResultResponse:
    """Command replies remain private; the original command body owns mutations."""
    def __init__(self, interaction, root):
        self.interaction = interaction
        self.root = root

    def is_done(self):
        return False

    async def send_message(self, content=None, *, view=None, embed=None, ephemeral=True, **kwargs):
        if view is None:
            view = discord.ui.LayoutView(timeout=900)
            text = content or ''
            if embed:
                text += f'\n## {embed.title or "Result"}\n{embed.description or ""}'
                for field in embed.fields:
                    text += f'\n### {field.name}\n{field.value}'
            view.add_item(discord.ui.Container(discord.ui.TextDisplay('-# ✦ X SYSTEM · ADMIN / RESULT\n# ACTION RESULT\n'+(text[:3750] or 'Completed.')), accent_colour=0x36CFC9))
            content = None
        if isinstance(view, discord.ui.LayoutView):
            view.add_item(discord.ui.ActionRow(ReturnButton(self.root)))
        await self.interaction.edit_original_response(content=content, view=view, allowed_mentions=discord.AllowedMentions.none())


class ReturnButton(discord.ui.Button):
    def __init__(self, root):
        super().__init__(label='‹ Back to tools', style=discord.ButtonStyle.secondary)
        self.root = root

    async def callback(self, interaction):
        if await self.root.interaction_check(interaction):
            await interaction.response.edit_message(content=None, embed=None, view=self.root.clone())


class ToolButton(discord.ui.Button):
    def __init__(self, root, name):
        self.root, self.name = root, name
        super().__init__(label=TOOLS[name][1], style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        if not await self.root.interaction_check(interaction):
            return
        command = lookup(self.root, self.name, interaction.guild)
        if command is None:
            await interaction.response.send_message('This tool is unavailable in this server. Refresh after the Bot finishes loading.', ephemeral=True)
            return
        await interaction.response.edit_message(embed=None, view=ToolView(self.root, command))


class ToolSelect(discord.ui.Select):
    def __init__(self, root):
        self.root = root
        super().__init__(placeholder='Choose a management tool…', row=2, options=[
            discord.SelectOption(label=label, value=name, description=group)
            for name, (group, label) in TOOLS.items()])

    async def callback(self, interaction):
        if not await self.root.interaction_check(interaction):
            return
        command = lookup(self.root, self.values[0], interaction.guild)
        if command is None:
            await interaction.response.send_message('This tool is unavailable in this server. Refresh after the Bot finishes loading.', ephemeral=True)
            return
        await interaction.response.edit_message(embed=None, view=ToolView(self.root, command))


class InputButton(discord.ui.Button):
    def __init__(self, wizard, parameter):
        super().__init__(label=f'Edit {parameter.name.replace("_", " ")}'[:80])
        self.wizard, self.parameter = wizard, parameter

    async def callback(self, interaction):
        if await self.wizard.interaction_check(interaction):
            await interaction.response.send_modal(ValueModal(self.wizard, self.parameter))


class ValueModal(discord.ui.Modal):
    def __init__(self, wizard, parameter):
        super().__init__(title=parameter.name.replace('_', ' ').title())
        self.wizard, self.parameter = wizard, parameter
        self.entry = discord.ui.TextInput(label=parameter.description[:45],
            default=str(wizard.values.get(parameter.name, '')), required=parameter.required,
            placeholder=(f'Whole number: {parameter.min_value} to {parameter.max_value}' if parameter.type == discord.AppCommandOptionType.integer else None),
            max_length=1900 if parameter.name == 'message' else 200,
            style=discord.TextStyle.paragraph if parameter.name in {'message', 'reason'} else discord.TextStyle.short)
        self.add_item(self.entry)

    async def on_submit(self, interaction):
        if not await self.wizard.interaction_check(interaction):
            return
        raw = self.entry.value.strip()
        p = self.parameter
        try:
            value = parse_text(p, raw)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.wizard.values[p.name] = value
        await interaction.response.edit_message(view=self.wizard.rebuild())


def parse_text(parameter, raw):
    if not raw:
        if parameter.required:
            raise ValueError('This field is required.')
        return parameter.default
    if parameter.type == discord.AppCommandOptionType.integer:
        try:
            value = int(raw)
        except ValueError:
            raise ValueError('Enter a whole number.')
        if (parameter.min_value is not None and value < parameter.min_value) or (parameter.max_value is not None and value > parameter.max_value):
            raise ValueError(f'Allowed range: {parameter.min_value} to {parameter.max_value}.')
        if parameter.name == 'amount' and value == 0:
            raise ValueError('Amount cannot be zero.')
        return value
    return raw


class ResourceSelect:
    """Attach a callback to a native Discord selector without parsing mentions."""
    @staticmethod
    def build(wizard, p):
        base = dict(placeholder=f'Choose {p.name.replace("_", " ")}…', min_values=1, max_values=1)
        previous = wizard.values.get(p.name)
        if previous is not None:
            base['default_values'] = [discord.Object(id=previous.id)]
        if p.type == discord.AppCommandOptionType.user:
            select = discord.ui.UserSelect(**base)
        elif p.type == discord.AppCommandOptionType.role:
            select = discord.ui.RoleSelect(**base)
        else:
            select = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text], **base)
        async def callback(interaction):
            if await wizard.interaction_check(interaction):
                wizard.values[p.name] = select.values[0]
                await interaction.response.edit_message(view=wizard.rebuild())
        select.callback = callback
        return select


class OptionsSelect(discord.ui.Select):
    def __init__(self, wizard, p, options):
        self.wizard, self.parameter, self.source = wizard, p, options
        super().__init__(placeholder=f'Choose {p.name.replace("_", " ")}…', options=[
            discord.SelectOption(label=label[:100], value=str(i), default=wizard.values.get(p.name) == value)
            for i, (label, value) in enumerate(options)])

    async def callback(self, interaction):
        if await self.wizard.interaction_check(interaction):
            self.wizard.values[self.parameter.name] = self.source[int(self.values[0])][1]
            await interaction.response.edit_message(view=self.wizard.rebuild())


class StepButton(discord.ui.Button):
    def __init__(self, wizard, label, action):
        super().__init__(label=label, style=discord.ButtonStyle.primary if action == 'review' else discord.ButtonStyle.secondary)
        self.wizard, self.action = wizard, action

    async def callback(self, interaction):
        w = self.wizard
        if not await w.interaction_check(interaction):
            return
        if self.action == 'review':
            missing = [p.name for p in w.command.parameters if p.required and w.values.get(p.name) is None]
            if missing:
                await interaction.response.send_message('Choose or enter: ' + ', '.join(missing), ephemeral=True)
                return
            await interaction.response.edit_message(view=ConfirmView(w))
        elif self.action == 'clear':
            w.values.pop(w.command.parameters[w.step].name, None)
            await interaction.response.edit_message(view=w.rebuild())
        else:
            p = w.command.parameters[w.step]
            if self.action.startswith('options:'):
                w.offset = max(0, w.offset + int(self.action.split(':')[1]) * 25)
            else:
                w.step = (w.step + (1 if self.action == 'next' else -1)) % len(w.command.parameters)
                w.offset = 0
            await interaction.response.edit_message(view=w.rebuild())


class FieldSelect(discord.ui.Select):
    def __init__(self,wizard):
        self.wizard=wizard
        super().__init__(placeholder='Jump to a field…',options=[discord.SelectOption(
            label=f"{p.name.replace('_',' ').title()}",value=str(n),default=n==wizard.step,
            description='Selected' if wizard.values.get(p.name) is not None else 'Required · not set' if p.required else 'Optional · using default')
            for n,p in enumerate(wizard.command.parameters)])
    async def callback(self,i):
        if await self.wizard.interaction_check(i):
            self.wizard.step=int(self.values[0]);self.wizard.offset=0
            await i.response.edit_message(view=self.wizard.rebuild())


class ToolView(discord.ui.LayoutView):
    def __init__(self, root, command, values=None, step=0, offset=0):
        super().__init__(timeout=900)
        self.root, self.command = root, command
        self.values = dict(values or {})
        self.step, self.offset = step, offset
        required = [p for p in command.parameters if p.required]
        complete = sum(self.values.get(p.name) is not None for p in required)
        parts = [discord.ui.TextDisplay(f'-# ✦ X SYSTEM · ADMIN / {TOOLS[command.name][0]}\n# {TOOLS[command.name][1]}\n**Details → Review → Result** · Required fields **{complete}/{len(required)}**\nNothing changes before confirmation.')]
        if self.values:
            summary = '\n'.join(f"{p.name.replace('_',' ').title()}: {display(self.values[p.name])}" for p in command.parameters if p.name in self.values)
            parts.append(discord.ui.TextDisplay('### Your selection\n'+summary[:1700]))
        if command.parameters:
            if len(command.parameters)>1:
                parts.append(discord.ui.ActionRow(FieldSelect(self)))
            p = command.parameters[step]
            shown = self.values.get(p.name)
            parts.append(discord.ui.TextDisplay(f'### {step + 1} / {len(command.parameters)} · {p.name.replace("_", " ").title()}\n{p.description}\nSelected: **{display(shown)}**'))
            if p.type in {discord.AppCommandOptionType.user, discord.AppCommandOptionType.role, discord.AppCommandOptionType.channel}:
                parts.append(discord.ui.ActionRow(ResourceSelect.build(self, p)))
            else:
                options = [(c.name, c) for c in p.choices]
                if p.name == 'item':
                    options = [(r['name'], r['name']) for r in root.db.execute('SELECT name FROM items WHERE enabled=1 ORDER BY name')]
                elif 'alliance' in p.name:
                    options = [(r['name'], r['name']) for r in root.db.execute('SELECT name FROM alliances ORDER BY name')]
                if options:
                    self.offset = min(offset, (len(options)-1)//25*25)
                    parts.append(discord.ui.ActionRow(OptionsSelect(self, p, options[self.offset:self.offset+25])))
                    if len(options) > 25:
                        prev = StepButton(self, '‹ Choices', 'options:-1'); prev.disabled = self.offset == 0
                        nxt = StepButton(self, 'Choices ›', 'options:1'); nxt.disabled = self.offset + 25 >= len(options)
                        parts.append(discord.ui.ActionRow(prev, nxt))
                elif p.name == 'item' or 'alliance' in p.name:
                    parts.append(discord.ui.TextDisplay('No available records.'))
                else:
                    parts.append(discord.ui.ActionRow(InputButton(self, p)))
            field_buttons = [StepButton(self, '‹ Field', 'previous'), StepButton(self, 'Next field ›', 'next')]
            field_buttons[0].disabled = step == 0
            field_buttons[1].disabled = step == len(command.parameters)-1
            if not p.required:
                field_buttons.append(StepButton(self, 'Use default', 'clear'))
            parts.append(discord.ui.ActionRow(*field_buttons))
        parts.append(discord.ui.ActionRow(StepButton(self, 'Review & Confirm', 'review'), ReturnButton(root)))
        self.add_item(discord.ui.Container(*parts, accent_colour=0x36CFC9))

    def rebuild(self):
        return ToolView(self.root, self.command, self.values, self.step, self.offset)

    async def interaction_check(self, interaction):
        return await self.root.interaction_check(interaction)

    async def on_error(self, interaction, error, item):
        await self.root.on_error(interaction, error, item)


def display(value):
    if value is None:
        return 'Not selected / default'
    if isinstance(value, app_commands.Choice):
        return value.name
    if hasattr(value, 'id'):
        return discord.utils.escape_markdown(str(getattr(value, 'display_name', getattr(value, 'name', 'Selected'))))[:100] + f' · ID {value.id}'
    return discord.utils.escape_markdown(str(getattr(value, 'display_name', getattr(value, 'name', value))))[:1900]


class ConfirmView(discord.ui.LayoutView):
    def __init__(self, wizard):
        super().__init__(timeout=300)
        self.wizard = wizard
        self.lock = asyncio.Lock()
        self.used = False
        self.role_state = None
        if wizard.command.name == 'role':
            member, role = wizard.values.get('member'), wizard.values.get('role')
            if member is not None and role is not None:
                self.role_state = any(r.id == role.id for r in getattr(member, 'roles', []))
        summary = '\n'.join(f'**{p.name.replace("_", " ").title()}:** {display(wizard.values.get(p.name, p.default))}' for p in wizard.command.parameters)
        if self.role_state is not None:
            summary += '\n**Action:** ' + ('Remove role' if self.role_state else 'Give role')
        self.round_id = self.current_round()
        if wizard.command.name in {'lottery_draw', 'war_end'}:
            summary += f'\n**Current round / war:** {self.round_id or "None"}'
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(f'-# ✦ X SYSTEM · ADMIN\n# Review · {TOOLS[wizard.command.name][1]}\n{summary}\n\n' + ('Read-only inspection.' if wizard.command.name in READ_ONLY else 'This can change server state or player assets. Confirm only after checking every field.')), accent_colour=0x36CFC9))
        confirm = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.danger if wizard.command.name not in READ_ONLY else discord.ButtonStyle.primary)
        confirm.callback = self.execute
        edit = discord.ui.Button(label='‹ Edit')
        async def back(interaction):
            if await wizard.interaction_check(interaction):
                await interaction.response.edit_message(view=wizard.rebuild())
        edit.callback = back
        self.add_item(discord.ui.ActionRow(edit, confirm, ReturnButton(wizard.root)))

    def current_round(self):
        name = self.wizard.command.name
        table = 'lottery_rounds' if name == 'lottery_draw' else 'wars' if name == 'war_end' else None
        if table is None:
            return None
        row = self.wizard.root.db.execute(f'SELECT id FROM {table} WHERE active=1 ORDER BY id DESC LIMIT 1').fetchone()
        return row[0] if row else None

    async def execute(self, interaction):
        w = self.wizard
        if not await w.interaction_check(interaction):
            return
        async with self.lock:
            if self.used:
                await interaction.response.send_message('This confirmation has already been used. Check the result before starting another action.', ephemeral=True)
                return
            self.used = True
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                if interaction.guild is None:
                    raise ValueError('Use this panel inside a server.')
                actor = await interaction.guild.fetch_member(interaction.user.id)
                ctx = Context(interaction, w.command, actor, ResultResponse(interaction, w.root))
                if not w.root.staff_check(ctx):
                    raise ValueError('Your staff access has changed. Reopen /admin.')
                permissions = w.command.default_permissions
                if permissions and not actor.guild_permissions.administrator and (actor.guild_permissions.value & permissions.value) != permissions.value:
                    raise ValueError('You do not have the Discord permissions required by this command.')
                if not await advanced_systems.command_access_check(ctx, w.root.db):
                    return
                for check in w.command.checks:
                    result = check(ctx)
                    if inspect.isawaitable(result):
                        result = await result
                    if not result:
                        raise ValueError('This command is not available to you here.')
                args = {}
                for p in w.command.parameters:
                    value = w.values.get(p.name, p.default)
                    if value is None and p.required:
                        raise ValueError(f'{p.name} is required.')
                    if value is not None and p.type == discord.AppCommandOptionType.user:
                        value = await interaction.guild.fetch_member(value.id)
                    elif value is not None and p.type == discord.AppCommandOptionType.role:
                        value = next((r for r in await interaction.guild.fetch_roles() if r.id == value.id), None)
                        if value is None:
                            raise ValueError('The selected role no longer exists.')
                    elif value is not None and p.type == discord.AppCommandOptionType.channel:
                        value = await interaction.guild.fetch_channel(value.id)
                        if not isinstance(value, discord.TextChannel):
                            raise ValueError('Choose a normal text channel.')
                    if value is not None and p.type == discord.AppCommandOptionType.integer:
                        value = parse_text(p, str(value))
                    args[p.name] = value
                if self.role_state is not None and any(r.id == args['role'].id for r in args['member'].roles) != self.role_state:
                    raise ValueError('Role membership changed. Reopen the tool and review the new action.')
                if self.current_round() != self.round_id:
                    raise ValueError('The active round or war changed. Reopen the tool before confirming.')
                await w.command.callback(ctx, **args)
            except ValueError as error:
                await ResultResponse(interaction, w.root).send_message(str(error))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Staff tool failed: %s', w.command.name)
                await ResultResponse(interaction, w.root).send_message('Could not finish. Check the current state and logs before retrying; this confirmation cannot run twice.')
