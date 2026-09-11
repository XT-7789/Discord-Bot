"""Validation metadata, not configuration. Values remain in economy_settings.

Used by every Dashboard editor of these keys and by the read-only staff panel.
No migrations, defaults writes, player mutations or network activity on import.
"""
import json
import re
import time

import economy
import economy_extra
import casino
import tier6
import tier8
import advanced_systems
import svip

GENERAL_KEYS = tuple(('starting_xc work_cooldown collect_cooldown land_income_per_land '
    'work_crystal_chance mine_cooldown mine_crystal_chance exchange_xc_to_war_percent '
    'exchange_war_to_xc_percent daily_reward daily_cooldown transfer_min transfer_max '
    'transfer_tax_percent market_enabled market_fee_percent market_min_price market_max_price '
    'attack_cooldown capital_damage capital_reward_percent capital_repair_cost_per_hp '
    'fortified_defense_bonus aggressive_attack_bonus scout_cost scout_cooldown '
    'demobilize_refund_percent fortify_base_cost fortify_power_percent fortify_max_level '
    'casino_enabled casino_min_bet casino_max_bet lottery_ticket_price lottery_starting_prize lottery_prize_ratio').split())
TIER6_KEYS = tuple(tier6.DEFAULTS) + ('daily_reward', 'daily_cooldown', 'transfer_tax_percent',
    'market_enabled', 'market_fee_percent', 'market_min_price', 'market_max_price')
VIP_KEYS = ('casino_vip_daily_cost', 'casino_vip_duration_seconds', 'casino_vip_cooldown_percent',
    'server_svip_cooldown_percent', 'casino_cooldown_seconds', 'crash_daily_net_win_limit') + tuple(svip.DEFAULTS)
RESEARCH_KEYS = ('tier8_enabled', 'tier8_queue_limit')
MINING_KEYS = ('mining_energy_enabled', 'mining_max_energy', 'mining_energy_regen_amount',
    'mining_energy_regen_seconds', 'mining_starter_pickaxe_enabled', 'mining_collection_xc_reward', 'mining_collection_xcrystal_reward')
TOGGLE_KEYS = ('casino_enabled', 'market_enabled', 'auction_enabled', 'recipes_enabled',
    'bills_enabled', 'income_enabled', 'role_shop_enabled', 'economy_shop_enabled')
KEYS = set(GENERAL_KEYS + TIER6_KEYS + VIP_KEYS + RESEARCH_KEYS + MINING_KEYS + TOGGLE_KEYS)
LEGACY_KEYS = {'collect_cooldown', 'land_income_per_land', 'mine_cooldown', 'mine_crystal_chance'}
# Fallbacks are the Bot's existing definitions, never a second editable store.
DEFAULTS = {**economy.DEFAULT_SETTINGS, **economy_extra.DEFAULTS, **casino.CASINO_DEFAULTS, **tier6.DEFAULTS, **advanced_systems.DEFAULTS}


def bounds(key):
    if key.endswith('_enabled'):
        return 0, 1
    special = {'tier8_queue_limit': (1, 6), 'tier6_production_queue_limit': (1, 25),
        'tier6_market_expiry_days': (1, 365), 'tier6_market_max_listings': (1, 100),
        'tier6_stock_max_change_percent': (1, 50), 'casino_vip_duration_seconds': (60, 9223372036854775807),
        'tier6_stock_update_seconds': (60, 9223372036854775807),
        'tier6_production_seconds_per_item': (30, 9223372036854775807),
        'mining_max_energy': (1, 9223372036854775807),
        'mining_energy_regen_amount': (1, 9223372036854775807),
        'mining_energy_regen_seconds': (10, 9223372036854775807),
        'tier6_industrial_speed_cap_percent': (0, 95), 'casino_vip_cooldown_percent': (0, 95),
        'server_svip_cooldown_percent': (0, 95), 'server_svip_production_slots':(0,25),
        'server_svip_market_listings':(0,100),'server_svip_production_percent':(0,95)}
    if key in special:
        return special[key]
    if 'percent' in key or key.endswith('_chance') or key in {'lottery_prize_ratio', 'tier6_stock_price_impact'}:
        return 0, 100
    minimum = 1 if key in {'casino_min_bet', 'casino_max_bet', 'lottery_ticket_price',
        'market_min_price', 'market_max_price', 'tier6_stock_holding_limit', 'fortify_max_level', 'capital_damage'} else 0
    return minimum, 9223372036854775807


def integer(raw, label, low, high):
    label = label.replace('_', ' ').capitalize()
    text = str(raw).strip()
    if not re.fullmatch(r'-?\d+', text) or len(text) > 20:
        raise ValueError(f'{label}: enter a whole number ({low}–{high}).')
    value = int(text)
    if not low <= value <= high:
        raise ValueError(f'{label}: allowed range is {low}–{high}. Nothing was saved.')
    return value


def metadata(key):
    if key not in KEYS:
        return None
    if key.startswith(('casino_', 'server_svip_', 'lottery_', 'crash_')):
        group, reader = 'Casino / VIP', 'casino.setting'
    elif key == 'recipes_enabled':
        group, reader = 'Production / Research', 'advanced_systems.setting'
    elif key in {'auction_enabled', 'role_shop_enabled', 'economy_shop_enabled'}:
        group, reader = 'Items / Market', 'economy.setting' if key == 'economy_shop_enabled' else 'advanced_systems.setting'
    elif key in {'bills_enabled', 'income_enabled'}:
        group, reader = 'Accounts / Earnings', 'advanced_systems.setting'
    elif key.startswith(('tier6_production', 'tier6_industrial', 'tier8_')):
        group, reader = 'Production / Research', ('tier8.enabled' if key == 'tier8_enabled' else 'tier8.quote') if key.startswith('tier8') else 'tier6.setting'
    elif 'market' in key or 'stock' in key:
        group, reader = 'Items / Market', 'tier6.setting' if key.startswith('tier6') else 'economy_extra.setting'
    elif key in GENERAL_KEYS[18:30]:
        group, reader = 'War', 'war_tier.setting'
    else:
        group = 'Accounts / Earnings'
        reader = 'tier6.setting' if key.startswith('tier6') else 'economy_extra.setting' if key in economy_extra.DEFAULTS else 'economy.setting'
    unit = '0 = off · 1 = on' if key.endswith('_enabled') else '%' if 'percent' in key or 'chance' in key or key in {'lottery_prize_ratio', 'tier6_stock_price_impact'} else 'seconds' if 'seconds' in key or 'cooldown' in key else 'days' if key.endswith('_days') else 'count' if any(t in key for t in ('limit', 'listings', 'level')) else 'XC' if any(t in key for t in ('xc', 'bet', 'price', 'daily_reward', 'transfer_', 'lottery_', 'daily_cost', 'win_limit')) else 'units'
    unit = {'land_income_per_land': 'War Credits / Land', 'scout_cost': 'War Credits',
        'fortified_defense_bonus': '%', 'aggressive_attack_bonus': '%',
        'crash_daily_net_win_limit': 'XC',
        'capital_repair_cost_per_hp': 'War Credits / HP', 'fortify_base_cost': 'War Credits',
        'capital_damage': 'HP', 'tier6_production_seconds_per_item': 'seconds / item',
        'mining_max_energy': 'energy / player', 'mining_energy_regen_amount': 'energy / tick',
        'mining_collection_xcrystal_reward': 'XCrystals'}.get(key, unit)
    lo, hi = bounds(key)
    timing = 'Next action / refresh.'
    if key == 'starting_xc':
        timing = 'New player accounts only.'
    elif key == 'mining_starter_pickaxe_enabled':
        reader = 'economy.initialise'
        timing = 'Restart required: initialization grants starter tools to eligible players.'
    elif key in LEGACY_KEYS:
        reader = 'No active reader (legacy)'
        timing = 'Legacy only. Current rules are in Mining Areas.' if key.startswith('mine_') else 'Legacy only. Current City rules are in War.'
    elif key == 'casino_cooldown_seconds':
        timing = 'Fallback only; individual game cooldowns take priority.'
    elif key in {'casino_vip_daily_cost', 'casino_vip_duration_seconds'}:
        timing = 'New VIP purchases; existing expiry dates stay unchanged.'
    elif key == 'lottery_starting_prize':
        timing = 'New Lottery rounds; existing prize pools stay unchanged.'
    elif key in {'mining_collection_xc_reward', 'mining_collection_xcrystal_reward'}:
        timing = 'Future collection completions only.'
    if key in {'tier6_market_expiry_days', 'tier6_market_max_listings'}:
        reader = 'economy_extra.tier6_setting'
    if key in svip.DEFAULTS:
        reader='svip.benefits → tier6.start_production / economy_transactions.quote'
        timing='Next confirmed order; existing jobs and listings remain unchanged. Verified server SVIP only.'
        unit='%' if key.endswith('_percent') else 'extra slots' if key.endswith('_slots') else 'extra listings'
    return dict(group=group, reader=reader, unit=unit, minimum=lo, maximum=str(hi),
        range=f'{lo} or more' if hi == 9223372036854775807 else f'{lo}–{hi}',
        impact=key.replace('tier6_', '').replace('tier8_', '').replace('_', ' ').capitalize(),
        timing=timing)


def audit(db, actor, endpoint, changes):
    db.execute('''INSERT INTO dashboard_audit_logs
        (actor_id,actor_name,access_level,endpoint,method,detail,status_code,created_at)
        VALUES(?,?,?,?,?,?,?,?)''', (*actor, endpoint, 'POST', json.dumps(changes), 200, int(time.time())))


def save(db, submitted, allowed, actor, endpoint):
    """Caller supplies a dedicated connection. Audit and changes share one commit."""
    allowed = set(allowed) & KEYS
    unknown = set(submitted) - allowed
    if unknown:
        raise ValueError('Unexpected setting field. Nothing was saved.')
    values = {k: str(integer(v, k, *bounds(k))) for k, v in submitted.items()}
    if not values:
        raise ValueError('No settings submitted. Nothing was saved.')
    try:
        db.execute('BEGIN IMMEDIATE')
        previous = dict(db.execute('SELECT key,value FROM economy_settings'))
        effective = {**DEFAULTS, **previous, **values}
        for low, high in (('transfer_min', 'transfer_max'), ('market_min_price', 'market_max_price'), ('casino_min_bet', 'casino_max_bet')):
            if {low, high} & values.keys() and int(effective[low]) > int(effective[high]):
                raise ValueError(f'{low.replace("_", " ").capitalize()} must not exceed {high.replace("_", " ")}. Nothing was saved.')
        if {'casino_min_bet', 'casino_max_bet'} & values.keys():
            for game in db.execute('SELECT game,min_bet,max_bet FROM casino_game_settings'):
                low = game['min_bet'] or int(effective['casino_min_bet'])
                high = game['max_bet'] or int(effective['casino_max_bet'])
                if low > high:
                    raise ValueError(f"{game['game']}: inherited bet limits conflict. Nothing was saved.")
        changes = {k: {'before': previous.get(k), 'after': v} for k, v in values.items() if previous.get(k) != v}
        db.executemany('INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', values.items())
        audit(db, actor, endpoint, changes)
        db.commit()
        return changes
    except Exception:
        db.rollback()
        raise


def status_sections(db):
    rows = dict(db.execute('SELECT key,value FROM economy_settings'))
    groups = [('Features', ('tier6_economy_enabled', 'tier6_contracts_enabled', 'bills_enabled', 'income_enabled')),
        ('Daily', ('daily_reward', 'daily_cooldown')),
        ('Market', ('market_enabled', 'tier6_stock_enabled', 'economy_shop_enabled', 'auction_enabled', 'role_shop_enabled')),
        ('Casino', ('casino_enabled', 'casino_cooldown_seconds')),
        ('Production / Research', ('recipes_enabled', 'tier6_production_enabled', 'tier6_production_queue_limit', 'tier8_enabled', 'tier8_queue_limit'))]
    for title, keys in groups:
        lines = []
        for key in keys:
            value = rows.get(key, DEFAULTS.get(key))
            shown = 'Unavailable' if value is None else ('On' if value == '1' else 'Off') if key.endswith('_enabled') else f'{value} {metadata(key)["unit"]}'
            lines.append(f'{metadata(key)["impact"]}: **{shown}**')
        yield title, '\n'.join(lines)


def save_game(db, game, submitted, actor, endpoint):
    limits = {'enabled': (0, 1), 'min_bet': (0, 9223372036854775807),
        'max_bet': (0, 9223372036854775807), 'cooldown_seconds': (0, 9223372036854775807)}
    values = {k: integer(submitted[k], k, *limit) for k, limit in limits.items()}
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM casino_game_settings WHERE game=?', (game,)).fetchone()
        if row is None:
            raise ValueError('Unknown Casino game. Nothing was saved.')
        minimum = values['min_bet'] or casino.setting(db, 'casino_min_bet')
        maximum = values['max_bet'] or casino.setting(db, 'casino_max_bet')
        if minimum > maximum:
            raise ValueError('Effective minimum bet exceeds maximum bet. Nothing was saved.')
        changes = {f'{game}.{k}': {'before': row[k], 'after': v} for k, v in values.items() if row[k] != v}
        db.execute('UPDATE casino_game_settings SET enabled=?,min_bet=?,max_bet=?,cooldown_seconds=? WHERE game=?', (*values.values(), game))
        audit(db, actor, endpoint, changes)
        db.commit()
    except Exception:
        db.rollback()
        raise


def technology_limits(code):
    spec = next((t for t in tier8.TECHS if t[0] == code), None)
    if spec is None:
        raise ValueError('Unknown research project. Nothing was saved.')
    return {'max_level': (1, 10), 'base_cost': (0, 1000000), 'seconds': (0, 86400),
            'bonus_per_level': (0, spec[5]), 'bonus_cap': (0, spec[5]), 'enabled': (0, 1)}


def save_technology(db, code, submitted, actor, endpoint):
    limits = technology_limits(code)
    values = {key: integer(submitted[key], key, *limit) for key, limit in limits.items()}
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM tier8_technologies WHERE code=?', (code,)).fetchone()
        if row is None:
            raise ValueError('Research project is unavailable. Nothing was saved.')
        changes = {f'{code}.{key}': {'before': row[key], 'after': value} for key, value in values.items() if row[key] != value}
        db.execute('''UPDATE tier8_technologies SET max_level=?,base_cost=?,seconds=?,
            bonus_per_level=?,bonus_cap=?,enabled=? WHERE code=?''', (*values.values(), code))
        audit(db, actor, endpoint, changes)
        db.commit()
    except Exception:
        db.rollback()
        raise
