"""Server-role conveniences. No role cache, asset writes or subscription grants."""
from contextlib import contextmanager
from contextvars import ContextVar
import functools

DEFAULTS={'server_svip_production_slots':'2','server_svip_market_listings':'5','server_svip_production_percent':'10'}
_member=ContextVar('svip_interaction_member',default=None)

@contextmanager
def context(member):
    token=_member.set(member)
    try:yield
    finally:_member.reset(token)

def interaction_context(fn):
    @functools.wraps(fn)
    async def wrapped(*args,**kwargs):
        interaction=next((a for a in args if hasattr(a,'user') and hasattr(a,'response')),None)
        with context(interaction.user if interaction is not None else None):
            return await fn(*args,**kwargs)
    return wrapped

def setting(db,key,default):
    row=db.execute('SELECT value FROM economy_settings WHERE key=?',(key,)).fetchone()
    return int(row[0]) if row else int(default)

def benefits(db,uid):
    member=_member.get()
    role=setting(db,'server_svip_role_id',0)
    active=bool(member is not None and getattr(member,'id',None)==uid and getattr(member,'guild',None) is not None and role and any(getattr(r,'id',None)==role for r in getattr(member,'roles',())))
    return dict(active=active,
        slots=max(0,min(25,setting(db,'server_svip_production_slots',2))) if active else 0,
        listings=max(0,min(100,setting(db,'server_svip_market_listings',5))) if active else 0,
        percent=max(0,min(95,setting(db,'server_svip_production_percent',10))) if active else 0)

def production_limit(db,uid):return setting(db,'tier6_production_queue_limit',5)+benefits(db,uid)['slots']
def market_limit(db,uid):return max(1,setting(db,'tier6_market_max_listings',20))+benefits(db,uid)['listings']
def summary(db,uid):
    b=benefits(db,uid)
    if not b['active']:return ''
    return f"💎 **SVIP** · Queue +{b['slots']} · Listings +{b['listings']} · Production time −{b['percent']}%"
