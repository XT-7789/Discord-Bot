"""Shared presentation only. Keeps every existing route and form endpoint."""
import re

SETTINGS_PANEL = '''<section class="panel"><h2>Economy settings</h2>
<div class="notice">One save applies the whole form. Active rules are read on the next action or refresh; see field notes for exceptions.
Existing orders keep their costs and finish times; current VIP expiry dates stay unchanged.</div>
<form class="fields" method="post" action="{{setting_action}}" data-settings-grouped>
{% for group, fields in setting_groups %}<fieldset class="setting-group"><legend>{{group}}</legend><div class="fields-grid">
{% for key,spec in fields %}<label>{{spec.impact}}
{% if key.endswith('_enabled') %}<select name="{{key}}" aria-describedby="hint-{{key}}">
<option value="1" {% if setting_values.get(key)=='1' %}selected{% endif %}>Enabled</option>
<option value="0" {% if setting_values.get(key)=='0' %}selected{% endif %}>Disabled</option></select>
{% else %}<input name="{{key}}" type="number" step="1" min="{{spec.minimum}}" max="{{spec.maximum}}" value="{{setting_values.get(key,'')}}" required aria-describedby="hint-{{key}}">{% endif %}
<small class="setting-hint" id="hint-{{key}}">{{spec.unit}} · {{spec.range}}. {{spec.timing}}
{% if key in ['mine_cooldown','mine_crystal_chance'] %}<a href="{{url_for('mining_control')}}">Open Mining Areas</a>{% elif key in ['collect_cooldown','land_income_per_land'] %}<a href="{{url_for('war_control')}}">Open War settings</a>{% endif %}</small></label>{% endfor %}
</div></fieldset>{% endfor %}<button type="submit">Save settings</button></form></section>'''


def setting_groups(keys):
    import economy_settings_admin
    groups = {}
    for key in keys:
        spec = economy_settings_admin.metadata(key)
        groups.setdefault(spec['group'], []).append((key, spec))
    return list(groups.items())

GROUPS = {
    'Overview': {'home', 'dashboard_users'},
    'Players': {'players', 'leveling_control'},
    'Economy': {'settings', 'tier6_economy_control', 'mining_control', 'items', 'item_shop_control',
        'recipes_control', 'finance_control', 'role_shop_control', 'market_control',
        'auction_control', 'casino_control', 'research_control'},
    'War': {'war_control', 'diplomacy_control'},
    'Administration': {'applications_control', 'reward_codes_control', 'command_access',
        'log_settings_control', 'dashboard_access', 'logs', 'logout'},
}


def header(source):
    start = source.index('<nav id="dashboard-nav">')
    end = source.index('</nav>', start)
    links = re.findall(r'<a\b[^>]*>.*?</a>', source[start:end], re.S)
    buckets = {name: [] for name in GROUPS}
    for link in links:
        match = re.search(r"url_for\('([^']+)'", link)
        endpoint = match[1] if match else ''
        group = next((name for name, endpoints in GROUPS.items() if endpoint in endpoints), 'Administration')
        buckets[group].append(link)
    navigation = '<nav id="dashboard-nav" aria-label="Administration navigation"><div class="nav-brand">X<span>CONTROL</span></div><label class="nav-search">Find a page<input type="search" id="page-search" placeholder="Search pages…" autocomplete="off"></label><p id="page-search-empty" hidden>No matching pages.</p>' + ''.join(
        f'<section class="nav-group"><h2>{name}</h2>{"".join(items)}</section>' for name, items in buckets.items())
    result = source[:start] + navigation + source[end:]
    result = result.replace('⚔️ X BOT Admin', 'X SYSTEM')
    result = result.replace('id="mobile-menu"', 'id="mobile-menu" aria-label="Toggle navigation"')
    result = result.replace('</head>', '''<link rel="stylesheet" href="{{url_for('static',filename='admin-system.css')}}">
        <script id="setting-metadata" type="application/json">{{setting_metadata|tojson}}</script>
        <script defer src="{{url_for('static',filename='admin-system.js')}}"></script></head>''')
    result = result.replace('<main>', '''<main id="main-content"><div class="page-heading"><div><span>X SYSTEM / CONTROL CENTRE</span><h1>{{title}}</h1><p>Manage your community, games and economy.</p></div><a class="btn secondary" href="{{url_for('home')}}">Overview</a></div><nav class="page-index" aria-label="On this page"></nav>''')
    result = result.replace("filename='admin-system.css'", "filename='admin-system.css',v='play-dashboard-2'")
    result = result.replace("filename='admin-system.js'", "filename='admin-system.js',v='play-dashboard-2'")
    result = result.replace('messages=get_flashed_messages()', 'messages=get_flashed_messages(with_categories=true)')
    result = result.replace('for message in messages', 'for category,message in messages')
    result = result.replace('<div class="flash">', '<div role="status" class="flash {{category}}">')
    return result
