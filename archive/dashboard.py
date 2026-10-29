"""X BOT V3.0 administration dashboard."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, render_template_string, request, session, url_for

import economy
import casino
import war_system
import war_tier
import economy_extra
import advanced_systems
import leveling
import applications
import tier4
import tier5
import tier6
import tier7
import tier8
import economy_settings_admin as settings_admin
import dashboard_ui
import casual_games

load_dotenv()
DATABASE_PATH = Path(__file__).resolve().parent / "xwar.db"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    raise ValueError("DASHBOARD_PASSWORD was not found. Add it to your .env file.")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://127.0.0.1:5000/auth/discord/callback")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DASHBOARD_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
DASHBOARD_OWNER_ID = os.getenv("DASHBOARD_OWNER_ID", "")
OAUTH_CONFIGURED = all((DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, DISCORD_GUILD_ID))

app = Flask(__name__)
fallback_secret = hashlib.sha256(("xbot-dashboard:" + DISCORD_CLIENT_SECRET + DASHBOARD_PASSWORD).encode()).digest()
app.secret_key = os.getenv("DASHBOARD_SECRET") or fallback_secret


@app.template_filter("timestamp")
def format_timestamp(value):
    if not value:
        return "—"
    return datetime.fromtimestamp(int(value)).strftime("%d %b %Y, %H:%M")


app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Discord is only needed when the Dashboard session is first created.
    # Keep the signed-in browser session for three months unless the member
    # explicitly logs out or clears browser data.
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    SESSION_REFRESH_EACH_REQUEST=True,
)

AUTOMATED_DASHBOARD_NAMES = {
    "Test",
    "UI Test",
    "Automated Test",
    "Council Test",
    "Reward Test",
    "War Regression",
    "Test Render",
    "Codex Test",
}

REGION_PICKER_CACHE = None


def is_automated_dashboard_session(user_id, user_name):
    """Keep local regression/render clients out of the real user session list."""
    normalized_id = str(user_id or "").strip().lower()
    normalized_name = str(user_name or "").strip()
    return normalized_name in AUTOMATED_DASHBOARD_NAMES or normalized_id.startswith("codex-")


def get_db():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


with get_db() as startup_db:
    economy.initialise(startup_db)
    casino.initialise(startup_db)
    war_system.initialise(startup_db)
    war_tier.initialise(startup_db)
    economy_extra.initialise(startup_db)
    advanced_systems.initialise(startup_db)
    leveling.initialise(startup_db)
    applications.initialise(startup_db)
    tier4.initialise(startup_db)
    tier5.initialise(startup_db)
    tier6.initialise(startup_db)
    tier7.initialise(startup_db)
    tier8.initialise(startup_db)
    casual_games.initialise(startup_db)
    # Performance Indexes to fix slow loading
    startup_db.execute("CREATE INDEX IF NOT EXISTS idx_pw_uid ON player_war_units(user_id);")
    startup_db.execute("CREATE INDEX IF NOT EXISTS idx_inv_uid ON inventories(user_id);")
    startup_db.execute("CREATE INDEX IF NOT EXISTS idx_ml_sid ON market_listings(seller_id);")
    startup_db.execute("CREATE INDEX IF NOT EXISTS idx_am_uid ON alliance_members(user_id);")
    startup_db.execute("CREATE INDEX IF NOT EXISTS idx_xp_uid ON xp_profiles(user_id);")

    startup_db.execute("""CREATE TABLE IF NOT EXISTS dashboard_role_access(
        role_id TEXT PRIMARY KEY, access_level TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1
    )""")
    startup_db.execute("""CREATE TABLE IF NOT EXISTS dashboard_audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
        access_level TEXT NOT NULL, endpoint TEXT NOT NULL, method TEXT NOT NULL,
        detail TEXT NOT NULL, status_code INTEGER NOT NULL, created_at INTEGER NOT NULL
    )""")
    startup_db.execute("""CREATE TABLE IF NOT EXISTS dashboard_sessions(
        session_key TEXT PRIMARY KEY,user_id TEXT NOT NULL,user_name TEXT NOT NULL,
        access_level TEXT NOT NULL,login_at INTEGER NOT NULL,last_seen INTEGER NOT NULL,
        last_endpoint TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1
    )""")
    # Personal Dashboard codes are an alternative to Discord OAuth for trusted
    # Moderators.  Only a hash is stored, so a code cannot be recovered from
    # the database after it has been created.
    startup_db.execute("""CREATE TABLE IF NOT EXISTS dashboard_access_codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        access_level TEXT NOT NULL DEFAULT 'admin',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER NOT NULL DEFAULT 0,
        expires_at INTEGER NOT NULL DEFAULT 0
    )""")
    startup_db.execute("""DELETE FROM dashboard_sessions AS old WHERE EXISTS(
        SELECT 1 FROM dashboard_sessions AS newer WHERE newer.user_id=old.user_id AND
        (newer.last_seen>old.last_seen OR (newer.last_seen=old.last_seen AND newer.rowid>old.rowid))
    )""")
    startup_db.execute("""DELETE FROM dashboard_sessions
        WHERE user_name IN ('Test','UI Test','Automated Test','Council Test','Reward Test','War Regression','Test Render','Codex Test')
        OR lower(user_id) LIKE 'codex-%'""")
    startup_db.execute("CREATE UNIQUE INDEX IF NOT EXISTS dashboard_sessions_one_account ON dashboard_sessions(user_id)")
    startup_db.execute("""CREATE TABLE IF NOT EXISTS discord_resource_cache(
        kind TEXT NOT NULL,resource_id TEXT NOT NULL,name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL,
        PRIMARY KEY(kind,resource_id)
    )""")
    startup_db.execute("INSERT OR IGNORE INTO dashboard_role_access(role_id,access_level,label) VALUES('1531176720097476708','admin','Administration')")
    # This is the original X Council role, now renamed Moderator in Discord.
    # Role IDs do not change when a role is renamed, so every Moderator gets
    # full Dashboard access automatically.
    startup_db.execute("INSERT OR IGNORE INTO dashboard_role_access(role_id,access_level,label) VALUES('1523954152701431848','admin','Moderator')")
    startup_db.execute("UPDATE dashboard_role_access SET access_level='admin',label='Moderator',enabled=1 WHERE role_id='1523954152701431848'")
    startup_db.commit()

startup_db.close()

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("dashboard_role"):
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


COUNCIL_ENDPOINTS = {"home", "players", "logs", "logout"}
VIEWER_ENDPOINTS = {"home", "logout"}
ECONOMY_MANAGER_DENIED = {
    "dashboard_access", "save_dashboard_access", "command_access",
    "log_settings_control", "settings",
}


@app.before_request
def enforce_dashboard_access():
    if request.endpoint in {None, "login", "logout", "discord_login", "discord_callback", "static"}:
        return None
    level = session.get("dashboard_role")
    if not level:
        return redirect(url_for("login"))
    session_key = session.get("dashboard_session_key")
    if not session_key:
        session_key = secrets.token_urlsafe(18); session["dashboard_session_key"] = session_key
    tracked_user_id = str(session.get("discord_user_id", "local-owner"))
    tracked_user_name = session.get("discord_name", "Local Owner")
    if not is_automated_dashboard_session(tracked_user_id, tracked_user_name):
        now = int(time.time()); tracking_db = get_db()
        try:
            tracking_db.execute("""INSERT INTO dashboard_sessions(session_key,user_id,user_name,access_level,login_at,last_seen,last_endpoint,active)
                VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(user_id) DO UPDATE SET session_key=excluded.session_key,user_name=excluded.user_name,
                access_level=excluded.access_level,login_at=excluded.login_at,last_seen=excluded.last_seen,last_endpoint=excluded.last_endpoint,active=1""",
                (session_key, tracked_user_id, tracked_user_name, level, now, now, request.endpoint or "",))
            tracking_db.commit()
        except sqlite3.OperationalError:
            # Session presence is informational. A short database writer must
            # never turn the requested Dashboard page into HTTP 500.
            tracking_db.rollback()
        finally:
            tracking_db.close()
    if level in {"owner", "admin", "council"}:
        return None
    if request.method != "GET":
        abort(403)
    if level == "council" and request.endpoint not in COUNCIL_ENDPOINTS:
        abort(403)
    if level == "viewer" and request.endpoint not in VIEWER_ENDPOINTS:
        abort(403)
    if level == "economy_manager" and request.endpoint in ECONOMY_MANAGER_DENIED:
        abort(403)
    return None


@app.after_request
def audit_dashboard_change(response):
    if (request.method in {"POST", "PUT", "PATCH", "DELETE"} and session.get("dashboard_role")
            and request.endpoint != "login" and not getattr(g, "skip_dashboard_audit", False)):
        safe_form = {key: ("[redacted]" if any(word in key.lower() for word in ("password", "secret", "access_code", "token", "csrf")) else str(value)[:200]) for key, value in request.form.items()}
        db = None
        try:
            db = get_db()
            db.execute("""INSERT INTO dashboard_audit_logs(actor_id,actor_name,access_level,endpoint,method,detail,status_code,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (str(session.get("discord_user_id", "local-owner")), session.get("discord_name", "Local Owner"),
                session.get("dashboard_role", "unknown"), request.endpoint or "unknown", request.method,
                json.dumps(safe_form, ensure_ascii=False), response.status_code, int(time.time())))
            db.commit()
        except sqlite3.OperationalError:
            # An audit record must never turn a successful Dashboard action into a 500 error.
            if db is not None:
                db.rollback()
        finally:
            if db is not None:
                db.close()
    return response


def save_economy_form(allowed, destination, *, values=None, game=None, technology=None):
    """Existing role checks run before this handler; never broaden access."""
    g.skip_dashboard_audit = True
    db = None
    try:
        db = get_db()
        db.execute('PRAGMA busy_timeout = 3000')
        actor = (str(session.get("discord_user_id", "local-owner")),
                 session.get("discord_name", "Local Owner"), session.get("dashboard_role", "unknown"))
        if technology is not None:
            settings_admin.save_technology(db, technology, request.form, actor, request.endpoint)
        elif game is not None:
            settings_admin.save_game(db, game, request.form, actor, request.endpoint)
        else:
            payload = values if values is not None else {k: v for k, v in request.form.items() if k not in {"action", "csrf"}}
            settings_admin.save(db, payload, allowed, actor, request.endpoint)
        flash("Saved. See field notes for when changes apply. Existing Discord messages stay unchanged.", "success")
    except (ValueError, KeyError) as error:
        flash(str(error), "error")
    except sqlite3.Error:
        flash("Not saved. Database unavailable or busy. No settings were changed; please retry.", "error")
    finally:
        if db is not None:
            db.close()
    return redirect(url_for(destination))


def record_dashboard_audit(db, action, detail, status_code=200):
    """Add a human-readable Dashboard change record before committing."""
    db.execute("""INSERT INTO dashboard_audit_logs(actor_id,actor_name,access_level,endpoint,method,detail,status_code,created_at)
        VALUES(?,?,?,?,?,?,?,?)""", (
        str(session.get("discord_user_id", "local-owner")),
        session.get("discord_name", "Local Owner"),
        session.get("dashboard_role", "unknown"),
        "war_unit", action, detail, status_code, int(time.time()),
    ))





def admin_page(title, body, **context):
    context.setdefault("server_roles", discord_server_roles())
    context.setdefault("picker_channels", context.get("server_channels") or discord_server_text_channels())
    context.setdefault("server_members", discord_server_members())
    body = body.replace(" @Role / Role ID", " Role").replace(" @Role / ID", " Role").replace(" @Roles / IDs", " Roles")
    body = body.replace(" @Users / IDs", " Users").replace("Discord @Role or Role ID", "Discord Role").replace("Role ID:", "Role:")
    if title == "War Control":
        # Edit Model is a navigation action, so jump directly to its open form
        # instead of leaving phone users at an unrelated position.
        body = body.replace('<details class="creator" {% if edit_unit or creating %}open{% endif %}>',
                            '<details id="war-unit-editor" class="creator" {% if edit_unit or creating %}open{% endif %}>')
        body = body.replace("href=\"{{url_for('war_control',edit_unit=u['id'])}}\"",
                            "href=\"{{url_for('war_control',edit_unit=u['id'])}}#war-unit-editor\"")
    elif title == "Jobs":
        body = body.replace('<section class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Job</h2>',
                            '<section id="job-editor" class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Job</h2>')
        body = body.replace("href=\"{{url_for('jobs',edit=j['id'])}}\"",
                            "href=\"{{url_for('jobs',edit=j['id'])}}#job-editor\"")
    elif title == "Items & Categories":
        body = body.replace("href=\"{{url_for('items',edit=i['id'])}}\"",
                            "href=\"{{url_for('items',edit=i['id'])}}#item-editor\"")
    rendered_body = render_template_string(body, **context)
    return render_template('legacy.html', title=title, body=rendered_body, setting_metadata={k: settings_admin.metadata(k) for k in settings_admin.KEYS}, **context)




@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("login_mode") == "access_code":
            supplied_code = request.form.get("access_code", "").strip()
            code_hash = hashlib.sha256(supplied_code.encode()).hexdigest()
            now = int(time.time()); db = get_db()
            record = db.execute("""SELECT * FROM dashboard_access_codes
                WHERE code_hash=? AND enabled=1 AND (expires_at=0 OR expires_at>=?)""", (code_hash, now)).fetchone()
            if record:
                db.execute("UPDATE dashboard_access_codes SET last_used_at=? WHERE id=?", (now, record["id"]))
                db.commit(); db.close()
                session.clear(); session.permanent = True
                session["dashboard_role"] = record["access_level"]
                session["discord_user_id"] = f"access-code-{record['id']}"
                session["discord_name"] = record["label"]
                return redirect(url_for("home"))
            db.close(); flash("That access code is invalid, expired, or has been revoked.")
        elif hmac.compare_digest(request.form.get("password", ""), DASHBOARD_PASSWORD):
            session.clear(); session.permanent = True
            session["dashboard_role"] = "owner"; session["discord_name"] = "Local Owner"
            return redirect(url_for("home"))
        else:
            flash("Incorrect password.")
    return render_template('login.html', oauth_configured=OAUTH_CONFIGURED)


def discord_api(url, token=None, data=None):
    headers = {"User-Agent": "X-BOT-Dashboard/1.4B"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode())


def _cache_discord_resources(kind, rows):
    if not rows:
        return
    db = get_db(); now = int(time.time())
    db.execute("DELETE FROM discord_resource_cache WHERE kind=?", (kind,))
    db.executemany("INSERT INTO discord_resource_cache(kind,resource_id,name,position,updated_at) VALUES(?,?,?,?,?)",
        [(kind, str(row["id"]), row["name"], int(row.get("position", 0)), now) for row in rows])
    db.commit(); db.close()


def _cached_discord_resources(kind):
    db = get_db(); rows = db.execute("SELECT resource_id id,name,position FROM discord_resource_cache WHERE kind=? ORDER BY position,name", (kind,)).fetchall(); db.close()
    return [dict(row) for row in rows]


def discord_server_text_channels():
    """Return server text/announcement channels without exposing the Bot token to the browser."""
    if not DASHBOARD_BOT_TOKEN or not DISCORD_GUILD_ID:
        return _cached_discord_resources("channel")
    request_object = urllib.request.Request(
        f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/channels",
        headers={"Authorization": f"Bot {DASHBOARD_BOT_TOKEN}", "User-Agent": "X-BOT-Dashboard/1.4B"},
    )
    try:
        with urllib.request.urlopen(request_object, timeout=10) as response:
            rows = json.loads(response.read().decode())
        result = sorted((row for row in rows if row.get("type") in {0, 5}), key=lambda row: (row.get("position", 0), row.get("name", "")))
        _cache_discord_resources("channel", result); return result
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return _cached_discord_resources("channel")


def discord_server_roles():
    """Return selectable guild roles; the Bot token never reaches the browser."""
    if not DASHBOARD_BOT_TOKEN or not DISCORD_GUILD_ID:
        return _cached_discord_resources("role")
    request_object = urllib.request.Request(
        f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/roles",
        headers={"Authorization": f"Bot {DASHBOARD_BOT_TOKEN}", "User-Agent": "X-BOT-Dashboard/1.4B"},
    )
    try:
        with urllib.request.urlopen(request_object, timeout=10) as response:
            rows = json.loads(response.read().decode())
        result = sorted(
            (row for row in rows if row.get("name") != "@everyone" and not row.get("managed")),
            key=lambda row: (-row.get("position", 0), row.get("name", "").lower()),
        )
        _cache_discord_resources("role", result); return result
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return _cached_discord_resources("role")


def discord_server_members():
    """Return Discord members for searchable user pickers, with database fallback."""
    members = {}
    if DASHBOARD_BOT_TOKEN and DISCORD_GUILD_ID:
        request_object = urllib.request.Request(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members?limit=1000",
            headers={"Authorization": f"Bot {DASHBOARD_BOT_TOKEN}", "User-Agent": "X-BOT-Dashboard/1.5"},
        )
        try:
            with urllib.request.urlopen(request_object, timeout=10) as response:
                rows = json.loads(response.read().decode())
            for row in rows:
                user = row.get("user", {})
                user_id = str(user.get("id", ""))
                if not user_id or user.get("bot"):
                    continue
                name = row.get("nick") or user.get("global_name") or user.get("username") or user_id
                members[user_id] = {"id": user_id, "name": name}
            _cache_discord_resources("member", list(members.values()))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
            members.update({str(row["id"]): {"id": str(row["id"]), "name": row["name"]} for row in _cached_discord_resources("member")})
    try:
        db = get_db()
        for row in db.execute("SELECT user_id,display_name,nation_name FROM players ORDER BY display_name,nation_name").fetchall():
            user_id = str(row["user_id"])
            members.setdefault(user_id, {"id": user_id, "name": row["display_name"] or row["nation_name"] or user_id})
        db.close()
    except sqlite3.Error:
        pass
    return sorted(members.values(), key=lambda row: row["name"].lower())


@app.route("/auth/discord")
def discord_login():
    if not OAUTH_CONFIGURED:
        flash("Discord OAuth is not configured."); return redirect(url_for("login"))
    state = secrets.token_urlsafe(32); session["oauth_state"] = state
    query = urllib.parse.urlencode({"client_id": DISCORD_CLIENT_ID, "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code", "scope": "identify guilds.members.read", "state": state})
    return redirect(f"https://discord.com/oauth2/authorize?{query}")


@app.route("/auth/discord/callback")
def discord_callback():
    if not OAUTH_CONFIGURED or not request.args.get("state") or not hmac.compare_digest(request.args.get("state", ""), session.pop("oauth_state", "")):
        flash("Discord login state was invalid. Please try again."); return redirect(url_for("login"))
    if not request.args.get("code"):
        flash("Discord login was cancelled."); return redirect(url_for("login"))
    try:
        token_data = discord_api("https://discord.com/api/v10/oauth2/token", data={"client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET, "grant_type": "authorization_code", "code": request.args["code"],
            "redirect_uri": DISCORD_REDIRECT_URI})
        access_token = token_data["access_token"]
        user = discord_api("https://discord.com/api/v10/users/@me", access_token)
        member = discord_api(f"https://discord.com/api/v10/users/@me/guilds/{DISCORD_GUILD_ID}/member", access_token)
    except (KeyError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        flash("Discord could not verify your server membership."); return redirect(url_for("login"))
    db = get_db(); mappings = db.execute("SELECT role_id,access_level FROM dashboard_role_access WHERE enabled=1").fetchall(); db.close()
    role_ids = set(member.get("roles", [])); priority = {"viewer": 1, "council": 2, "economy_manager": 3, "admin": 4}
    levels = [row["access_level"] for row in mappings if row["role_id"] in role_ids]
    level = "owner" if DASHBOARD_OWNER_ID and user["id"] == DASHBOARD_OWNER_ID else max(levels, key=lambda value: priority.get(value, 0), default=None)
    if not level:
        flash("Your Discord roles do not have Dashboard access."); return redirect(url_for("login"))
    session.clear(); session.permanent = True
    session["dashboard_role"] = level; session["discord_user_id"] = user["id"]
    session["discord_name"] = member.get("nick") or user.get("global_name") or user["username"]
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session_key = session.get("dashboard_session_key")
    if session_key:
        db = get_db(); db.execute("UPDATE dashboard_sessions SET active=0,last_seen=? WHERE session_key=?", (int(time.time()), session_key)); db.commit(); db.close()
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard-users")
@login_required
def dashboard_users():
    now = int(time.time()); db = get_db()
    rows = db.execute("""SELECT * FROM dashboard_sessions
        WHERE last_seen>=? ORDER BY active DESC,last_seen DESC""", (now - 30 * 86400,)).fetchall()
    db.close()
    rows = [row for row in rows if not is_automated_dashboard_session(row["user_id"], row["user_name"])]
    body = render_template('dashboard/users_online.html', rows=rows, now=now)
    return admin_page("Dashboard Online", body, rows=rows, now=now)


@app.errorhandler(403)
def forbidden(_error):
    if not session.get("dashboard_role"):
        return redirect(url_for("login"))
    body = """<section class="panel"><h2>🔒 Access Denied</h2><div class="notice">Your Dashboard access level is <b>{{session.get('dashboard_role')}}</b>. This page or action requires a higher permission level.</div><div class="pad"><a class="btn" href="{{url_for('home')}}">Return to Overview</a></div></section>"""
    return admin_page("Access Denied", body), 403


@app.route("/")
@login_required
def home():
    db = get_db()
    stats = db.execute("""SELECT COUNT(*) nations,COALESCE(SUM(xc),0) xc,COALESCE(SUM(bank_xc),0) bank_xc,COALESCE(SUM(money),0) war_credits,COALESCE(SUM(xcrystals),0) crystals,COALESCE(SUM(land),0) land,
        COALESCE((SELECT SUM(w.quantity*u.power) FROM player_war_units w JOIN war_unit_types u ON u.id=w.unit_type_id WHERE u.enabled=1),0) power,
        (SELECT COUNT(*) FROM items WHERE enabled=1) items,0 jobs,
        (SELECT COUNT(*) FROM market_listings WHERE active=1) listings,(SELECT COUNT(*) FROM battle_history) battles FROM players""").fetchone()
    active_war = db.execute("""SELECT w.*,a.name attacker_name,a.tag attacker_tag,d.name defender_name,d.tag defender_tag FROM wars w JOIN alliances a ON a.id=w.attacker_alliance_id JOIN alliances d ON d.id=w.defender_alliance_id WHERE w.active=1 ORDER BY w.id DESC LIMIT 1""").fetchone()
    recent = db.execute("SELECT * FROM economy_logs ORDER BY id DESC LIMIT 8").fetchall()
    setting_keys = ("economy_shop_enabled", "market_enabled", "auction_enabled", "casino_enabled", "recipes_enabled", "bills_enabled", "income_enabled", "applications_enabled", "verification_enabled", "xp_enabled")
    setting_values = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in setting_keys)})", setting_keys
    ).fetchall()}
    systems = (
        {"label": "Item Shop", "key": "economy_shop_enabled", "endpoint": "settings"},
        {"label": "Market", "key": "market_enabled", "endpoint": "market_control"},
        {"label": "Auction", "key": "auction_enabled", "endpoint": "auction_control"},
        {"label": "Casino", "key": "casino_enabled", "endpoint": "casino_control"},
        {"label": "Recipes", "key": "recipes_enabled", "endpoint": "recipes_control"},
        {"label": "Bills", "key": "bills_enabled", "endpoint": "finance_control"},
        {"label": "Income", "key": "income_enabled", "endpoint": "finance_control"},
        {"label": "Applications", "key": "applications_enabled", "endpoint": "applications_control"},
        {"label": "Verification", "key": "verification_enabled", "endpoint": "applications_control"},
        {"label": "Level XP", "key": "xp_enabled", "endpoint": "leveling_control"},
    )
    systems = [dict(system, enabled=setting_values.get(system["key"], "1") == "1") for system in systems]
    all_item_ids = [row["id"] for row in db.execute("SELECT id FROM items").fetchall()]
    unused_items = sum(not item_usage_details(db, item_id) for item_id in all_item_ids)
    disabled_items = db.execute("SELECT COUNT(*) count FROM items WHERE enabled=0").fetchone()["count"]
    pending_applications = db.execute("SELECT COUNT(*) count FROM application_submissions WHERE status='pending'").fetchone()["count"]
    leaders = db.execute("""SELECT x.*,COALESCE(NULLIF(p.display_name,''),CAST(x.user_id AS TEXT)) display_name
        FROM xp_profiles x LEFT JOIN players p ON p.user_id=x.user_id ORDER BY x.total_xp DESC LIMIT 50""").fetchall()
    # Engagement stats for today
    today_start = int(time.time()) - (int(time.time()) % 86400)
    try:
        active_today = db.execute("SELECT COUNT(DISTINCT user_id) c FROM xp_profiles WHERE last_message_xp>=? OR last_voice_xp>=?", (today_start, today_start)).fetchone()["c"]
        xp_today = db.execute("SELECT COALESCE(SUM(weekly_xp),0) c FROM xp_profiles WHERE last_message_xp>=?", (today_start,)).fetchone()["c"]
        games_today = db.execute("SELECT COUNT(*) c FROM casual_game_completions WHERE completed_at>=?", (today_start,)).fetchone()["c"]
        active_streaks = db.execute("SELECT COUNT(*) c FROM xp_profiles WHERE current_streak>0").fetchone()["c"]
        engagement = {"active_today": active_today, "xp_today": xp_today, "games_today": games_today, "active_streaks": active_streaks}
    except Exception:
        engagement = None
    db.close()
    body = render_template('dashboard/home.html',
        s=stats, war=active_war, recent=recent, systems=systems,
        unused_items=unused_items, disabled_items=disabled_items,
        pending_applications=pending_applications, top_players=leaders[:5],
        engagement=engagement)
    return admin_page("Overview", body, s=stats, war=active_war, recent=recent, systems=systems, unused_items=unused_items, disabled_items=disabled_items, pending_applications=pending_applications)


@app.route("/levels", methods=["GET", "POST"])
@login_required
def leveling_control():
    db = get_db()
    keys = tuple(leveling.DEFAULTS)
    if request.method == "POST":
        numeric = {"xp_message_min", "xp_message_max", "xp_message_cooldown", "xp_voice_per_minute"}
        toggles = {"xp_enabled", "xp_message_enabled", "xp_voice_enabled", "xp_announcement_enabled"}
        for key in keys:
            if key in toggles:
                value = "1" if request.form.get(key) == "1" else "0"
            elif key in numeric:
                value = str(settings_admin.integer(request.form.get(key, 0), key, 0, 999999999))
            elif key in {"xp_ignored_channel_ids", "xp_ignored_role_ids"}:
                value = ",".join(filter(None, (mention_id(item) for item in request.form.getlist(key))))
            else:
                value = request.form.get(key, "").strip()
            db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        db.commit(); flash("Level XP and announcement settings saved.")
    values = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in keys)})", keys).fetchall()}
    rewards = db.execute("SELECT * FROM xp_rewards ORDER BY level,id").fetchall()
    item_rows = db.execute("SELECT id,name,emoji FROM items WHERE enabled=1 ORDER BY name").fetchall()
    streak_rewards = db.execute("""SELECT s.*,i.name item_name,i.emoji item_emoji FROM streak_rewards s
        LEFT JOIN items i ON i.id=s.item_id ORDER BY s.days""").fetchall()
    leaders = db.execute("""SELECT x.*,COALESCE(NULLIF(p.display_name,''),CAST(x.user_id AS TEXT)) display_name
        FROM xp_profiles x LEFT JOIN players p ON p.user_id=x.user_id ORDER BY x.total_xp DESC LIMIT 50""").fetchall()
    db.close(); server_channels = discord_server_text_channels()
    body = """<section class="panel"><h2>⭐ XP Earning & Announcements</h2><div class="notice">Choose ignored channels and roles directly from Discord. Announcement placeholders: <code>{mention}</code>, <code>{user}</code>, <code>{level}</code>, <code>{reward}</code>.</div><form class="fields" method="post"><div class="fields-grid">
    <label>Level System<select name="xp_enabled"><option value="1" {% if v['xp_enabled']=='1' %}selected{% endif %}>Open</option><option value="0" {% if v['xp_enabled']!='1' %}selected{% endif %}>Closed</option></select></label>
    <label>Message XP<select name="xp_message_enabled"><option value="1" {% if v['xp_message_enabled']=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if v['xp_message_enabled']!='1' %}selected{% endif %}>Disabled</option></select></label><label>Message XP Minimum<input type="number" min="0" name="xp_message_min" value="{{v['xp_message_min']}}"></label><label>Message XP Maximum<input type="number" min="0" name="xp_message_max" value="{{v['xp_message_max']}}"></label><label>Message Cooldown (seconds)<input type="number" min="0" name="xp_message_cooldown" value="{{v['xp_message_cooldown']}}"></label>
    <label>Voice XP<select name="xp_voice_enabled"><option value="1" {% if v['xp_voice_enabled']=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if v['xp_voice_enabled']!='1' %}selected{% endif %}>Disabled</option></select></label><label>Voice XP per Minute<input type="number" min="0" name="xp_voice_per_minute" value="{{v['xp_voice_per_minute']}}"></label><label>Ignored Channels<textarea name="xp_ignored_channel_ids">{{v['xp_ignored_channel_ids']}}</textarea><span class="picker-help">XP is not earned in any selected channel.</span></label><label>Ignored Roles<textarea name="xp_ignored_role_ids">{{v['xp_ignored_role_ids']}}</textarea><span class="picker-help">Members with any selected role do not earn XP.</span></label>
    <label>Level Announcements<select name="xp_announcement_enabled"><option value="1" {% if v['xp_announcement_enabled']=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if v['xp_announcement_enabled']!='1' %}selected{% endif %}>Disabled</option></select></label><label>Announcement Channel<select name="xp_announcement_channel_id"><option value="0">Server System Channel</option>{% for channel in server_channels %}<option value="{{channel['id']}}" {% if v['xp_announcement_channel_id']==channel['id'] %}selected{% endif %}># {{channel['name']}}</option>{% endfor %}</select>{% if not server_channels %}<span class="bad">X BOT could not load server channels. Check that the Bot is online and DISCORD_GUILD_ID is correct.</span>{% endif %}</label><label>Default Announcement<textarea name="xp_announcement_template">{{v['xp_announcement_template']}}</textarea></label></div><button>Save XP Settings</button></form></section>
    <details class="creator"><summary class="btn">＋ Create Level Reward</summary><section class="panel"><h2>Create Reward Role</h2><form class="fields" method="post" action="{{url_for('save_level_reward')}}"><div class="fields-grid"><label>Level<input type="number" min="2" name="level" value="2" required></label><label>Name<input name="name" placeholder="Member" required></label><label>Emoji<input name="emoji" value="🏅"></label><label>Discord @Role / Role ID<input name="role_id" required></label><label>Reward Behaviour<select name="reward_mode"><option value="permanent">Permanent — keep forever (recommended for Level 2–5)</option><option value="exclusive">Title — replace previous title (recommended after Level 5)</option></select></label><label>Custom Announcement (optional)<textarea name="announcement" placeholder="Leave empty to use the default announcement"></textarea></label></div><button>Create Reward</button></form></section></details>
    <section class="panel"><h2>🎁 Level Reward Roles</h2><div class="notice">Set Level 2–5 rewards to <b>Permanent</b>. Set Active, Elite, Senior and later ranks to <b>Title</b>; X BOT then removes the previous title when a higher one is earned.</div><div class="library">{% for r in rewards %}<article class="library-card"><form method="post" action="{{url_for('save_level_reward')}}"><input type="hidden" name="id" value="{{r['id']}}"><h3>{{r['emoji']}} {{r['name']}}</h3><div class="fields-grid"><label>Level<input type="number" min="2" name="level" value="{{r['level']}}"></label><label>Name<input name="name" value="{{r['name']}}"></label><label>Emoji<input name="emoji" value="{{r['emoji']}}"></label><label>@Role / ID<input name="role_id" value="{{r['role_id']}}"></label><label>Behaviour<select name="reward_mode"><option value="permanent" {% if r['reward_mode']=='permanent' %}selected{% endif %}>Permanent</option><option value="exclusive" {% if r['reward_mode']=='exclusive' %}selected{% endif %}>Title / Replace</option></select></label><label>Status<select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select></label><label>Custom Announcement<textarea name="announcement">{{r['announcement']}}</textarea></label></div><div class="actions"><button>Save</button><button class="danger" name="action" value="delete">Delete</button></div></form></article>{% else %}<p class="notice">No reward roles configured. Create Level 2–5 permanent rewards first.</p>{% endfor %}</div></section>
    <section class="panel"><h2>🏆 Level Leaderboard</h2><table><tr><th>#</th><th>User</th><th>Level</th><th>Total XP</th><th>Weekly XP</th><th>Streak</th><th>Message / Voice</th></tr>{% for r in leaders %}<tr><td>{{loop.index}}</td><td>{{r['display_name']}}<br><span class="muted">{{r['user_id']}}</span></td><td>{{r['level']}}</td><td>{{r['total_xp']}}</td><td>{{r['weekly_xp']}}</td><td>🔥 {{r['current_streak']}} days<br><span class="muted">Best {{r['best_streak']}}</span></td><td>{{r['message_xp']}} / {{r['voice_xp']}}</td></tr>{% else %}<tr><td colspan="7">No XP activity yet.</td></tr>{% endfor %}</table></section>"""
    create_reward_fields = """<label>Reward Item<select name="item_id"><option value="">No item</option>{% for i in item_rows %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Item Quantity<input type="number" min="0" name="item_quantity" value="0"></label><label>XC Reward<input type="number" min="0" name="xc" value="0"></label><label>War Credits Reward<input type="number" min="0" name="war_credits" value="0"></label><label>XCrystals Reward<input type="number" min="0" name="xcrystals" value="0"></label>"""
    edit_reward_fields = """<label>Reward Item<select name="item_id"><option value="">No item</option>{% for i in item_rows %}<option value="{{i['id']}}" {% if r['item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Item Quantity<input type="number" min="0" name="item_quantity" value="{{r['item_quantity']}}"></label><label>XC Reward<input type="number" min="0" name="xc" value="{{r['xc']}}"></label><label>War Credits Reward<input type="number" min="0" name="war_credits" value="{{r['war_credits']}}"></label><label>XCrystals Reward<input type="number" min="0" name="xcrystals" value="{{r['xcrystals']}}"></label>"""
    body = body.replace('<label>Custom Announcement (optional)<textarea name="announcement"', create_reward_fields + '<label>Custom Announcement (optional)<textarea name="announcement"', 1)
    body = body.replace('<label>Custom Announcement<textarea name="announcement">{{r[\'announcement\']}}</textarea></label>', edit_reward_fields + '<label>Custom Announcement<textarea name="announcement">{{r[\'announcement\']}}</textarea></label>')
    streak_panel = """<section class="panel"><h2>🔥 Daily Activity Streak Rewards</h2><div class="notice">A streak advances on the member's first XP activity each UTC day. Missing a day resets the streak to Day 1. Existing XP and level settings are preserved.</div><details class="creator"><summary class="btn">＋ Create Streak Reward</summary><form class="fields" method="post" action="{{url_for('save_streak_reward')}}"><div class="fields-grid"><label>Streak Days<input type="number" min="1" name="days" value="3" required></label><label>Reward Item<select name="item_id"><option value="">No item</option>{% for i in item_rows %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Item Quantity<input type="number" min="0" name="item_quantity" value="0"></label><label>XC<input type="number" min="0" name="xc" value="0"></label><label>War Credits<input type="number" min="0" name="war_credits" value="0"></label><label>XCrystals<input type="number" min="0" name="xcrystals" value="0"></label><label>Status<select name="enabled"><option value="1">Enabled</option><option value="0">Disabled</option></select></label></div><button>Save Streak Reward</button></form></details><div class="library">{% for s in streak_rewards %}<article class="library-card"><h3>🔥 Day {{s['days']}}</h3><div class="statline">{{s['item_emoji'] or '📦'}} Item: {{s['item_quantity']}}x {{s['item_name'] or 'None'}}<br>🪙 {{s['xc']}} XC · ⚔️ {{s['war_credits']}} War Credits · 💎 {{s['xcrystals']}} XCrystals</div><form class="fields" method="post" action="{{url_for('save_streak_reward')}}"><input type="hidden" name="id" value="{{s['id']}}"><div class="fields-grid"><label>Days<input type="number" min="1" name="days" value="{{s['days']}}"></label><label>Item<select name="item_id"><option value="">No item</option>{% for i in item_rows %}<option value="{{i['id']}}" {% if s['item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Quantity<input type="number" min="0" name="item_quantity" value="{{s['item_quantity']}}"></label><label>XC<input type="number" min="0" name="xc" value="{{s['xc']}}"></label><label>War Credits<input type="number" min="0" name="war_credits" value="{{s['war_credits']}}"></label><label>XCrystals<input type="number" min="0" name="xcrystals" value="{{s['xcrystals']}}"></label><label>Status<select name="enabled"><option value="1" {% if s['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not s['enabled'] %}selected{% endif %}>Disabled</option></select></label></div><div class="actions"><button>Save</button><button class="danger" name="action" value="delete">Delete</button></div></form></article>{% else %}<p class="notice">No streak rewards configured yet.</p>{% endfor %}</div></section>"""
    return admin_page("Levels & XP", body + streak_panel, v=values, rewards=rewards, leaders=leaders, server_channels=server_channels, item_rows=item_rows, streak_rewards=streak_rewards)


@app.post("/levels/reward/save")
@login_required
def save_level_reward():
    db = get_db(); action = request.form.get("action", "save"); reward_id = request.form.get("id")
    if action == "delete" and reward_id:
        db.execute("DELETE FROM xp_rewards WHERE id=?", (int(reward_id),)); flash("Level reward deleted.")
    else:
        role_text = request.form.get("role_id", "").strip(); role_id = "".join(c for c in role_text if c.isdigit())
        item_id = int(request.form["item_id"]) if request.form.get("item_id") else None
        values = (settings_admin.integer(request.form["level"], "level", 2, 999999999), role_id, request.form["name"].strip(), request.form.get("emoji", "🏅").strip() or "🏅", request.form.get("reward_mode", "exclusive"), request.form.get("announcement", "").strip(), int(request.form.get("enabled", 1)), item_id, settings_admin.integer(request.form.get("item_quantity", 0), "item_quantity", 0, 999999999), settings_admin.integer(request.form.get("xc", 0), "xc", 0, 999999999), settings_admin.integer(request.form.get("war_credits", 0), "war_credits", 0, 999999999), settings_admin.integer(request.form.get("xcrystals", 0), "xcrystals", 0, 999999999))
        if reward_id:
            db.execute("UPDATE xp_rewards SET level=?,role_id=?,name=?,emoji=?,reward_mode=?,announcement=?,enabled=?,item_id=?,item_quantity=?,xc=?,war_credits=?,xcrystals=? WHERE id=?", values + (int(reward_id),))
        else:
            db.execute("INSERT INTO xp_rewards(level,role_id,name,emoji,reward_mode,announcement,enabled,item_id,item_quantity,xc,war_credits,xcrystals) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)
        flash("Level reward saved.")
    db.commit(); db.close(); return redirect(url_for("leveling_control"))


@app.post("/levels/streak/save")
@login_required
def save_streak_reward():
    db=get_db(); reward_id=request.form.get("id"); action=request.form.get("action","save")
    if action=="delete" and reward_id:
        db.execute("DELETE FROM streak_rewards WHERE id=?",(int(reward_id),)); flash("Streak reward deleted.")
    else:
        item_id=int(request.form["item_id"]) if request.form.get("item_id") else None
        values=(settings_admin.integer(request.form.get("days", 1), "days", 1, 999999999),item_id,settings_admin.integer(request.form.get("item_quantity", 0), "item_quantity", 0, 999999999),settings_admin.integer(request.form.get("xc", 0), "xc", 0, 999999999),settings_admin.integer(request.form.get("war_credits", 0), "war_credits", 0, 999999999),settings_admin.integer(request.form.get("xcrystals", 0), "xcrystals", 0, 999999999),int(request.form.get("enabled",1)))
        try:
            if reward_id: db.execute("UPDATE streak_rewards SET days=?,item_id=?,item_quantity=?,xc=?,war_credits=?,xcrystals=?,enabled=? WHERE id=?",values+(int(reward_id),))
            else: db.execute("INSERT INTO streak_rewards(days,item_id,item_quantity,xc,war_credits,xcrystals,enabled) VALUES(?,?,?,?,?,?,?)",values)
            flash("Streak reward saved.")
        except sqlite3.IntegrityError: flash("A streak reward already exists for that day.")
    db.commit();db.close();return redirect(url_for("leveling_control"))


@app.route("/players")
@login_required
def players():
    db = get_db()
    search = request.args.get("q", "").strip(); sort = request.args.get("sort", "wallet")
    order = {"wallet":"p.xc DESC", "bank":"p.bank_xc DESC", "crystals":"p.xcrystals DESC", "power":"power DESC", "name":"display_name"}.get(sort, "p.xc DESC")
    rows = db.execute(f"""SELECT p.*,NULL job_name,a.name alliance_name,a.tag alliance_tag,
        COALESCE(x.level,1) activity_level,COALESCE(x.total_xp,0) activity_xp,
        COALESCE((SELECT SUM(w.quantity*u.power) FROM player_war_units w JOIN war_unit_types u ON u.id=w.unit_type_id WHERE w.user_id=p.user_id AND u.enabled=1),0) power,
        COALESCE((SELECT SUM(quantity) FROM inventories WHERE user_id=p.user_id),0) inventory_count,
        COALESCE((SELECT COUNT(*) FROM market_listings WHERE seller_id=p.user_id AND active=1),0) listing_count
        FROM players p LEFT JOIN alliance_members am ON am.user_id=p.user_id LEFT JOIN alliances a ON a.id=am.alliance_id LEFT JOIN xp_profiles x ON x.user_id=p.user_id
        WHERE (?='' OR p.display_name LIKE ? OR p.nation_name LIKE ? OR CAST(p.user_id AS TEXT) LIKE ?) ORDER BY {order}""", (search, f"%{search}%", f"%{search}%", f"%{search}%")).fetchall()
    db.close()
    body = render_template('dashboard/players.html', rows=rows, search=search, sort=sort)
    return admin_page("Users", body, rows=rows, search=search, sort=sort)


PLAYER_FORM = """
<section class="panel"><h2>Edit User Profile</h2><div class="notice"><b>Discord Name:</b> {{player['display_name'] or 'Unknown'}} · <b>Discord ID:</b> {{player['user_id']}} · <b>Nation:</b> {{player['nation_name']}}<br>The Discord ID remains the safe database key; the Dashboard displays the username everywhere possible.</div><form class="fields" method="post"><div class="fields-grid">
<label>Nation Name<input name="nation_name" value="{{player['nation_name']}}" required></label><label>Capital Name<input name="capital_name" value="{{player['capital_name']}}" required></label><label>Wallet XC<input type="number" min="0" name="xc" value="{{player['xc']}}" required></label><label>Bank XC<input type="number" min="0" name="bank_xc" value="{{player['bank_xc']}}" required></label><label>War Credits<input type="number" min="0" name="money" value="{{player['money']}}" required></label><label>XCrystals<input type="number" min="0" name="xcrystals" value="{{player['xcrystals']}}" required></label>
<label>Land<input type="number" min="0" name="land" value="{{player['land']}}" required></label><label>Capital HP<input type="number" min="0" max="100" name="capital_health" value="{{player['capital_health']}}" required></label>
<label>Fortification Level<input type="number" min="0" name="fortification_level" value="{{war_state['fortification_level'] if war_state else 0}}"></label><label>Military Morale (%)<input type="number" min="0" max="100" name="morale" value="{{war_state['morale'] if war_state else 100}}"></label>
</div><div class="actions"><button>Save User</button><a class="btn secondary" href="{{url_for('players')}}">Cancel</a></div></form></section>
<section class="panel"><h2>Owned Items</h2><div class="pad"><details class="creator"><summary class="btn">＋ Add Item</summary><form class="fields" method="post" action="{{url_for('set_inventory',user_id=player['user_id'])}}"><div class="fields-grid"><label>Choose Item<select name="item_id">{% for item in available_items %}<option value="{{item['id']}}">{{item['emoji']}} {{item['name']}}</option>{% endfor %}</select></label><label>Quantity<input type="number" min="1" name="quantity" value="1"></label></div><button>Add Item</button></form></details></div><table><tr><th>Item</th><th>Quantity</th><th>Set Quantity</th></tr>{% for item in items %}<tr><td>{{item['emoji']}} {{item['name']}}</td><td>{{item['quantity']}}</td><td><form class="inline" method="post" action="{{url_for('set_inventory',user_id=player['user_id'])}}"><input type="hidden" name="item_id" value="{{item['id']}}"><input type="number" min="0" name="quantity" value="{{item['quantity']}}"><button>Set</button></form></td></tr>{% else %}<tr><td colspan="3">This user does not own any items.</td></tr>{% endfor %}</table></section>"""


@app.route("/player/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_player(user_id):
    db = get_db()
    player = db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    if player is None:
        db.close()
        return "Player not found", 404
    if request.method == "POST":
        try:
            nation = request.form["nation_name"].strip()
            capital = request.form["capital_name"].strip()
            if not 3 <= len(nation) <= 30 or not 3 <= len(capital) <= 30:
                raise ValueError("Nation and Capital names must be 3–30 characters.")
            nums = {key: settings_admin.integer(request.form[key], key, 0, 999999999) for key in ("xc", "bank_xc", "money", "xcrystals", "land", "capital_health")}
            nums.update({"land_army": player["land_army"], "air_army": player["air_army"], "navy": player["navy"]})
            nums["capital_health"] = min(100, nums["capital_health"])
            job_id = int(request.form["job_id"]) if request.form.get("job_id") else None
            db.execute("""UPDATE players SET nation_name=?,capital_name=?,xc=?,bank_xc=?,money=?,xcrystals=?,job_id=?,land=?,land_army=?,air_army=?,navy=?,capital_health=? WHERE user_id=?""", (nation, capital, nums['xc'], nums['bank_xc'], nums['money'], nums['xcrystals'], job_id, nums['land'], nums['land_army'], nums['air_army'], nums['navy'], nums['capital_health'], user_id))
            fortification = settings_admin.integer(request.form.get("fortification_level", 0), "fortification_level", 0, 999999999); morale = min(100, settings_admin.integer(request.form.get("morale", 100), "morale", 0, 999999999))
            db.execute("""INSERT INTO player_war_settings(user_id,fortification_level,morale) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET fortification_level=excluded.fortification_level,morale=excluded.morale""", (user_id,fortification,morale))
            db.commit()
            flash("User data saved.")
        except (ValueError, KeyError) as error:
            flash(str(error))
        player = db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    jobs_rows = []
    item_rows = db.execute("""SELECT i.*,v.quantity FROM inventories v JOIN items i ON i.id=v.item_id WHERE v.user_id=? AND v.quantity>0 ORDER BY i.name""", (user_id,)).fetchall()
    available_items = db.execute("""SELECT id,name,emoji FROM items i WHERE enabled=1 AND NOT EXISTS(
        SELECT 1 FROM inventories v WHERE v.item_id=i.id AND v.user_id=? AND v.quantity>0) ORDER BY name""", (user_id,)).fetchall()
    unit_rows = [unit for unit in war_system.units_for_player(db, user_id) if unit["quantity"] > 0]
    available_units = db.execute("""SELECT id,name,emoji,branch FROM war_unit_types u WHERE enabled=1 AND NOT EXISTS(
        SELECT 1 FROM player_war_units p WHERE p.unit_type_id=u.id AND p.user_id=? AND p.quantity>0) ORDER BY position,name""", (user_id,)).fetchall()
    war_state = db.execute("SELECT * FROM player_war_settings WHERE user_id=?", (user_id,)).fetchone()
    db.close()
    unit_panel = """<section class="panel"><h2>Owned Military Units</h2><div class="pad"><details class="creator"><summary class="btn">＋ Add Unit</summary><form class="fields" method="post" action="{{url_for('set_war_units',user_id=player['user_id'])}}"><div class="fields-grid"><label>Choose Unit<select name="unit_type_id">{% for unit in available_units %}<option value="{{unit['id']}}">{{unit['emoji']}} {{unit['name']}} — {{unit['branch']}}</option>{% endfor %}</select></label><label>Quantity<input type="number" min="1" name="quantity" value="1"></label></div><button>Add Unit</button></form></details></div><table><tr><th>Unit Model</th><th>Branch</th><th>Power Each</th><th>Quantity</th><th>Set Quantity</th></tr>{% for unit in units %}<tr><td>{{unit['emoji']}} {{unit['name']}}</td><td>{{unit['branch']}}</td><td>{{unit['power']}}</td><td>{{unit['quantity']}}</td><td><form class="inline" method="post" action="{{url_for('set_war_units',user_id=player['user_id'])}}"><input type="hidden" name="unit_type_id" value="{{unit['id']}}"><input type="number" min="0" name="quantity" value="{{unit['quantity']}}"><button>Set</button></form></td></tr>{% else %}<tr><td colspan="5">This user does not own any military units.</td></tr>{% endfor %}</table></section>"""
    return admin_page("Edit User", PLAYER_FORM + unit_panel, player=player, jobs=jobs_rows, items=item_rows, available_items=available_items, units=unit_rows, available_units=available_units, war_state=war_state)


@app.post("/player/<int:user_id>/delete")
@login_required
def delete_player_data(user_id):
    """Remove one person's X BOT profile only; this never touches their Discord account."""
    db = get_db()
    player = db.execute("SELECT display_name,nation_name FROM players WHERE user_id=?", (user_id,)).fetchone()
    if player is None:
        db.close(); flash("Player was already deleted."); return redirect(url_for("players"))
    # Delete child records first. Logs remain as an audit trail, labelled with the Discord ID.
    # Most tables use user_id. Map territory ownership uses owner_user_id instead.
    # Keeping the column beside the table prevents a partial delete when schemas differ.
    one_user_tables = (
        ("inventories", "user_id"), ("player_war_units", "user_id"),
        ("player_war_settings", "user_id"), ("player_cities", "user_id"),
        ("player_city_state", "user_id"), ("player_bill_state", "user_id"),
        ("player_income_state", "user_id"), ("xp_profiles", "user_id"),
        ("alliance_members", "user_id"), ("map_territories", "owner_user_id"),
        ("casino_cooldowns", "user_id"), ("casino_daily_game_net", "user_id"),
        ("casino_stats", "user_id"), ("casino_vip_members", "user_id"),
        ("lottery_entries", "user_id"), ("reward_code_redemptions", "user_id"),
        ("war_season_scores", "user_id"), ("dashboard_sessions", "user_id"),
        ("tier5_profiles", "user_id"), ("tier5_mission_claims", "user_id"),
        ("tier6_stock_holdings", "user_id"), ("tier6_stock_trades", "user_id"),
        ("tier6_contract_claims", "user_id"), ("tier6_production_queue", "user_id"),
        ("tier7_defence_profiles", "user_id"), ("tier7_territory_defence", "owner_user_id"),
        ("tier8_research_jobs", "user_id"), ("tier8_levels", "user_id"), ("tier8_preferences", "user_id"),
        ("casual_game_sessions", "user_id"), ("casual_game_completions", "user_id"),
        ("casual_game_progress", "user_id"), ("casual_collectibles", "user_id"),
    )
    for table, column in one_user_tables:
        db.execute(f"DELETE FROM {table} WHERE {column}=?", (user_id,))
    db.execute("DELETE FROM division_template_units WHERE template_id IN (SELECT id FROM division_templates WHERE user_id=?)", (user_id,))
    db.execute("DELETE FROM division_templates WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM battle_history WHERE attacker_id=? OR defender_id=? OR winner_id=?", (user_id, user_id, user_id))
    db.execute("DELETE FROM scout_history WHERE scout_id=? OR target_id=?", (user_id, user_id))
    db.execute("DELETE FROM tier6_market_trades WHERE buyer_id=? OR seller_id=?", (user_id, user_id))
    tier7_plan_ids = [row["id"] for row in db.execute(
        "SELECT id FROM tier7_battle_plans WHERE attacker_id=? OR defender_id=?", (user_id, user_id)
    ).fetchall()]
    if tier7_plan_ids:
        placeholders = ",".join("?" for _ in tier7_plan_ids)
        db.execute(f"DELETE FROM tier7_plan_units WHERE plan_id IN ({placeholders})", tier7_plan_ids)
        db.execute(f"DELETE FROM tier7_events WHERE plan_id IN ({placeholders})", tier7_plan_ids)
    db.execute("DELETE FROM tier7_battle_plans WHERE attacker_id=? OR defender_id=?", (user_id, user_id))
    db.execute("DELETE FROM tier7_battle_reports WHERE attacker_id=? OR defender_id=? OR winner_id=?", (user_id, user_id, user_id))
    db.execute("DELETE FROM tier7_events WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM players WHERE user_id=?", (user_id,))
    record_dashboard_audit(db, "DELETED", f"Deleted X BOT data for {player['display_name'] or player['nation_name']} ({user_id})")
    db.commit(); db.close(); g.skip_dashboard_audit = True
    flash(f"Deleted X BOT data for {player['display_name'] or player['nation_name']}. Discord membership was not changed.")
    return redirect(url_for("players"))


@app.post("/player/<int:user_id>/inventory")
@login_required
def set_inventory(user_id):
    item_id = int(request.form["item_id"])
    quantity = settings_admin.integer(request.form["quantity"], "quantity", 0, 999999999)
    db = get_db()
    db.execute("INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=excluded.quantity", (user_id, item_id, quantity))
    db.commit(); db.close(); flash("Inventory updated.")
    return redirect(url_for("edit_player", user_id=user_id))


@app.post("/player/<int:user_id>/war-units")
@login_required
def set_war_units(user_id):
    unit_type_id = int(request.form["unit_type_id"]); quantity = settings_admin.integer(request.form["quantity"], "quantity", 0, 999999999); db = get_db()
    db.execute("""INSERT INTO player_war_units(user_id,unit_type_id,quantity) VALUES(?,?,?)
        ON CONFLICT(user_id,unit_type_id) DO UPDATE SET quantity=excluded.quantity""", (user_id, unit_type_id, quantity))
    db.commit(); db.close(); flash("Military unit quantity updated.")
    return redirect(url_for("edit_player", user_id=user_id))


def setting_groups(keys):
    import economy_settings_admin as settings_admin
    groups = {}
    for key in keys:
        spec = settings_admin.metadata(key)
        if spec:
            groups.setdefault(spec['group'], []).append((key, spec))
    return list(groups.items())

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        return save_economy_form(settings_admin.GENERAL_KEYS, "settings")
    db = get_db()
    values = {row['key']: row['value'] for row in db.execute("SELECT * FROM economy_settings")}
    db.close()
    return admin_page("Settings", render_template("settings_panel.html", setting_values=values, setting_groups=setting_groups(settings_admin.GENERAL_KEYS), setting_action=url_for("settings")))



@app.route("/applications", methods=["GET", "POST"])
@login_required
def applications_control():
    db = get_db()
    if request.method == "POST":
        for key in ("verification_enabled", "applications_enabled"):
            value = "1" if request.form.get(key) == "1" else "0"
            db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        for key in ("verification_unverified_role_id", "verification_guest_role_id", "verification_member_role_id", "verification_log_channel_id", "verification_min_account_days"):
            raw = request.form.get(key, "0").strip(); value = "".join(c for c in raw if c.isdigit()) or "0"
            db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            if key == "verification_member_role_id" and value != "0":
                db.execute("UPDATE xp_rewards SET role_id=? WHERE level=2 AND name='Member' COLLATE NOCASE", (value,))
        db.commit(); flash("Verification and application settings saved.")
    setting_keys = tuple(applications.DEFAULTS)
    values = {r["key"]: r["value"] for r in db.execute(f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in setting_keys)})", setting_keys)}
    forms = db.execute("SELECT * FROM application_forms ORDER BY name").fetchall()
    questions = db.execute("SELECT * FROM application_questions ORDER BY form_id,position,id").fetchall()
    department_options = db.execute("SELECT * FROM application_question_options ORDER BY question_id,position,id").fetchall()
    submissions = db.execute("""SELECT s.*,f.name form_name FROM application_submissions s
        JOIN application_forms f ON f.id=s.form_id ORDER BY s.id DESC LIMIT 100""").fetchall()
    db.close(); server_channels = discord_server_text_channels()
    body = """<section class="panel"><h2>✅ Verification</h2><div class="notice">New members automatically receive <b>Unverified</b>. After they click Verify, X BOT removes Unverified and gives <b>Guest</b>. At Level 2, X BOT removes Guest and gives <b>Member</b>. Keep X BOT's role above all three roles.</div><form class="fields" method="post"><div class="fields-grid"><label>Verification<select name="verification_enabled"><option value="1" {% if v['verification_enabled']=='1' %}selected{% endif %}>Open</option><option value="0" {% if v['verification_enabled']!='1' %}selected{% endif %}>Closed</option></select></label><label>Unverified Role<input name="verification_unverified_role_id" value="{{v['verification_unverified_role_id']}}"></label><label>Guest Role<input name="verification_guest_role_id" value="{{v['verification_guest_role_id']}}"></label><label>Member Role<input name="verification_member_role_id" value="{{v['verification_member_role_id']}}"></label><label>Minimum Account Age (days)<input type="number" min="0" name="verification_min_account_days" value="{{v['verification_min_account_days']}}"></label><label>Verification Log Channel<select name="verification_log_channel_id"><option value="0">No Discord Log</option>{% for channel in server_channels %}<option value="{{channel['id']}}" {% if v['verification_log_channel_id']==channel['id'] %}selected{% endif %}># {{channel['name']}}</option>{% endfor %}</select></label><label>Applications<select name="applications_enabled"><option value="1" {% if v['applications_enabled']=='1' %}selected{% endif %}>Open</option><option value="0" {% if v['applications_enabled']!='1' %}selected{% endif %}>Closed</option></select></label></div><button>Save Main Settings</button></form></section>
    <details class="creator"><summary class="btn">＋ Create Application Form</summary><section class="panel"><h2>Create Application</h2><form class="fields" method="post" action="{{url_for('save_application_form')}}"><div class="fields-grid"><label>Name<input name="name" required></label><label>Emoji<input name="emoji" value="📋"></label><label>Description<textarea name="description"></textarea></label><label>Reviewer @Role / ID<input name="reviewer_role_id"></label><label>Role Given When Accepted<input name="accepted_role_id"></label><label>Review Channel<select name="review_channel_id"><option value="0">None</option>{% for channel in server_channels %}<option value="{{channel['id']}}"># {{channel['name']}}</option>{% endfor %}</select></label><label>Result Channel<select name="result_channel_id"><option value="0">None</option>{% for channel in server_channels %}<option value="{{channel['id']}}"># {{channel['name']}}</option>{% endfor %}</select></label><label>Reapply Cooldown (hours)<input type="number" min="0" name="cooldown_hours" value="24"></label></div><button>Create Form</button></form></section></details>
    <section class="panel"><h2>📋 Application Forms</h2><div class="library">{% for f in forms %}<article class="library-card"><form class="fields" method="post" action="{{url_for('save_application_form')}}"><input type="hidden" name="id" value="{{f['id']}}"><h3>{{f['emoji']}} {{f['name']}}</h3><label>Name<input name="name" value="{{f['name']}}"></label><label>Emoji<input name="emoji" value="{{f['emoji']}}"></label><label>Description<textarea name="description">{{f['description']}}</textarea></label><label>Reviewer @Role / ID<input name="reviewer_role_id" value="{{f['reviewer_role_id']}}"></label><label>Accepted @Role / ID<input name="accepted_role_id" value="{{f['accepted_role_id']}}"></label><label>Review Channel<select name="review_channel_id"><option value="0">None</option>{% for c in server_channels %}<option value="{{c['id']}}" {% if f['review_channel_id']==c['id'] %}selected{% endif %}># {{c['name']}}</option>{% endfor %}</select></label><label>Result Channel<select name="result_channel_id"><option value="0">None</option>{% for c in server_channels %}<option value="{{c['id']}}" {% if f['result_channel_id']==c['id'] %}selected{% endif %}># {{c['name']}}</option>{% endfor %}</select></label><label>Cooldown Hours<input type="number" min="0" name="cooldown_hours" value="{{f['cooldown_seconds']//3600}}"></label><label>Status<select name="enabled"><option value="1" {% if f['enabled'] %}selected{% endif %}>Open</option><option value="0" {% if not f['enabled'] %}selected{% endif %}>Closed</option></select></label><div class="actions"><button>Save Form</button><button class="danger" name="action" value="delete">Delete</button></div></form><hr><h3>Questions</h3>{% for q in questions if q['form_id']==f['id'] %}<form class="fields" method="post" action="{{url_for('save_application_question')}}"><input type="hidden" name="id" value="{{q['id']}}"><input type="hidden" name="form_id" value="{{f['id']}}"><label>Question<input name="label" value="{{q['label']}}"></label><label>Placeholder<input name="placeholder" value="{{q['placeholder']}}"></label><div class="fields-grid"><label>Answer Type<select name="paragraph"><option value="1" {% if q['paragraph'] %}selected{% endif %}>Long</option><option value="0" {% if not q['paragraph'] %}selected{% endif %}>Short</option></select></label><label>Required<select name="required"><option value="1" {% if q['required'] %}selected{% endif %}>Yes</option><option value="0" {% if not q['required'] %}selected{% endif %}>No</option></select></label><label>Order<input type="number" min="0" name="position" value="{{q['position']}}"></label></div><div class="actions"><button>Save</button><button class="danger" name="action" value="delete">Delete</button></div></form>{% endfor %}<form class="fields" method="post" action="{{url_for('save_application_question')}}"><input type="hidden" name="form_id" value="{{f['id']}}"><h3>＋ Add Question (maximum 5)</h3><label>Question<input name="label" required></label><label>Placeholder<input name="placeholder"></label><div class="fields-grid"><label>Type<select name="paragraph"><option value="1">Long</option><option value="0">Short</option></select></label><label>Required<select name="required"><option value="1">Yes</option><option value="0">No</option></select></label><label>Order<input type="number" name="position" value="10"></label></div><button>Add Question</button></form></article>{% else %}<p class="notice">No application forms yet.</p>{% endfor %}</div></section>
    <section class="panel"><h2>📨 Recent Applications</h2><table><tr><th>ID</th><th>Form</th><th>Applicant</th><th>Status</th><th>Reviewer</th><th>Reason</th></tr>{% for s in submissions %}<tr><td>#{{s['id']}}</td><td>{{s['form_name']}}</td><td>{{s['user_name']}}<br><span class="muted">{{s['user_id']}}</span></td><td>{{s['status']}}</td><td>{{s['reviewer_name'] or 'Waiting'}}</td><td>{{s['reason']}}</td></tr>{% else %}<tr><td colspan="6">No applications submitted.</td></tr>{% endfor %}</table></section>"""
    body = body.replace("maximum 5", "maximum 10 · Discord opens these in two pages")
    body = body.replace("reviewer_role_id", "reviewer_role_ids").replace("accepted_role_id", "accepted_role_ids")
    body = body.replace("Reviewer @Role / ID", "Reviewer Roles (choose one or more)").replace("Role Given When Accepted", "Roles Always Given When Accepted").replace("Accepted @Role / ID", "Roles Always Given When Accepted")
    body = body.replace('<label>Answer Type<select name="paragraph"><option value="1" {% if q[\'paragraph\'] %}selected{% endif %}>Long</option><option value="0" {% if not q[\'paragraph\'] %}selected{% endif %}>Short</option></select></label>', '<label>Answer Type<select name="question_type"><option value="long" {% if q[\'question_type\'] in (\'text\',\'long\') and q[\'paragraph\'] %}selected{% endif %}>Long Text</option><option value="short" {% if q[\'question_type\']==\'short\' or (q[\'question_type\']==\'text\' and not q[\'paragraph\']) %}selected{% endif %}>Short Text</option><option value="choice" {% if q[\'question_type\']==\'choice\' %}selected{% endif %}>Multiple Choice</option><option value="department" {% if q[\'question_type\']==\'department\' %}selected{% endif %}>Department Choice + Role</option></select></label>')
    body = body.replace('<label>Type<select name="paragraph"><option value="1">Long</option><option value="0">Short</option></select></label>', '<label>Type<select name="question_type"><option value="long">Long Text</option><option value="short">Short Text</option><option value="choice">Multiple Choice</option><option value="department">Department Choice + Role</option></select></label>')
    department_panel = """<section class="panel"><h2>☑️ Choice Answers & Department Roles</h2><div class="notice">Multiple Choice answers do not need a role. Department Choice answers may give the selected Department role after staff accepts the application. Put Trainee Team in “Roles Always Given When Accepted”.</div>{% for q in questions if q['question_type'] in ('department','choice') %}<article class="library-card"><h3>{{q['label']}}</h3><span class="badge">{{'Department + Role' if q['question_type']=='department' else 'Multiple Choice'}}</span>{% for o in department_options if o['question_id']==q['id'] %}<form class="fields" method="post" action="{{url_for('save_application_option')}}"><input type="hidden" name="id" value="{{o['id']}}"><input type="hidden" name="question_id" value="{{q['id']}}"><div class="fields-grid"><label>Choice<input name="label" value="{{o['label']}}" required></label><label>Emoji<input name="emoji" value="{{o['emoji']}}"></label><label>Role Given (department only)<input name="role_id" value="{{o['role_id']}}"></label><label>Order<input type="number" name="position" value="{{o['position']}}"></label></div><div class="actions"><button>Save Choice</button><button class="danger" name="action" value="delete">Delete</button></div></form>{% endfor %}<form class="fields" method="post" action="{{url_for('save_application_option')}}"><input type="hidden" name="question_id" value="{{q['id']}}"><h3>＋ Add Answer Choice</h3><div class="fields-grid"><label>Answer<input name="label" required></label><label>Emoji<input name="emoji" value="✅"></label><label>Role Given (optional)<input name="role_id"></label><label>Order<input type="number" name="position" value="10"></label></div><button>Add Choice</button></form></article>{% endfor %}</section>"""
    body = body.replace('<section class="panel"><h2>📨 Recent Applications</h2>', department_panel + '<section class="panel"><h2>📨 Recent Applications</h2>')
    return admin_page("Applications & Verification", body, v=values, forms=forms, questions=questions, department_options=department_options, submissions=submissions, server_channels=server_channels)


@app.post("/applications/form/save")
@login_required
def save_application_form():
    db=get_db(); form_id=request.form.get("id"); action=request.form.get("action","save")
    if action=="delete" and form_id:
        submission_ids = [row[0] for row in db.execute("SELECT id FROM application_submissions WHERE form_id=?", (int(form_id),))]
        if submission_ids:
            db.executemany("DELETE FROM application_answers WHERE submission_id=?", [(submission_id,) for submission_id in submission_ids])
        question_ids = [row[0] for row in db.execute("SELECT id FROM application_questions WHERE form_id=?", (int(form_id),))]
        if question_ids:
            db.executemany("DELETE FROM application_question_options WHERE question_id=?", [(question_id,) for question_id in question_ids])
        db.execute("DELETE FROM application_submissions WHERE form_id=?", (int(form_id),))
        db.execute("DELETE FROM application_questions WHERE form_id=?", (int(form_id),))
        db.execute("DELETE FROM application_forms WHERE id=?", (int(form_id),))
        flash("Application form and its questions were permanently deleted.")
    else:
        digits=lambda name: "".join(c for c in request.form.get(name,"") if c.isdigit()) or "0"
        role_ids=lambda name: ",".join(filter(None,(mention_id(value) for value in request.form.getlist(name))))
        reviewer_ids=role_ids("reviewer_role_ids"); accepted_ids=role_ids("accepted_role_ids")
        values=(request.form["name"].strip(),request.form.get("emoji","📋").strip() or "📋",request.form.get("description","").strip(),reviewer_ids,accepted_ids,digits("review_channel_id"),digits("result_channel_id"),settings_admin.integer(request.form.get("cooldown_hours", 24), "cooldown_hours", 0, 999999999)*3600,int(request.form.get("enabled",1)))
        if form_id: db.execute("UPDATE application_forms SET name=?,emoji=?,description=?,reviewer_role_ids=?,accepted_role_ids=?,review_channel_id=?,result_channel_id=?,cooldown_seconds=?,enabled=? WHERE id=?",values+(int(form_id),))
        else: db.execute("INSERT INTO application_forms(name,emoji,description,reviewer_role_ids,accepted_role_ids,review_channel_id,result_channel_id,cooldown_seconds,enabled) VALUES(?,?,?,?,?,?,?,?,?)",values)
        flash("Application form saved.")
    db.commit();db.close();return redirect(url_for("applications_control"))


@app.post("/applications/question/save")
@login_required
def save_application_question():
    db=get_db();question_id=request.form.get("id");form_id=int(request.form["form_id"]);action=request.form.get("action","save")
    if action=="delete" and question_id:
        db.execute("DELETE FROM application_question_options WHERE question_id=?",(int(question_id),))
        db.execute("DELETE FROM application_questions WHERE id=?",(int(question_id),));flash("Question deleted.")
    else:
        if not question_id and db.execute("SELECT COUNT(*) FROM application_questions WHERE form_id=?",(form_id,)).fetchone()[0]>=10:
            db.close();flash("X BOT applications support a maximum of 10 questions (two Discord pages).");return redirect(url_for("applications_control"))
        question_type=request.form.get("question_type","long"); question_type=question_type if question_type in {"long","short","choice","department"} else "long"
        paragraph=1 if question_type=="long" else 0
        values=(form_id,request.form["label"].strip(),request.form.get("placeholder","").strip(),paragraph,int(request.form.get("required",1)),settings_admin.integer(request.form.get("position", 10), "position", 0, 999999999),question_type)
        if question_id: db.execute("UPDATE application_questions SET form_id=?,label=?,placeholder=?,paragraph=?,required=?,position=?,question_type=? WHERE id=?",values+(int(question_id),))
        else: db.execute("INSERT INTO application_questions(form_id,label,placeholder,paragraph,required,position,question_type) VALUES(?,?,?,?,?,?,?)",values)
        flash("Application question saved.")
    db.commit();db.close();return redirect(url_for("applications_control"))


@app.post("/applications/option/save")
@login_required
def save_application_option():
    db=get_db(); option_id=request.form.get("id"); action=request.form.get("action","save"); question_id=int(request.form["question_id"])
    if action=="delete" and option_id:
        db.execute("DELETE FROM application_question_options WHERE id=?", (int(option_id),)); flash("Department choice deleted.")
    else:
        role_id=mention_id(request.form.get("role_id","")) or ""
        values=(question_id,request.form["label"].strip(),request.form.get("emoji","🏢").strip() or "🏢",role_id,settings_admin.integer(request.form.get("position", 10), "position", 0, 999999999))
        if option_id: db.execute("UPDATE application_question_options SET question_id=?,label=?,emoji=?,role_id=?,position=? WHERE id=?", values+(int(option_id),))
        else: db.execute("INSERT INTO application_question_options(question_id,label,emoji,role_id,position) VALUES(?,?,?,?,?)", values)
        flash("Department choice and role mapping saved.")
    db.commit();db.close();return redirect(url_for("applications_control"))


@app.route("/jobs")
@login_required
def jobs():
    flash("The Job system has been retired. X BOT is now focused on War, Cities, Casino and Staff tools.")
    return redirect(url_for("home"))
    db = get_db()
    rows = db.execute("""SELECT j.*,i.name requirement_item_name FROM jobs j
        LEFT JOIN items i ON i.id=j.requirement_item_id ORDER BY j.min_salary""").fetchall()
    edit = db.execute("SELECT * FROM jobs WHERE id=?", (request.args.get("edit", -1),)).fetchone()
    item_rows = db.execute("SELECT id,name,emoji FROM items ORDER BY name").fetchall()
    incentive_pool = db.execute("""SELECT p.*,i.name,i.emoji FROM job_incentive_pool p
        JOIN items i ON i.id=p.item_id ORDER BY p.weight DESC,i.name""").fetchall()
    incentive_keys = ["job_recommendation_item_id", "job_apology_item_id", "job_vacation_item_id",
        "job_drop_base_chance", "job_drop_tenure_multiplier", "job_drop_max_chance",
        "job_keep_recommendation", "job_keep_apology", "job_inactivity_enabled",
        "job_warning_days", "job_fire_days", "job_log_channel_id"]
    incentive_settings = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in incentive_keys)})", incentive_keys
    ).fetchall()}
    db.close(); server_channels = discord_server_text_channels()
    body = """<section class="panel"><h2>{{'Edit' if edit else 'Create New'}} Job</h2><div class="notice">The maximum daily shifts, salary range, required item and Discord @Role are enforced automatically by <code>/work</code> and <code>/job_apply</code>.</div><form class="fields" method="post" action="{{url_for('save_job')}}"><input type="hidden" name="id" value="{{edit['id'] if edit else ''}}"><div class="fields-grid"><label>Job Name<input name="name" value="{{edit['name'] if edit else ''}}" required></label><label>Job Emoji<input name="emoji" maxlength="12" value="{{edit['emoji'] if edit else '💼'}}"></label><label>Description<textarea name="description" required>{{edit['description'] if edit else ''}}</textarea></label><label>Minimum Pay / Shift (XC)<input type="number" min="0" name="min_salary" value="{{edit['min_salary'] if edit else 50}}"></label><label>Maximum Pay / Shift (XC)<input type="number" min="0" name="max_salary" value="{{edit['max_salary'] if edit else 100}}"></label><label>Max Shifts / Work Per Day<input type="number" min="1" name="shifts_per_day" value="{{edit['shifts_per_day'] if edit else 1}}"></label><label>Required Item (optional)<select name="requirement_item_id"><option value="">None</option>{% for i in item_rows %}<option value="{{i['id']}}" {% if edit and edit['requirement_item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Required Item Amount<input type="number" min="0" name="requirement_quantity" value="{{edit['requirement_quantity'] if edit else 0}}"></label><label>Required Discord @Role or Role ID<input name="required_role_id" value="{{edit['required_role_id'] if edit else ''}}" placeholder="Paste @Role mention or Role ID"></label><label>Enabled<select name="enabled"><option value="1" {% if not edit or edit['enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['enabled'] %}selected{% endif %}>No</option></select></label></div><div class="actions"><button>Save Job</button>{% if edit %}<a class="btn secondary" href="{{url_for('jobs')}}">Cancel</a>{% endif %}</div></form></section><section class="panel"><h2>💼 Department Jobs ({{rows|length}})</h2><div class="library">{% for j in rows %}<article class="library-card"><h3>{{j['emoji']}} {{j['name']}}</h3><p>{{j['description']}}</p><div class="statline">💰 <b>Pay:</b> {{j['min_salary']}}{% if j['max_salary']!=j['min_salary'] %}–{{j['max_salary']}}{% endif %} XC per shift<br>⏰ <b>Shifts/day:</b> {{j['shifts_per_day']}}<br>🎟️ <b>Required item:</b> {% if j['requirement_quantity'] %}{{j['requirement_quantity']}}x {{j['requirement_item_name'] or 'Item'}}{% else %}None{% endif %}<br>🎭 <b>Role ID:</b> {{j['required_role_id'] or 'None'}}<br><span class="{{'ok' if j['enabled'] else 'bad'}}">{{'Enabled' if j['enabled'] else 'Disabled'}}</span></div><div class="actions"><a class="btn" href="{{url_for('jobs',edit=j['id'])}}">Edit Job</a></div></article>{% else %}<p class="notice">No jobs yet.</p>{% endfor %}</div></section>"""
    body = body.replace('<section class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Job</h2>', '<details class="creator" {% if edit %}open{% endif %}><summary class="btn">＋ Create New Job</summary><section class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Job</h2>', 1)
    body = body.replace('</form></section><section class="panel"><h2>💼 Department Jobs ({{rows|length}})</h2>', '</form></section></details><section class="panel"><h2>💼 Department Jobs ({{rows|length}})</h2>', 1)
    body = body.replace('placeholder="Copy role ID from Discord"', 'placeholder="Paste @Role mention or Role ID"')
    body = body.replace('<a class="btn" href="{{url_for(\'jobs\',edit=j[\'id\'])}}">Edit Job</a></div>', '<a class="btn" href="{{url_for(\'jobs\',edit=j[\'id\'])}}">Edit Job</a><form method="post" action="{{url_for(\'delete_job\',job_id=j[\'id\'])}}" onsubmit="return confirm(\'Delete this job? Members assigned to it will become unemployed.\')"><button class="danger">Delete Job</button></form></div>')
    incentive_panel = """<section class="panel"><h2>🎁 Job Incentive Items</h2>
    <form class="fields" method="post" action="{{url_for('save_job_incentives')}}"><div class="fields-grid">
    {% for key,label,help_text in incentive_selectors %}<label>{{label}}<select name="{{key}}">{% for i in item_rows %}<option value="{{i['id']}}" {% if incentive_settings.get(key)==i['id']|string %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select><small>{{help_text}}</small></label>{% endfor %}
    </div><hr><h3>Job Drop Chance</h3><div class="notice">The chance increases automatically based on how many days the member has kept the same job.</div><div class="fields-grid">
    <label>Base Chance (%)<input type="number" min="0" max="100" name="job_drop_base_chance" value="{{incentive_settings.get('job_drop_base_chance','6')}}"></label>
    <label>Tenure Multiplier (% / day)<input type="number" min="0" max="100" name="job_drop_tenure_multiplier" value="{{incentive_settings.get('job_drop_tenure_multiplier','1')}}"></label>
    <label>Maximum Chance (%)<input type="number" min="0" max="100" name="job_drop_max_chance" value="{{incentive_settings.get('job_drop_max_chance','25')}}"></label>
    <label>Recommendation Preservation<select name="job_keep_recommendation"><option value="1" {% if incentive_settings.get('job_keep_recommendation','1')=='1' %}selected{% endif %}>Keep item when starting a job</option><option value="0" {% if incentive_settings.get('job_keep_recommendation')=='0' %}selected{% endif %}>Consume item</option></select></label>
    <label>Apology Preservation<select name="job_keep_apology"><option value="1" {% if incentive_settings.get('job_keep_apology','1')=='1' %}selected{% endif %}>Keep item when reapplying</option><option value="0" {% if incentive_settings.get('job_keep_apology')=='0' %}selected{% endif %}>Consume item</option></select></label>
    </div><div class="actions"><button>💾 Save Incentive Settings</button></div></form>
    <hr><div class="top-actions"><h3>Incentive Item Pool</h3></div><div class="notice">Every successful shift rolls the calculated chance. Weight controls relative rarity after a drop succeeds.</div>
    <div class="library">{% for p in incentive_pool %}<article class="library-card"><h3>{{p['emoji']}} {{p['name']}}</h3><form method="post" action="{{url_for('save_job_incentive_pool')}}"><input type="hidden" name="item_id" value="{{p['item_id']}}"><div class="fields-grid"><label>Amount<input type="number" min="1" name="amount" value="{{p['amount']}}"></label><label>Weight<input type="number" min="1" name="weight" value="{{p['weight']}}"></label></div><div class="actions"><button name="action" value="save">Save</button><button class="archive" name="action" value="remove">Remove</button></div></form></article>{% endfor %}</div>
    <form class="fields" method="post" action="{{url_for('save_job_incentive_pool')}}"><h3>＋ Add Incentive Item</h3><div class="fields-grid"><label>Item<select name="item_id">{% for i in item_rows %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Amount<input type="number" min="1" name="amount" value="1"></label><label>Weight<input type="number" min="1" name="weight" value="1"></label></div><button name="action" value="save">＋ Add Item</button></form></section>"""
    selectors = [("job_recommendation_item_id", "Letter of Recommendation", "Required for higher-paying department jobs."),
        ("job_apology_item_id", "Letter of Apology", "Used by future reapplication and firing flows."),
        ("job_vacation_item_id", "Vacation", "Reserved for taking leave without losing a job.")]
    inactivity_panel = """<section class="panel"><h2>⏰ Job Inactivity Rules</h2><div class="notice">X BOT checks once per hour. A successful <code>/work</code> resets the inactivity warning. Automatic firing only removes the X BOT job; Discord staff roles are never removed automatically.</div><form class="fields" method="post" action="{{url_for('save_job_inactivity')}}"><div class="fields-grid">
    <label>Automatic Inactivity Actions<select name="job_inactivity_enabled"><option value="1" {% if incentive_settings.get('job_inactivity_enabled','1')=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if incentive_settings.get('job_inactivity_enabled')=='0' %}selected{% endif %}>Disabled</option></select></label>
    <label>Warning After Inactive Days<input type="number" min="1" max="365" name="job_warning_days" value="{{incentive_settings.get('job_warning_days','7')}}"></label>
    <label>Fire After Inactive Days<input type="number" min="2" max="365" name="job_fire_days" value="{{incentive_settings.get('job_fire_days','14')}}"></label>
    <label>Discord Job Log Channel<select name="job_log_channel_id"><option value="0">No Discord Log</option>{% for channel in server_channels %}<option value="{{channel['id']}}" {% if incentive_settings.get('job_log_channel_id','0')==channel['id'] %}selected{% endif %}># {{channel['name']}}</option>{% endfor %}</select></label>
    </div><div class="actions"><button>💾 Save Inactivity Rules</button></div></form></section>"""
    return admin_page("Jobs", body + incentive_panel + inactivity_panel, rows=rows, edit=edit, item_rows=item_rows,
        incentive_pool=incentive_pool, incentive_settings=incentive_settings, incentive_selectors=selectors, server_channels=server_channels)


@app.post("/jobs/inactivity/settings")
@login_required
def save_job_inactivity():
    db = get_db()
    warning = min(365, settings_admin.integer(request.form.get("job_warning_days", 7), "job_warning_days", 1, 999999999))
    firing = min(365, max(warning + 1, int(request.form.get("job_fire_days", 14))))
    channel_text = request.form.get("job_log_channel_id", "0").strip()
    channel_digits = "".join(character for character in channel_text if character.isdigit()) or "0"
    values = {"job_inactivity_enabled": "1" if request.form.get("job_inactivity_enabled") == "1" else "0",
        "job_warning_days": str(warning), "job_fire_days": str(firing), "job_log_channel_id": channel_digits}
    for key, value in values.items():
        db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit(); db.close(); flash("Job inactivity rules saved.")
    return redirect(url_for("jobs"))


@app.post("/jobs/incentives/settings")
@login_required
def save_job_incentives():
    db = get_db()
    item_keys = ("job_recommendation_item_id", "job_apology_item_id", "job_vacation_item_id")
    for key in item_keys:
        item_id = settings_admin.integer(request.form[key], key, 1, 999999999)
        if db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(item_id)))
    base = min(100, settings_admin.integer(request.form.get("job_drop_base_chance", 6), "job_drop_base_chance", 0, 999999999))
    multiplier = min(100, settings_admin.integer(request.form.get("job_drop_tenure_multiplier", 1), "job_drop_tenure_multiplier", 0, 999999999))
    maximum = min(100, max(base, int(request.form.get("job_drop_max_chance", 25))))
    values = {"job_drop_base_chance": base, "job_drop_tenure_multiplier": multiplier,
        "job_drop_max_chance": maximum, "job_keep_recommendation": int(request.form.get("job_keep_recommendation", 1)),
        "job_keep_apology": int(request.form.get("job_keep_apology", 1))}
    for key, value in values.items():
        db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit(); db.close(); flash("Job incentive settings saved.")
    return redirect(url_for("jobs"))


@app.post("/jobs/incentives/pool")
@login_required
def save_job_incentive_pool():
    db = get_db(); item_id = settings_admin.integer(request.form["item_id"], "item_id", 1, 999999999); action = request.form.get("action", "save")
    if action == "remove":
        db.execute("DELETE FROM job_incentive_pool WHERE item_id=?", (item_id,)); flash("Incentive item removed.")
    elif db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        amount = settings_admin.integer(request.form.get("amount", 1), "amount", 1, 999999999); weight = settings_admin.integer(request.form.get("weight", 1), "weight", 1, 999999999)
        db.execute("""INSERT INTO job_incentive_pool(item_id,amount,weight,enabled) VALUES(?,?,?,1)
            ON CONFLICT(item_id) DO UPDATE SET amount=excluded.amount,weight=excluded.weight,enabled=1""", (item_id, amount, weight))
        flash("Incentive item saved.")
    db.commit(); db.close()
    return redirect(url_for("jobs"))


@app.post("/jobs/save")
@login_required
def save_job():
    db = get_db(); job_id = request.form.get("id"); name = request.form["name"].strip(); description = request.form["description"].strip(); low = settings_admin.integer(request.form["min_salary"], "min_salary", 0, 999999999); high = max(low, int(request.form["max_salary"])); enabled = int(request.form["enabled"]); emoji = request.form.get("emoji", "💼").strip() or "💼"; shifts = settings_admin.integer(request.form["shifts_per_day"], "shifts_per_day", 1, 999999999); item_id = int(request.form["requirement_item_id"]) if request.form.get("requirement_item_id") else None; quantity = settings_admin.integer(request.form["requirement_quantity"], "requirement_quantity", 0, 999999999); role_text = request.form.get("required_role_id", "").strip(); digits = "".join(character for character in role_text if character.isdigit()); role_id = digits or role_text.removeprefix("@").strip()
    try:
        if job_id:
            db.execute("UPDATE jobs SET name=?,description=?,min_salary=?,max_salary=?,enabled=?,emoji=?,shifts_per_day=?,requirement_item_id=?,requirement_quantity=?,required_role_id=? WHERE id=?", (name, description, low, high, enabled, emoji, shifts, item_id, quantity, role_id, int(job_id)))
        else:
            db.execute("INSERT INTO jobs(name,description,min_salary,max_salary,enabled,emoji,shifts_per_day,requirement_item_id,requirement_quantity,required_role_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (name, description, low, high, enabled, emoji, shifts, item_id, quantity, role_id))
        db.commit(); flash("Job saved.")
    except sqlite3.IntegrityError:
        flash("Job name already exists.")
    db.close()
    return redirect(url_for("jobs"))


@app.post("/jobs/<int:job_id>/delete")
@login_required
def delete_job(job_id):
    db = get_db(); job = db.execute("SELECT name FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        db.close(); flash("Job was not found."); return redirect(url_for("jobs"))
    assigned = db.execute("SELECT COUNT(*) count FROM players WHERE job_id=?", (job_id,)).fetchone()["count"]
    db.execute("UPDATE players SET job_id=NULL,job_started_at=0,job_warning_sent=0,work_shifts_today=0 WHERE job_id=?", (job_id,))
    db.execute("DELETE FROM jobs WHERE id=?", (job_id,)); db.commit(); db.close()
    flash(f"Job '{job['name']}' deleted. {assigned} assigned member(s) were set to no job.")
    return redirect(url_for("jobs"))


def item_usage_details(db, item_id):
    """Return human-readable references that make an item unsafe to delete."""
    item_id = int(item_id)
    checks = (
        ("SELECT COUNT(*) count,COALESCE(SUM(quantity),0) total FROM inventories WHERE item_id=? AND quantity>0", "player inventories", "total"),
        ("SELECT COUNT(*) count,COUNT(*) total FROM xp_rewards WHERE item_id=?", "level rewards", "count"),
        ("SELECT COUNT(*) count,COUNT(*) total FROM streak_rewards WHERE item_id=?", "activity streak rewards", "count"),
        ("SELECT COUNT(*) count,COALESCE(SUM(quantity),0) total FROM market_listings WHERE item_id=?", "market listings/history", "count"),
        ("SELECT COUNT(*) count,COUNT(*) total FROM mining_area_drops WHERE item_id=?", "mining drop pools", "count"),
        ("SELECT COUNT(*) count,COUNT(*) total FROM recipes WHERE output_item_id=?", "recipe outputs", "count"),
        ("SELECT COUNT(*) count,COUNT(*) total FROM recipe_ingredients WHERE item_id=?", "recipe ingredients", "count"),
    )
    usage = []
    for sql, label, value_column in checks:
        row = db.execute(sql, (item_id,)).fetchone()
        if row and int(row["count"] or 0) > 0:
            value = int(row[value_column] or 0)
            usage.append(f"{label}: {value}")

    setting_count = db.execute(
        """SELECT COUNT(*) count FROM economy_settings
           WHERE key LIKE '%item_id' AND CAST(value AS TEXT)=?""",
        (str(item_id),),
    ).fetchone()["count"]
    if setting_count:
        usage.append(f"system item settings: {int(setting_count)}")
    return usage


@app.route("/items")
@login_required
def items():
    db = get_db()
    category_filter = request.args.get("category", "")
    search = request.args.get("q", "").strip()
    query = """SELECT i.*,c.label category_label,c.emoji category_emoji FROM items i
        LEFT JOIN item_categories c ON c.id=i.category_id WHERE 1=1"""
    parameters = []
    if category_filter:
        query += " AND i.category_id=?"; parameters.append(category_filter)
    if search:
        query += " AND (i.name LIKE ? OR i.aliases LIKE ? OR i.description LIKE ?)"; parameters.extend([f"%{search}%"] * 3)
    rows = db.execute(query + " ORDER BY c.position,i.name", parameters).fetchall()
    edit = db.execute("SELECT * FROM items WHERE id=?", (request.args.get("edit", -1),)).fetchone()
    category_manage_rows = db.execute("""SELECT c.*,COUNT(i.id) item_count FROM item_categories c
        LEFT JOIN items i ON i.category_id=c.id GROUP BY c.id ORDER BY c.position,c.label""").fetchall()
    categories_rows = [row for row in category_manage_rows if row["enabled"]]
    item_usage = {row["id"]: item_usage_details(db, row["id"]) for row in rows}
    db.close()
    body = """<section class="panel"><h2>{{'Edit' if edit else 'Create New'}} Item</h2><div class="notice">Materials, pickaxes, job items, war items and collectables all live in this one item library. For mining: <b>crafting_material</b> uses Weight and Yield; <b>mine_tool</b> uses Effect Value as its bonus chance.</div><form class="fields" method="post" action="{{url_for('save_item')}}"><input type="hidden" name="id" value="{{edit['id'] if edit else ''}}"><div class="fields-grid"><label>Item Name<input name="name" value="{{edit['name'] if edit else ''}}" required></label><label>Item Aliases (separate with commas)<input name="aliases" value="{{edit['aliases'] if edit else ''}}" placeholder="e.g. basicpick, bp"></label><label>Description<textarea name="description" required>{{edit['description'] if edit else ''}}</textarea></label><label>Item Emoji<input name="emoji" value="{{edit['emoji'] if edit else '📦'}}"></label><label>Item Category<select name="category_id">{% for c in categories %}<option value="{{c['id']}}" {% if edit and edit['category_id']==c['id'] %}selected{% endif %}>{{c['emoji']}} {{c['label']}}</option>{% endfor %}</select></label><label>Price<input type="number" min="0" name="price" value="{{edit['price'] if edit else 100}}"></label><label>Currency<select name="currency"><option value="xc" {% if not edit or edit['currency']=='xc' %}selected{% endif %}>XC</option><option value="xcrystals" {% if edit and edit['currency']=='xcrystals' %}selected{% endif %}>XCrystals</option></select></label><label>Shop Visible<select name="shop_visible"><option value="1" {% if not edit or edit['shop_visible'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['shop_visible'] %}selected{% endif %}>No</option></select></label><label>Sell-back Price<input type="number" min="0" name="sell_price" value="{{edit['sell_price'] if edit else 0}}"></label><label>Stock (-1 = unlimited)<input type="number" min="-1" name="stock" value="{{edit['stock'] if edit else -1}}"></label><label>Effect<select name="effect">{% for x in ['none','xc_reward','war_credits','capital_repair','mine_tool','crafting_material','job_bonus','collectible'] %}<option {% if edit and edit['effect']==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label><label>Effect Value / Pickaxe Bonus %<input type="number" min="0" max="100" name="effect_value" value="{{edit['effect_value'] if edit else 0}}"></label><label>Mining Drop Weight<input type="number" min="0" name="mine_weight" value="{{edit['mine_weight'] if edit else 0}}"></label><label>Mining Min Yield<input type="number" min="1" name="mine_min_yield" value="{{edit['mine_min_yield'] if edit else 1}}"></label><label>Mining Max Yield<input type="number" min="1" name="mine_max_yield" value="{{edit['mine_max_yield'] if edit else 1}}"></label><label>Sellable<select name="sellable"><option value="1" {% if not edit or edit['sellable'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['sellable'] %}selected{% endif %}>No</option></select></label><label>Tradeable (market comes next)<select name="tradeable"><option value="1" {% if edit and edit['tradeable'] %}selected{% endif %}>Yes</option><option value="0" {% if not edit or not edit['tradeable'] %}selected{% endif %}>No</option></select></label><label>Enabled<select name="enabled"><option value="1" {% if not edit or edit['enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['enabled'] %}selected{% endif %}>No</option></select></label></div><div class="actions"><button>Save Item</button>{% if edit %}<a class="btn secondary" href="{{url_for('items')}}">Cancel</a>{% endif %}</div></form></section><section class="panel"><div class="top-actions"><h2 style="padding:0;border:0;margin-right:auto">Item Library ({{rows|length}})</h2><form><input name="q" value="{{search}}" placeholder="Search items"><select name="category"><option value="">All Categories</option>{% for c in categories %}<option value="{{c['id']}}" {% if selected_category|string==c['id']|string %}selected{% endif %}>{{c['emoji']}} {{c['label']}}</option>{% endfor %}</select><button>Filter</button></form><a class="btn secondary" href="{{url_for('categories')}}">Manage Categories</a></div><div class="library">{% for i in rows %}<article class="library-card"><h3>{{i['emoji']}} {{i['name']}}</h3><span class="badge">{{i['category_emoji'] or '📦'}} {{i['category_label'] or 'Misc'}}</span><p>{{i['description']}}</p><div class="statline">💰 {{i['price']}} {{'XC' if i['currency']=='xc' else 'XCrystals'}} · {{'Unlimited' if i['stock']<0 else i['stock']}} stock<br>⚙️ {{i['effect']}}{% if i['effect_value'] %} ({{i['effect_value']}}){% endif %}{% if i['effect']=='crafting_material' %}<br>⛏️ Weight {{i['mine_weight']}} · Yield {{i['mine_min_yield']}}–{{i['mine_max_yield']}}{% endif %}<br>🛒 Shop: {{'Yes' if i['shop_visible'] else 'No'}} · Sell: {{'Yes' if i['sellable'] else 'No'}}<br><span class="{{'ok' if i['enabled'] else 'bad'}}">{{'Enabled' if i['enabled'] else 'Disabled'}}</span></div><div class="actions"><a class="btn" href="{{url_for('items',edit=i['id'])}}">Edit Item</a></div></article>{% else %}<p class="notice">No matching items.</p>{% endfor %}</div></section>"""
    body = body.replace("'job_bonus','collectible'", "'job_bonus','lottery_ticket','spin_token','balloon_token','collectible'")
    body = body.replace('<section class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Item</h2>', '<details id="item-editor" class="creator" {% if edit %}open{% endif %}><summary class="btn">{{"Edit Item" if edit else "＋ Create New Item"}}</summary><section class="panel"><h2>{{\'Edit\' if edit else \'Create New\'}} Item</h2>', 1)
    body = body.replace('</form></section><section class="panel"><div class="top-actions">', '</form></section></details><section class="panel"><div class="top-actions">', 1)
    body = body.replace('Manage Categories</a>', 'Manage Categories</a><a class="btn" href="{{url_for(\'categories\',create=1)}}">＋ Create Category</a>')
    old_toolbar = '<div class="top-actions"><h2 style="padding:0;border:0;margin-right:auto">Item Library ({{rows|length}})</h2><form><input name="q" value="{{search}}" placeholder="Search items"><select name="category"><option value="">All Categories</option>{% for c in categories %}<option value="{{c[\'id\']}}" {% if selected_category|string==c[\'id\']|string %}selected{% endif %}>{{c[\'emoji\']}} {{c[\'label\']}}</option>{% endfor %}</select><button>Filter</button></form><a class="btn secondary" href="{{url_for(\'categories\')}}">Manage Categories</a><a class="btn" href="{{url_for(\'categories\',create=1)}}">＋ Create Category</a></div>'
    new_toolbar = '''<form class="item-toolbar" method="get" action="{{url_for('items')}}"><label>Search items<input type="search" name="q" value="{{search}}" placeholder="Item name, alias or description"></label><label>Category<select name="category"><option value="">All Items</option>{% for c in categories %}<option value="{{c['id']}}" {% if selected_category|string==c['id']|string %}selected{% endif %}>{{c['emoji']}} {{c['label']}}</option>{% endfor %}</select></label><button type="submit">Filter</button><button class="purple" type="button" onclick="document.getElementById('category-manager').open=true;document.getElementById('category-manager').scrollIntoView()">⚙ Categories</button>{% if edit %}<a class="btn" href="{{url_for('items')}}#item-editor">＋ Create New Item</a>{% else %}<button type="button" onclick="document.getElementById('item-editor').open=true;document.getElementById('item-editor').scrollIntoView();document.querySelector('#item-editor input[name=name]').focus({preventScroll:true})">＋ Create New Item</button>{% endif %}</form><h2>📦 Items <span class="badge">{{rows|length}} items</span></h2>'''
    body = body.replace(old_toolbar, new_toolbar, 1)
    old_actions = '<div class="actions"><a class="btn" href="{{url_for(\'items\',edit=i[\'id\'])}}">Edit Item</a></div>'
    new_actions = '''{% set uses=item_usage.get(i['id'],[]) %}{% if uses %}<div class="notice item-use">🔒 Cannot delete — {{uses|join(' · ')}}</div>{% else %}<div class="ok item-use">✓ Unused — safe to delete</div>{% endif %}<div class="icon-actions"><form method="post" action="{{url_for('item_action',item_id=i['id'])}}"><input type="hidden" name="action" value="shop"><button class="icon-btn teal" title="Toggle Shop Visibility">⇧</button></form><form method="post" action="{{url_for('item_action',item_id=i['id'])}}"><input type="hidden" name="action" value="enabled"><button class="icon-btn purple" title="Enable or Disable">⚡</button></form><a class="btn icon-btn" title="Edit Item" href="{{url_for('items',edit=i['id'])}}">✎</a>{% if uses %}<button class="icon-btn archive" disabled title="Remove this item's references before deleting">🔒</button>{% else %}<form method="post" action="{{url_for('item_action',item_id=i['id'])}}" onsubmit="return confirm('Permanently delete this item? This cannot be undone.')"><input type="hidden" name="action" value="delete"><button class="icon-btn archive" title="Permanently Delete Item">🗑</button></form>{% endif %}</div>'''
    body = body.replace(old_actions, new_actions)
    category_panel = """<details class="creator" id="category-manager"><summary class="btn purple">⚙ Manage Categories</summary><section class="panel"><h2>Categories inside Items</h2><form class="fields" method="post" action="{{url_for('save_item_category')}}"><div class="fields-grid"><label>New Category Name<input name="label" required placeholder="Category name"></label><label>Emoji<input name="emoji" value="📦"></label><label>Display Order<input type="number" min="0" name="position" value="10"></label></div><div class="actions"><button>＋ Create Category</button></div></form><div class="library">{% for c in categories %}<article class="library-card"><form method="post" action="{{url_for('save_item_category')}}"><input type="hidden" name="id" value="{{c['id']}}"><h3>{{c['emoji']}} {{c['label']}}</h3><div class="fields-grid"><label>Name<input name="label" value="{{c['label']}}" required></label><label>Emoji<input name="emoji" value="{{c['emoji']}}"></label><label>Order<input type="number" min="0" name="position" value="{{c['position']}}"></label><label>Status<select name="enabled"><option value="1" {% if c['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not c['enabled'] %}selected{% endif %}>Disabled</option></select></label></div><p>{{c['item_count']}} item(s){% if c['system_category'] %} · Protected fallback{% endif %}</p><button>Save Category</button></form></article>{% endfor %}</div></section></details>"""
    category_panel = category_panel.replace('{% for c in categories %}', '{% for c in all_categories %}')
    category_panel = category_panel.replace(
        '<button>Save Category</button></form></article>',
        '<div class="actions"><button name="action" value="save">Save Category</button>'
        '{% if not c[\'system_category\'] and not c[\'item_count\'] %}'
        '<button class="danger" name="action" value="delete" '
        'onclick="return confirm(\'Delete this empty item category?\')">Delete Category</button>'
        '{% else %}<button class="secondary" type="button" disabled title="Move every item first; protected categories cannot be deleted">Protected / In Use</button>{% endif %}'
        '</div></form></article>'
    )
    body += '''<script>(() => {
      const openEditor = () => {
        if (location.hash !== '#item-editor') return;
        const editor = document.getElementById('item-editor');
        editor.open = true;
        editor.scrollIntoView({block: 'start'});
        editor.querySelector('input[name="name"]').focus({preventScroll: true});
      };
      window.addEventListener('hashchange', openEditor);
      openEditor();
    })();</script>'''
    return admin_page("Items & Categories", category_panel + body, rows=rows, edit=edit, categories=categories_rows, all_categories=category_manage_rows, item_usage=item_usage, search=search, selected_category=category_filter)


@app.route("/mining")
@login_required
def mining_control():
    db=get_db(); areas=db.execute("SELECT * FROM mining_areas ORDER BY position,required_level").fetchall()
    drops=db.execute("""SELECT d.*,i.name item_name,i.emoji item_emoji,a.name area_name FROM mining_area_drops d
        JOIN items i ON i.id=d.item_id JOIN mining_areas a ON a.id=d.area_id ORDER BY a.position,d.weight DESC""").fetchall()
    materials=db.execute("SELECT id,name,emoji FROM items WHERE effect='crafting_material' AND enabled=1 ORDER BY name").fetchall()
    pickaxes=db.execute("SELECT * FROM items WHERE effect='mine_tool' ORDER BY pickaxe_required_level,price").fetchall()
    keys=("mining_energy_enabled","mining_max_energy","mining_energy_regen_amount","mining_energy_regen_seconds","mining_starter_pickaxe_enabled","mining_collection_xc_reward","mining_collection_xcrystal_reward")
    settings_rows={r['key']:r['value'] for r in db.execute(f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in keys)})",keys)}; db.close()
    body="""<section class="panel"><h2>⛏️ Mining System Settings</h2><form class="fields" method="post" action="{{url_for('save_mining_settings')}}"><div class="fields-grid"><label>Energy System<select name="mining_energy_enabled"><option value="1" {% if settings.get('mining_energy_enabled','1')=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if settings.get('mining_energy_enabled')=='0' %}selected{% endif %}>Disabled</option></select></label><label>Maximum Energy<input type="number" min="1" name="mining_max_energy" value="{{settings.get('mining_max_energy','100')}}"></label><label>Energy per Regeneration<input type="number" min="1" name="mining_energy_regen_amount" value="{{settings.get('mining_energy_regen_amount','5')}}"></label><label>Regeneration Seconds<input type="number" min="10" name="mining_energy_regen_seconds" value="{{settings.get('mining_energy_regen_seconds','1800')}}"></label><label>Give Starter Pickaxe<select name="mining_starter_pickaxe_enabled"><option value="1" {% if settings.get('mining_starter_pickaxe_enabled','1')=='1' %}selected{% endif %}>Enabled</option><option value="0" {% if settings.get('mining_starter_pickaxe_enabled')=='0' %}selected{% endif %}>Disabled</option></select></label><label>Collection Completion XC<input type="number" min="0" name="mining_collection_xc_reward" value="{{settings.get('mining_collection_xc_reward','500')}}"></label><label>Collection Completion XCrystals<input type="number" min="0" name="mining_collection_xcrystal_reward" value="{{settings.get('mining_collection_xcrystal_reward','5')}}"></label></div><button>Save Mining Settings</button></form></section>
    <details class="creator"><summary class="btn">＋ Create Mining Area</summary><section class="panel"><form class="fields" method="post" action="{{url_for('save_mining_area')}}"><div class="fields-grid"><label>Name<input name="name" required></label><label>Emoji<input name="emoji" value="⛏️"></label><label>Description<textarea name="description"></textarea></label><label>Required Level<input type="number" min="1" name="required_level" value="1"></label><label>Cooldown Seconds<input type="number" min="1" name="cooldown_seconds" value="60"></label><label>Energy Cost<input type="number" min="0" name="energy_cost" value="10"></label><label>EXP Min<input type="number" min="1" name="exp_min" value="10"></label><label>EXP Max<input type="number" min="1" name="exp_max" value="20"></label><label>XCrystal Chance %<input type="number" min="0" max="100" name="crystal_chance" value="0"></label><label>Position<input type="number" min="0" name="position" value="10"></label><label>Enabled<select name="enabled"><option value="1">Yes</option><option value="0">No</option></select></label></div><button>Create Area</button></form></section></details>
    <section class="panel"><h2>🗺️ Mining Areas</h2><div class="library">{% for a in areas %}<article class="library-card"><h3>{{a['emoji']}} {{a['name']}}</h3><form class="fields" method="post" action="{{url_for('save_mining_area')}}"><input type="hidden" name="id" value="{{a['id']}}"><label>Area Name<input name="name" value="{{a['name']}}" required></label><label>Emoji<input name="emoji" value="{{a['emoji']}}"></label><label>Description<textarea name="description">{{a['description']}}</textarea></label><div class="fields-grid"><label>Level<input type="number" min="1" name="required_level" value="{{a['required_level']}}"></label><label>Cooldown<input type="number" min="1" name="cooldown_seconds" value="{{a['cooldown_seconds']}}"></label><label>Energy<input type="number" min="0" name="energy_cost" value="{{a['energy_cost']}}"></label><label>EXP Min<input type="number" min="1" name="exp_min" value="{{a['exp_min']}}"></label><label>EXP Max<input type="number" min="1" name="exp_max" value="{{a['exp_max']}}"></label><label>Crystal %<input type="number" min="0" max="100" name="crystal_chance" value="{{a['crystal_chance']}}"></label><label>Position<input type="number" min="0" name="position" value="{{a['position']}}"></label><label>Status<select name="enabled"><option value="1" {% if a['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not a['enabled'] %}selected{% endif %}>Disabled</option></select></label></div><button>Save Area</button></form><hr><h3>Drop Pool</h3>{% for d in drops if d['area_id']==a['id'] %}<form class="mining-drop-fields" method="post" action="{{url_for('save_mining_drop')}}"><input type="hidden" name="area_id" value="{{a['id']}}"><input type="hidden" name="item_id" value="{{d['item_id']}}"><span>{{d['item_emoji']}} {{d['item_name']}}</span><label>Weight<input type="number" min="1" name="weight" value="{{d['weight']}}"></label><label>Min Yield<input type="number" min="1" name="min_yield" value="{{d['min_yield']}}"></label><label>Max Yield<input type="number" min="1" name="max_yield" value="{{d['max_yield']}}"></label><label>Rarity<select name="rare"><option value="0" {% if not d['rare'] %}selected{% endif %}>Normal</option><option value="1" {% if d['rare'] %}selected{% endif %}>Rare</option></select></label><button name="action" value="save">Save</button><button class="danger" name="action" value="remove">Remove Drop</button></form>{% endfor %}<form class="fields" method="post" action="{{url_for('save_mining_drop')}}"><input type="hidden" name="area_id" value="{{a['id']}}"><label>Add Material<select name="item_id">{% for i in materials %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><input type="hidden" name="weight" value="1"><input type="hidden" name="min_yield" value="1"><input type="hidden" name="max_yield" value="1"><input type="hidden" name="rare" value="0"><button name="action" value="save">＋ Add Drop</button></form></article>{% endfor %}</div></section>
    <section class="panel"><h2>🛠️ Pickaxe Configuration</h2><div class="library">{% for p in pickaxes %}<article class="library-card"><h3>{{p['emoji']}} {{p['name']}}</h3><form class="fields" method="post" action="{{url_for('save_pickaxe_stats')}}"><input type="hidden" name="id" value="{{p['id']}}"><label>Power<input type="number" min="1" name="pickaxe_power" value="{{p['pickaxe_power']}}"></label><label>Luck %<input type="number" min="0" max="100" name="pickaxe_luck" value="{{p['pickaxe_luck']}}"></label><label>Yield Bonus %<input type="number" min="0" max="500" name="pickaxe_yield_bonus" value="{{p['pickaxe_yield_bonus']}}"></label><label>Cooldown Reduction %<input type="number" min="0" max="90" name="pickaxe_cooldown_reduction" value="{{p['pickaxe_cooldown_reduction']}}"></label><label>Required Level<input type="number" min="1" name="pickaxe_required_level" value="{{p['pickaxe_required_level']}}"></label><button>Save Pickaxe</button></form></article>{% endfor %}</div></section>"""
    body=body.replace('<button>Save Area</button></form><hr><h3>Drop Pool</h3>', '<div class="actions"><button name="action" value="save">Save Area</button>{% if areas|length > 1 %}<button class="danger" name="action" value="delete" onclick="return confirm(\'Delete this mining area and its drop pool?\')">Delete Area</button>{% else %}<button class="secondary" type="button" disabled>Last Area Protected</button>{% endif %}</div></form><hr><h3>Drop Pool</h3>')
    return admin_page("Mining",body,areas=areas,drops=drops,materials=materials,pickaxes=pickaxes,settings=settings_rows)


@app.post("/mining/settings")
@login_required
def save_mining_settings():
    return save_economy_form(settings_admin.MINING_KEYS, 'mining_control')


@app.post("/mining/area/save")
@login_required
def save_mining_area():
    db=get_db(); row_id=request.form.get("id"); action=request.form.get("action","save")
    if action=="delete" and row_id:
        area=db.execute("SELECT name FROM mining_areas WHERE id=?",(int(row_id),)).fetchone()
        fallback=db.execute("SELECT id FROM mining_areas WHERE id<>? ORDER BY enabled DESC,position,id LIMIT 1",(int(row_id),)).fetchone()
        if area is None:
            flash("Mining area not found.")
        elif fallback is None:
            flash("The final mining area is protected. Create another area before deleting it.")
        else:
            db.execute("UPDATE players SET mining_area_id=? WHERE mining_area_id=?",(fallback["id"],int(row_id)))
            db.execute("DELETE FROM mining_area_drops WHERE area_id=?",(int(row_id),))
            db.execute("DELETE FROM mining_areas WHERE id=?",(int(row_id),));db.commit()
            flash(f"Mining area {area['name']} and its drop pool deleted.")
        db.close();return redirect(url_for("mining_control"))
    low=settings_admin.integer(request.form["exp_min"], "exp_min", 1, 999999999); high=max(low,int(request.form["exp_max"])); values=(request.form["name"].strip(),request.form.get("emoji","⛏️").strip() or "⛏️",request.form.get("description","").strip(),settings_admin.integer(request.form["required_level"], "required_level", 1, 999999999),settings_admin.integer(request.form["cooldown_seconds"], "cooldown_seconds", 1, 999999999),settings_admin.integer(request.form["energy_cost"], "energy_cost", 0, 999999999),low,high,min(100,settings_admin.integer(request.form["crystal_chance"], "crystal_chance", 0, 999999999)),settings_admin.integer(request.form["position"], "position", 0, 999999999),int(request.form.get("enabled",1)))
    try:
        if row_id: db.execute("UPDATE mining_areas SET name=?,emoji=?,description=?,required_level=?,cooldown_seconds=?,energy_cost=?,exp_min=?,exp_max=?,crystal_chance=?,position=?,enabled=? WHERE id=?",values+(int(row_id),))
        else: db.execute("INSERT INTO mining_areas(name,emoji,description,required_level,cooldown_seconds,energy_cost,exp_min,exp_max,crystal_chance,position,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?)",values)
        db.commit();flash("Mining area saved.")
    except sqlite3.IntegrityError as e: flash(str(e))
    db.close();return redirect(url_for("mining_control"))


@app.post("/mining/drop/save")
@login_required
def save_mining_drop():
    db=get_db(); area_id=int(request.form["area_id"]);item_id=int(request.form["item_id"])
    if request.form.get("action")=="remove": db.execute("DELETE FROM mining_area_drops WHERE area_id=? AND item_id=?",(area_id,item_id))
    else:
        low=settings_admin.integer(request.form.get("min_yield", 1), "min_yield", 1, 999999999);high=max(low,int(request.form.get("max_yield",1)))
        db.execute("""INSERT INTO mining_area_drops(area_id,item_id,weight,min_yield,max_yield,rare) VALUES(?,?,?,?,?,?) ON CONFLICT(area_id,item_id) DO UPDATE SET weight=excluded.weight,min_yield=excluded.min_yield,max_yield=excluded.max_yield,rare=excluded.rare""",(area_id,item_id,settings_admin.integer(request.form.get("weight", 1), "weight", 1, 999999999),low,high,int(request.form.get("rare",0))))
    db.commit();db.close();flash("Mining drop pool updated.");return redirect(url_for("mining_control"))


@app.post("/mining/pickaxe/save")
@login_required
def save_pickaxe_stats():
    db=get_db();db.execute("""UPDATE items SET pickaxe_power=?,pickaxe_luck=?,pickaxe_yield_bonus=?,pickaxe_cooldown_reduction=?,pickaxe_required_level=? WHERE id=? AND effect='mine_tool'""",(settings_admin.integer(request.form["pickaxe_power"], "pickaxe_power", 1, 999999999),min(100,settings_admin.integer(request.form["pickaxe_luck"], "pickaxe_luck", 0, 999999999)),min(500,settings_admin.integer(request.form["pickaxe_yield_bonus"], "pickaxe_yield_bonus", 0, 999999999)),min(90,settings_admin.integer(request.form["pickaxe_cooldown_reduction"], "pickaxe_cooldown_reduction", 0, 999999999)),settings_admin.integer(request.form["pickaxe_required_level"], "pickaxe_required_level", 1, 999999999),int(request.form["id"])));db.commit();db.close();flash("Pickaxe stats saved.");return redirect(url_for("mining_control"))


@app.post("/items/save")
@login_required
def save_item():
    db = get_db(); item_id = request.form.get("id")
    category_id = int(request.form['category_id']); category = db.execute("SELECT label FROM item_categories WHERE id=?", (category_id,)).fetchone()
    if category is None:
        db.close(); flash("Choose a valid category."); return redirect(url_for("items"))
    mine_min = settings_admin.integer(request.form['mine_min_yield'], 'mine_min_yield', 1, 999999999); mine_max = max(mine_min, int(request.form['mine_max_yield']))
    values = (request.form['name'].strip(), request.form['description'].strip(), request.form.get('emoji', '📦').strip() or '📦', request.form.get('aliases', '').strip(), category['label'].lower(), category_id, settings_admin.integer(request.form['price'], 'price', 0, 999999999), request.form['currency'], settings_admin.integer(request.form['sell_price'], 'sell_price', 0, 999999999), max(-1, int(request.form['stock'])), request.form['effect'], min(100, settings_admin.integer(request.form['effect_value'], 'effect_value', 0, 999999999)), int(request.form['sellable']), int(request.form['tradeable']), int(request.form['shop_visible']), settings_admin.integer(request.form['mine_weight'], 'mine_weight', 0, 999999999), mine_min, mine_max, int(request.form['enabled']))
    try:
        if item_id:
            db.execute("UPDATE items SET name=?,description=?,emoji=?,aliases=?,category=?,category_id=?,price=?,currency=?,sell_price=?,stock=?,effect=?,effect_value=?,sellable=?,tradeable=?,shop_visible=?,mine_weight=?,mine_min_yield=?,mine_max_yield=?,enabled=? WHERE id=?", values + (int(item_id),))
        else:
            db.execute("INSERT INTO items(name,description,emoji,aliases,category,category_id,price,currency,sell_price,stock,effect,effect_value,sellable,tradeable,shop_visible,mine_weight,mine_min_yield,mine_max_yield,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        db.commit(); flash("Item saved.")
    except sqlite3.IntegrityError:
        flash("Item name already exists.")
    db.close()
    return redirect(url_for("items"))


@app.post("/items/<int:item_id>/action")
@login_required
def item_action(item_id):
    db = get_db(); action = request.form.get("action")
    if action == "shop":
        db.execute("UPDATE items SET shop_visible=1-shop_visible WHERE id=?", (item_id,)); flash("Shop visibility updated.")
    elif action == "enabled":
        db.execute("UPDATE items SET enabled=1-enabled WHERE id=?", (item_id,)); flash("Item status updated.")
    elif action == "archive":
        db.execute("UPDATE items SET enabled=0,shop_visible=0 WHERE id=?", (item_id,)); flash("Item archived. Inventory records were preserved.")
    elif action == "delete":
        item = db.execute("SELECT name FROM items WHERE id=?", (item_id,)).fetchone()
        if item is None:
            flash("Item was not found.")
        else:
            # Empty inventory rows have no gameplay value and must not block cleanup.
            db.execute("DELETE FROM inventories WHERE item_id=? AND quantity<=0", (item_id,))
            usage = item_usage_details(db, item_id)
            if usage:
                flash(f"Cannot delete {item['name']}. Remove its usage first: " + "; ".join(usage))
            else:
                db.execute("DELETE FROM items WHERE id=?", (item_id,))
                flash(f"{item['name']} was permanently deleted.")
    db.commit(); db.close()
    return redirect(url_for("items"))


@app.post("/items/categories/save")
@login_required
def save_item_category():
    db = get_db(); category_id = request.form.get("id"); action = request.form.get("action", "save")
    if action == "delete" and category_id:
        current = db.execute("SELECT * FROM item_categories WHERE id=?", (int(category_id),)).fetchone()
        item_count = db.execute("SELECT COUNT(*) count FROM items WHERE category_id=?", (int(category_id),)).fetchone()["count"]
        if current is None:
            flash("Item category was not found.")
        elif current["system_category"]:
            flash("Protected fallback categories cannot be deleted.")
        elif item_count:
            flash(f"Cannot delete {current['label']}. Move its {item_count} item(s) to another category first.")
        else:
            db.execute("DELETE FROM item_categories WHERE id=?", (int(category_id),)); db.commit()
            flash(f"Item category {current['label']} deleted.")
        db.close(); return redirect(url_for("items") + "#category-manager")
    label = request.form["label"].strip()
    emoji = request.form.get("emoji", "📦").strip() or "📦"; position = settings_admin.integer(request.form.get("position", 99), "position", 0, 999999999)
    try:
        if not label:
            raise ValueError("Category name is required.")
        if category_id:
            current = db.execute("SELECT * FROM item_categories WHERE id=?", (int(category_id),)).fetchone()
            enabled = 1 if current and current["system_category"] else int(request.form.get("enabled", 1))
            db.execute("UPDATE item_categories SET label=?,emoji=?,position=?,enabled=? WHERE id=?", (label, emoji, position, enabled, int(category_id)))
        else:
            db.execute("INSERT INTO item_categories(label,emoji,position,enabled) VALUES(?,?,?,1)", (label, emoji, position))
        db.commit(); flash("Category saved inside Items.")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error))
    db.close(); return redirect(url_for("items"))


@app.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "save":
                category_id = request.form.get("id")
                label = request.form["label"].strip()
                emoji = request.form.get("emoji", "📦").strip() or "📦"
                position = settings_admin.integer(request.form.get("position", 99), "position", 0, 999999999)
                if not label:
                    raise ValueError("Category label is required.")
                if category_id:
                    db.execute("UPDATE item_categories SET label=?,emoji=?,position=?,enabled=? WHERE id=?", (label, emoji, position, int(request.form.get("enabled", 1)), int(category_id)))
                else:
                    db.execute("INSERT INTO item_categories(label,emoji,position) VALUES(?,?,?)", (label, emoji, position))
                db.commit(); flash("Category saved.")
            elif action == "disable":
                category_id = int(request.form["id"])
                category = db.execute("SELECT * FROM item_categories WHERE id=?", (category_id,)).fetchone()
                if category and category['system_category']:
                    raise ValueError("The Misc category is kept as a safe default and cannot be disabled.")
                item_count = db.execute("SELECT COUNT(*) c FROM items WHERE category_id=?", (category_id,)).fetchone()['c']
                if item_count:
                    raise ValueError("Move items to another category before disabling this category.")
                db.execute("UPDATE item_categories SET enabled=0 WHERE id=?", (category_id,)); db.commit(); flash("Category disabled.")
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error))
    rows = db.execute("""SELECT c.*,COUNT(i.id) item_count FROM item_categories c
        LEFT JOIN items i ON i.category_id=c.id GROUP BY c.id ORDER BY c.position,c.label""").fetchall()
    edit = db.execute("SELECT * FROM item_categories WHERE id=?", (request.args.get("edit", -1),)).fetchone()
    db.close()
    body = """<section class="panel"><h2>{{'Edit' if edit else 'Create'}} Category</h2><div class="notice">Categories control how all Items appear in the shop and inventory. <b>Misc</b> is the protected fallback category.</div><form class="fields" method="post"><input type="hidden" name="action" value="save"><input type="hidden" name="id" value="{{edit['id'] if edit else ''}}"><div class="fields-grid"><label>Label<input name="label" value="{{edit['label'] if edit else ''}}" required></label><label>Emoji<input name="emoji" value="{{edit['emoji'] if edit else '📦'}}"></label><label>Display Order<input type="number" min="0" name="position" value="{{edit['position'] if edit else 10}}"></label><label>Enabled<select name="enabled"><option value="1" {% if not edit or edit['enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['enabled'] %}selected{% endif %}>No</option></select></label></div><div class="actions"><button>Save Category</button>{% if edit %}<a class="btn secondary" href="{{url_for('categories')}}">Cancel</a>{% endif %}</div></form></section><section class="panel"><h2>Manage Categories</h2><div class="library">{% for c in rows %}<article class="library-card"><h3>{{c['emoji']}} {{c['label']}}</h3><div class="statline">Items: <b>{{c['item_count']}}</b><br>Order: {{c['position']}}<br><span class="{{'ok' if c['enabled'] else 'bad'}}">{{'Enabled' if c['enabled'] else 'Disabled'}}</span>{% if c['system_category'] %}<br><span class="muted">Protected fallback</span>{% endif %}</div><div class="actions"><a class="btn" href="{{url_for('categories',edit=c['id'])}}">Edit</a>{% if not c['system_category'] and c['enabled'] %}<form method="post"><input type="hidden" name="action" value="disable"><input type="hidden" name="id" value="{{c['id']}}"><button class="danger">Disable</button></form>{% endif %}</div></article>{% endfor %}</div></section>"""
    body = body.replace('<section class="panel"><h2>{{\'Edit\' if edit else \'Create\'}} Category</h2>', '<details class="creator" {% if edit or creating %}open{% endif %}><summary class="btn">＋ Create New Category</summary><section class="panel"><h2>{{\'Edit\' if edit else \'Create\'}} Category</h2>', 1)
    body = body.replace('</form></section><section class="panel"><h2>Manage Categories</h2>', '</form></section></details><section class="panel"><h2>Manage Categories</h2>', 1)
    return admin_page("Categories", body, rows=rows, edit=edit, creating=request.args.get('create'))


@app.route("/casino", methods=["GET", "POST"])
@login_required
def casino_control():
    if request.method == "POST":
        action = request.form.get("action", "toggle")
        if action == "save-game":
            return save_economy_form((), "casino_control", game=request.form.get("game", ""))
        allowed = settings_admin.VIP_KEYS if action == "save-vip" else settings_admin.FREE_GAME_KEYS if action == "save-free-games" else ("casino_enabled",)
        return save_economy_form(allowed, "casino_control")
    db = get_db()
    totals = db.execute("""SELECT COALESCE(SUM(games_played),0) games,COALESCE(SUM(total_wagered),0) wagered,
        COALESCE(SUM(total_won),0) paid,COUNT(*) players FROM casino_stats""").fetchone()
    leaders = db.execute("""SELECT s.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(s.user_id AS TEXT)) display_name FROM casino_stats s LEFT JOIN players p ON p.user_id=s.user_id ORDER BY s.total_won-s.total_wagered DESC,s.biggest_payout DESC LIMIT 10""").fetchall()
    round_row = db.execute("SELECT * FROM lottery_rounds WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    tickets = 0 if round_row is None else db.execute("SELECT COALESCE(SUM(tickets),0) t FROM lottery_entries WHERE round_id=?", (round_row['id'],)).fetchone()['t']
    activity = db.execute("""SELECT l.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(l.user_id AS TEXT)) display_name FROM economy_logs l LEFT JOIN players p ON p.user_id=l.user_id WHERE action LIKE 'casino_%' OR action LIKE 'lottery_%' ORDER BY l.id DESC LIMIT 25""").fetchall()
    casino_enabled = db.execute("SELECT value FROM economy_settings WHERE key='casino_enabled'").fetchone()['value']
    game_rows = db.execute("SELECT * FROM casino_game_settings ORDER BY game").fetchall()
    casino_values = {row['key']: row['value'] for row in db.execute("SELECT key,value FROM economy_settings") if row['key'] in settings_admin.VIP_KEYS + settings_admin.FREE_GAME_KEYS}
    memory_totals = db.execute("""SELECT COUNT(*) games,COALESCE(SUM(reward_xc),0) rewards,
        COUNT(DISTINCT user_id) players FROM casual_game_completions""").fetchone()
    memory_today = db.execute("""SELECT COUNT(*) games,COALESCE(SUM(reward_xc),0) rewards
        FROM casual_game_completions WHERE day=?""", (casual_games.game_day(),)).fetchone()
    memory_sessions = db.execute("""SELECT COUNT(*) started,
        SUM(CASE WHEN status='abandoned' THEN 1 ELSE 0 END) abandoned,
        SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) expired
        FROM casual_game_sessions""").fetchone()
    memory_returning = db.execute("""SELECT COUNT(*) FROM (
        SELECT user_id FROM casual_game_completions WHERE day>=date('now','localtime','-6 days')
        GROUP BY user_id HAVING COUNT(DISTINCT day)>=2)""").fetchone()[0]
    db.close()
    body = """<div class="grid"><div class="card"><small>Casino Games</small><strong>{{totals['games']}}</strong></div><div class="card"><small>XC Wagered</small><strong>{{totals['wagered']}}</strong></div><div class="card"><small>XC Paid Out</small><strong>{{totals['paid']}}</strong></div><div class="card"><small>Casino Players</small><strong>{{totals['players']}}</strong></div></div><section class="panel"><h2>💎 Casino VIP & Cooldowns</h2><div class="notice">Casino VIP costs XC for a temporary Discord Role and shorter Casino command cooldowns. It does not change game odds.</div><form class="fields" method="post"><input type="hidden" name="action" value="save-vip"><div class="fields-grid"><label>Standard Game Cooldown (seconds)<input type="number" min="0" name="casino_cooldown_seconds" value="{{casino_values.get('casino_cooldown_seconds','45')}}"></label><label>Casino VIP Price (XC)<input type="number" min="0" name="casino_vip_daily_cost" value="{{casino_values.get('casino_vip_daily_cost','100')}}"></label><label>VIP Duration (seconds)<input type="number" min="60" name="casino_vip_duration_seconds" value="{{casino_values.get('casino_vip_duration_seconds','86400')}}"></label><label>VIP Cooldown Reduction %<input type="number" min="0" max="95" name="casino_vip_cooldown_percent" value="{{casino_values.get('casino_vip_cooldown_percent','50')}}"></label></div><button>Save Casino VIP</button></form></section><section class="panel"><h2>🎰 Individual Game Control</h2><div class="notice">Set 0 for a game minimum or maximum bet to inherit the global Casino limit. Set a game's cooldown to 0 for no cooldown; otherwise it uses that game's own number of seconds.</div><div class="library">{% for game in games %}<article class="library-card"><h3>🎲 /{{game['game']}}</h3><form class="fields" method="post"><input type="hidden" name="action" value="save-game"><input type="hidden" name="game" value="{{game['game']}}"><label>Status<select name="enabled"><option value="1" {% if game['enabled'] %}selected{% endif %}>Open</option><option value="0" {% if not game['enabled'] %}selected{% endif %}>Closed</option></select></label><div class="fields-grid"><label>Min Bet (0 = global)<input type="number" min="0" name="min_bet" value="{{game['min_bet']}}"></label><label>Max Bet (0 = global)<input type="number" min="0" name="max_bet" value="{{game['max_bet']}}"></label><label>Cooldown Seconds (0 = none)<input type="number" min="0" name="cooldown_seconds" value="{{game['cooldown_seconds']}}"><small>Seconds · 0 or more. This game only; 0 = no cooldown.</small></label></div><button>Save /{{game['game']}}</button></form></article>{% endfor %}</div></section><section class="panel"><h2>Active Lottery</h2><div class="pad">{% if round %}<b>Round #{{round['id']}}</b><br>Prize pool: <b>{{round['prize_pool']}} XC</b><br>Tickets entered: <b>{{tickets}}</b><p class="muted">Use <code>/lottery_draw</code> in Discord as a server administrator to draw this round.</p>{% else %}<span class="muted">The first /lottery command creates a round.</span>{% endif %}</div></section><section class="panel"><h2>Casino Leaderboard</h2><table><tr><th>Player</th><th>Games</th><th>Wagered</th><th>Paid Out</th><th>Net</th><th>Biggest Payout</th></tr>{% for r in leaders %}<tr><td><b>{{r['display_name']}}</b><br><span class="muted">{{r['user_id']}}</span></td><td>{{r['games_played']}}</td><td>{{r['total_won']}} XC</td><td>{{r['total_won']}} XC</td><td>{{r['total_won']-r['total_wagered']}} XC</td><td>{{r['biggest_payout']}} XC</td></tr>{% else %}<tr><td colspan="6">No casino activity yet.</td></tr>{% endfor %}</table></section><section class="panel"><h2>Recent Casino Activity</h2><table><tr><th>User</th><th>Game</th><th>Detail</th></tr>{% for r in activity %}<tr><td>{{r['display_name']}}</td><td>{{r['action']}}</td><td>{{r['detail']}}</td></tr>{% else %}<tr><td colspan="3">No activity yet.</td></tr>{% endfor %}</table></section>"""
    control = """<section class="panel"><h2>Casino Master Control</h2><form class="fields" method="post"><input type="hidden" name="casino_enabled" value="{{0 if enabled=='1' else 1}}"><p class="{{'ok' if enabled=='1' else 'bad'}}">Casino is currently {{'OPEN' if enabled=='1' else 'CLOSED'}}.</p><div class="actions"><button class="{{'danger' if enabled=='1' else 'teal'}}">{{'Close Casino' if enabled=='1' else 'Open Casino'}}</button></div></form></section>"""
    svip = """<section class="panel"><h2>🪙 Server Very Important Person (SVIP)</h2><div class="notice">SVIP uses the permanent Discord role <code>Server Very Important Person (SVIP)</code>. It is the top Casino VIP and replaces the paid Casino VIP benefit; the two reductions never stack.</div><form class="fields" method="post"><input type="hidden" name="action" value="save-vip"><div class="fields-grid"><label>SVIP Casino Cooldown Reduction %<input type="number" min="0" max="95" name="server_svip_cooldown_percent" value="{{casino_values.get('server_svip_cooldown_percent','75')}}"></label></div><button>Save SVIP Benefit</button></form></section>"""
    svip_fields="""<label>SVIP Extra Production Slots<input type="number" min="0" max="25" name="server_svip_production_slots" value="{{casino_values.get('server_svip_production_slots','2')}}"><small>0–25 extra active orders; new orders only.</small></label><label>SVIP Extra Market Listings<input type="number" min="0" max="100" name="server_svip_market_listings" value="{{casino_values.get('server_svip_market_listings','5')}}"><small>0–100 extra listings; existing listings are retained on role loss.</small></label><label>SVIP Production Time Reduction %<input type="number" min="0" max="95" name="server_svip_production_percent" value="{{casino_values.get('server_svip_production_percent','10')}}"><small>0–95%; after existing discounts, minimum 30 seconds. New jobs only. No Casino odds change.</small></label>"""
    svip=svip.replace('</div><button>Save SVIP Benefit',svip_fields+'</div><button>Save SVIP Benefit')
    free_games = """<section class="panel"><h2>🧠 Free Games</h2><div class="notice">Memory Match never requires a bet. Rewarded games share one daily XC limit; players may continue in Practice mode after reaching it.</div><div class="grid pad"><div class="card"><small>Started</small><strong>{{memory_sessions['started']}}</strong></div><div class="card"><small>Completed</small><strong>{{memory_totals['games']}}</strong></div><div class="card"><small>Exited / Expired</small><strong>{{memory_sessions['abandoned'] or 0}} / {{memory_sessions['expired'] or 0}}</strong></div><div class="card"><small>Players</small><strong>{{memory_totals['players']}}</strong></div><div class="card"><small>XC Awarded</small><strong>{{memory_totals['rewards']}}</strong></div><div class="card"><small>Today</small><strong>{{memory_today['games']}} games · {{memory_today['rewards']}} XC</strong></div><div class="card"><small>7-day Returning</small><strong>{{memory_returning}}</strong></div></div><form class="fields" method="post"><input type="hidden" name="action" value="save-free-games"><div class="fields-grid"><label>Free Games<select name="free_games_enabled"><option value="1" {% if casino_values.get('free_games_enabled','1')=='1' %}selected{% endif %}>Open</option><option value="0" {% if casino_values.get('free_games_enabled','1')=='0' %}selected{% endif %}>Closed</option></select><small>Applies at the next game start and reward settlement.</small></label><label>Rewarded Games / Day<input type="number" min="0" max="10" name="memory_daily_reward_games" value="{{casino_values.get('memory_daily_reward_games','3')}}"><small>0–10. Practice remains available while Free Games are open.</small></label><label>Daily Memory XC Limit<input type="number" min="0" max="100" name="memory_daily_xc_limit" value="{{casino_values.get('memory_daily_xc_limit','25')}}"><small>0–100 XC per player per server-local day.</small></label></div><button>Save Free Game Settings</button></form></section>"""
    body = control + svip + free_games + body
    body = body.replace('<label>VIP Cooldown Reduction %<input type="number" min="0" max="95" name="casino_vip_cooldown_percent" value="{{casino_values.get(\'casino_vip_cooldown_percent\',\'50\')}}"></label>', '<label>VIP Cooldown Reduction %<input type="number" min="0" max="95" name="casino_vip_cooldown_percent" value="{{casino_values.get(\'casino_vip_cooldown_percent\',\'50\')}}"></label><label>Crash Daily Net Win Limit (XC, 0 = off)<input type="number" min="0" name="crash_daily_net_win_limit" value="{{casino_values.get(\'crash_daily_net_win_limit\',\'2500\')}}"></label>')
    body = body.replace("<td>{{r['user_id']}}</td>", "<td><b>{{r['display_name']}}</b><br><span class='muted'>{{r['user_id']}}</span></td>")
    body = body.replace("<td>{{r['total_won']}} XC</td><td>{{r['total_won']}} XC</td>", "<td>{{r['total_wagered']}} XC</td><td>{{r['total_won']}} XC</td>")
    return admin_page("Casino", body, totals=totals, leaders=leaders, round=round_row, tickets=tickets, activity=activity, enabled=casino_enabled, games=game_rows, casino_values=casino_values, memory_totals=memory_totals, memory_today=memory_today, memory_sessions=memory_sessions, memory_returning=memory_returning)


@app.route("/market", methods=["GET", "POST"])
@login_required
def market_control():
    if request.method == "POST":
        return save_economy_form(("market_enabled",), "market_control")
    db = get_db()
    rows = db.execute("""SELECT l.*,i.name item_name,i.emoji,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(l.seller_id AS TEXT)) seller_name
        FROM market_listings l JOIN items i ON i.id=l.item_id LEFT JOIN players p ON p.user_id=l.seller_id
        ORDER BY l.active DESC,l.id DESC LIMIT 200""").fetchall()
    enabled = db.execute("SELECT value FROM economy_settings WHERE key='market_enabled'").fetchone()['value']
    db.close()
    body = """<section class="panel"><h2>Player Market Control</h2><form class="fields" method="post"><input type="hidden" name="market_enabled" value="{{0 if enabled=='1' else 1}}"><p class="{{'ok' if enabled=='1' else 'bad'}}">Market is currently {{'OPEN' if enabled=='1' else 'CLOSED'}}.</p><div class="actions"><button class="{{'danger' if enabled=='1' else 'teal'}}">{{'Close Market' if enabled=='1' else 'Open Market'}}</button></div></form></section><section class="panel"><h2>Market Listings</h2><table><tr><th>ID</th><th>Seller</th><th>Item</th><th>Quantity</th><th>Price Each</th><th>Status</th><th></th></tr>{% for r in rows %}<tr><td>#{{r['id']}}</td><td><b>{{r['seller_name']}}</b><br><span class="muted">{{r['seller_id']}}</span></td><td>{{r['emoji']}} {{r['item_name']}}</td><td>{{r['quantity']}}</td><td>{{r['price_each']}} XC</td><td><span class="{{'ok' if r['active'] else 'bad'}}">{{'Active' if r['active'] else 'Closed'}}</span></td><td>{% if r['active'] %}<form method="post" action="{{url_for('admin_cancel_market',listing_id=r['id'])}}"><button class="danger">Cancel & Return</button></form>{% endif %}</td></tr>{% else %}<tr><td colspan="7">No market listings.</td></tr>{% endfor %}</table></section>"""
    return admin_page("Market", body, rows=rows, enabled=enabled)


@app.post("/market/<int:listing_id>/cancel")
@login_required
def admin_cancel_market(listing_id):
    db = get_db(); row = db.execute("SELECT * FROM market_listings WHERE id=? AND active=1", (listing_id,)).fetchone()
    if row:
        db.execute("UPDATE market_listings SET active=0 WHERE id=?", (listing_id,))
        db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""", (row['seller_id'], row['item_id'], row['quantity']))
        db.commit(); flash("Listing cancelled and items returned to the seller.")
    db.close(); return redirect(url_for("market_control"))


@app.route("/tier6-economy")
@login_required
def tier6_economy_control():
    db = get_db()
    setting_keys = list(tier6.DEFAULTS) + [
        "daily_reward", "daily_cooldown", "transfer_tax_percent",
        "market_enabled", "market_fee_percent", "market_min_price", "market_max_price",
    ]
    settings = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in setting_keys)})", setting_keys
    ).fetchall()}
    totals = db.execute("""SELECT COUNT(*) players,COALESCE(SUM(xc),0) wallet,
        COALESCE(SUM(bank_xc),0) bank,COALESCE(SUM(money),0) war_credits,
        COALESCE(SUM(xcrystals),0) crystals FROM players""").fetchone()
    stock_value = int(db.execute("""SELECT COALESCE(SUM(h.quantity*c.price),0)
        FROM tier6_stock_holdings h JOIN tier6_stock_companies c ON c.id=h.company_id""").fetchone()[0])
    companies = db.execute("""SELECT c.*,
        COALESCE((SELECT SUM(quantity) FROM tier6_stock_holdings h WHERE h.company_id=c.id),0) held,
        COALESCE((SELECT COUNT(*) FROM tier6_stock_trades t WHERE t.company_id=c.id),0) trades
        FROM tier6_stock_companies c ORDER BY c.symbol""").fetchall()
    contracts = db.execute("""SELECT c.*,i.name item_name,i.emoji item_emoji,
        COALESCE((SELECT COUNT(*) FROM tier6_contract_claims x WHERE x.contract_id=c.id),0) claims
        FROM tier6_contracts c LEFT JOIN items i ON i.id=c.reward_item_id ORDER BY c.period,c.id""").fetchall()
    items = db.execute("SELECT id,name,emoji FROM items WHERE enabled=1 ORDER BY name").fetchall()
    recent_trades = db.execute("""SELECT t.*,c.symbol,c.name company_name,
        COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(t.user_id AS TEXT)) player_name
        FROM tier6_stock_trades t JOIN tier6_stock_companies c ON c.id=t.company_id
        LEFT JOIN players p ON p.user_id=t.user_id ORDER BY t.id DESC LIMIT 100""").fetchall()
    market_trades = db.execute("""SELECT t.*,i.name item_name,i.emoji item_emoji,
        COALESCE(NULLIF(b.display_name,''),b.nation_name,CAST(t.buyer_id AS TEXT)) buyer_name,
        COALESCE(NULLIF(s.display_name,''),s.nation_name,CAST(t.seller_id AS TEXT)) seller_name
        FROM tier6_market_trades t JOIN items i ON i.id=t.item_id
        LEFT JOIN players b ON b.user_id=t.buyer_id LEFT JOIN players s ON s.user_id=t.seller_id
        ORDER BY t.id DESC LIMIT 100""").fetchall()
    production = db.execute("""SELECT q.*,COALESCE(r.name,'Archived Recipe') recipe_name,
        COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(q.user_id AS TEXT)) player_name
        FROM tier6_production_queue q LEFT JOIN recipes r ON r.id=q.recipe_id
        LEFT JOIN players p ON p.user_id=q.user_id ORDER BY q.id DESC LIMIT 100""").fetchall()
    activity = db.execute("""SELECT action,COUNT(*) count FROM economy_logs
        WHERE created_at>=? GROUP BY action ORDER BY count DESC LIMIT 12""", (int(time.time()) - 86400,)).fetchall()
    health = tier6.health_report(db)
    db.close()
    body = """
    <section class="panel"><h2>💰 Economy Control Centre</h2><div class="pad">
      <div class="notice">Every economy rule is stored in the shared Bot database. Changes apply after the next panel refresh; no code edit is required.</div>
      <div class="grid">
        <div class="card"><small>Players</small><strong>{{totals['players']}}</strong></div>
        <div class="card"><small>Wallet + Bank XC</small><strong>{{totals['wallet']+totals['bank']}}</strong></div>
        <div class="card"><small>War Credits</small><strong>{{totals['war_credits']}}</strong></div>
        <div class="card"><small>Stock Market Value</small><strong>{{stock_value}} XC</strong></div>
      </div>
      <p class="notice">{{health}}</p>
      <div class="actions">
        <form method="post" action="{{url_for('tier6_force_stock_update')}}"><button class="purple">Update Stock Prices Now</button></form>
        <form method="post" action="{{url_for('tier6_repair')}}" onsubmit="return confirm('Run a safe Economy repair?')"><button class="teal">Health Check & Repair</button></form>
      </div>
    </div></section>

    <nav class="economy-tabs" aria-label="Economy sections">
      <a href="#economy-rules">Rules</a><a href="#economy-stocks">Stocks</a><a href="#economy-contracts">Contracts</a><a href="#economy-trades">Transactions</a><a href="#economy-production">Production</a>
    </nav>
    <div class="economy-tab-panel" id="economy-rules">
    """ + dashboard_ui.SETTINGS_PANEL + """

    <section class="panel"><h2>🔗 Detailed Economy Editors</h2><div class="library">
      <a class="library-card" href="{{url_for('mining_control')}}"><h3>⛏️ Mining</h3><p>Areas, energy, drops, tools, probabilities and yield.</p></a>
      <a class="library-card" href="{{url_for('items')}}"><h3>📦 Items</h3><p>Prices, effects, stock, sell-back value and trade rules.</p></a>
      <a class="library-card" href="{{url_for('recipes_control')}}"><h3>🧪 Recipes</h3><p>Ingredients, outputs, quantity, XC cost and availability.</p></a>
      <a class="library-card" href="{{url_for('market_control')}}"><h3>🏷️ Player Market</h3><p>Open listings, seller details and administrative cancellation.</p></a>
      <a class="library-card" href="{{url_for('finance_control')}}"><h3>🏦 Bills & Income</h3><p>Recurring XC sources and optional currency sinks.</p></a>
    </div></section>

    </div><div class="economy-tab-panel" id="economy-stocks">
    <details class="creator" id="company-create"><summary class="btn purple">＋ Create Stock Company</summary><section class="panel"><h2>New Virtual Company</h2>
      <form class="fields" method="post" action="{{url_for('tier6_save_company')}}"><div class="fields-grid">
        <label>Symbol<input name="symbol" maxlength="8" required></label><label>Name<input name="name" required></label>
        <label>Emoji<input name="emoji" value="📈"></label><label>Industry<input name="industry" value="Industry" required></label>
        <label>Description<textarea name="description"></textarea></label><label>Starting Price<input type="number" min="1" name="price" value="100"></label>
        <label>Minimum Price<input type="number" min="1" name="min_price" value="10"></label><label>Maximum Price<input type="number" min="1" name="max_price" value="1000"></label>
        <label>Total Shares<input type="number" min="1" name="total_shares" value="100000"></label><label>Volatility %<input type="number" min="1" max="50" name="volatility" value="8"></label>
        <label>Trend % per Update<input type="number" min="-20" max="20" name="trend" value="0"></label><label>Status<select name="enabled"><option value="1">Open</option><option value="0">Paused</option></select></label>
      </div><button>Create Company</button></form></section></details>

    <section class="panel"><h2>📈 Virtual Stock Companies</h2><div class="library">{% for c in companies %}<article class="library-card economy-record"><form class="fields" method="post" action="{{url_for('tier6_save_company')}}">
      <input type="hidden" name="id" value="{{c['id']}}"><h3>{{c['emoji']}} {{c['symbol']}} · {{c['name']}}</h3>
      <div class="statline">Price {{c['price']}} XC · Previous {{c['previous_price']}}<br>Held {{c['held']}} / {{c['total_shares']}} · Trades {{c['trades']}}</div>
      <span class="record-status">{{ 'Open' if c['enabled'] else 'Paused' }}</span><details class="creator record-editor" id="company-{{c['id']}}"><summary class="btn">Edit Company</summary><div class="record-fields">
      <div class="fields-grid"><label>Symbol<input name="symbol" value="{{c['symbol']}}" required></label><label>Name<input name="name" value="{{c['name']}}" required></label>
      <label>Emoji<input name="emoji" value="{{c['emoji']}}"></label><label>Industry<input name="industry" value="{{c['industry']}}"></label>
      <label>Description<textarea name="description">{{c['description']}}</textarea></label><label>Current Price<input type="number" min="1" name="price" value="{{c['price']}}"></label>
      <label>Minimum<input type="number" min="1" name="min_price" value="{{c['min_price']}}"></label><label>Maximum<input type="number" min="1" name="max_price" value="{{c['max_price']}}"></label>
      <label>Total Shares<input type="number" min="1" name="total_shares" value="{{c['total_shares']}}"></label><label>Volatility %<input type="number" min="1" max="50" name="volatility" value="{{c['volatility']}}"></label>
      <label>Trend<input type="number" min="-20" max="20" name="trend" value="{{c['trend']}}"></label><label>Status<select name="enabled"><option value="1" {% if c['enabled'] %}selected{% endif %}>Open</option><option value="0" {% if not c['enabled'] %}selected{% endif %}>Paused</option></select></label></div>
      <button>Save Company</button></div></details></form></article>{% endfor %}</div></section>

    </div><div class="economy-tab-panel" id="economy-contracts">
    <details class="creator" id="contract-create"><summary class="btn teal">＋ Create Contract</summary><section class="panel"><h2>New Economy Contract</h2>
      <form class="fields" method="post" action="{{url_for('tier6_save_contract')}}"><div class="fields-grid">
        <label>Unique Key<input name="contract_key" required></label><label>Title<input name="title" required></label><label>Emoji<input name="emoji" value="📋"></label>
        <label>Description<textarea name="description"></textarea></label><label>Action<select name="action_type">{% for a in ['mine','sell','collect','develop','trade','recruit','craft','stock_trade'] %}<option>{{a}}</option>{% endfor %}</select></label>
        <label>Target<input type="number" min="1" name="target" value="1"></label><label>Reward XC<input type="number" min="0" name="reward_xc" value="50"></label>
        <label>Reward War Credits<input type="number" min="0" name="reward_war_credits" value="100"></label><label>Reward Item<select name="reward_item_id"><option value="">None</option>{% for i in items %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label>
        <label>Item Quantity<input type="number" min="0" name="reward_item_quantity" value="0"></label><label>Period<select name="period"><option>daily</option><option>weekly</option><option>once</option></select></label>
        <label>Minimum Nation Level<input type="number" min="1" max="10" name="minimum_level" value="1"></label><label>Status<select name="enabled"><option value="1">Open</option><option value="0">Closed</option></select></label>
      </div><button>Create Contract</button></form></section></details>

    <section class="panel"><h2>📋 Contracts</h2><div class="library">{% for c in contracts %}<article class="library-card economy-record"><form class="fields" method="post" action="{{url_for('tier6_save_contract')}}">
      <input type="hidden" name="id" value="{{c['id']}}"><h3>{{c['emoji']}} {{c['title']}}</h3><div class="statline">{{c['period']}} · {{c['action_type']}} ×{{c['target']}} · {{c['claims']}} claims</div>
      <span class="record-status">{{ 'Open' if c['enabled'] else 'Closed' }}</span><details class="creator record-editor" id="contract-{{c['id']}}"><summary class="btn">Edit Contract</summary><div class="record-fields">
      <div class="fields-grid"><label>Unique Key<input name="contract_key" value="{{c['contract_key']}}"></label><label>Title<input name="title" value="{{c['title']}}"></label><label>Emoji<input name="emoji" value="{{c['emoji']}}"></label>
      <label>Description<textarea name="description">{{c['description']}}</textarea></label><label>Action<select name="action_type">{% for a in ['mine','sell','collect','develop','trade','recruit','craft','stock_trade'] %}<option {% if c['action_type']==a %}selected{% endif %}>{{a}}</option>{% endfor %}</select></label>
      <label>Target<input type="number" min="1" name="target" value="{{c['target']}}"></label><label>Reward XC<input type="number" min="0" name="reward_xc" value="{{c['reward_xc']}}"></label>
      <label>Reward WC<input type="number" min="0" name="reward_war_credits" value="{{c['reward_war_credits']}}"></label><label>Reward Item<select name="reward_item_id"><option value="">None</option>{% for i in items %}<option value="{{i['id']}}" {% if c['reward_item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label>
      <label>Item Quantity<input type="number" min="0" name="reward_item_quantity" value="{{c['reward_item_quantity']}}"></label><label>Period<select name="period">{% for p in ['daily','weekly','once'] %}<option {% if c['period']==p %}selected{% endif %}>{{p}}</option>{% endfor %}</select></label>
      <label>Minimum Level<input type="number" min="1" max="10" name="minimum_level" value="{{c['minimum_level']}}"></label><label>Status<select name="enabled"><option value="1" {% if c['enabled'] %}selected{% endif %}>Open</option><option value="0" {% if not c['enabled'] %}selected{% endif %}>Closed</option></select></label></div>
      <button>Save Contract</button></div></details></form></article>{% endfor %}</div></section>

    </div><div class="economy-tab-panel" id="economy-trades">
    <section class="panel"><h2>📊 Last 24 Hours Activity</h2><table><tr><th>Action</th><th>Count</th></tr>{% for a in activity %}<tr><td>{{a['action']}}</td><td>{{a['count']}}</td></tr>{% else %}<tr><td colspan="2">No Economy activity.</td></tr>{% endfor %}</table></section>
    <section class="panel"><h2>💹 Recent Stock Trades</h2><table><tr><th>Player</th><th>Company</th><th>Side</th><th>Quantity</th><th>Price</th><th>Fee</th><th>Time</th></tr>{% for t in recent_trades %}<tr><td>{{t['player_name']}}</td><td>{{t['symbol']}}</td><td>{{t['side']}}</td><td>{{t['quantity']}}</td><td>{{t['price']}} XC</td><td>{{t['fee']}}</td><td>{{t['created_at']|timestamp}}</td></tr>{% else %}<tr><td colspan="7">No trades.</td></tr>{% endfor %}</table></section>
    <section class="panel"><h2>🏷️ Recent Player Market Trades</h2><table><tr><th>Buyer</th><th>Seller</th><th>Item</th><th>Quantity</th><th>Price</th><th>Fee</th><th>Time</th></tr>{% for t in market_trades %}<tr><td>{{t['buyer_name']}}</td><td>{{t['seller_name']}}</td><td>{{t['item_emoji']}} {{t['item_name']}}</td><td>{{t['quantity']}}</td><td>{{t['price_each']}} XC</td><td>{{t['fee']}}</td><td>{{t['created_at']|timestamp}}</td></tr>{% else %}<tr><td colspan="7">No player market trades.</td></tr>{% endfor %}</table></section>
    </div><div class="economy-tab-panel" id="economy-production">
    <section class="panel"><h2>🏭 Production Queue</h2><table><tr><th>Player</th><th>Recipe</th><th>Quantity</th><th>Status</th><th>Ready</th></tr>{% for q in production %}<tr><td>{{q['player_name']}}</td><td>{{q['recipe_name']}}</td><td>{{q['quantity']}}</td><td>{{q['status']}}</td><td>{{q['ready_at']|timestamp}}</td></tr>{% else %}<tr><td colspan="5">No production jobs.</td></tr>{% endfor %}</table></section>
    """
    body += '</div>'
    return admin_page("Economy Control", body, settings=settings, totals=totals, stock_value=stock_value,
                      companies=companies, contracts=contracts, items=items, recent_trades=recent_trades,
                      market_trades=market_trades, production=production, activity=activity, health=health,
                      setting_values=settings, setting_groups=setting_groups(settings_admin.TIER6_KEYS),
                      setting_action=url_for("tier6_save_settings"))


@app.post("/tier6-economy/settings")
@login_required
def tier6_save_settings():
    return save_economy_form(settings_admin.TIER6_KEYS, "tier6_economy_control")


@app.post("/tier6-economy/company/save")
@login_required
def tier6_save_company():
    db = get_db(); company_id = request.form.get("id")
    symbol = request.form["symbol"].strip().upper()[:8]
    name = request.form["name"].strip()[:80]
    price = settings_admin.integer(request.form.get("price", 100), "price", 1, 999999999)
    minimum = settings_admin.integer(request.form.get("min_price", 1), "min_price", 1, 999999999)
    maximum = max(minimum, int(request.form.get("max_price", 1000)))
    price = min(maximum, max(minimum, price))
    total = settings_admin.integer(request.form.get("total_shares", 100000), "total_shares", 1, 999999999)
    values = (symbol, name, request.form.get("emoji", "📈").strip() or "📈", request.form.get("industry", "Industry").strip()[:60],
              request.form.get("description", "").strip()[:500], price, minimum, maximum, total,
              settings_admin.integer(request.form.get("volatility", 8), "volatility", 1, 50), max(-20, min(20, int(request.form.get("trend", 0)))), int(request.form.get("enabled", 1)))
    try:
        if company_id:
            held = int(db.execute("SELECT COALESCE(SUM(quantity),0) FROM tier6_stock_holdings WHERE company_id=?", (int(company_id),)).fetchone()[0])
            total = max(total, held)
            values = values[:8] + (total,) + values[9:]
            db.execute("""UPDATE tier6_stock_companies SET symbol=?,name=?,emoji=?,industry=?,description=?,price=?,min_price=?,max_price=?,total_shares=?,available_shares=?,volatility=?,trend=?,enabled=? WHERE id=?""",
                       values[:9] + (total - held,) + values[9:] + (int(company_id),))
        else:
            now = int(time.time())
            db.execute("""INSERT INTO tier6_stock_companies(symbol,name,emoji,industry,description,price,previous_price,min_price,max_price,total_shares,available_shares,volatility,trend,enabled,last_update)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values[:6] + (price,) + values[6:9] + (total,) + values[9:] + (now,))
        db.commit(); flash(f"Stock company {symbol} saved.")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback(); flash(str(error))
    db.close(); return redirect(url_for("tier6_economy_control"))


@app.post("/tier6-economy/contract/save")
@login_required
def tier6_save_contract():
    db = get_db(); contract_id = request.form.get("id")
    item_id = request.form.get("reward_item_id")
    values = (request.form["contract_key"].strip()[:80], request.form["title"].strip()[:100], request.form.get("emoji", "📋").strip() or "📋",
              request.form.get("description", "").strip()[:500], request.form.get("action_type", "mine"), settings_admin.integer(request.form.get("target", 1), "target", 1, 999999999),
              settings_admin.integer(request.form.get("reward_xc", 0), "reward_xc", 0, 999999999), settings_admin.integer(request.form.get("reward_war_credits", 0), "reward_war_credits", 0, 999999999), int(item_id) if item_id else None,
              settings_admin.integer(request.form.get("reward_item_quantity", 0), "reward_item_quantity", 0, 999999999), request.form.get("period", "daily"), settings_admin.integer(request.form.get("minimum_level", 1), "minimum_level", 1, 10), int(request.form.get("enabled", 1)))
    try:
        if contract_id:
            db.execute("""UPDATE tier6_contracts SET contract_key=?,title=?,emoji=?,description=?,action_type=?,target=?,reward_xc=?,reward_war_credits=?,reward_item_id=?,reward_item_quantity=?,period=?,minimum_level=?,enabled=? WHERE id=?""", values + (int(contract_id),))
        else:
            db.execute("""INSERT INTO tier6_contracts(contract_key,title,emoji,description,action_type,target,reward_xc,reward_war_credits,reward_item_id,reward_item_quantity,period,minimum_level,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        db.commit(); flash("Economy Contract saved.")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback(); flash(str(error))
    db.close(); return redirect(url_for("tier6_economy_control"))


@app.post("/tier6-economy/stocks/update")
@login_required
def tier6_force_stock_update():
    db = get_db()
    interval = max(60, tier6.setting(db, "tier6_stock_update_seconds"))
    db.execute("UPDATE tier6_stock_companies SET last_update=MAX(0,last_update-?) WHERE enabled=1", (interval,))
    changed = tier6.update_stock_prices(db)
    db.close(); flash(f"Updated {changed} Stock price(s).")
    return redirect(url_for("tier6_economy_control"))


@app.post("/tier6-economy/repair")
@login_required
def tier6_repair():
    db = get_db(); message = tier6.repair(db); db.close(); flash(message)
    return redirect(url_for("tier6_economy_control"))


@app.route("/logs")
@login_required
def logs():
    db = get_db(); rows = db.execute("""SELECT l.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(l.user_id AS TEXT)) display_name FROM economy_logs l LEFT JOIN players p ON p.user_id=l.user_id ORDER BY l.id DESC LIMIT 200""").fetchall()
    commands = db.execute("""SELECT c.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(c.user_id AS TEXT)) display_name
        FROM command_usage_logs c LEFT JOIN players p ON p.user_id=c.user_id ORDER BY c.id DESC LIMIT 200""").fetchall()
    audits = db.execute("SELECT * FROM dashboard_audit_logs ORDER BY id DESC LIMIT 200").fetchall(); db.close()
    body = """<section class="panel"><h2>Command Access Logs</h2><table><tr><th>ID</th><th>User</th><th>Command</th><th>Result</th><th>Reason</th><th>Guild / Channel</th><th>Unix Time</th></tr>{% for r in commands %}<tr><td>{{r['id']}}</td><td><b>{{r['display_name']}}</b><br><span class="muted">{{r['user_id']}}</span></td><td>/{{r['command_name']}}</td><td><span class="{{'ok' if r['allowed'] else 'bad'}}">{{'Allowed' if r['allowed'] else 'Denied'}}</span></td><td>{{r['reason']}}</td><td>{{r['guild_id']}} / {{r['channel_id']}}</td><td>{{r['created_at']}}</td></tr>{% else %}<tr><td colspan="7">No command access logs.</td></tr>{% endfor %}</table></section><section class="panel"><h2>Economy Logs</h2><table><tr><th>ID</th><th>User</th><th>Action</th><th>Detail</th><th>Unix Time</th></tr>{% for r in rows %}<tr><td>{{r['id']}}</td><td><b>{{r['display_name']}}</b><br><span class="muted">{{r['user_id']}}</span></td><td>{{r['action']}}</td><td>{{r['detail']}}</td><td>{{r['created_at']}}</td></tr>{% else %}<tr><td colspan="5">No logs.</td></tr>{% endfor %}</table></section><section class="panel"><h2>Dashboard Audit Logs</h2><table><tr><th>Actor</th><th>Access</th><th>Action</th><th>Result</th><th>Detail</th><th>Time</th></tr>{% for r in audits %}<tr><td><b>{{r['actor_name']}}</b><br><span class="muted">{{r['actor_id']}}</span></td><td>{{r['access_level']}}</td><td>{{r['method']}} {{r['endpoint']}}</td><td>{{r['status_code']}}</td><td>{{r['detail']}}</td><td>{{r['created_at']}}</td></tr>{% else %}<tr><td colspan="6">No Dashboard changes recorded.</td></tr>{% endfor %}</table></section>"""
    return admin_page("Logs", body, rows=rows, commands=commands, audits=audits)


def _reward_code_expiry_input(value):
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%dT%H:%M") if value else ""


@app.route("/reward-codes")
@login_required
def reward_codes_control():
    db = get_db()
    codes = db.execute("""SELECT r.*,i.name item_name,i.emoji item_emoji,
        COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(r.created_by AS TEXT)) creator_name
        FROM reward_codes r LEFT JOIN items i ON i.id=r.item_id
        LEFT JOIN players p ON p.user_id=r.created_by ORDER BY r.id DESC""").fetchall()
    items = db.execute("SELECT id,name,emoji FROM items WHERE enabled=1 ORDER BY name").fetchall()
    redemptions = db.execute("""SELECT h.*,r.code,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(h.user_id AS TEXT)) player_name
        FROM reward_code_redemptions h JOIN reward_codes r ON r.id=h.code_id
        LEFT JOIN players p ON p.user_id=h.user_id ORDER BY h.id DESC LIMIT 100""").fetchall()
    db.close()
    body = """<section class="panel"><h2>🎟 Reward Code Manager</h2><div class="notice">Create rewards for events, staff giveaways or game milestones. One member can redeem each code once. A maximum use value of <b>0</b> means unlimited uses.</div><details class="creator" open><summary class="btn">＋ Create Reward Code</summary><form class="fields pad" method="post" action="{{url_for('save_reward_code')}}"><div class="fields-grid"><label>Code<input name="code" maxlength="40" placeholder="SUMMER100" required></label><label>Description<input name="description" maxlength="180" placeholder="Summer event reward"></label><label>XC Reward<input type="number" min="0" name="reward_xc" value="0"></label><label>War Credits Reward<input type="number" min="0" name="reward_war_credits" value="0"></label><label>XCrystals Reward<input type="number" min="0" name="reward_xcrystals" value="0"></label><label>Item Reward<select name="item_id"><option value="">No item</option>{% for i in items %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Item Quantity<input type="number" min="0" name="item_quantity" value="0"></label><label>Maximum Uses (0 = unlimited)<input type="number" min="0" name="max_uses" value="0"></label><label>Expiry (optional)<input type="datetime-local" name="expires_at"></label><label>Enabled<select name="enabled"><option value="1">Enabled</option><option value="0">Disabled</option></select></label></div><button>Create Reward Code</button></form></details></section><section class="panel"><h2>Active & Saved Codes</h2><div class="library">{% for r in codes %}<article class="library-card"><h3>🎟 <code>{{r['code']}}</code> <button class="secondary copy-code" type="button" data-code="{{r['code']}}">Copy</button></h3><p>{{r['description'] or 'No description.'}}</p><div class="statline">🪙 {{r['reward_xc']}} XC · ⚔️ {{r['reward_war_credits']}} War Credits · 💎 {{r['reward_xcrystals']}} XCrystals{% if r['item_name'] and r['item_quantity'] %}<br>{{r['item_emoji']}} {{r['item_name']}} ×{{r['item_quantity']}}{% endif %}<br>Uses: <b>{{r['uses']}} / {{'∞' if r['max_uses']==0 else r['max_uses']}}</b><br>Expires: {{expiry(r['expires_at']) if r['expires_at'] else 'Never'}}<br><span class="{{'ok' if r['enabled'] else 'bad'}}">{{'Enabled' if r['enabled'] else 'Disabled'}}</span></div><details><summary>Edit code</summary><form class="fields" method="post" action="{{url_for('save_reward_code')}}"><input type="hidden" name="id" value="{{r['id']}}"><input type="hidden" name="code" value="{{r['code']}}"><div class="fields-grid"><label>Description<input name="description" value="{{r['description']}}"></label><label>XC<input type="number" min="0" name="reward_xc" value="{{r['reward_xc']}}"></label><label>War Credits<input type="number" min="0" name="reward_war_credits" value="{{r['reward_war_credits']}}"></label><label>XCrystals<input type="number" min="0" name="reward_xcrystals" value="{{r['reward_xcrystals']}}"></label><label>Item<select name="item_id"><option value="">No item</option>{% for i in items %}<option value="{{i['id']}}" {% if r['item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Item Quantity<input type="number" min="0" name="item_quantity" value="{{r['item_quantity']}}"></label><label>Maximum Uses<input type="number" min="0" name="max_uses" value="{{r['max_uses']}}"></label><label>Expiry<input type="datetime-local" name="expires_at" value="{{expiry_input(r['expires_at'])}}"></label><label>Status<select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select></label></div><div class="actions"><button>Save Code</button></form><form method="post" action="{{url_for('delete_reward_code',code_id=r['id'])}}" onsubmit="return confirm('Delete this reward code and its redemption history?')"><button class="danger">Delete</button></form></div></details></article>{% else %}<p>No reward codes yet.</p>{% endfor %}</div></section><section class="panel"><h2>Recent Redemptions</h2><table><tr><th>Code</th><th>Player</th><th>Time</th></tr>{% for r in redemptions %}<tr><td><code>{{r['code']}}</code></td><td>{{r['player_name']}}</td><td>{{r['redeemed_at']}}</td></tr>{% else %}<tr><td colspan="3">No codes have been redeemed.</td></tr>{% endfor %}</table></section><script>document.querySelectorAll('.copy-code').forEach(button=>button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(button.dataset.code);button.textContent='Copied!';setTimeout(()=>button.textContent='Copy',1200)}catch(e){prompt('Copy this code:',button.dataset.code)}}));</script>"""
    return admin_page("Reward Codes", body, codes=codes, items=items, redemptions=redemptions,
                      expiry=lambda value: datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M"),
                      expiry_input=_reward_code_expiry_input)


@app.post("/reward-codes/save")
@login_required
def save_reward_code():
    db = get_db(); code_id = request.form.get("id", "").strip(); code = request.form.get("code", "").strip().upper()
    if not code or len(code) > 40 or any(not (character.isalnum() or character in "_-") for character in code):
        db.close(); flash("Code must use only letters, numbers, _ or -."); return redirect(url_for("reward_codes_control"))
    try:
        expires_text = request.form.get("expires_at", "").strip()
        expires_at = int(datetime.fromisoformat(expires_text).timestamp()) if expires_text else 0
        item_id = int(request.form["item_id"]) if request.form.get("item_id") else None
        values = (request.form.get("description", "").strip()[:180], settings_admin.integer(request.form.get("reward_xc", 0), "reward_xc", 0, 999999999), settings_admin.integer(request.form.get("reward_war_credits", 0), "reward_war_credits", 0, 999999999), settings_admin.integer(request.form.get("reward_xcrystals", 0), "reward_xcrystals", 0, 999999999), item_id, settings_admin.integer(request.form.get("item_quantity", 0), "item_quantity", 0, 999999999), settings_admin.integer(request.form.get("max_uses", 0), "max_uses", 0, 999999999), expires_at, int(request.form.get("enabled", 0)))
        if code_id:
            db.execute("""UPDATE reward_codes SET description=?,reward_xc=?,reward_war_credits=?,reward_xcrystals=?,item_id=?,item_quantity=?,max_uses=?,expires_at=?,enabled=? WHERE id=?""", (*values, int(code_id)))
            flash("Reward code saved.")
        else:
            actor = int(session.get("discord_id") or 0)
            db.execute("""INSERT INTO reward_codes(code,description,reward_xc,reward_war_credits,reward_xcrystals,item_id,item_quantity,max_uses,expires_at,enabled,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (code, *values, actor, int(time.time())))
            flash("Reward code created.")
        db.commit()
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback(); flash(f"Could not save reward code: {error}")
    db.close(); return redirect(url_for("reward_codes_control"))


@app.post("/reward-codes/<int:code_id>/delete")
@login_required
def delete_reward_code(code_id):
    db = get_db(); db.execute("DELETE FROM reward_code_redemptions WHERE code_id=?", (code_id,)); db.execute("DELETE FROM reward_codes WHERE id=?", (code_id,)); db.commit(); db.close(); flash("Reward code deleted.")
    return redirect(url_for("reward_codes_control"))


@app.route("/diplomacy")
@login_required
def diplomacy_control():
    db = get_db()
    alliances = db.execute(
        """SELECT a.*,p.nation_name leader_name,COUNT(m.user_id) members
           FROM alliances a LEFT JOIN players p ON p.user_id=a.leader_id
           LEFT JOIN alliance_members m ON m.alliance_id=a.id
           GROUP BY a.id ORDER BY members DESC,a.name"""
    ).fetchall()
    relations = db.execute(
        """SELECT r.*,a.nation_name first_name,b.nation_name second_name
           FROM nation_relations r LEFT JOIN players a ON a.user_id=r.first_user_id
           LEFT JOIN players b ON b.user_id=r.second_user_id
           ORDER BY r.started_at DESC LIMIT 100"""
    ).fetchall()
    trades = db.execute(
        """SELECT t.*,a.nation_name proposer_name,b.nation_name target_name
           FROM nation_trades t LEFT JOIN players a ON a.user_id=t.proposer_id
           LEFT JOIN players b ON b.user_id=t.target_id
           ORDER BY t.id DESC LIMIT 100"""
    ).fetchall()
    pending = {
        "Alliance invites": db.execute("SELECT COUNT(*) n FROM alliance_invites WHERE status='pending'").fetchone()["n"],
        "Relation requests": db.execute("SELECT COUNT(*) n FROM nation_relation_requests WHERE status='pending'").fetchone()["n"],
        "Peace offers": db.execute("SELECT COUNT(*) n FROM nation_peace_offers WHERE status='pending'").fetchone()["n"],
        "Nation trades": db.execute("SELECT COUNT(*) n FROM nation_trades WHERE status='pending'").fetchone()["n"],
    }
    db.close()
    body = """
    <div class="grid">{% for label,value in pending.items() %}<div class="card"><small>{{label}}</small><strong>{{value}}</strong></div>{% endfor %}</div>
    <section class="panel"><h2>🤝 Alliances</h2><div class="notice">Membership now uses private invitations through the Discord Diplomacy Inbox.</div><table><tr><th>Alliance</th><th>Leader</th><th>Members</th><th>Created</th></tr>{% for row in alliances %}<tr><td><b>[{{row['tag']}}] {{row['name']}}</b></td><td>{{row['leader_name'] or row['leader_id']}}</td><td>{{row['members']}}</td><td>{{row['created_at']}}</td></tr>{% else %}<tr><td colspan="4">No Alliances yet.</td></tr>{% endfor %}</table></section>
    <section class="panel"><h2>🕊️ Nation Relations</h2><table><tr><th>Nation A</th><th>Nation B</th><th>Relation</th><th>Ends</th></tr>{% for row in relations %}<tr><td>{{row['first_name'] or row['first_user_id']}}</td><td>{{row['second_name'] or row['second_user_id']}}</td><td>{{row['relation_type'].replace('_',' ')|title}}</td><td>{{row['ends_at'] or 'Permanent'}}</td></tr>{% else %}<tr><td colspan="4">No active relations.</td></tr>{% endfor %}</table></section>
    <section class="panel"><h2>📦 Nation Trade Audit</h2><table><tr><th>ID</th><th>From</th><th>To</th><th>Offer</th><th>Request</th><th>Status</th></tr>{% for row in trades %}<tr><td>#{{row['id']}}</td><td>{{row['proposer_name'] or row['proposer_id']}}</td><td>{{row['target_name'] or row['target_id']}}</td><td>{{row['offer_xc']}} XC · {{row['offer_war_credits']}} WC · {{row['offer_supply']}} Supply</td><td>{{row['request_xc']}} XC · {{row['request_war_credits']}} WC · {{row['request_supply']}} Supply</td><td>{{row['status']|title}}</td></tr>{% else %}<tr><td colspan="6">No Nation trades.</td></tr>{% endfor %}</table></section>
    """
    return admin_page("Diplomacy", body, alliances=alliances, relations=relations, trades=trades, pending=pending)


@app.route("/war")
@login_required
def war_control():
    db = get_db()
    alliances = db.execute("""SELECT a.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(a.leader_id AS TEXT)) leader_name
        FROM alliances a LEFT JOIN players p ON p.user_id=a.leader_id ORDER BY a.name""").fetchall()
    players_list = db.execute("SELECT user_id,COALESCE(NULLIF(display_name,''),nation_name) display_name,nation_name FROM players ORDER BY display_name").fetchall()
    active = db.execute("SELECT * FROM wars WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    category_rows = db.execute("""SELECT c.*,COUNT(u.id) unit_count FROM war_unit_categories c
        LEFT JOIN war_unit_types u ON u.category_id=c.id GROUP BY c.id ORDER BY c.position,c.label""").fetchall()
    default_category_row = db.execute("SELECT value FROM economy_settings WHERE key='army_recruit_default_category_id'").fetchone()
    default_category_id = int(default_category_row["value"]) if default_category_row and str(default_category_row["value"]).isdigit() else 0
    default_sort_row = db.execute("SELECT value FROM economy_settings WHERE key='army_recruit_default_sort'").fetchone()
    default_recruit_sort = default_sort_row["value"] if default_sort_row else "manual"
    recruit_sort_sql = {
        "manual": "u.position,u.name",
        "cost_low": "u.cost ASC,u.power ASC,u.name",
        "cost_high": "u.cost DESC,u.power DESC,u.name",
        "power_high": "u.power DESC,u.cost DESC,u.name",
        "name": "u.name COLLATE NOCASE",
    }
    if default_recruit_sort not in recruit_sort_sql:
        default_recruit_sort = "manual"
    unit_types = db.execute(f"""SELECT u.*,c.label category_label,c.emoji category_emoji,
        ROUND(CAST(u.cost AS REAL)/CASE WHEN u.power>0 THEN u.power ELSE 1 END,1) cost_per_power FROM war_unit_types u
        LEFT JOIN war_unit_categories c ON c.id=u.category_id
        ORDER BY c.position,{recruit_sort_sql[default_recruit_sort]}""").fetchall()
    enabled_categories = [row for row in category_rows if row["enabled"]]
    service_totals = {row["branch"]: dict(row) for row in db.execute("""SELECT u.branch,COALESCE(SUM(p.quantity),0) units,
        COALESCE(SUM(p.quantity*u.power),0) power FROM war_unit_types u LEFT JOIN player_war_units p ON p.unit_type_id=u.id
        WHERE u.enabled=1 GROUP BY u.branch""").fetchall()}
    edit_unit = db.execute("SELECT * FROM war_unit_types WHERE id=?", (request.args.get("edit_unit", -1),)).fetchone()
    battles = db.execute("""SELECT b.*,COALESCE(NULLIF(pa.display_name,''),pa.nation_name,CAST(b.attacker_id AS TEXT)) attacker_name,
        COALESCE(NULLIF(pd.display_name,''),pd.nation_name,CAST(b.defender_id AS TEXT)) defender_name,
        COALESCE(NULLIF(pw.display_name,''),pw.nation_name,CAST(b.winner_id AS TEXT)) winner_name
        FROM battle_history b LEFT JOIN players pa ON pa.user_id=b.attacker_id LEFT JOIN players pd ON pd.user_id=b.defender_id
        LEFT JOIN players pw ON pw.user_id=b.winner_id ORDER BY b.id DESC LIMIT 25""").fetchall()
    divisions=db.execute("""SELECT t.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(t.user_id AS TEXT)) owner_name,
        COALESCE(SUM(d.quantity*u.power),0) template_power FROM division_templates t LEFT JOIN players p ON p.user_id=t.user_id
        LEFT JOIN division_template_units d ON d.template_id=t.id LEFT JOIN war_unit_types u ON u.id=d.unit_type_id GROUP BY t.id ORDER BY t.created_at DESC LIMIT 100""").fetchall()
    readiness_rows=db.execute("""SELECT p.user_id,p.capital_health,p.money,p.nation_name,
        COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(p.user_id AS TEXT)) display_name,
        COALESCE(s.defense_stance,'balanced') defense_stance,COALESCE(s.fortification_level,0) fortification_level,
        COALESCE(s.morale,100) morale,COALESCE(s.supply,500) supply,COALESCE(s.readiness,100) readiness
        FROM players p LEFT JOIN player_war_settings s ON p.user_id=s.user_id
        ORDER BY readiness DESC,supply DESC LIMIT 100""").fetchall()
    season_active = db.execute("SELECT * FROM war_seasons WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    season_scores = db.execute("""SELECT s.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(s.user_id AS TEXT)) player_name
        FROM war_season_scores s LEFT JOIN players p ON p.user_id=s.user_id
        WHERE s.season_id=? ORDER BY s.points DESC,s.wins DESC,s.land_captured DESC LIMIT 50""",
        (season_active["id"],)).fetchall() if season_active else []
    season_history = db.execute("""SELECT s.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,'No champion') champion_name
        FROM war_seasons s LEFT JOIN players p ON p.user_id=s.champion_user_id
        ORDER BY s.id DESC LIMIT 10""").fetchall()
    season_rule_keys=("war_season_win_points","war_season_loss_points","war_season_land_points",
        "war_season_capital_damage_per_point","war_season_defense_points","war_season_capital_capture_points",
        "war_season_land_hold_points","war_season_participation_min_battles",
        "war_season_participation_xc","war_new_nation_protection_seconds","war_pair_attack_cooldown")
    season_rules={row["key"]:row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in season_rule_keys)})",season_rule_keys)}
    war_keys=("war_max_supply","war_supply_cost","war_supply_per_attack","war_readiness_per_prepare","war_prepare_supply_cost",
        "city_collect_cooldown","city_capital_war_credits","city_capital_supply","city_civilian_build_cost","city_industrial_build_cost","city_civilian_war_credits","city_industrial_war_credits","city_industrial_supply","city_upgrade_base_cost","city_rename_cost","land_max_level","land_upgrade_2_credits","land_upgrade_2_supply","land_upgrade_3_credits","land_upgrade_3_supply","land_upgrade_4_credits","land_upgrade_4_supply","land_upgrade_5_credits","land_upgrade_5_supply",
        "attack_cooldown","capital_damage","capital_reward_percent","capital_repair_cost_per_hp",
        "fortified_defense_bonus","aggressive_attack_bonus","scout_cost","scout_cooldown",
        "demobilize_refund_percent","fortify_base_cost","fortify_power_percent","fortify_max_level",
        "war_battle_variance_percent","war_winner_loss_percent","war_loser_loss_percent",
        "war_readiness_loss_per_battle","war_winner_morale_gain","war_loser_morale_loss",
        "war_rally_supply_cost","war_rally_morale_gain","war_air_superiority_bonus",
        "war_navy_blockade_penalty","war_navy_supply_damage")
    war_settings={r['key']:r['value'] for r in db.execute(f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in war_keys)})",war_keys)}
    real_regions = war_tier.world_city_tiles(war_tier._province_features())
    real_capitals = war_tier.world_capital_tiles()
    # Refresh improved full labels while preserving every owner's stable code,
    # land level, capital flag and acquisition time.  Only write labels which
    # actually changed; the old all-world UPDATE loop held a needless SQLite
    # write lock every time an administrator opened this GET page.
    mapped_names = {
        str(row["territory_code"]): str(row["territory_name"])
        for row in db.execute("SELECT territory_code,territory_name FROM map_territories").fetchall()
    }
    label_updates = [
        (str(region[1]), code)
        for code, region in real_regions.items()
        if code in mapped_names and mapped_names[code] != str(region[1])
    ]
    if label_updates:
        db.executemany("UPDATE map_territories SET territory_name=? WHERE territory_code=?", label_updates)
        db.commit()
    territory_rows = db.execute("""SELECT t.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(t.owner_user_id AS TEXT)) owner_name,
        p.nation_name,p.capital_name FROM map_territories t LEFT JOIN players p ON p.user_id=t.owner_user_id
        ORDER BY owner_name,t.is_capital DESC,t.territory_name""").fetchall()
    territory_nations = []
    seen_territory_owners = set()
    for territory in territory_rows:
        owner_id = str(territory["owner_user_id"])
        if owner_id not in seen_territory_owners:
            seen_territory_owners.add(owner_id)
            territory_nations.append((owner_id, territory["owner_name"], territory["nation_name"] or "Unnamed Nation"))
    city_rows = db.execute("""SELECT c.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(c.user_id AS TEXT)) owner_name,
        COALESCE(t.territory_name,'Unassigned') territory_name
        FROM player_cities c LEFT JOIN players p ON p.user_id=c.user_id
        LEFT JOIN map_territories t ON t.territory_code=c.territory_code
        ORDER BY owner_name,c.name""").fetchall()
    claimed_codes = {row["territory_code"] for row in territory_rows}
    available_regions = [real_regions[code] for code in real_regions if code not in claimed_codes]
    available_capitals = [real_capitals[code] for code in real_capitals if code not in claimed_codes]
    available_region_countries = sorted({
        (region[1].rsplit(", ", 1)[-1].split(" [", 1)[0] if ", " in region[1] else "Other")
        for region in available_regions
    } | {
        (capital[1].rsplit(", ", 1)[-1] if ", " in capital[1] else "Other")
        for capital in available_capitals
    })
    tier7_settings = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM economy_settings WHERE key IN ({','.join('?' for _ in tier7.DEFAULTS)})",
        tuple(tier7.DEFAULTS),
    ).fetchall()}
    tier7_setting_fields = [
        {
            "key": key,
            "label": key.removeprefix("tier7_").replace("_", " ").title(),
            "min": tier7.SETTING_LIMITS[key][0],
            "max": tier7.SETTING_LIMITS[key][1],
        }
        for key in tier7.DEFAULTS
    ]
    tier7_health = tier7.health_report(db)
    tier7_profiles = db.execute("""SELECT d.*,COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(d.user_id AS TEXT)) player_name
        FROM tier7_defence_profiles d LEFT JOIN players p ON p.user_id=d.user_id
        ORDER BY player_name LIMIT 250""").fetchall()
    tier7_territories = db.execute("""SELECT d.*,t.territory_name,t.is_capital,
        COALESCE(NULLIF(p.display_name,''),p.nation_name,CAST(d.owner_user_id AS TEXT)) owner_name
        FROM tier7_territory_defence d JOIN map_territories t ON t.territory_code=d.territory_code
        LEFT JOIN players p ON p.user_id=d.owner_user_id
        ORDER BY owner_name,t.is_capital DESC,t.territory_name LIMIT 500""").fetchall()
    tier7_plans = db.execute("""SELECT x.*,
        COALESCE(NULLIF(a.display_name,''),a.nation_name,CAST(x.attacker_id AS TEXT)) attacker_name,
        COALESCE(NULLIF(d.display_name,''),d.nation_name,CAST(x.defender_id AS TEXT),'Not selected') defender_name,
        COALESCE(t.territory_name,'Not selected') territory_name
        FROM tier7_battle_plans x LEFT JOIN players a ON a.user_id=x.attacker_id
        LEFT JOIN players d ON d.user_id=x.defender_id LEFT JOIN map_territories t ON t.territory_code=x.territory_code
        ORDER BY x.id DESC LIMIT 100""").fetchall()
    tier7_reports = db.execute("""SELECT r.*,
        COALESCE(NULLIF(a.display_name,''),a.nation_name,CAST(r.attacker_id AS TEXT)) attacker_name,
        COALESCE(NULLIF(d.display_name,''),d.nation_name,CAST(r.defender_id AS TEXT)) defender_name,
        COALESCE(NULLIF(w.display_name,''),w.nation_name,CAST(r.winner_id AS TEXT)) winner_name
        FROM tier7_battle_reports r LEFT JOIN players a ON a.user_id=r.attacker_id
        LEFT JOIN players d ON d.user_id=r.defender_id LEFT JOIN players w ON w.user_id=r.winner_id
        ORDER BY r.id DESC LIMIT 100""").fetchall()
    tier7_events = db.execute("SELECT * FROM tier7_events ORDER BY id DESC LIMIT 100").fetchall()
    db.close()
    tier7_panel = """<details class="creator" id="tier7-control"><summary class="btn purple">⚔️ Advanced Warfront</summary>
    <section class="panel"><h2>🩺 Warfront Health & Safety</h2><div class="grid pad">
      <div class="card"><small>Status</small><strong class="{{'ok' if tier7_health['healthy'] else 'bad'}}">{{'Healthy' if tier7_health['healthy'] else 'Needs repair'}}</strong></div>
      <div class="card"><small>Battle Reports</small><strong>{{tier7_health['reports']}}</strong></div>
      <div class="card"><small>Open Plans</small><strong>{{tier7_health['draft_plans']}}</strong></div>
      <div class="card"><small>Defended Lands</small><strong>{{tier7_health['territories']}}</strong></div>
      <div class="card"><small>Expired Drafts</small><strong class="{{'bad' if tier7_health['expired_drafts'] else 'ok'}}">{{tier7_health['expired_drafts']}}</strong></div>
      <div class="card"><small>Interrupted Battles</small><strong class="{{'bad' if tier7_health['stuck_resolving'] else 'ok'}}">{{tier7_health['stuck_resolving']}}</strong></div>
      <div class="card"><small>Missing Reports</small><strong class="{{'bad' if tier7_health['missing_reports'] else 'ok'}}">{{tier7_health['missing_reports']}}</strong></div>
      <div class="card"><small>Ownership Problems</small><strong class="{{'bad' if tier7_health['orphan_defence'] else 'ok'}}">{{tier7_health['orphan_defence']}}</strong></div>
    </div><div class="top-actions"><form method="post" action="{{url_for('tier7_repair_dashboard')}}"><button class="teal">Health Check & Safe Repair</button></form></div></section>

    <section class="panel"><h2>⚙️ Advanced Warfront Balance</h2><div class="notice">These values control Attack Planner modes, cooldowns, terrain, fortifications, reports and defence automation. Changes apply to new previews and battles immediately.</div>
      <form class="fields" method="post" action="{{url_for('tier7_save_settings')}}"><div class="fields-grid">
      {% for field in tier7_setting_fields %}<label>{{field['label']}}<input type="number" name="{{field['key']}}" min="{{field['min']}}" max="{{field['max']}}" value="{{tier7_settings.get(field['key'],tier7_defaults[field['key']])}}" required></label>{% endfor %}
      </div><button>Save All Warfront Settings</button></form></section>

    <section class="panel"><h2>🛡️ Nation Defence Profiles</h2><div class="library">
      {% for row in tier7_profiles %}<article class="library-card"><h3>🏳️ {{row['player_name']}}</h3><form class="fields" method="post" action="{{url_for('tier7_save_profile')}}"><input type="hidden" name="user_id" value="{{row['user_id']}}"><div class="fields-grid">
        <label>Garrison Commitment %<input type="number" min="10" max="100" name="garrison_percent" value="{{row['garrison_percent']}}"></label>
        <label>Capital Priority<select name="capital_priority"><option value="1" {% if row['capital_priority'] %}selected{% endif %}>On</option><option value="0" {% if not row['capital_priority'] %}selected{% endif %}>Off</option></select></label>
        <label>Auto Reinforce<select name="auto_reinforce"><option value="1" {% if row['auto_reinforce'] %}selected{% endif %}>On</option><option value="0" {% if not row['auto_reinforce'] %}selected{% endif %}>Off</option></select></label>
      </div><button>Save Defence Profile</button></form></article>{% else %}<p>No Nation defence profiles yet.</p>{% endfor %}
    </div></section>

    <section class="panel"><h2>🗺️ Territory Terrain & Fortification</h2><div class="notice">Terrain is generated once and can be corrected here. Captured Land keeps its terrain and fortification, while ownership is synchronised automatically.</div><table><tr><th>Nation / Land</th><th>Terrain</th><th>Fortification</th><th>Save</th></tr>
      {% for row in tier7_territories %}<tr><td><b>{{'★ ' if row['is_capital'] else ''}}{{row['territory_name']}}</b><br><span class="muted">{{row['owner_name']}}</span></td><td colspan="3"><form class="inline" method="post" action="{{url_for('tier7_save_territory')}}"><input type="hidden" name="territory_code" value="{{row['territory_code']}}"><select name="terrain">{% for terrain in tier7_terrains %}<option value="{{terrain}}" {% if row['terrain']==terrain %}selected{% endif %}>{{terrain|title}}</option>{% endfor %}</select><input type="number" min="0" max="{{tier7_settings.get('tier7_fortification_max_level','5')}}" name="fortification_level" value="{{row['fortification_level']}}"><button>Save</button></form></td></tr>{% else %}<tr><td colspan="4">No mapped Land yet.</td></tr>{% endfor %}
    </table></section>

    <section class="panel"><h2>🎯 Recent Attack Plans</h2><table><tr><th>ID</th><th>Attacker</th><th>Target / Land</th><th>Mode</th><th>Status</th><th>Control</th></tr>
      {% for row in tier7_plans %}<tr><td>#{{row['id']}}</td><td>{{row['attacker_name']}}</td><td>{{row['defender_name']}}<br><span class="muted">{{row['territory_name']}}</span></td><td>{{row['mode']|title}}</td><td class="{{'ok' if row['status']=='resolved' else 'bad' if row['status']=='failed' else ''}}">{{row['status']|title}}</td><td>{% if row['status']=='draft' %}<form method="post" action="{{url_for('tier7_cancel_plan',plan_id=row['id'])}}"><button class="danger">Cancel Draft</button></form>{% else %}—{% endif %}</td></tr>{% else %}<tr><td colspan="6">No attack plans yet.</td></tr>{% endfor %}
    </table></section>

    <section class="panel"><h2>📜 Battle Audit</h2><table><tr><th>Battle</th><th>Sides</th><th>Objective</th><th>Power</th><th>Winner</th><th>Result</th></tr>
      {% for row in tier7_reports %}<tr><td>#{{row['battle_id']}}<br><span class="muted">{{row['created_at']|timestamp}}</span></td><td>{{row['attacker_name']}} → {{row['defender_name']}}</td><td>{{row['territory_name']}}<br><span class="muted">{{row['terrain']|title}} · {{row['mode']|title}}</span></td><td>{{row['attacker_score']}} vs {{row['defender_score']}}</td><td>{{row['winner_name']}}</td><td>{{row['land_captured']}} Land · {{row['capital_damage']}} HP · {{row['credits_captured']}} WC</td></tr>{% else %}<tr><td colspan="6">No advanced battles yet.</td></tr>{% endfor %}
    </table></section>

    <section class="panel"><h2>🧾 Recent Warfront Events</h2><table><tr><th>Time</th><th>Event</th><th>User</th><th>Plan</th><th>Safe Detail</th></tr>
      {% for row in tier7_events %}<tr><td>{{row['created_at']|timestamp}}</td><td>{{row['event_type'].replace('_',' ')|title}}</td><td>{{row['user_id'] or 'System'}}</td><td>{{('#' ~ row['plan_id']) if row['plan_id'] else '—'}}</td><td><code>{{row['detail_json'][:180]}}</code></td></tr>{% else %}<tr><td colspan="5">No recent Warfront events.</td></tr>{% endfor %}
    </table></section></details>"""
    body = """<section class="panel"><h2>Alliance War Control</h2><div class="pad">{% if active %}<p class="bad"><b>War #{{active['id']}} is active.</b></p><form class="fields-grid" method="post" action="{{url_for('end_war_dashboard')}}"><label>Winner (optional)<select name="winner"><option value="">No winner</option>{% for a in alliances %}{% if a['id'] in [active['attacker_alliance_id'],active['defender_alliance_id']] %}<option value="{{a['id']}}">[{{a['tag']}}] {{a['name']}}</option>{% endif %}{% endfor %}</select></label><div class="actions"><button>End War</button></div></form>{% else %}<p class="ok">No active war.</p><form class="fields-grid" method="post" action="{{url_for('start_war_dashboard')}}"><label>Attacker<select name="attacker" required>{% for a in alliances %}<option value="{{a['id']}}">[{{a['tag']}}] {{a['name']}}</option>{% endfor %}</select></label><label>Defender<select name="defender" required>{% for a in alliances %}<option value="{{a['id']}}">[{{a['tag']}}] {{a['name']}}</option>{% endfor %}</select></label><div class="actions"><button>Start War</button></div></form>{% endif %}</div></section><section class="panel"><h2>Alliance Management</h2><table><tr><th>Alliance</th><th>Leader Discord ID</th><th>Save</th></tr>{% for a in alliances %}<tr><td><form id="alliance-{{a['id']}}" method="post" action="{{url_for('save_alliance',alliance_id=a['id'])}}"><div class="inline"><input aria-label="Alliance tag" name="tag" value="{{a['tag']}}" maxlength="5" required><input aria-label="Alliance name" name="name" value="{{a['name']}}" maxlength="30" required></div></form></td><td><input form="alliance-{{a['id']}}" aria-label="Leader Discord ID" name="leader_id" type="number" value="{{a['leader_id']}}" required></td><td><button form="alliance-{{a['id']}}">Save</button></td></tr>{% else %}<tr><td colspan="3">No Alliances. Players can use /alliance_create.</td></tr>{% endfor %}</table></section>"""
    units_panel = """<details class="creator" {% if edit_unit or creating %}open{% endif %}><summary class="btn">＋ Create New War Unit / Tank Model</summary><section class="panel"><h2>{{'Edit' if edit_unit else 'Create'}} War Unit Model</h2><div class="notice">Categories control the menus in <code>/army_recruit</code>. Branch controls whether the unit appears in <code>/army</code>, <code>/navy</code> or <code>/airforce</code>. Multi-emoji names remain visible in text; buttons automatically use a safe single emoji.</div><form class="fields" method="post" action="{{url_for('save_war_unit')}}"><input type="hidden" name="id" value="{{edit_unit['id'] if edit_unit else ''}}"><div class="fields-grid"><label>Internal Code<input name="code" value="{{edit_unit['code'] if edit_unit else ''}}" placeholder="m1a2_abrams" required></label><label>Model Name<input name="name" value="{{edit_unit['name'] if edit_unit else ''}}" placeholder="M1A2 Abrams" required></label><label>Emoji<input name="emoji" value="{{edit_unit['emoji'] if edit_unit else '🪖'}}"></label><label>Recruit Category<select name="category_id">{% for category in categories %}<option value="{{category['id']}}" {% if edit_unit and edit_unit['category_id']==category['id'] %}selected{% endif %}>{{category['emoji']}} {{category['label']}}</option>{% endfor %}</select></label><label>Service Branch<select name="branch">{% for branch in ['land','tank','air','navy','special'] %}<option {% if edit_unit and edit_unit['branch']==branch %}selected{% endif %}>{{branch}}</option>{% endfor %}</select></label><label>Recruit Cost (War Credits)<input type="number" min="0" name="cost" value="{{edit_unit['cost'] if edit_unit else 100}}" required></label><label>Power per Unit<input type="number" min="0" name="power" value="{{edit_unit['power'] if edit_unit else 1}}" required></label><label>Display Order<input type="number" min="0" name="position" value="{{edit_unit['position'] if edit_unit else 10}}"></label><label>Enabled<select name="enabled"><option value="1" {% if not edit_unit or edit_unit['enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if edit_unit and not edit_unit['enabled'] %}selected{% endif %}>No</option></select></label><label>Description<textarea name="description">{{edit_unit['description'] if edit_unit else ''}}</textarea></label></div><div class="actions"><button>Save War Unit</button></div></form></section></details><section class="panel"><h2>War Unit & Tank Model Library</h2><div class="notice">The library below now uses the same saved default sorting as <code>/army_recruit</code>: <b>{{default_recruit_sort.replace('_',' ').title()}}</b>.</div><div class="library">{% for u in units %}<article class="library-card"><h3>{{u['emoji']}} {{u['name']}}</h3><span class="badge">{{u['category_emoji'] or '⚔️'}} {{u['category_label'] or 'Uncategorised'}} · {{u['branch']|upper}}</span><p>{{u['description']}}</p><div class="statline">Code: {{u['code']}}<br>⚔️ Cost: {{u['cost']}} War Credits<br>💥 Power: {{u['power']}} each<br>📊 Cost / Power: {{u['cost_per_power']}}<br>Order: {{u['position']}}<br><span class="{{'ok' if u['enabled'] else 'bad'}}">{{'Enabled' if u['enabled'] else 'Disabled'}}</span></div><div class="actions"><a class="btn" href="{{url_for('war_control',edit_unit=u['id'])}}">Edit Model</a><form method="post" action="{{url_for('toggle_war_unit',unit_id=u['id'])}}"><button class="{{'danger' if u['enabled'] else 'secondary'}}">{{'Disable' if u['enabled'] else 'Enable'}}</button></form></div></article>{% endfor %}</div></section>"""
    units_panel=units_panel.replace('</form></div></article>{% endfor %}', '</form><form method="post" action="{{url_for(\'delete_war_unit\',unit_id=u[\'id\'])}}" onsubmit="return confirm(\'Delete this unit model? Units owned by players will block deletion.\')"><button class="danger">🗑 Delete</button></form></div></article>{% endfor %}')
    body = body.replace('<input name="leader_id" type="number" value="{{a[\'leader_id\']}}" required>', '<select name="leader_id">{% for p in players %}<option value="{{p[\'user_id\']}}" {% if p[\'user_id\']==a[\'leader_id\'] %}selected{% endif %}>{{p[\'display_name\']}} — {{p[\'nation_name\']}}</option>{% endfor %}</select>')
    history_panel = """<section class="panel"><h2>Recent Battle History</h2><table><tr><th>Battle</th><th>Attacker</th><th>Defender</th><th>Power</th><th>Winner</th><th>Effects</th><th>Result</th></tr>{% for b in battles %}<tr><td>#{{b['id']}}{% if b['war_id'] %}<br><span class="badge">War #{{b['war_id']}}</span>{% endif %}</td><td>{{b['attacker_name']}}</td><td>{{b['defender_name']}}</td><td>{{b['attacker_power']}} vs {{b['defender_power']}}</td><td>{{b['winner_name']}}</td><td>{% if b['land_captured'] %}🗺️ +{{b['land_captured']}} land<br>{% endif %}{% if b['capital_damage'] %}🏛️ {{b['capital_damage']}} damage<br>{% endif %}{% if b['credits_captured'] %}💰 {{b['credits_captured']}} credits{% endif %}{% if not b['land_captured'] and not b['capital_damage'] and not b['credits_captured'] %}—{% endif %}</td><td>{{b['outcome']}}</td></tr>{% else %}<tr><td colspan="7">No battles recorded yet.</td></tr>{% endfor %}</table></section>"""
    service_panel = """<section class="panel"><h2>🌐 Armed Forces Overview</h2><div class="grid pad"><div class="card"><small>🪖 Army</small><strong>{{(services.get('land',{}).get('units',0) if services.get('land') else 0)+(services.get('tank',{}).get('units',0) if services.get('tank') else 0)}}</strong><span class="muted">Use /army</span></div><div class="card"><small>✈️ Air Force</small><strong>{{services.get('air',{}).get('units',0) if services.get('air') else 0}}</strong><span class="muted">Use /airforce</span></div><div class="card"><small>⚓ Navy</small><strong>{{services.get('navy',{}).get('units',0) if services.get('navy') else 0}}</strong><span class="muted">Use /navy</span></div></div></section>"""
    season_panel = """<section class="panel" id="war-season"><h2>🏁 War Campaign & Season</h2><div class="notice">Real battles and objectives award Season Points. The top three Nations receive only <b>XC</b> and <b>War Credits</b>; no War Items are awarded.</div>{% if season_active %}<div class="pad"><h3 class="ok">Active: {{season_active['name']}}</h3><p>🥇 {{season_active['reward_xc']}} XC + {{season_active['reward_war_credits']}} WC · 🥈 {{season_active['reward_xc_2']}} XC + {{season_active['reward_war_credits_2']}} WC · 🥉 {{season_active['reward_xc_3']}} XC + {{season_active['reward_war_credits_3']}} WC</p><form method="post" action="{{url_for('end_war_season_dashboard')}}" onsubmit="return confirm('End this season and pay the top three rewards?')"><button class="danger">End Season & Pay Top 3</button></form></div><div class="table-wrap"><table><tr><th>#</th><th>Nation</th><th>Points</th><th>Record</th><th>Objectives</th></tr>{% for s in season_scores %}<tr><td>{{loop.index}}</td><td>{{s['player_name']}}</td><td><b>{{s['points']}}</b></td><td>{{s['wins']}}W / {{s['losses']}}L</td><td>{{s['land_captured']}} land · {{s['capital_damage']}} capital damage</td></tr>{% else %}<tr><td colspan="5">No scored battles yet.</td></tr>{% endfor %}</table></div>{% else %}<form class="fields pad" method="post" action="{{url_for('start_war_season_dashboard')}}"><div class="fields-grid"><label>Season Name<input name="name" maxlength="60" placeholder="Season 1 — Iron Front" required></label><label>🥇 XC<input type="number" min="0" name="reward_xc" value="1000"></label><label>🥇 War Credits<input type="number" min="0" name="reward_war_credits" value="1000"></label><label>🥈 XC<input type="number" min="0" name="reward_xc_2" value="500"></label><label>🥈 War Credits<input type="number" min="0" name="reward_war_credits_2" value="500"></label><label>🥉 XC<input type="number" min="0" name="reward_xc_3" value="250"></label><label>🥉 War Credits<input type="number" min="0" name="reward_war_credits_3" value="250"></label></div><button>Start New War Season</button></form>{% endif %}<details class="creator"><summary class="btn purple">⚙ Campaign Scoring</summary><form class="fields pad" method="post" action="{{url_for('save_war_season_rules')}}"><div class="fields-grid"><label>Winner Points<input type="number" min="0" name="war_season_win_points" value="{{season_rules.get('war_season_win_points','3')}}"></label><label>Loser Participation Points<input type="number" min="0" name="war_season_loss_points" value="{{season_rules.get('war_season_loss_points','1')}}"></label><label>Points per Land Captured<input type="number" min="0" name="war_season_land_points" value="{{season_rules.get('war_season_land_points','2')}}"></label><label>Capital Damage per 1 Point<input type="number" min="1" name="war_season_capital_damage_per_point" value="{{season_rules.get('war_season_capital_damage_per_point','10')}}"></label></div><button>Save Campaign Scoring</button></form></details><details class="creator"><summary class="btn">Season History</summary><div class="table-wrap"><table><tr><th>Season</th><th>Status</th><th>Champion</th><th>🥇 Reward</th></tr>{% for s in season_history %}<tr><td>#{{s['id']}} {{s['name']}}</td><td>{{s['status']}}</td><td>{{s['champion_name']}}</td><td>{{s['reward_xc']}} XC + {{s['reward_war_credits']}} WC</td></tr>{% else %}<tr><td colspan="4">No seasons yet.</td></tr>{% endfor %}</table></div></details></section>"""
    category_panel = """<details class="creator" id="war-category-manager"><summary class="btn purple">⚙ Manage Recruit Categories & Sorting</summary><section class="panel"><h2>Army Recruit Menu</h2><div class="notice"><b>First Page</b> controls which category opens first. <b>Default Sort</b> controls the first unit order; players can temporarily change sorting inside Discord. Manual order uses each unit's Display Order.</div><form class="fields" method="post" action="{{url_for('save_default_war_category')}}"><div class="fields-grid"><label>Recruit Menu First Page<select name="category_id" required>{% for c in all_categories %}{% if c['enabled'] and c['unit_count'] %}<option value="{{c['id']}}" {% if c['id']==default_category_id %}selected{% endif %}>{{c['emoji']}} {{c['label']}}</option>{% endif %}{% endfor %}</select></label><label>Default Unit Sort<select name="sort_mode"><option value="manual" {% if default_recruit_sort=='manual' %}selected{% endif %}>Manual Dashboard Order</option><option value="cost_low" {% if default_recruit_sort=='cost_low' %}selected{% endif %}>Cost: Low to High</option><option value="cost_high" {% if default_recruit_sort=='cost_high' %}selected{% endif %}>Cost: High to Low</option><option value="power_high" {% if default_recruit_sort=='power_high' %}selected{% endif %}>Power: High to Low</option><option value="name" {% if default_recruit_sort=='name' %}selected{% endif %}>Name: A to Z</option></select></label></div><button>Save Recruit Menu Defaults</button></form><hr><form class="fields" method="post" action="{{url_for('save_war_category')}}"><div class="fields-grid"><label>New Category Name<input name="label" required></label><label>Emoji<input name="emoji" value="⚔️"></label><label>Order<input type="number" min="0" name="position" value="10"></label></div><button>＋ Create Category</button></form><div class="library">{% for c in all_categories %}<article class="library-card"><form method="post" action="{{url_for('save_war_category')}}"><input type="hidden" name="id" value="{{c['id']}}"><h3>{{c['emoji']}} {{c['label']}} {% if c['id']==default_category_id %}<span class="badge">FIRST PAGE</span>{% endif %}</h3><div class="fields-grid"><label>Name<input name="label" value="{{c['label']}}" required></label><label>Emoji<input name="emoji" value="{{c['emoji']}}"></label><label>Order<input type="number" min="0" name="position" value="{{c['position']}}"></label><label>Status<select name="enabled"><option value="1" {% if c['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not c['enabled'] %}selected{% endif %}>Disabled</option></select></label></div><p>{{c['unit_count']}} unit model(s)</p><button>Save Category</button></form></article>{% endfor %}</div></section></details>"""
    # Season 1 — First Conquest additions are layered onto the existing panel
    # so older season records and saved Dashboard layout remain compatible.
    season_panel = season_panel.replace(
        '<h3 class="ok">Active: {{season_active[\'name\']}}</h3>',
        '<h3 class="ok">Active: {{season_active[\'name\']}}</h3><p><b>Testing Season</b> · Started <span data-unix="{{season_active[\'starts_at\']}}">{{season_active[\'starts_at\']}}</span>{% if season_active[\'scheduled_ends_at\'] %} · Scheduled end: <span data-unix="{{season_active[\'scheduled_ends_at\']}}">{{season_active[\'scheduled_ends_at\']}}</span>{% endif %}</p>')
    season_panel = season_panel.replace(
        '<label>Season Name<input name="name" maxlength="60" placeholder="Season 1 — Iron Front" required></label>',
        '<label>Season Name<input name="name" maxlength="60" value="Season 1 — First Conquest" required></label><label>Duration (days)<input type="number" min="1" max="365" name="duration_days" value="14" required></label>')
    season_panel = season_panel.replace('name="reward_xc" value="1000"', 'name="reward_xc" value="2000"')
    season_panel = season_panel.replace('name="reward_war_credits" value="1000"', 'name="reward_war_credits" value="3000"')
    season_panel = season_panel.replace('name="reward_xc_2" value="500"', 'name="reward_xc_2" value="1200"')
    season_panel = season_panel.replace('name="reward_war_credits_2" value="500"', 'name="reward_war_credits_2" value="2000"')
    season_panel = season_panel.replace('name="reward_xc_3" value="250"', 'name="reward_xc_3" value="700"')
    season_panel = season_panel.replace('name="reward_war_credits_3" value="250"', 'name="reward_war_credits_3" value="1200"')
    season_panel = season_panel.replace(
        '<label>Capital Damage per 1 Point<input type="number" min="1" name="war_season_capital_damage_per_point" value="{{season_rules.get(\'war_season_capital_damage_per_point\',\'10\')}}"></label>',
        '<label>Capital Damage per 1 Point<input type="number" min="1" name="war_season_capital_damage_per_point" value="{{season_rules.get(\'war_season_capital_damage_per_point\',\'10\')}}"></label><label>Successful Defence Bonus<input type="number" min="0" name="war_season_defense_points" value="{{season_rules.get(\'war_season_defense_points\',\'2\')}}"></label><label>Capital Capture Bonus<input type="number" min="0" name="war_season_capital_capture_points" value="{{season_rules.get(\'war_season_capital_capture_points\',\'10\')}}"></label><label>End Points per Held Land<input type="number" min="0" name="war_season_land_hold_points" value="{{season_rules.get(\'war_season_land_hold_points\',\'1\')}}"></label><label>Participation Minimum Battles<input type="number" min="1" name="war_season_participation_min_battles" value="{{season_rules.get(\'war_season_participation_min_battles\',\'3\')}}"></label><label>Participation Reward (XC)<input type="number" min="0" name="war_season_participation_xc" value="{{season_rules.get(\'war_season_participation_xc\',\'200\')}}"></label><label>New Nation Protection (hours)<input type="number" min="0" name="war_new_nation_protection_hours" value="{{(season_rules.get(\'war_new_nation_protection_seconds\',\'172800\')|int / 3600)|int}}"></label><label>Same Target Cooldown (hours)<input type="number" min="0" name="war_pair_attack_cooldown_hours" value="{{(season_rules.get(\'war_pair_attack_cooldown\',\'43200\')|int / 3600)|int}}"></label>')
    season_panel = season_panel.replace(
        '<label>Same Target Cooldown (hours)<input type="number" min="0" name="war_pair_attack_cooldown_hours" value="{{(season_rules.get(\'war_pair_attack_cooldown\',\'43200\')|int / 3600)|int}}"></label>',
        '<label>Same Target Cooldown (hours)<input type="number" min="0" name="war_pair_attack_cooldown_hours" value="{{(season_rules.get(\'war_pair_attack_cooldown\',\'43200\')|int / 3600)|int}}"></label><label>Nation War Duration (hours)<input type="number" min="1" max="168" name="nation_war_duration_hours" value="{{(season_rules.get(\'nation_war_duration\',\'172800\')|int / 3600)|int}}"></label>')
    season_panel = season_panel.replace("{{s['land_captured']}} land · {{s['capital_damage']}} capital damage", "{{s['land_captured']}} land · {{s['capital_damage']}} capital damage · {{s['defences']}} defences · {{s['capitals_captured']}} capitals")
    # Categories are a second level below the three service buttons.  The
    # Dashboard therefore asks which service owns every custom category.
    category_panel = category_panel.replace(
        '<label>Emoji<input name="emoji" value="⚔️"></label><label>Order',
        '<label>Emoji<input name="emoji" value="⚔️"></label><label>Service<select name="parent_branch"><option value="land">🪖 Land Army</option><option value="air">✈️ Air Force</option><option value="navy">⚓ Navy</option></select></label><label>Order')
    category_panel = category_panel.replace(
        '<label>Emoji<input name="emoji" value="{{c[\'emoji\']}}"></label><label>Order',
        '<label>Emoji<input name="emoji" value="{{c[\'emoji\']}}"></label><label>Service<select name="parent_branch">{% for branch,label in [(\'land\',\'🪖 Land Army\'),(\'air\',\'✈️ Air Force\'),(\'navy\',\'⚓ Navy\')] %}<option value="{{branch}}" {% if c[\'parent_branch\']==branch %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label><label>Order')
    category_panel=category_panel.replace('<button>Save Category</button></form></article>', '<div class="actions"><button name="action" value="save">Save Category</button>{% if not c[\'system_category\'] and not c[\'unit_count\'] %}<button class="danger" name="action" value="delete" onclick="return confirm(\'Delete this empty recruit category?\')">Delete Category</button>{% else %}<button class="secondary" type="button" disabled>Protected / In Use</button>{% endif %}</div></form></article>')
    logistics_panel="""<details class="creator" open><summary class="btn purple">⚙ War Rules & Balance</summary><section class="panel"><h2>⚙ Complete War Rules</h2><div class="notice">All values apply immediately after saving. Percentages are limited to 0–100; battle variance is limited to 0–50.</div><form class="fields" method="post" action="{{url_for('save_war_logistics')}}"><div class="fields-grid"><label>Attack Cooldown (seconds)<input type="number" min="0" name="attack_cooldown" value="{{war_settings.get('attack_cooldown','60')}}"></label><label>Battle Power Variance %<input type="number" min="0" max="50" name="war_battle_variance_percent" value="{{war_settings.get('war_battle_variance_percent','10')}}"></label><label>Winner Unit Loss %<input type="number" min="0" max="100" name="war_winner_loss_percent" value="{{war_settings.get('war_winner_loss_percent','10')}}"></label><label>Loser Unit Loss %<input type="number" min="0" max="100" name="war_loser_loss_percent" value="{{war_settings.get('war_loser_loss_percent','30')}}"></label><label>Capital Damage per Victory<input type="number" min="0" max="100" name="capital_damage" value="{{war_settings.get('capital_damage','25')}}"></label><label>Capital Conquest Reward %<input type="number" min="0" max="100" name="capital_reward_percent" value="{{war_settings.get('capital_reward_percent','25')}}"></label><label>Capital Repair Cost / HP<input type="number" min="0" name="capital_repair_cost_per_hp" value="{{war_settings.get('capital_repair_cost_per_hp','10')}}"></label><label>Maximum Supply<input type="number" min="1" name="war_max_supply" value="{{war_settings.get('war_max_supply','1000')}}"></label><label>War Credits per Supply<input type="number" min="0" name="war_supply_cost" value="{{war_settings.get('war_supply_cost','2')}}"></label><label>Supply Used per Battle<input type="number" min="0" name="war_supply_per_attack" value="{{war_settings.get('war_supply_per_attack','25')}}"></label><label>Readiness Lost per Battle %<input type="number" min="0" max="100" name="war_readiness_loss_per_battle" value="{{war_settings.get('war_readiness_loss_per_battle','5')}}"></label><label>Readiness Gained by /prepare %<input type="number" min="1" max="100" name="war_readiness_per_prepare" value="{{war_settings.get('war_readiness_per_prepare','10')}}"></label><label>Supply Cost per /prepare<input type="number" min="0" name="war_prepare_supply_cost" value="{{war_settings.get('war_prepare_supply_cost','20')}}"></label><label>Winner Morale Gain %<input type="number" min="0" max="100" name="war_winner_morale_gain" value="{{war_settings.get('war_winner_morale_gain','5')}}"></label><label>Loser Morale Loss %<input type="number" min="0" max="100" name="war_loser_morale_loss" value="{{war_settings.get('war_loser_morale_loss','10')}}"></label><label>Rally Supply Cost<input type="number" min="0" name="war_rally_supply_cost" value="{{war_settings.get('war_rally_supply_cost','25')}}"></label><label>Rally Morale Gain %<input type="number" min="1" max="100" name="war_rally_morale_gain" value="{{war_settings.get('war_rally_morale_gain','10')}}"></label><label>Fortified Stance Bonus %<input type="number" min="0" max="100" name="fortified_defense_bonus" value="{{war_settings.get('fortified_defense_bonus','20')}}"></label><label>Aggressive Stance Bonus %<input type="number" min="0" max="100" name="aggressive_attack_bonus" value="{{war_settings.get('aggressive_attack_bonus','10')}}"></label><label>Fortification Base Cost<input type="number" min="0" name="fortify_base_cost" value="{{war_settings.get('fortify_base_cost','250')}}"></label><label>Fortification Bonus / Level %<input type="number" min="0" max="100" name="fortify_power_percent" value="{{war_settings.get('fortify_power_percent','5')}}"></label><label>Maximum Fortification Level<input type="number" min="0" max="100" name="fortify_max_level" value="{{war_settings.get('fortify_max_level','10')}}"></label><label>Scout Cost<input type="number" min="0" name="scout_cost" value="{{war_settings.get('scout_cost','25')}}"></label><label>Scout Cooldown (seconds)<input type="number" min="0" name="scout_cooldown" value="{{war_settings.get('scout_cooldown','300')}}"></label><label>Demobilize Refund %<input type="number" min="0" max="100" name="demobilize_refund_percent" value="{{war_settings.get('demobilize_refund_percent','50')}}"></label></div><button>Save All War Rules</button></form></section></details><section class="panel"><h2>🎯 Nation War State Editor</h2><div class="notice">Edit a Nation's live Supply, Readiness, Morale, Fortification, stance and Capital HP. Values shown here are current database values.</div><div class="library">{% for r in readiness_rows %}<article class="library-card"><form method="post" action="{{url_for('save_nation_war_state')}}"><input type="hidden" name="user_id" value="{{r['user_id']}}"><h3>🏳️ {{r['display_name']}}</h3><p>{{r['nation_name']}} · {{r['money']}} War Credits</p><div class="fields-grid"><label>Supply<input type="number" min="0" name="supply" value="{{r['supply']}}"></label><label>Readiness %<input type="number" min="0" max="100" name="readiness" value="{{r['readiness']}}"></label><label>Morale %<input type="number" min="0" max="100" name="morale" value="{{r['morale']}}"></label><label>Fortification<input type="number" min="0" name="fortification_level" value="{{r['fortification_level']}}"></label><label>Capital HP<input type="number" min="0" max="100" name="capital_health" value="{{r['capital_health']}}"></label><label>Stance<select name="defense_stance">{% for stance in ['balanced','fortified','aggressive'] %}<option {% if r['defense_stance']==stance %}selected{% endif %}>{{stance}}</option>{% endfor %}</select></label></div><button>Save Nation State</button></form></article>{% else %}<p>No Nation war states yet.</p>{% endfor %}</div></section><section class="panel"><h2>📐 Saved Division Templates</h2><table><tr><th>Owner</th><th>Name</th><th>Description</th><th>Template Power</th><th>Manage</th></tr>{% for d in divisions %}<tr><td>{{d['owner_name']}}</td><td>{{d['name']}}</td><td>{{d['description']}}</td><td>{{d['template_power']}}</td><td><form method="post" action="{{url_for('delete_division_dashboard',template_id=d['id'])}}" onsubmit="return confirm('Delete this Division Template?')"><button class="danger">Delete</button></form></td></tr>{% else %}<tr><td colspan="5">No templates saved.</td></tr>{% endfor %}</table></section>"""
    logistics_panel = logistics_panel.replace(
        '<label>Winner Unit Loss %',
        '<label>Air Superiority Land Bonus %<input type="number" min="0" max="100" name="war_air_superiority_bonus" value="{{war_settings.get(\'war_air_superiority_bonus\',\'15\')}}"></label>'
        '<label>Naval Control Enemy Land Penalty %<input type="number" min="0" max="100" name="war_navy_blockade_penalty" value="{{war_settings.get(\'war_navy_blockade_penalty\',\'10\')}}"></label>'
        '<label>Naval Blockade Supply Damage<input type="number" min="0" name="war_navy_supply_damage" value="{{war_settings.get(\'war_navy_supply_damage\',\'25\')}}"></label>'
        '<label>Winner Unit Loss %', 1)
    logistics_panel = logistics_panel.replace(
        '<button>Save All War Rules</button>',
        '<label>Free Land Claim Cooldown (seconds)<input type="number" min="3600" name="free_land_claim_cooldown" value="{{war_settings.get(\'free_land_claim_cooldown\',\'43200\')}}"></label><button>Save All War Rules</button>',
        1)
    logistics_panel = logistics_panel.replace(
        '<button>Save All War Rules</button>',
        '''<h3>🏙️ City System — 12 Hour Production</h3><div class="fields-grid"><label>Collection Cooldown (seconds)<input type="number" min="3600" name="city_collect_cooldown" value="{{war_settings.get('city_collect_cooldown','43200')}}"></label><label>Capital War Credits / cycle<input type="number" min="0" name="city_capital_war_credits" value="{{war_settings.get('city_capital_war_credits','100')}}"></label><label>Capital Supply / cycle<input type="number" min="0" name="city_capital_supply" value="{{war_settings.get('city_capital_supply','20')}}"></label><label>Civilian City Build Cost<input type="number" min="0" name="city_civilian_build_cost" value="{{war_settings.get('city_civilian_build_cost','250')}}"></label><label>Industrial City Build Cost<input type="number" min="0" name="city_industrial_build_cost" value="{{war_settings.get('city_industrial_build_cost','400')}}"></label><label>Civilian Credits / Level<input type="number" min="0" name="city_civilian_war_credits" value="{{war_settings.get('city_civilian_war_credits','150')}}"></label><label>Industrial Credits / Level<input type="number" min="0" name="city_industrial_war_credits" value="{{war_settings.get('city_industrial_war_credits','50')}}"></label><label>Industrial Supply / Level<input type="number" min="0" name="city_industrial_supply" value="{{war_settings.get('city_industrial_supply','100')}}"></label><label>City Upgrade Base Cost<input type="number" min="0" name="city_upgrade_base_cost" value="{{war_settings.get('city_upgrade_base_cost','250')}}"></label></div><button>Save All War Rules</button>''', 1)
    logistics_panel = logistics_panel.replace(
        '<label>City Upgrade Base Cost<input type="number" min="0" name="city_upgrade_base_cost" value="{{war_settings.get(\'city_upgrade_base_cost\',\'250\')}}"></label>',
        '''<label>City Upgrade Base Cost<input type="number" min="0" name="city_upgrade_base_cost" value="{{war_settings.get('city_upgrade_base_cost','250')}}"></label><label>City Rename Cost<input type="number" min="0" name="city_rename_cost" value="{{war_settings.get('city_rename_cost','100')}}"></label><label>Maximum Land Level<input type="number" min="1" max="5" name="land_max_level" value="{{war_settings.get('land_max_level','5')}}"></label><label>Land Lv2 Credits<input type="number" min="0" name="land_upgrade_2_credits" value="{{war_settings.get('land_upgrade_2_credits','500')}}"></label><label>Land Lv2 Supply<input type="number" min="0" name="land_upgrade_2_supply" value="{{war_settings.get('land_upgrade_2_supply','100')}}"></label><label>Land Lv3 Credits<input type="number" min="0" name="land_upgrade_3_credits" value="{{war_settings.get('land_upgrade_3_credits','1200')}}"></label><label>Land Lv3 Supply<input type="number" min="0" name="land_upgrade_3_supply" value="{{war_settings.get('land_upgrade_3_supply','250')}}"></label><label>Land Lv4 Credits<input type="number" min="0" name="land_upgrade_4_credits" value="{{war_settings.get('land_upgrade_4_credits','2500')}}"></label><label>Land Lv4 Supply<input type="number" min="0" name="land_upgrade_4_supply" value="{{war_settings.get('land_upgrade_4_supply','500')}}"></label><label>Land Lv5 Credits<input type="number" min="0" name="land_upgrade_5_credits" value="{{war_settings.get('land_upgrade_5_credits','5000')}}"></label><label>Land Lv5 Supply<input type="number" min="0" name="land_upgrade_5_supply" value="{{war_settings.get('land_upgrade_5_supply','1000')}}"></label>''', 1)
    # Nation settings can be a very long wall of forms.  Keep every nation
    # collapsed until an administrator chooses it, especially for phones.
    logistics_panel = logistics_panel.replace(
        '<div class="library">{% for r in readiness_rows %}<article class="library-card"><form method="post" action="{{url_for(\'save_nation_war_state\')}}">',
        '<div class="nation-state-actions"><span class="muted">Open a nation to edit only that card, or use the buttons below.</span><button type="button" class="btn secondary" onclick="document.querySelectorAll(\'.nation-state-card\').forEach(x=>x.open=true)">▣ Show all nations</button><button type="button" class="btn secondary" onclick="document.querySelectorAll(\'.nation-state-card\').forEach(x=>x.open=false)">▸ Minimize all nations</button></div><div class="library nation-state-library">{% for r in readiness_rows %}<details class="library-card nation-state-card"><summary>🏳️ <b>{{r[\'display_name\']}}</b><span>{{r[\'nation_name\']}} · {{r[\'money\']}} War Credits</span></summary><form method="post" action="{{url_for(\'save_nation_war_state\')}}">')
    logistics_panel = logistics_panel.replace(
        '<input type="hidden" name="user_id" value="{{r[\'user_id\']}}"><h3>🏳️ {{r[\'display_name\']}}</h3><p>{{r[\'nation_name\']}} · {{r[\'money\']}} War Credits</p>',
        '<input type="hidden" name="user_id" value="{{r[\'user_id\']}}"><p class="nation-state-note">Edit this nation\'s live combat state.</p>')
    logistics_panel = logistics_panel.replace(
        '</button></form></article>{% else %}<p>No Nation war states yet.</p>{% endfor %}</div></section><section class="panel"><h2>📐 Saved Division Templates</h2>',
        '</button></form></details>{% else %}<p>No Nation war states yet.</p>{% endfor %}</div></section><section class="panel"><h2>📐 Saved Division Templates</h2>')
    territory_panel = """<details class="creator" id="territory-editor"><summary class="btn purple">🗺️ Nation Territory, Land & Cities</summary><section class="panel"><h2>Real-World Territory Editor</h2><div class="notice"><b>Capital Land</b> and <b>State / Province Land</b> are now separate. Select a current Capital to move it to a country's official capital city; select normal Land to move it to a state/province and optionally rename its city.</div><form class="fields pad" method="post" action="{{url_for('move_nation_territory')}}"><div class="fields-grid"><label>Current Nation Land<select name="territory_code" required>{% for t in territory_rows %}<option value="{{t['territory_code']}}">{{'★' if t['is_capital'] else '🗺️'}} {{t['owner_name']}} — {{t['territory_name']}}</option>{% endfor %}</select></label><label>New Real Province / State<select name="new_territory_code" required>{% for r in available_regions %}<option value="{{r[0]}}" data-kind="state">{{r[1]}}</option>{% endfor %}{% for r in available_capitals %}<option value="{{r[0]}}" data-kind="capital">★ {{r[1]}}</option>{% endfor %}</select></label></div><button>Move Land Location</button></form><form class="fields pad" method="post" action="{{url_for('set_land_development')}}"><div class="fields-grid"><label>Nation Land<select name="territory_code" required>{% for t in territory_rows %}<option value="{{t['territory_code']}}">{{'★' if t['is_capital'] else '🗺️'}} {{t['owner_name']}} — {{t['territory_name']}} · Lv {{t['level']}}</option>{% endfor %}</select></label><label>Land Level / City Capacity<input type="number" min="1" max="5" name="level" value="1" required></label></div><button>Save Land Development</button></form><details class="creator"><summary class="btn">🏙️ Manage Existing Cities</summary><div class="library">{% for c in city_rows %}<article class="library-card"><form class="fields" method="post" action="{{url_for('save_city_dashboard',city_id=c['id'])}}"><h3>🏙️ {{c['name']}}</h3><p>{{c['owner_name']}} · {{c['territory_name']}}</p><label>City Name<input name="name" value="{{c['name']}}" minlength="2" maxlength="32" required></label><label>Real Land Location<select name="territory_code" required>{% for t in territory_rows %}{% if t['owner_user_id']==c['user_id'] %}<option value="{{t['territory_code']}}" {% if t['territory_code']==c['territory_code'] %}selected{% endif %}>{{t['territory_name']}} · Lv {{t['level']}}</option>{% endif %}{% endfor %}</select></label><button>Save City</button></form></article>{% else %}<p>No Cities yet.</p>{% endfor %}</div></details></section></details>"""
    territory_panel = territory_panel.replace(
        '<form class="fields pad" method="post" action="{{url_for(\'move_nation_territory\')}}">',
        '<div class="war-step"><h3>1 · Move a Nation Land</h3><p class="step-help">Choose the current Land, then click a real province/state on the map. Claimed territory is grey and cannot be selected.</p><form class="fields" method="post" action="{{url_for(\'move_nation_territory\')}}">', 1)
    territory_panel = territory_panel.replace(
        '<div class="fields-grid"><label>Current Nation Land<select name="territory_code" required>',
        '''<div class="fields-grid"><label>Nation<select id="territory-nation" required><option value="">Choose a Nation first</option>{% for n in territory_nations %}<option value="{{n[0]}}">{{n[1]}} · {{n[2]}}</option>{% endfor %}</select></label><label>Current Nation Land<select id="territory-current-land" name="territory_code" required disabled>''', 1)
    territory_panel = territory_panel.replace(
        '<option value="{{t[\'territory_code\']}}">{{\'★\' if t[\'is_capital\'] else \'🗺️\'}} {{t[\'owner_name\']}} — {{t[\'territory_name\']}}</option>',
        '<option value="{{t[\'territory_code\']}}" data-owner="{{t[\'owner_user_id\']}}" data-capital="{{1 if t[\'is_capital\'] else 0}}" data-capital-name="{{t[\'capital_name\'] or \'\'}}">{{\'★\' if t[\'is_capital\'] else \'🗺️\'}} {{t[\'territory_name\']}}</option>', 1)
    territory_panel = territory_panel.replace(
        '<label>New Real Province / State<select name="new_territory_code" required>',
        '''<div class="territory-map-shell"><canvas id="territory-map" class="territory-map" width="900" height="450"></canvas><aside class="territory-map-tools"><label>Search Country / Area<input id="territory-country" type="search" placeholder="Type a country name…" autocomplete="off"></label><div id="territory-country-results" class="territory-search-results territory-country-results"></div><label>Search Province / State<input id="territory-search" type="search" placeholder="Type any part of a province/state name…" autocomplete="off"></label><div id="territory-search-results" class="territory-search-results"></div><button type="button" id="territory-clear" class="btn secondary">Clear Search</button><div id="territory-map-status" class="territory-map-status"><span class="territory-loading">Loading interactive world map…</span></div></aside></div><label>Selected Real Province / State<select id="territory-destination" name="new_territory_code" required>''', 1)
    territory_panel = territory_panel.replace(
        '</select></label></div><button>Move Land Location</button>',
        '</select></label><label id="territory-capital-city-label" hidden>City Name (optional)<input id="territory-capital-city" name="city_name" minlength="2" maxlength="32" placeholder="Rename this normal city name"></label></div><button>Move Land Location</button>', 1)
    territory_panel = territory_panel.replace(
        '</button></form><form class="fields pad" method="post" action="{{url_for(\'set_land_development\')}}">',
        '</button></form></div><div class="war-step"><h3>2 · Land Development</h3><p class="step-help">Each Land level unlocks one City slot. Players normally upgrade this with War Credits and Supply.</p><form class="fields" method="post" action="{{url_for(\'set_land_development\')}}">', 1)
    territory_panel = territory_panel.replace(
        '</button></form><details class="creator"><summary class="btn">🏙️ Manage Existing Cities</summary>',
        '</button></form></div><details class="creator"><summary class="btn">3 · Manage Existing Cities</summary>', 1)
    territory_panel += """<script>
(() => {
 const canvas=document.querySelector('#territory-map'), destination=document.querySelector('#territory-destination');
 const country=document.querySelector('#territory-country'), countryResults=document.querySelector('#territory-country-results'), search=document.querySelector('#territory-search'), status=document.querySelector('#territory-map-status'), results=document.querySelector('#territory-search-results'), clear=document.querySelector('#territory-clear');
 const nation=document.querySelector('#territory-nation'), currentLand=document.querySelector('#territory-current-land'), capitalLabel=document.querySelector('#territory-capital-city-label'), capitalInput=document.querySelector('#territory-capital-city');
 if(!canvas||!destination)return;
 const original=[...destination.options].map(option=>({value:option.value,label:option.textContent,kind:option.dataset.kind||'state'}));
 const countries={{available_region_countries|tojson}};
 let regions=[], paths=[], selected='', view={w:-180,e:180,s:-90,n:90}; const ctx=canvas.getContext('2d');
 const project=point=>[(point[0]-view.w)/(view.e-view.w)*canvas.width,(view.n-point[1])/(view.n-view.s)*canvas.height];
 const fitCountry=name=>{const rows=regions.filter(r=>!name||r.country===name);if(!name||!rows.length){view={w:-180,e:180,s:-90,n:90};return}const pts=rows.flatMap(r=>r.points);const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);let w=Math.min(...xs),e=Math.max(...xs),s=Math.min(...ys),n=Math.max(...ys);const px=Math.max(2,(e-w)*.12),py=Math.max(2,(n-s)*.12);view={w:w-px,e:e+px,s:s-py,n:n+py}};
 const fitRegion=code=>{const region=regions.find(r=>r.code===code);if(!region||!region.points.length)return false;const xs=region.points.map(p=>p[0]),ys=region.points.map(p=>p[1]);let w=Math.min(...xs),e=Math.max(...xs),s=Math.min(...ys),n=Math.max(...ys);const px=Math.max(3,(e-w)*1.2),py=Math.max(2.5,(n-s)*1.2);view={w:w-px,e:e+px,s:s-py,n:n+py};return true};
 const currentCode=()=>currentLand&&currentLand.value||'';
 const draw=()=>{ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#0e2b3a';ctx.fillRect(0,0,canvas.width,canvas.height);paths=[];regions.forEach(r=>{const current=r.code===currentCode();if(r.kind==='capital'){const [x,y]=project(r.points[0]);ctx.beginPath();ctx.arc(x,y,r.code===selected?8:current?7:5,0,Math.PI*2);ctx.fillStyle=r.code===selected?'#f0a11a':current?'#4f8cff':r.claimed?'#46515a':'#54d6c7';ctx.fill();ctx.strokeStyle=current?'#6ee7ff':'#fff';ctx.lineWidth=current?3:1.5;ctx.stroke();return}const path=new Path2D();r.points.forEach((p,i)=>{const [x,y]=project(p);i?path.lineTo(x,y):path.moveTo(x,y)});path.closePath();ctx.fillStyle=r.code===selected?'#f0a11a':current?'#4f8cff':r.claimed?'#46515a':'#405a48';ctx.strokeStyle=r.code===selected?'#fff':current?'#6ee7ff':'#82929a';ctx.lineWidth=r.code===selected||current?2.5:0.65;ctx.fill(path);ctx.stroke(path);paths.push([path,r])})};
 const mode=()=>currentLand&&currentLand.selectedOptions[0]&&currentLand.selectedOptions[0].dataset.capital==='1'?'capital':'state';
 const matching=()=>{const q=search.value.trim().toLowerCase(),c=country.value.trim().toLowerCase();return original.filter(o=>o.kind===mode()&&(!c||o.label.toLowerCase().includes(', '+c))&&(!q||o.label.toLowerCase().includes(q)))};
 const renderOptions=()=>{const q=search.value.trim().toLowerCase(),c=country.value;destination.innerHTML='';destination.append(new Option(c||q?(mode()==='capital'?'Choose a capital city…':'Choose a province/state…'):'Choose a country or search first…',''));matching().slice(0,250).forEach(o=>{const option=new Option(o.label,o.value);option.dataset.kind=o.kind;destination.append(option)});if(selected&&[...destination.options].some(o=>o.value===selected))destination.value=selected};
 const selectCountry=name=>{country.value=name;selected='';fitCountry(name);renderOptions();renderCountryResults(false);renderSearchResults();draw();status.innerHTML='<span>Filtered to country/area</span><strong>'+name+'</strong>';search.focus()};
 const renderCountryResults=(showAll=true)=>{if(!countryResults)return;const q=country.value.trim().toLowerCase();countryResults.innerHTML='';if(!q&&!showAll)return;const rows=countries.filter(name=>!q||name.toLowerCase().includes(q));const count=document.createElement('span');count.className='muted';count.textContent=rows.length+' countr'+(rows.length===1?'y':'ies');countryResults.append(count);rows.forEach(name=>{const button=document.createElement('button');button.type='button';button.className='territory-result';button.textContent=name;button.addEventListener('click',()=>selectCountry(name));countryResults.append(button)});if(!rows.length)countryResults.innerHTML='<span class="muted">No matching country or area.</span>'};
 const renderSearchResults=()=>{if(!results)return;const q=search.value.trim(),c=country.value.trim();results.innerHTML='';if(!q&&!c)return;const rows=matching();const count=document.createElement('span');count.className='muted';count.textContent=rows.length+' available result'+(rows.length===1?'':'s')+(rows.length>100?' · showing first 100; refine your search':'');results.append(count);rows.slice(0,100).forEach(o=>{const button=document.createElement('button');button.type='button';button.className='territory-result';button.textContent=o.label;button.addEventListener('click',()=>{const r=regions.find(x=>x.code===o.value);if(r)choose(r);else{selected=o.value;destination.value=o.value;status.innerHTML='<span>Selected destination</span><strong>'+o.label+'</strong>'}results.innerHTML=''});results.append(button)});if(!rows.length)results.innerHTML='<span class="muted">No matching available province/state. Try removing the country filter.</span>'};
 const choose=r=>{if(r.claimed){status.innerHTML='<span class="bad">Already claimed</span><strong>'+r.name+'</strong>';return}selected=r.code;country.value=r.country;fitCountry(r.country);renderOptions();destination.value=r.code;status.innerHTML='<span>Selected destination</span><strong>'+r.name+'</strong>';draw()};
 country.addEventListener('focus',()=>renderCountryResults(true));
 country.addEventListener('input',()=>{selected='';fitCountry(country.value);renderOptions();renderCountryResults(true);renderSearchResults();draw();status.innerHTML=country.value?'<span>Choose a country from the scrollable list</span><strong>'+country.value+'</strong>':'<span>Search globally or choose a country.</span>'});
    search.addEventListener('input',()=>{renderOptions();renderSearchResults()});destination.addEventListener('change',()=>{selected=destination.value;const r=regions.find(x=>x.code===selected);if(r){status.innerHTML='<span>Selected destination</span><strong>'+r.name+'</strong>';draw()}});
 if(clear)clear.addEventListener('click',()=>{country.value='';search.value='';selected='';fitCountry('');renderOptions();renderCountryResults(false);renderSearchResults();draw();status.innerHTML='<span>Search cleared. Choose a country, type a region, or click the map.</span>'});
 if(nation&&currentLand){const landOptions=[...currentLand.options].map(o=>({value:o.value,label:o.textContent,owner:o.dataset.owner,isCapital:o.dataset.capital==='1',capitalName:o.dataset.capitalName||''}));const updateCapital=()=>{const option=currentLand.selectedOptions[0],isCapital=option&&option.dataset.capital==='1';if(capitalLabel)capitalLabel.hidden=!!isCapital;if(capitalInput){capitalInput.required=false;capitalInput.value=''}selected='';if(option&&option.value)fitRegion(option.value);renderOptions();renderSearchResults();draw();status.innerHTML=isCapital?'<span>Capital Land mode</span><strong>Choose a country, then its official capital city.</strong>':'<span>State / Province Land mode</span><strong>Choose a country, then a normal province/state.</strong>'};const filterLands=()=>{currentLand.innerHTML='';currentLand.append(new Option(nation.value?"Choose this Nation's Land…":"Choose a Nation first…",''));landOptions.filter(o=>o.owner===nation.value).forEach(o=>{const option=new Option(o.label,o.value);option.dataset.capital=o.isCapital?'1':'0';option.dataset.capitalName=o.capitalName;currentLand.append(option)});currentLand.disabled=!nation.value;currentLand.required=!!nation.value;updateCapital()};nation.addEventListener('change',filterLands);currentLand.addEventListener('change',updateCapital);filterLands()}
 canvas.addEventListener('click',event=>{const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)*canvas.width/rect.width,y=(event.clientY-rect.top)*canvas.height/rect.height;for(let i=paths.length-1;i>=0;i--){if(ctx.isPointInPath(paths[i][0],x,y)){choose(paths[i][1]);break}}});
 const sidebar=document.querySelector('.territory-map-tools'), destinationLabel=destination.closest('label'), moveForm=destination.closest('form'), moveButton=moveForm.querySelector('button'), nationLabel=nation&&nation.closest('label'), landLabel=currentLand&&currentLand.closest('label');if(sidebar){if(nationLabel)sidebar.prepend(nationLabel);if(landLabel)sidebar.insertBefore(landLabel,sidebar.children[1]||null);if(destinationLabel)sidebar.append(destinationLabel);if(capitalLabel)sidebar.append(capitalLabel);if(moveButton)sidebar.append(moveButton)};
 renderOptions();fetch('{{url_for("territory_map_data")}}').then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}).then(data=>{regions=data.regions;const active=currentLand&&currentLand.value;if(active)fitRegion(active);draw();status.innerHTML='<span>Map ready · '+regions.length+' regions loaded.</span><strong>Select a current Land to zoom nearby, or search another country.</strong>'}).catch(error=>{console.error('Territory map load failed',error);status.innerHTML='<span class="bad">Map could not load.</span><strong>Restart the Dashboard, then try again. Search and selection remain available.</strong>'});
})();
</script>"""
    season_panel = '<details class="creator"><summary class="btn purple">🏁 War Campaign & Season</summary>' + season_panel + '</details>'
    service_panel = '<details class="creator"><summary class="btn purple">🌐 Armed Forces Overview</summary>' + service_panel + '</details>'
    logistics_panel = logistics_panel.replace('<details class="creator" open>', '<details class="creator">', 1)
    logistics_panel = '<details class="creator"><summary class="btn purple">🎯 War Rules, State & Divisions</summary>' + logistics_panel + '</details>'
    body = '<details class="creator"><summary class="btn purple">🤝 Alliance War Control</summary>' + body + '</details>'
    units_panel = '<details class="creator"><summary class="btn purple">🧰 War Unit Models & Library</summary>' + units_panel + '</details>'
    history_panel = '<details class="creator"><summary class="btn purple">📜 Recent Battle History</summary>' + history_panel + '</details>'
    war_page = '<div class="war-control-page">' + tier7_panel + season_panel + territory_panel + service_panel + logistics_panel + body + category_panel + units_panel + history_panel + '''</div><script>(()=>{
 const sections=[...document.querySelectorAll(".war-control-page > details.creator")],storageKey="xbot-war-open-sections-v1";
 sections.forEach((section,index)=>{if(!section.id)section.id="war-section-"+index});
 let saved=[];try{saved=JSON.parse(localStorage.getItem(storageKey)||"[]")}catch(error){saved=[]}
 if(!Array.isArray(saved))saved=[];
 sections.forEach(section=>section.open=saved.includes(section.id));
 const saveState=()=>{try{localStorage.setItem(storageKey,JSON.stringify(sections.filter(section=>section.open).map(section=>section.id)))}catch(error){}};
 sections.forEach(section=>section.addEventListener("toggle",()=>{saveState();if(!section.open&&location.hash==="#"+section.id)history.replaceState(null,"",location.pathname+location.search)}));
 if(location.hash){const target=document.getElementById(location.hash.slice(1));if(target&&target.matches("details")){let node=target;while(node){if(node.matches("details"))node.open=true;node=node.parentElement}saveState();setTimeout(()=>{target.scrollIntoView({block:"start"});history.replaceState(null,"",location.pathname+location.search)},0)}}
})();</script>'''
    return admin_page("War Control", war_page, alliances=alliances, active=active, units=unit_types, edit_unit=edit_unit, creating=request.args.get('create_unit'), players=players_list, battles=battles, services=service_totals, categories=enabled_categories, all_categories=category_rows, default_category_id=default_category_id, default_recruit_sort=default_recruit_sort,divisions=divisions,readiness_rows=readiness_rows,war_settings=war_settings,season_active=season_active,season_scores=season_scores,season_history=season_history,season_rules=season_rules,territory_rows=territory_rows,territory_nations=territory_nations,available_regions=available_regions,available_capitals=available_capitals,available_region_countries=available_region_countries,city_rows=city_rows,tier7_settings=tier7_settings,tier7_defaults=tier7.DEFAULTS,tier7_setting_fields=tier7_setting_fields,tier7_health=tier7_health,tier7_profiles=tier7_profiles,tier7_territories=tier7_territories,tier7_plans=tier7_plans,tier7_reports=tier7_reports,tier7_events=tier7_events,tier7_terrains=tier7.TERRAINS)


@app.post("/war/tier7/settings")
@login_required
def tier7_save_settings():
    db = get_db()
    changed = 0
    try:
        for key, default in tier7.DEFAULTS.items():
            if key not in request.form:
                continue
            minimum, maximum = tier7.SETTING_LIMITS[key]
            value = max(minimum, min(maximum, int(request.form.get(key, default))))
            db.execute(
                """INSERT INTO economy_settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, str(value)),
            )
            changed += 1
        db.commit()
        flash(f"Saved {changed} Warfront setting(s).")
    except (TypeError, ValueError) as error:
        db.rollback()
        flash(f"Warfront settings were not saved: {error}")
    db.close()
    return redirect(url_for("war_control") + "#tier7-control")


@app.post("/war/tier7/territory")
@login_required
def tier7_save_territory():
    db = get_db()
    code = request.form.get("territory_code", "")
    terrain = request.form.get("terrain", "plains").strip().lower()
    try:
        if terrain not in tier7.TERRAINS:
            raise ValueError("Choose a valid terrain type.")
        row = db.execute(
            """SELECT t.territory_name,t.owner_user_id,d.fortification_level
               FROM map_territories t JOIN tier7_territory_defence d ON d.territory_code=t.territory_code
               WHERE t.territory_code=?""",
            (code,),
        ).fetchone()
        if row is None:
            raise ValueError("That mapped Land no longer exists.")
        maximum = tier7.setting(db, "tier7_fortification_max_level")
        fortification = max(0, min(maximum, int(request.form.get("fortification_level", 0))))
        db.execute(
            """UPDATE tier7_territory_defence SET terrain=?,fortification_level=?,owner_user_id=?,updated_at=?
               WHERE territory_code=?""",
            (terrain, fortification, row["owner_user_id"], int(time.time()), code),
        )
        db.commit()
        flash(f"Saved {row['territory_name']}: {terrain.title()}, Fortification Lv {fortification}.")
    except (TypeError, ValueError) as error:
        db.rollback()
        flash(f"Territory defence was not saved: {error}")
    db.close()
    return redirect(url_for("war_control") + "#tier7-control")


@app.post("/war/tier7/profile")
@login_required
def tier7_save_profile():
    db = get_db()
    try:
        user_id = int(request.form.get("user_id", 0))
        if not db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
            raise ValueError("That Nation no longer exists.")
        tier7.save_defence_profile(
            db, user_id,
            garrison_percent=int(request.form.get("garrison_percent", 100)),
            capital_priority=int(request.form.get("capital_priority", 1)),
            auto_reinforce=int(request.form.get("auto_reinforce", 0)),
        )
        flash("Warfront defence profile saved.")
    except (TypeError, ValueError) as error:
        db.rollback()
        flash(f"Defence profile was not saved: {error}")
    db.close()
    return redirect(url_for("war_control") + "#tier7-control")


@app.post("/war/tier7/plan/<int:plan_id>/cancel")
@login_required
def tier7_cancel_plan(plan_id):
    db = get_db()
    changed = db.execute(
        "UPDATE tier7_battle_plans SET status='cancelled',error_text='Cancelled from Dashboard' WHERE id=? AND status='draft'",
        (plan_id,),
    ).rowcount
    db.commit()
    db.close()
    flash(f"Battle Plan #{plan_id} cancelled." if changed else f"Battle Plan #{plan_id} was already closed.")
    return redirect(url_for("war_control") + "#tier7-control")


@app.post("/war/tier7/repair")
@login_required
def tier7_repair_dashboard():
    db = get_db()
    message = tier7.repair(db)
    db.close()
    flash(message)
    return redirect(url_for("war_control") + "#tier7-control")


@app.get("/war/territories/map-data")
@login_required
def territory_map_data():
    """Small real-province geometry feed for the Dashboard territory picker."""
    global REGION_PICKER_CACHE
    if REGION_PICKER_CACHE is None:
        regions = []
        seen_codes = set()
        for index, feature in enumerate(war_tier._province_features()):
            meta = war_tier._feature_meta(feature)
            if not meta:
                continue
            code, name, _centre, _bounds = meta
            code = code or f"ADM1-{index}"
            if code in seen_codes:
                code = f"{code}-{index}"
            seen_codes.add(code)
            polygons = war_tier._geometry_polygons(feature)
            outer_rings = [polygon[0] for polygon in polygons if polygon and polygon[0]]
            if not outer_rings:
                continue
            ring = max(outer_rings, key=lambda value: war_tier._ring_area_centroid(value)[0])
            # Keep the picker responsive over Tailscale/mobile connections. Twenty-four
            # outline points are enough for clicking a province without sending a huge feed.
            step = max(1, len(ring) // 24)
            points = [[round(point[0], 2), round(point[1], 2)] for point in ring[::step]]
            if points and points[-1] != points[0]:
                points.append(points[0])
            properties = feature.get("properties") or {}
            country = str(properties.get("admin") or "Other")
            regions.append({"code": code, "name": name, "country": country, "points": points, "kind": "state"})
        for code, (_stable, name, centre, _bounds) in war_tier.world_capital_tiles().items():
            country = name.rsplit(", ", 1)[-1] if ", " in name else "Other"
            regions.append({"code": code, "name": name, "country": country,
                            "points": [[round(centre[0], 2), round(centre[1], 2)]], "kind": "capital"})
        REGION_PICKER_CACHE = regions
    db = get_db()
    claimed = {row["territory_code"]: row["owner_user_id"] for row in db.execute(
        "SELECT territory_code,owner_user_id FROM map_territories")}
    db.close()
    return jsonify({"regions": [dict(region, claimed=region["code"] in claimed) for region in REGION_PICKER_CACHE]})


@app.post("/war/territory/move")
@login_required
def move_nation_territory():
    db = get_db()
    old_code = request.form.get("territory_code", "")
    new_code = request.form.get("new_territory_code", "")
    city_name = " ".join(request.form.get("city_name", "").split())[:32]
    old = db.execute("SELECT * FROM map_territories WHERE territory_code=?", (old_code,)).fetchone()
    regions = war_tier.world_city_tiles(war_tier._province_features())
    capitals = war_tier.world_capital_tiles()
    try:
        if not old or new_code not in regions | capitals:
            raise ValueError("Select a valid current Land and destination.")
        is_capital_destination = new_code in capitals
        if bool(old["is_capital"]) != is_capital_destination:
            raise ValueError("Capital Land must move to an official Capital City; normal Land must move to a State / Province.")
        if city_name and ("discord.gg" in city_name.lower() or "@everyone" in city_name.lower()):
            raise ValueError("Enter a safe City Name.")
        if db.execute("SELECT 1 FROM map_territories WHERE territory_code=?", (new_code,)).fetchone():
            raise ValueError("That Capital City or State / Province is already claimed.")
        destination = capitals.get(new_code) or regions.get(new_code)
        db.execute("""INSERT INTO map_territories
            (territory_code,territory_name,owner_user_id,is_capital,acquired_at,level)
            VALUES(?,?,?,?,?,?)""", (new_code, destination[1], old["owner_user_id"],
            old["is_capital"], old["acquired_at"], old["level"]))
        db.execute("UPDATE player_cities SET territory_code=? WHERE territory_code=? AND user_id=?",
                   (new_code, old_code, old["owner_user_id"]))
        if old["is_capital"]:
            capital_city = destination[1].split(" — ", 1)[0]
            db.execute("UPDATE players SET capital_name=? WHERE user_id=?",
                       (capital_city, old["owner_user_id"]))
        elif city_name:
            city = db.execute("""SELECT id FROM player_cities WHERE user_id=? AND territory_code=?
                ORDER BY created_at,id LIMIT 1""", (old["owner_user_id"], new_code)).fetchone()
            if city:
                db.execute("UPDATE player_cities SET name=? WHERE id=?", (city_name, city["id"]))
            else:
                db.execute("""INSERT INTO player_cities(user_id,city_type,name,level,created_at,territory_code)
                    VALUES(?,?,?,?,?,?)""", (old["owner_user_id"], "civilian", city_name, 1, int(time.time()), new_code))
        db.execute("DELETE FROM map_territories WHERE territory_code=?", (old_code,))
        db.commit()
        suffix = f" Capital City: {capital_city}." if old["is_capital"] else (f" City renamed to {city_name}." if city_name else "")
        flash(f"Land moved from {old['territory_name']} to {destination[1]}.{suffix}")
    except (ValueError, sqlite3.IntegrityError) as error:
        db.rollback(); flash(f"Could not move Land: {error}")
    db.close(); return redirect(url_for("war_control") + "#territory-editor")


@app.post("/war/territory/development")
@login_required
def set_land_development():
    db = get_db()
    code = request.form.get("territory_code", "")
    level = settings_admin.integer(request.form.get("level", 1), "level", 1, 5)
    row = db.execute("SELECT territory_name FROM map_territories WHERE territory_code=?", (code,)).fetchone()
    if row:
        db.execute("UPDATE map_territories SET level=? WHERE territory_code=?", (level, code))
        db.commit(); flash(f"{row['territory_name']} is now Land Level {level} with {level} City slots.")
    else:
        flash("Land was not found.")
    db.close(); return redirect(url_for("war_control") + "#territory-editor")


@app.post("/war/city/<int:city_id>/save")
@login_required
def save_city_dashboard(city_id):
    db = get_db()
    city = db.execute("SELECT * FROM player_cities WHERE id=?", (city_id,)).fetchone()
    name = " ".join(request.form.get("name", "").split())[:32]
    territory_code = request.form.get("territory_code", "")
    territory = db.execute("SELECT * FROM map_territories WHERE territory_code=?", (territory_code,)).fetchone()
    try:
        if not city:
            raise ValueError("City was not found.")
        if len(name) < 2 or "discord.gg" in name.lower() or "@everyone" in name.lower():
            raise ValueError("City name must be 2–32 safe characters.")
        if not territory or territory["owner_user_id"] != city["user_id"]:
            raise ValueError("The selected Land is not owned by this Nation.")
        used = db.execute("SELECT COUNT(*) amount FROM player_cities WHERE territory_code=? AND id<>?",
                          (territory_code, city_id)).fetchone()["amount"]
        if used >= territory["level"]:
            raise ValueError(f"{territory['territory_name']} has no free City slots (Lv {territory['level']}).")
        old_name = city["name"]
        db.execute("UPDATE player_cities SET name=?,territory_code=? WHERE id=?", (name, territory_code, city_id))
        db.commit()
        flash(f"City saved: {old_name} → {name}, located in {territory['territory_name']}.")
    except ValueError as error:
        db.rollback(); flash(f"Could not save City: {error}")
    db.close(); return redirect(url_for("war_control") + "#territory-editor")


@app.post("/war/season/start")
@login_required
def start_war_season_dashboard():
    db=get_db()
    if db.execute("SELECT 1 FROM war_seasons WHERE status='active'").fetchone():
        flash("A War Campaign season is already active.")
    else:
        name=request.form.get("name","").strip()[:60]
        if not name:
            flash("Season name is required.")
        else:
            now=int(time.time())
            duration_days=settings_admin.integer(request.form.get("duration_days", 14), "duration_days", 1, 365)
            scheduled_ends_at=now+(duration_days*86400)
            participation_xc=war_tier.setting(db,"war_season_participation_xc")
            rewards = [settings_admin.integer(request.form.get(key, 0), key, 0, 999999999) for key in (
                "reward_xc", "reward_war_credits", "reward_xc_2", "reward_war_credits_2",
                "reward_xc_3", "reward_war_credits_3")]
            db.execute("""INSERT INTO war_seasons(name,status,starts_at,reward_xc,reward_war_credits,
                reward_xc_2,reward_war_credits_2,reward_xc_3,reward_war_credits_3,
                scheduled_ends_at,participation_xc,created_at)
                VALUES(?,'active',?,?,?,?,?,?,?,?,?,?)""",
                (name, now, *rewards, scheduled_ends_at, participation_xc, now))
            db.commit();flash(f"War Campaign {name} started.")
    db.close();return redirect(url_for("war_control")+"#war-season")


@app.post("/war/season/end")
@login_required
def end_war_season_dashboard():
    db=get_db();season=db.execute("SELECT * FROM war_seasons WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    if not season:
        flash("No active War Campaign season.")
    else:
        # Snapshot land held at the finish line once. This makes the season
        # result deterministic even if an administrator reopens this route.
        if not season["land_hold_awarded"]:
            hold_points=war_tier.setting(db,"war_season_land_hold_points")
            db.execute("""UPDATE war_season_scores SET
                land_hold_points=COALESCE((SELECT MAX(0,p.land)*? FROM players p
                    WHERE p.user_id=war_season_scores.user_id),0),
                points=points+COALESCE((SELECT MAX(0,p.land)*? FROM players p
                    WHERE p.user_id=war_season_scores.user_id),0)
                WHERE season_id=?""",(hold_points,hold_points,season["id"]))
            db.execute("UPDATE war_seasons SET land_hold_awarded=1 WHERE id=?",(season["id"],))
        winners=db.execute("""SELECT * FROM war_season_scores WHERE season_id=?
            ORDER BY points DESC,wins DESC,land_captured DESC LIMIT 3""",(season["id"],)).fetchall()
        champion_id=winners[0]["user_id"] if winners else None
        reward_pairs = ((season["reward_xc"], season["reward_war_credits"]),
                        (season["reward_xc_2"], season["reward_war_credits_2"]),
                        (season["reward_xc_3"], season["reward_war_credits_3"]))
        ranked={row["user_id"]: rank for rank,row in enumerate(winners,1)}
        minimum_battles=war_tier.setting(db,"war_season_participation_min_battles")
        participants=db.execute("SELECT * FROM war_season_scores WHERE season_id=? AND battles>=?",
            (season["id"],minimum_battles)).fetchall()
        candidates={row["user_id"]:row for row in participants}
        candidates.update({row["user_id"]:row for row in winners})
        paid = 0
        trophy_awarded = False
        now = int(time.time())
        for user_id, score in candidates.items():
            rank=ranked.get(user_id,0)
            reward_xc,reward_wc=(reward_pairs[rank-1] if rank else (0,0))
            if score["battles"]>=minimum_battles:
                reward_xc+=season["participation_xc"]
            if db.execute("SELECT 1 FROM war_season_payouts WHERE season_id=? AND user_id=?",
                          (season["id"], user_id)).fetchone():
                continue
            db.execute("UPDATE players SET xc=xc+?,money=money+? WHERE user_id=?",
                (reward_xc, reward_wc, user_id))
            db.execute("""INSERT INTO war_season_payouts
                (season_id,user_id,rank,reward_xc,reward_war_credits,paid_at) VALUES(?,?,?,?,?,?)""",
                (season["id"], user_id, rank, reward_xc, reward_wc, now))
            # Season 1's champion receives one permanent, non-tradeable
            # collectible in addition to the configured XC / War Credit prize.
            if rank == 1 and str(season["name"]).strip().casefold().startswith("season 1"):
                trophy = db.execute("SELECT id FROM items WHERE name='Season 1 Champion Trophy' COLLATE NOCASE").fetchone()
                if trophy:
                    db.execute("""INSERT INTO inventories(user_id,item_id,quantity) VALUES(?,?,1)
                        ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1""",
                        (user_id, trophy["id"]))
                    db.execute("INSERT INTO economy_logs(user_id,action,detail,created_at) VALUES(?,?,?,?)",
                        (user_id, "season_reward", "Awarded Season 1 Champion Trophy", now))
                    trophy_awarded = True
            paid += 1
        db.execute("UPDATE war_seasons SET status='ended',ends_at=?,champion_user_id=? WHERE id=?",
            (now,champion_id,season["id"]));db.commit()
        if champion_id is not None:
            trophy_note = " Season 1 Champion Trophy awarded." if trophy_awarded else ""
            flash(f"Season ended and {paid} Nation reward(s) paid, including eligible participation rewards.{trophy_note}")
        else:
            flash("Season ended with no scored battles.")
    db.close();return redirect(url_for("war_control")+"#war-season")


@app.post("/war/season/rules")
@login_required
def save_war_season_rules():
    db=get_db()
    for key in ("war_season_win_points","war_season_loss_points","war_season_land_points",
        "war_season_capital_damage_per_point","war_season_defense_points","war_season_capital_capture_points",
        "war_season_land_hold_points","war_season_participation_min_battles","war_season_participation_xc"):
        minimum=1 if key=="war_season_capital_damage_per_point" else 0
        value=max(minimum,int(request.form.get(key,minimum)))
        db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
    for form_key,setting_key in (("war_new_nation_protection_hours","war_new_nation_protection_seconds"),
                                 ("war_pair_attack_cooldown_hours","war_pair_attack_cooldown"),
                                 ("nation_war_duration_hours","nation_war_duration")):
        value=settings_admin.integer(request.form.get(form_key, 0), form_key, 0, 999999999)*3600
        db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (setting_key,str(value)))
    db.commit();db.close();flash("War Campaign scoring saved.");return redirect(url_for("war_control")+"#war-season")


@app.post("/war/logistics/save")
@login_required
def save_war_logistics():
    db=get_db();keys=("war_max_supply","war_supply_cost","war_supply_per_attack","war_readiness_per_prepare","war_prepare_supply_cost",
        "city_collect_cooldown","free_land_claim_cooldown","city_capital_war_credits","city_capital_supply","city_civilian_build_cost","city_industrial_build_cost","city_civilian_war_credits","city_industrial_war_credits","city_industrial_supply","city_upgrade_base_cost","city_rename_cost","land_max_level","land_upgrade_2_credits","land_upgrade_2_supply","land_upgrade_3_credits","land_upgrade_3_supply","land_upgrade_4_credits","land_upgrade_4_supply","land_upgrade_5_credits","land_upgrade_5_supply",
        "attack_cooldown","capital_damage","capital_reward_percent","capital_repair_cost_per_hp","fortified_defense_bonus",
        "aggressive_attack_bonus","scout_cost","scout_cooldown","demobilize_refund_percent","fortify_base_cost",
        "fortify_power_percent","fortify_max_level","war_battle_variance_percent","war_winner_loss_percent",
        "war_loser_loss_percent","war_readiness_loss_per_battle","war_winner_morale_gain","war_loser_morale_loss",
        "war_rally_supply_cost","war_rally_morale_gain","war_air_superiority_bonus",
        "war_navy_blockade_penalty","war_navy_supply_damage")
    percent_keys={"war_readiness_per_prepare","capital_damage","capital_reward_percent","fortified_defense_bonus","aggressive_attack_bonus","demobilize_refund_percent","fortify_power_percent","war_winner_loss_percent","war_loser_loss_percent","war_readiness_loss_per_battle","war_winner_morale_gain","war_loser_morale_loss","war_rally_morale_gain","war_air_superiority_bonus","war_navy_blockade_penalty"}
    for key in keys:
        value=settings_admin.integer(request.form.get(key, 0), key, 0, 999999999)
        if key in percent_keys: value=min(100,value)
        if key=="war_battle_variance_percent": value=min(50,value)
        if key=="war_max_supply": value=max(1,value)
        if key in {"city_collect_cooldown","free_land_claim_cooldown"}: value=max(3600,value)
        db.execute("INSERT INTO economy_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
    db.commit();db.close();flash("Complete War rules saved.");return redirect(url_for("war_control"))


@app.post("/war/nation-state/save")
@login_required
def save_nation_war_state():
    db=get_db(); user_id=int(request.form["user_id"])
    maximum_supply=int((db.execute("SELECT value FROM economy_settings WHERE key='war_max_supply'").fetchone() or {"value":1000})["value"])
    supply=min(maximum_supply,settings_admin.integer(request.form.get("supply", 0), "supply", 0, 999999999))
    readiness=settings_admin.integer(request.form.get("readiness", 100), "readiness", 0, 100)
    morale=settings_admin.integer(request.form.get("morale", 100), "morale", 0, 100)
    fortification=settings_admin.integer(request.form.get("fortification_level", 0), "fortification_level", 0, 999999999)
    stance=request.form.get("defense_stance","balanced")
    if stance not in {"balanced","fortified","aggressive"}: stance="balanced"
    capital=settings_admin.integer(request.form.get("capital_health", 100), "capital_health", 0, 100)
    db.execute("""INSERT INTO player_war_settings(user_id,defense_stance,fortification_level,morale,supply,readiness)
        VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET defense_stance=excluded.defense_stance,
        fortification_level=excluded.fortification_level,morale=excluded.morale,supply=excluded.supply,readiness=excluded.readiness""",
        (user_id,stance,fortification,morale,supply,readiness))
    db.execute("UPDATE players SET capital_health=? WHERE user_id=?",(capital,user_id));db.commit();db.close()
    flash("Nation War state saved.");return redirect(url_for("war_control"))


@app.post("/war/unit/save")
@login_required
def save_war_unit():
    db = get_db(); unit_id = request.form.get("id")
    code = request.form["code"].strip().lower().replace(" ", "_"); name = request.form["name"].strip()
    values = (code, name, request.form.get("emoji", "🪖").strip() or "🪖", request.form.get("branch", "land"), int(request.form["category_id"]), settings_admin.integer(request.form["cost"], "cost", 0, 999999999), settings_admin.integer(request.form["power"], "power", 0, 999999999), request.form.get("description", "").strip(), int(request.form.get("enabled", 1)), settings_admin.integer(request.form.get("position", 99), "position", 0, 999999999))
    try:
        if not code or not name:
            raise ValueError("Code and model name are required.")
        if unit_id:
            old_unit = db.execute("""SELECT name,cost,power,branch,category_id,enabled
                FROM war_unit_types WHERE id=?""", (int(unit_id),)).fetchone()
            if old_unit is None:
                raise ValueError("War unit model not found.")
            db.execute("UPDATE war_unit_types SET code=?,name=?,emoji=?,branch=?,category_id=?,cost=?,power=?,description=?,enabled=?,position=? WHERE id=?", values + (int(unit_id),))
            changes = []
            if old_unit["name"] != name:
                changes.append(f"Name: {old_unit['name']} → {name}")
            if int(old_unit["cost"]) != values[5]:
                changes.append(f"Price: {int(old_unit['cost']):,} → {values[5]:,} War Credits")
            if int(old_unit["power"]) != values[6]:
                changes.append(f"Power: {int(old_unit['power']):,} → {values[6]:,}")
            if old_unit["branch"] != values[3]:
                changes.append(f"Service: {old_unit['branch']} → {values[3]}")
            if int(old_unit["category_id"] or 0) != values[4]:
                changes.append("Recruit category changed")
            if int(old_unit["enabled"]) != values[8]:
                changes.append("Status: " + ("Enabled" if values[8] else "Disabled"))
            action = "RENAMED" if old_unit["name"] != name else "UPDATED"
            detail = f"War unit {name}: " + (" · ".join(changes) if changes else "Saved without field changes")
            record_dashboard_audit(db, action, detail)
        else:
            db.execute("INSERT INTO war_unit_types(code,name,emoji,branch,category_id,cost,power,description,enabled,position) VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            record_dashboard_audit(db, "CREATED", f"Created war unit: {name} · Price: {values[5]:,} War Credits · Power: {values[6]:,}")
        db.commit(); flash("War unit model saved.")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error))
    g.skip_dashboard_audit = True
    db.close(); return redirect(url_for("war_control"))


@app.post("/war/unit/<int:unit_id>/toggle")
@login_required
def toggle_war_unit(unit_id):
    db=get_db(); row=db.execute("SELECT name,enabled FROM war_unit_types WHERE id=?",(unit_id,)).fetchone()
    if row:
        enabled=0 if row["enabled"] else 1
        db.execute("UPDATE war_unit_types SET enabled=? WHERE id=?",(enabled,unit_id));db.commit()
        flash(f"{row['name']} {'enabled' if enabled else 'disabled'}.")
    else: flash("War unit model not found.")
    db.close();return redirect(url_for("war_control"))


@app.post("/war/unit/<int:unit_id>/delete")
@login_required
def delete_war_unit(unit_id):
    db=get_db(); row=db.execute("SELECT name FROM war_unit_types WHERE id=?",(unit_id,)).fetchone()
    owned=db.execute("SELECT COALESCE(SUM(quantity),0) total FROM player_war_units WHERE unit_type_id=?",(unit_id,)).fetchone()["total"]
    if row is None:
        flash("War unit model not found.")
    elif int(owned)>0:
        flash(f"Cannot delete {row['name']}: players still own {int(owned):,} unit(s). Disable it instead, or remove those units first.")
    else:
        db.execute("DELETE FROM player_war_units WHERE unit_type_id=?",(unit_id,))
        db.execute("DELETE FROM division_template_units WHERE unit_type_id=?",(unit_id,))
        db.execute("DELETE FROM war_unit_types WHERE id=?",(unit_id,));db.commit()
        flash(f"War unit model {row['name']} deleted.")
    db.close();return redirect(url_for("war_control"))


@app.post("/war/division/<int:template_id>/delete")
@login_required
def delete_division_dashboard(template_id):
    db=get_db(); row=db.execute("SELECT name FROM division_templates WHERE id=?",(template_id,)).fetchone()
    if row:
        db.execute("DELETE FROM division_template_units WHERE template_id=?",(template_id,))
        db.execute("DELETE FROM division_templates WHERE id=?",(template_id,));db.commit();flash(f"Division Template {row['name']} deleted.")
    else: flash("Division Template not found.")
    db.close();return redirect(url_for("war_control"))


@app.post("/war/categories/save")
@login_required
def save_war_category():
    db = get_db(); category_id = request.form.get("id"); action=request.form.get("action","save")
    if action=="delete" and category_id:
        category=db.execute("SELECT * FROM war_unit_categories WHERE id=?",(int(category_id),)).fetchone()
        unit_count=db.execute("SELECT COUNT(*) count FROM war_unit_types WHERE category_id=?",(int(category_id),)).fetchone()["count"]
        if category is None:
            flash("Recruit category not found.")
        elif category["system_category"]:
            flash("Core Army, Air Force and Navy categories cannot be deleted.")
        elif unit_count:
            flash(f"Cannot delete {category['label']}. Move or delete its {unit_count} unit model(s) first.")
        else:
            db.execute("DELETE FROM war_unit_categories WHERE id=?",(int(category_id),));db.commit()
            flash(f"Recruit category {category['label']} deleted.")
        db.close();return redirect(url_for("war_control")+"#war-category-manager")
    label = request.form["label"].strip()
    emoji = request.form.get("emoji", "⚔️").strip() or "⚔️"; position = settings_admin.integer(request.form.get("position", 99), "position", 0, 999999999)
    parent_branch = request.form.get("parent_branch", "land")
    if parent_branch not in {"land", "air", "navy"}:
        parent_branch = "land"
    try:
        if not label:
            raise ValueError("Category name is required.")
        if category_id:
            db.execute("UPDATE war_unit_categories SET label=?,emoji=?,parent_branch=?,position=?,enabled=? WHERE id=?", (label,emoji,parent_branch,position,int(request.form.get("enabled",1)),int(category_id)))
        else:
            db.execute("INSERT INTO war_unit_categories(label,emoji,parent_branch,position,enabled) VALUES(?,?,?,?,1)", (label,emoji,parent_branch,position))
        db.commit(); flash("Army Recruit category saved.")
    except (ValueError,sqlite3.IntegrityError) as error:
        flash(str(error))
    db.close(); return redirect(url_for("war_control"))


@app.post("/war/categories/default")
@login_required
def save_default_war_category():
    db = get_db()
    category_id = int(request.form.get("category_id", 0))
    sort_mode = request.form.get("sort_mode", "manual")
    if sort_mode not in {"manual","cost_low","cost_high","power_high","name"}: sort_mode = "manual"
    valid = db.execute("""SELECT 1 FROM war_unit_categories c WHERE c.id=? AND c.enabled=1
        AND EXISTS(SELECT 1 FROM war_unit_types u WHERE u.category_id=c.id AND u.enabled=1)""", (category_id,)).fetchone()
    if valid:
        db.execute("""INSERT INTO economy_settings(key,value) VALUES('army_recruit_default_category_id',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(category_id),))
        db.execute("""INSERT INTO economy_settings(key,value) VALUES('army_recruit_default_sort',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (sort_mode,))
        db.commit(); flash("Army Recruit first page and default sorting saved.")
    else:
        flash("Choose an enabled category that contains at least one enabled unit.")
    db.close(); return redirect(url_for("war_control") + "#war-category-manager")


@app.post("/war/start")
@login_required
def start_war_dashboard():
    attacker = int(request.form["attacker"]); defender = int(request.form["defender"]); db = get_db()
    if attacker == defender:
        flash("Attacker and defender must be different Alliances.")
    elif db.execute("SELECT 1 FROM wars WHERE active=1").fetchone():
        flash("A war is already active.")
    else:
        import time
        db.execute("INSERT INTO wars(attacker_alliance_id,defender_alliance_id,started_at,active) VALUES(?,?,?,1)", (attacker, defender, int(time.time())))
        db.commit(); flash("Alliance War started.")
    db.close(); return redirect(url_for("war_control"))


@app.post("/war/end")
@login_required
def end_war_dashboard():
    winner = int(request.form["winner"]) if request.form.get("winner") else None; db = get_db()
    active = db.execute("SELECT * FROM wars WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if active:
        allowed = {active['attacker_alliance_id'], active['defender_alliance_id']}
        if winner is None or winner in allowed:
            db.execute("UPDATE wars SET active=0,winner_alliance_id=? WHERE id=?", (winner, active['id'])); db.commit(); flash("Alliance War ended.")
        else:
            flash("Winner must be one of the fighting Alliances.")
    db.close(); return redirect(url_for("war_control"))


@app.post("/alliance/<int:alliance_id>/save")
@login_required
def save_alliance(alliance_id):
    name = request.form['name'].strip(); tag = request.form['tag'].strip().upper(); leader_id = int(request.form['leader_id']); db = get_db()
    try:
        if not 3 <= len(name) <= 30 or not 2 <= len(tag) <= 5 or not tag.isalnum():
            raise ValueError("Alliance name/tag is invalid.")
        db.execute("UPDATE alliances SET name=?,tag=?,leader_id=? WHERE id=?", (name, tag, leader_id, alliance_id)); db.commit(); flash("Alliance saved.")
    except (ValueError, sqlite3.IntegrityError) as error:
        flash(str(error))
    db.close(); return redirect(url_for("war_control"))


def mention_id(value):
    """Accept a raw Discord ID, <@user>, or <@&role> mention."""
    digits = "".join(character for character in (value or "") if character.isdigit())
    return digits


@app.route("/auction", methods=["GET", "POST"])
@login_required
def auction_control():
    db = get_db()
    if request.method == "POST":
        enabled = "1" if request.form.get("auction_enabled") == "1" else "0"
        db.execute("INSERT INTO economy_settings(key,value) VALUES('auction_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (enabled,))
        db.commit(); flash("Auction is now open." if enabled == "1" else "Auction is now closed.")
    enabled = db.execute("SELECT value FROM economy_settings WHERE key='auction_enabled'").fetchone()["value"]
    db.close()
    body = """<section class="panel"><h2>🔨 Auction Master Control</h2><div class="notice">Auction listings remain protected while this switch is closed. The player auction commands will be added in a later update.</div><form class="fields" method="post"><input type="hidden" name="auction_enabled" value="{{0 if enabled=='1' else 1}}"><p class="{{'ok' if enabled=='1' else 'bad'}}">Auction is currently {{'OPEN' if enabled=='1' else 'CLOSED'}}.</p><div class="actions"><button class="{{'danger' if enabled=='1' else 'teal'}}">{{'Close Auction' if enabled=='1' else 'Open Auction'}}</button></div></form></section>"""
    return admin_page("Auction", body, enabled=enabled)


@app.route("/recipes")
@login_required
def recipes_control():
    db = get_db(); edit_id = request.args.get("edit", -1)
    rows = db.execute("""SELECT r.*,i.name output_name,i.emoji output_emoji FROM recipes r
        JOIN items i ON i.id=r.output_item_id ORDER BY r.name""").fetchall()
    edit = db.execute("SELECT * FROM recipes WHERE id=?", (edit_id,)).fetchone()
    ingredient_slots = [] if edit is None else list(db.execute("SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY item_id LIMIT 4", (edit["id"],)).fetchall())
    ingredient_slots += [None] * (4 - len(ingredient_slots))
    item_rows = db.execute("SELECT id,name,emoji FROM items WHERE enabled=1 ORDER BY name").fetchall(); db.close()
    body = """<details class="creator" {% if edit %}open{% endif %}><summary class="btn">＋ Create New Recipe</summary><section class="panel"><h2>{{'Edit Recipe' if edit else 'Create New Recipe'}}</h2><form class="fields" method="post" action="{{url_for('save_recipe')}}"><input type="hidden" name="id" value="{{edit['id'] if edit else ''}}"><div class="fields-grid"><label>Recipe Name<input name="name" value="{{edit['name'] if edit else ''}}" required></label><label>Emoji<input name="emoji" value="{{edit['emoji'] if edit else '🧪'}}"></label><label>Description<textarea name="description">{{edit['description'] if edit else ''}}</textarea></label><label>Output Item<select name="output_item_id" required>{% for i in items %}<option value="{{i['id']}}" {% if edit and edit['output_item_id']==i['id'] %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Output Quantity<input type="number" min="1" name="output_quantity" value="{{edit['output_quantity'] if edit else 1}}"></label><label>XC Crafting Cost<input type="number" min="0" name="xc_cost" value="{{edit['xc_cost'] if edit else 0}}"></label><label>Enabled<select name="enabled"><option value="1" {% if not edit or edit['enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if edit and not edit['enabled'] %}selected{% endif %}>No</option></select></label></div><h2>Ingredients</h2><div class="fields-grid">{% for ingredient in ingredient_slots %}{% set slot=loop.index0 %}{% set chosen=ingredient['item_id'] if ingredient else 0 %}<label>Ingredient {{slot+1}}<select name="ingredient_item_{{slot}}"><option value="">None</option>{% for i in items %}<option value="{{i['id']}}" {% if i['id']==chosen %}selected{% endif %}>{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Ingredient {{slot+1}} Quantity<input type="number" min="1" name="ingredient_qty_{{slot}}" value="{{ingredient['quantity'] if ingredient else 1}}"></label>{% endfor %}</div><div class="actions"><button>Save Recipe</button></div></form></section></details><section class="panel"><h2>🧪 Recipe Library ({{rows|length}})</h2><div class="library">{% for r in rows %}<article class="library-card"><h3>{{r['emoji']}} {{r['name']}}</h3><p>{{r['description']}}</p><div class="statline">Output: {{r['output_emoji']}} {{r['output_name']}} ×{{r['output_quantity']}}<br>Cost: {{r['xc_cost']}} XC<br><span class="{{'ok' if r['enabled'] else 'bad'}}">{{'Enabled' if r['enabled'] else 'Disabled'}}</span></div><div class="actions"><a class="btn" href="{{url_for('recipes_control',edit=r['id'])}}">Edit</a></div></article>{% endfor %}</div></section>"""
    body = body.replace(
        '<div class="actions"><a class="btn" href="{{url_for(\'recipes_control\',edit=r[\'id\'])}}">Edit</a></div>',
        '<div class="actions"><a class="btn" href="{{url_for(\'recipes_control\',edit=r[\'id\'])}}">Edit</a>'
        '<form method="post" action="{{url_for(\'delete_recipe\',recipe_id=r[\'id\'])}}" '
        'onsubmit="return confirm(\'Delete this recipe?\')"><button class="danger">Delete</button></form></div>'
    )
    return admin_page("Recipes", body, rows=rows, edit=edit, items=item_rows, ingredient_slots=ingredient_slots)


@app.post("/recipes/save")
@login_required
def save_recipe():
    db = get_db(); recipe_id = request.form.get("id")
    values = (request.form["name"].strip(), request.form.get("emoji","🧪").strip() or "🧪", request.form.get("description","").strip(), int(request.form["output_item_id"]), settings_admin.integer(request.form["output_quantity"], "output_quantity", 1, 999999999), settings_admin.integer(request.form["xc_cost"], "xc_cost", 0, 999999999), int(request.form.get("enabled",1)))
    try:
        if recipe_id:
            db.execute("UPDATE recipes SET name=?,emoji=?,description=?,output_item_id=?,output_quantity=?,xc_cost=?,enabled=? WHERE id=?", values+(int(recipe_id),))
            rid = int(recipe_id); db.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (rid,))
        else:
            rid = db.execute("INSERT INTO recipes(name,emoji,description,output_item_id,output_quantity,xc_cost,enabled) VALUES(?,?,?,?,?,?,?)", values).lastrowid
        combined = {}
        for slot in range(4):
            item = request.form.get(f"ingredient_item_{slot}")
            if item:
                combined[int(item)] = combined.get(int(item),0) + settings_admin.integer(request.form.get(f"ingredient_qty_{slot}", 1), f"ingredient_qty_{slot}", 1, 999999999)
        db.executemany("INSERT INTO recipe_ingredients(recipe_id,item_id,quantity) VALUES(?,?,?)", [(rid,item,qty) for item,qty in combined.items()])
        db.commit(); flash("Recipe saved.")
    except (ValueError,sqlite3.IntegrityError) as error:
        flash(str(error))
    db.close(); return redirect(url_for("recipes_control"))


@app.post("/recipes/<int:recipe_id>/delete")
@login_required
def delete_recipe(recipe_id):
    db = get_db(); recipe = db.execute("SELECT name FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    if recipe:
        active = int(db.execute(
            "SELECT COUNT(*) FROM tier6_production_queue WHERE recipe_id=? AND status='working'",
            (recipe_id,),
        ).fetchone()[0])
        if active:
            db.close(); flash(f"Cannot delete {recipe['name']}: {active} active Production job(s) still use it. Disable it first.")
            return redirect(url_for("recipes_control"))
        db.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
        db.execute("DELETE FROM recipes WHERE id=?", (recipe_id,)); db.commit()
        flash(f"Recipe {recipe['name']} deleted.")
    else:
        flash("Recipe was not found.")
    db.close(); return redirect(url_for("recipes_control"))


@app.route("/finance")
@login_required
def finance_control():
    db=get_db(); bills=db.execute("SELECT * FROM bills ORDER BY name").fetchall(); incomes=db.execute("SELECT * FROM income_sources ORDER BY name").fetchall(); db.close()
    form = """<details class="creator"><summary class="btn">＋ Create Bill or Income</summary><section class="panel"><h2>Create Recurring Finance Rule</h2><form class="fields" method="post" action="{{url_for('save_finance_rule')}}"><div class="fields-grid"><label>Type<select name="kind"><option value="bill">Bill</option><option value="income">Income</option></select></label><label>Name<input name="name" required></label><label>Emoji<input name="emoji" value="🧾"></label><label>Amount (XC)<input type="number" min="0" name="amount" value="100"></label><label>Interval (seconds)<input type="number" min="60" name="interval_seconds" value="86400"></label><label>Enabled<select name="enabled"><option value="1">Yes</option><option value="0">No</option></select></label></div><div class="actions"><button>Save Rule</button></div></form></section></details>"""
    body = form + """<section class="panel"><h2>🧾 Bills</h2><div class="library">{% for r in bills %}<article class="library-card"><h3>{{r['emoji']}} {{r['name']}}</h3><div class="statline">{{r['amount']}} XC every {{r['interval_seconds']}} seconds<br><span class="{{'ok' if r['enabled'] else 'bad'}}">{{'Enabled' if r['enabled'] else 'Disabled'}}</span></div><form class="fields" method="post" action="{{url_for('save_finance_rule')}}"><input type="hidden" name="kind" value="bill"><input type="hidden" name="id" value="{{r['id']}}"><input name="name" value="{{r['name']}}"><input name="emoji" value="{{r['emoji']}}"><input type="number" min="0" name="amount" value="{{r['amount']}}"><input type="number" min="60" name="interval_seconds" value="{{r['interval_seconds']}}"><select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select><button>Save</button></form></article>{% endfor %}</div></section><section class="panel"><h2>💵 Income Sources</h2><div class="library">{% for r in incomes %}<article class="library-card"><h3>{{r['emoji']}} {{r['name']}}</h3><div class="statline">{{r['amount']}} XC every {{r['interval_seconds']}} seconds<br><span class="{{'ok' if r['enabled'] else 'bad'}}">{{'Enabled' if r['enabled'] else 'Disabled'}}</span></div><form class="fields" method="post" action="{{url_for('save_finance_rule')}}"><input type="hidden" name="kind" value="income"><input type="hidden" name="id" value="{{r['id']}}"><input name="name" value="{{r['name']}}"><input name="emoji" value="{{r['emoji']}}"><input type="number" min="0" name="amount" value="{{r['amount']}}"><input type="number" min="60" name="interval_seconds" value="{{r['interval_seconds']}}"><select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select><button>Save</button></form></article>{% endfor %}</div></section>"""
    body = body.replace(
        '<button>Save</button></form></article>',
        '<div class="actions"><button name="action" value="save">Save</button>'
        '<button class="danger" name="action" value="delete" onclick="return confirm(\'Delete this finance rule?\')">Delete</button></div></form></article>'
    )
    return admin_page("Bills & Income", body, bills=bills, incomes=incomes)


@app.post("/finance/save")
@login_required
def save_finance_rule():
    db=get_db(); kind=request.form.get("kind"); table="bills" if kind=="bill" else "income_sources"; row_id=request.form.get("id")
    action=request.form.get("action","save")
    if action=="delete" and row_id:
        row=db.execute(f"SELECT name FROM {table} WHERE id=?",(int(row_id),)).fetchone()
        state_table="player_bill_state" if table=="bills" else "player_income_state"
        state_column="bill_id" if table=="bills" else "income_id"
        db.execute(f"DELETE FROM {state_table} WHERE {state_column}=?",(int(row_id),))
        db.execute(f"DELETE FROM {table} WHERE id=?",(int(row_id),));db.commit()
        flash(f"{row['name'] if row else 'Finance rule'} deleted.")
        db.close();return redirect(url_for("finance_control"))
    values=(request.form["name"].strip(),request.form.get("emoji","🧾").strip() or "🧾",settings_admin.integer(request.form["amount"], "amount", 0, 999999999),settings_admin.integer(request.form["interval_seconds"], "interval_seconds", 60, 999999999),int(request.form.get("enabled",1)))
    try:
        if row_id: db.execute(f"UPDATE {table} SET name=?,emoji=?,amount=?,interval_seconds=?,enabled=? WHERE id=?",values+(int(row_id),))
        else: db.execute(f"INSERT INTO {table}(name,emoji,amount,interval_seconds,enabled) VALUES(?,?,?,?,?)",values)
        db.commit(); flash("Finance rule saved.")
    except sqlite3.IntegrityError as error: flash(str(error))
    db.close(); return redirect(url_for("finance_control"))


@app.route("/item-shop")
@login_required
def item_shop_control():
    db = get_db()
    rows = db.execute("""SELECT i.*,c.label category_label,c.emoji category_emoji FROM items i
        LEFT JOIN item_categories c ON c.id=i.category_id WHERE i.shop_visible=1 ORDER BY c.position,i.price,i.name""").fetchall()
    available = db.execute("SELECT id,name,emoji FROM items WHERE enabled=1 AND shop_visible=0 ORDER BY name").fetchall()
    enabled_row = db.execute("SELECT value FROM economy_settings WHERE key='economy_shop_enabled'").fetchone()
    enabled = enabled_row["value"] if enabled_row else "1"
    db.close()
    body = """<section class="panel"><h2>🏪 Item Shop Control</h2><div class="notice">This page controls which Item Library entries are sold by <code>/shop</code>. The Item Library remains the single source for item names, effects and categories.</div><form method="post" action="{{url_for('toggle_system',key='economy_shop_enabled')}}"><input type="hidden" name="return_to" value="item_shop_control"><input type="hidden" name="enabled" value="{{0 if enabled=='1' else 1}}"><button class="{{'danger' if enabled=='1' else 'teal'}}">{{'Close Economy Shop' if enabled=='1' else 'Open Economy Shop'}}</button></form></section>
    <details class="creator"><summary class="btn">＋ Sell New Item</summary><section class="panel"><h2>Create Item Offer</h2><form class="fields" method="post" action="{{url_for('save_item_offer')}}"><div class="fields-grid"><label>Item<select name="item_id">{% for i in available %}<option value="{{i['id']}}">{{i['emoji']}} {{i['name']}}</option>{% endfor %}</select></label><label>Price<input type="number" min="0" name="price" value="100"></label><label>Currency<select name="currency"><option value="xc">XC</option><option value="xcrystals">XCrystals</option></select></label><label>Stock (-1 unlimited)<input type="number" min="-1" name="stock" value="-1"></label><label>Sell-back Price<input type="number" min="0" name="sell_price" value="0"></label><label>Players Can Sell Back<select name="sellable"><option value="1">Yes</option><option value="0">No</option></select></label></div><button>Save Item Offer</button></form></section></details>
    <section class="panel"><h2>Items for Sale ({{rows|length}})</h2><div class="library">{% for i in rows %}<article class="library-card"><h3>{{i['emoji']}} {{i['name']}}</h3><span class="badge">{{i['category_emoji'] or '📦'}} {{i['category_label'] or 'Misc'}}</span><p>{{i['description']}}</p><form class="fields" method="post" action="{{url_for('save_item_offer')}}"><input type="hidden" name="item_id" value="{{i['id']}}"><div class="fields-grid"><label>Price<input type="number" min="0" name="price" value="{{i['price']}}"></label><label>Currency<select name="currency"><option value="xc" {% if i['currency']=='xc' %}selected{% endif %}>XC</option><option value="xcrystals" {% if i['currency']=='xcrystals' %}selected{% endif %}>XCrystals</option></select></label><label>Stock<input type="number" min="-1" name="stock" value="{{i['stock']}}"></label><label>Sell-back Price<input type="number" min="0" name="sell_price" value="{{i['sell_price']}}"></label><label>Sellable<select name="sellable"><option value="1" {% if i['sellable'] %}selected{% endif %}>Yes</option><option value="0" {% if not i['sellable'] %}selected{% endif %}>No</option></select></label></div><div class="actions"><button name="action" value="save">Save Offer</button><button class="archive" name="action" value="remove">Remove from Shop</button></div></form></article>{% else %}<p class="notice">No items are currently for sale.</p>{% endfor %}</div></section>"""
    return admin_page("Item Shop", body, rows=rows, available=available, enabled=enabled)


@app.post("/item-shop/save")
@login_required
def save_item_offer():
    db = get_db(); item_id = settings_admin.integer(request.form["item_id"], "item_id", 1, 999999999); action = request.form.get("action", "save")
    item = db.execute("SELECT name FROM items WHERE id=?", (item_id,)).fetchone()
    if item is None:
        db.close(); flash("Choose a valid Item Library entry."); return redirect(url_for("item_shop_control"))
    if action == "remove":
        db.execute("UPDATE items SET shop_visible=0 WHERE id=?", (item_id,)); flash(f"{item['name']} removed from the Economy Shop.")
    else:
        currency = request.form.get("currency", "xc")
        if currency not in {"xc", "xcrystals"}: currency = "xc"
        db.execute("""UPDATE items SET price=?,currency=?,stock=?,sell_price=?,sellable=?,shop_visible=1,enabled=1 WHERE id=?""",
            (settings_admin.integer(request.form.get("price", 0), "price", 0, 999999999),currency,max(-1,int(request.form.get("stock",-1))),
             settings_admin.integer(request.form.get("sell_price", 0), "sell_price", 0, 999999999),int(request.form.get("sellable",0)),item_id))
        flash(f"{item['name']} shop offer saved.")
    db.commit(); db.close(); return redirect(url_for("item_shop_control"))


@app.route("/role-shop")
@login_required
def role_shop_control():
    db=get_db(); rows=db.execute("SELECT * FROM role_shop ORDER BY price,name").fetchall(); enabled=db.execute("SELECT value FROM economy_settings WHERE key='role_shop_enabled'").fetchone()["value"]; db.close()
    body="""<section class="panel"><h2>🎭 Role Shop Control</h2><form class="fields" method="post" action="{{url_for('toggle_system',key='role_shop_enabled')}}"><input type="hidden" name="return_to" value="role_shop_control"><input type="hidden" name="enabled" value="{{0 if enabled=='1' else 1}}"><button class="{{'danger' if enabled=='1' else 'teal'}}">{{'Close Role Shop' if enabled=='1' else 'Open Role Shop'}}</button></form></section><details class="creator"><summary class="btn">＋ Sell New Role</summary><section class="panel"><h2>Create Role Offer</h2><form class="fields" method="post" action="{{url_for('save_role_offer')}}"><div class="fields-grid"><label>Display Name<input name="name" required></label><label>Discord @Role or Role ID<input name="role_id" placeholder="@VIP or 123456789" required></label><label>Emoji<input name="emoji" value="🎭"></label><label>Description<textarea name="description"></textarea></label><label>Price<input type="number" min="0" name="price" value="1000"></label><label>Currency<select name="currency"><option value="xc">XC</option><option value="xcrystals">XCrystals</option></select></label><label>Stock (-1 unlimited)<input type="number" min="-1" name="stock" value="-1"></label><label>Enabled<select name="enabled"><option value="1">Yes</option><option value="0">No</option></select></label></div><button>Save Role</button></form></section></details><section class="panel"><h2>Roles for Sale</h2><div class="library">{% for r in rows %}<article class="library-card"><h3>{{r['emoji']}} {{r['name']}}</h3><p>{{r['description']}}</p><div class="statline">Role: &lt;@&{{r['role_id']}}&gt;<br>Price: {{r['price']}} {{r['currency']}}<br>Stock: {{'Unlimited' if r['stock']<0 else r['stock']}}</div><form class="fields" method="post" action="{{url_for('save_role_offer')}}"><input type="hidden" name="id" value="{{r['id']}}"><input name="name" value="{{r['name']}}"><input name="role_id" value="{{r['role_id']}}"><input name="emoji" value="{{r['emoji']}}"><textarea name="description">{{r['description']}}</textarea><input type="number" min="0" name="price" value="{{r['price']}}"><select name="currency"><option value="xc" {% if r['currency']=='xc' %}selected{% endif %}>XC</option><option value="xcrystals" {% if r['currency']=='xcrystals' %}selected{% endif %}>XCrystals</option></select><input type="number" min="-1" name="stock" value="{{r['stock']}}"><select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select><button>Save</button></form></article>{% endfor %}</div></section>"""
    body=body.replace('<button>Save</button></form></article>', '<div class="actions"><button name="action" value="save">Save</button><button class="danger" name="action" value="delete" onclick="return confirm(\'Delete this role offer?\')">Delete</button></div></form></article>')
    return admin_page("Role Shop",body,rows=rows,enabled=enabled)


@app.post("/role-shop/save")
@login_required
def save_role_offer():
    db=get_db(); role_id=mention_id(request.form["role_id"]); row_id=request.form.get("id"); action=request.form.get("action","save")
    if action=="delete" and row_id:
        row=db.execute("SELECT name FROM role_shop WHERE id=?",(int(row_id),)).fetchone()
        db.execute("DELETE FROM role_shop WHERE id=?",(int(row_id),));db.commit()
        flash(f"{row['name'] if row else 'Role offer'} deleted.")
        db.close();return redirect(url_for("role_shop_control"))
    values=(role_id,request.form["name"].strip(),request.form.get("emoji","🎭").strip() or "🎭",request.form.get("description","").strip(),settings_admin.integer(request.form["price"], "price", 0, 999999999),request.form.get("currency","xc"),max(-1,int(request.form["stock"])),int(request.form.get("enabled",1)))
    try:
        if not role_id: raise ValueError("Enter a valid Discord @Role mention or Role ID.")
        if row_id: db.execute("UPDATE role_shop SET role_id=?,name=?,emoji=?,description=?,price=?,currency=?,stock=?,enabled=? WHERE id=?",values+(int(row_id),))
        else: db.execute("INSERT INTO role_shop(role_id,name,emoji,description,price,currency,stock,enabled) VALUES(?,?,?,?,?,?,?,?)",values)
        db.commit(); flash("Role offer saved.")
    except (ValueError,sqlite3.IntegrityError) as error: flash(str(error))
    db.close(); return redirect(url_for("role_shop_control"))


@app.post("/system/<key>/toggle")
@login_required
def toggle_system(key):
    allowed=set(settings_admin.TOGGLE_KEYS)
    if key not in allowed: return "Invalid system",400
    destination = request.form.get('return_to', 'home')
    if destination not in {'home','settings','casino_control','market_control','auction_control','recipes_control','finance_control','role_shop_control','item_shop_control'}:
        destination = 'home'
    return save_economy_form((key,), destination, values={key: request.form.get('enabled', '')})


@app.route("/command-access", methods=["GET","POST"])
@login_required
def command_access():
    db=get_db()
    if request.method=="POST":
        command=request.form["command_name"]; mode=request.form.get("access_mode","everyone")
        if mode not in {"everyone","admin","roles","users"}: mode="everyone"
        ids=lambda key: ",".join(filter(None,(mention_id(x) for value in request.form.getlist(key) for x in value.replace(";",",").split(","))))
        scope=request.form.get("cooldown_scope","user"); scope=scope if scope in {"user","guild","global"} else "user"
        values=(mode,ids("allowed_role_ids"),ids("allowed_user_ids"),int(request.form.get("enabled",1)),ids("blocked_role_ids"),
            ids("allowed_channel_ids"),ids("blocked_channel_ids"),settings_admin.integer(request.form.get("cooldown_seconds", 0), "cooldown_seconds", 0, 999999999),scope,
            ids("cooldown_bypass_role_ids"),request.form.get("required_permissions","").strip(),int(request.form.get("log_usage",0)),command)
        db.execute("""UPDATE command_permissions SET access_mode=?,allowed_role_ids=?,allowed_user_ids=?,enabled=?,blocked_role_ids=?,
            allowed_channel_ids=?,blocked_channel_ids=?,cooldown_seconds=?,cooldown_scope=?,cooldown_bypass_role_ids=?,required_permissions=?,log_usage=? WHERE command_name=?""",values)
        db.commit(); flash(f"/{command} command access settings saved.")
    rows=db.execute("SELECT * FROM command_permissions ORDER BY command_name").fetchall(); db.close(); server_channels=discord_server_text_channels()
    body="""<section class="panel"><h2>🔐 Command Access</h2><div class="top-actions"><input type="search" id="command-search" aria-label="Search commands" class="command-search" placeholder="Search commands, for example: attack"><span class="badge" id="command-count" role="status">{{rows|length}} Commands</span></div><div class="notice">Enter role and user IDs separated by commas; select channels below. On desktop, hold Ctrl (Windows) or Command (Mac) to select multiple channels. Administrators bypass role and cooldown rules, but disabled commands stay disabled.</div><p id="command-search-empty" class="notice" role="status" hidden>No matching commands. Try another name.</p><div class="library command-grid">{% for r in rows %}<article class="library-card command-card" data-command="{{r['command_name']|lower}}"><h3>/{{r['command_name']}}</h3><form class="fields" method="post"><input type="hidden" name="command_name" value="{{r['command_name']}}"><div class="fields-grid"><label>Command Status<select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select></label><label>Who Can Use<select name="access_mode"><option value="everyone" {% if r['access_mode']=='everyone' %}selected{% endif %}>Everyone</option><option value="admin" {% if r['access_mode']=='admin' %}selected{% endif %}>Administrators Only</option><option value="roles" {% if r['access_mode']=='roles' %}selected{% endif %}>Selected Roles</option><option value="users" {% if r['access_mode']=='users' %}selected{% endif %}>Selected Users</option></select></label><label>Allowed Roles<input name="allowed_role_ids" value="{{r['allowed_role_ids']}}"></label><label>Blocked Roles<input name="blocked_role_ids" value="{{r['blocked_role_ids']}}"></label><label>Allowed Users<input name="allowed_user_ids" value="{{r['allowed_user_ids']}}"></label><label>Allowed Channels<select multiple name="allowed_channel_ids">{% for c in server_channels %}<option value="{{c['id']}}" {% if c['id'] in r['allowed_channel_ids'].split(',') %}selected{% endif %}># {{c['name']}}</option>{% endfor %}</select></label><label>Blocked Channels<select multiple name="blocked_channel_ids">{% for c in server_channels %}<option value="{{c['id']}}" {% if c['id'] in r['blocked_channel_ids'].split(',') %}selected{% endif %}># {{c['name']}}</option>{% endfor %}</select></label><label>Cooldown Seconds<input type="number" min="0" name="cooldown_seconds" value="{{r['cooldown_seconds']}}"></label><label>Cooldown Scope<select name="cooldown_scope"><option value="user" {% if r['cooldown_scope']=='user' %}selected{% endif %}>Per User</option><option value="guild" {% if r['cooldown_scope']=='guild' %}selected{% endif %}>Server-wide</option><option value="global" {% if r['cooldown_scope']=='global' %}selected{% endif %}>Global</option></select></label><label>Cooldown Bypass Roles<input name="cooldown_bypass_role_ids" value="{{r['cooldown_bypass_role_ids']}}"></label><label>Required Discord Permissions<input name="required_permissions" value="{{r['required_permissions']}}" placeholder="manage_roles, manage_messages"></label><label>Discord Usage Log<select name="log_usage"><option value="1" {% if r['log_usage'] %}selected{% endif %}>On</option><option value="0" {% if not r['log_usage'] %}selected{% endif %}>Off</option></select></label></div><button>Save Command</button></form></article>{% endfor %}</div></section>"""
    body += '''<script>(() => {
      const search = document.getElementById('command-search');
      const cards = [...document.querySelectorAll('.command-card')];
      const filter = () => {
        let query = search.value.trim().toLowerCase();
        if (query.startsWith('/')) query = query.slice(1);
        let visible = 0;
        cards.forEach(card => {
          card.hidden = !card.dataset.command.includes(query);
          if (!card.hidden) visible++;
        });
        document.getElementById('command-count').textContent = visible + ' / ' + cards.length + ' Commands';
        document.getElementById('command-search-empty').hidden = visible !== 0;
      };
      search.addEventListener('input', filter);
      filter();
    })();</script>'''
    return admin_page("Command Access",body,rows=rows,server_channels=server_channels)


@app.route("/log-settings",methods=["GET","POST"])
@login_required
def log_settings_control():
    db=get_db()
    if request.method=="POST":
        log_type=request.form["log_type"]; channel=mention_id(request.form.get("channel_id","")) or "0"; mention=mention_id(request.form.get("mention_role_id","")) or ""
        db.execute("""UPDATE log_settings SET enabled=?,channel_id=?,log_success=?,log_failure=?,mention_role_id=? WHERE log_type=?""",
            (int(request.form.get("enabled",0)),channel,int(request.form.get("log_success",0)),int(request.form.get("log_failure",0)),mention,log_type)); db.commit(); flash(f"{log_type.title()} log settings saved.")
    rows=db.execute("SELECT * FROM log_settings ORDER BY log_type").fetchall(); db.close(); server_channels=discord_server_text_channels()
    body="""<section class="panel"><h2>📨 Log Settings System</h2><div class="notice">Choose a server channel. Command logs are sent immediately when Usage Log is enabled in Command Access.</div><div class="library">{% for r in rows %}<article class="library-card"><h3>{{r['log_type']|title}} Logs</h3><form class="fields" method="post"><input type="hidden" name="log_type" value="{{r['log_type']}}"><label>Status<select name="enabled"><option value="1" {% if r['enabled'] %}selected{% endif %}>Enabled</option><option value="0" {% if not r['enabled'] %}selected{% endif %}>Disabled</option></select></label><label>Discord Channel<select name="channel_id"><option value="0">No Discord Channel</option>{% for channel in server_channels %}<option value="{{channel['id']}}" {% if r['channel_id']==channel['id'] %}selected{% endif %}># {{channel['name']}}</option>{% endfor %}</select></label><label>Mention @Role / ID<input name="mention_role_id" value="{{r['mention_role_id']}}"></label><label>Success Events<select name="log_success"><option value="1" {% if r['log_success'] %}selected{% endif %}>Log</option><option value="0" {% if not r['log_success'] %}selected{% endif %}>Ignore</option></select></label><label>Failure Events<select name="log_failure"><option value="1" {% if r['log_failure'] %}selected{% endif %}>Log</option><option value="0" {% if not r['log_failure'] %}selected{% endif %}>Ignore</option></select></label><button>Save Log</button></form></article>{% endfor %}</div></section>"""
    return admin_page("Log Settings",body,rows=rows,server_channels=server_channels)


@app.route("/dashboard-access", methods=["GET", "POST"])
@login_required
def dashboard_access():
    if session.get("dashboard_role") not in {"owner", "admin", "council"}:
        abort(403)
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save"); role_text = request.form.get("role_id", "")
        role_id = mention_id(role_text)
        if action == "code_create":
            label = request.form.get("code_label", "Moderator").strip()[:80] or "Moderator"
            try:
                valid_days = settings_admin.integer(request.form.get("code_days", "90"), "code_days", 0, 365)
            except ValueError:
                valid_days = 90
            raw_code = "MOD-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(16))
            now = int(time.time()); expiry = now + valid_days * 86400 if valid_days else 0
            db.execute("""INSERT INTO dashboard_access_codes(code_hash,label,access_level,enabled,created_at,expires_at)
                VALUES(?,?, 'admin',1,?,?)""", (hashlib.sha256(raw_code.encode()).hexdigest(), label, now, expiry))
            db.commit()
            session["new_dashboard_access_code"] = raw_code
            flash(f"New personal code created for {label}. Copy it now; it will not be shown again after you leave this page.")
        elif action == "code_revoke":
            try:
                code_id = int(request.form.get("code_id", "0"))
            except ValueError:
                code_id = 0
            if code_id:
                db.execute("UPDATE dashboard_access_codes SET enabled=0 WHERE id=?", (code_id,)); db.commit()
                flash("Moderator access code revoked.")
        elif action == "code_delete":
            try:
                code_id = int(request.form.get("code_id", "0"))
            except ValueError:
                code_id = 0
            if code_id:
                db.execute("DELETE FROM dashboard_access_codes WHERE id=?", (code_id,)); db.commit()
                flash("Access-code record permanently deleted.")
        elif action == "code_clear_revoked":
            db.execute("DELETE FROM dashboard_access_codes WHERE enabled=0"); db.commit()
            flash("All revoked access-code records were cleared.")
        elif action == "delete" and role_id:
            db.execute("DELETE FROM dashboard_role_access WHERE role_id=?", (role_id,)); db.commit(); flash("Dashboard role access removed.")
        elif role_id:
            level = request.form.get("access_level", "viewer")
            if level not in {"admin", "economy_manager", "council", "viewer"}: level = "viewer"
            label = request.form.get("label", "").strip()[:100]
            db.execute("""INSERT INTO dashboard_role_access(role_id,access_level,label,enabled) VALUES(?,?,?,1)
                ON CONFLICT(role_id) DO UPDATE SET access_level=excluded.access_level,label=excluded.label,enabled=1""", (role_id, level, label))
            db.commit(); flash("Dashboard role access saved.")
        else:
            flash("Enter a valid Discord @Role mention or Role ID.")
    raw_rows = db.execute("SELECT * FROM dashboard_role_access ORDER BY access_level,label").fetchall()
    code_rows = db.execute("SELECT id,label,enabled,created_at,last_used_at,expires_at FROM dashboard_access_codes ORDER BY enabled DESC,id DESC").fetchall()
    db.close()
    role_names = {row["id"]: row["name"] for row in discord_server_roles()}
    rows = [dict(row, role_name=role_names.get(row["role_id"], row["label"] or "Unknown Discord Role")) for row in raw_rows]
    code_rows = [dict(row,
        created_text=datetime.fromtimestamp(row["created_at"]).strftime("%d %b %Y, %H:%M"),
        last_used_text=(datetime.fromtimestamp(row["last_used_at"]).strftime("%d %b %Y, %H:%M") if row["last_used_at"] else "Never used"),
        expiry_text=(datetime.fromtimestamp(row["expires_at"]).strftime("%d %b %Y, %H:%M") if row["expires_at"] else "No expiry"),
    ) for row in code_rows]
    new_code = session.pop("new_dashboard_access_code", None)
    body = """<section class="panel"><h2>🔐 Dashboard Role Access</h2><div class="notice">Administrators and Moderators have full Dashboard control. Discord login stays signed in for up to 90 days unless they log out or clear browser data. For a trusted Moderator who prefers not to use Discord OAuth, create one personal access code below.</div><form class="fields" method="post"><input type="hidden" name="action" value="save"><div class="fields-grid"><label>Discord Role<select name="role_id" required><option value="">Choose a role…</option>{% for role in server_roles %}<option value="{{role['id']}}">@{{role['name']}}</option>{% endfor %}</select></label><label>Display Label<input name="label" placeholder="Moderator"></label><label>Access Level<select name="access_level"><option value="admin">Administrator / Moderator — full control</option><option value="economy_manager">Economy Manager — economy pages</option><option value="viewer">Viewer — Overview only</option></select></label></div><button>Save Role Access</button></form></section><section class="panel"><h2>🔑 Moderator Personal Access Codes</h2><div class="notice">Each code is for one trusted person. They can sign in from the Dashboard login page without Discord. Never share a code in public chat; revoke or delete it immediately if it is shared.</div>{% if new_code %}<div class="notice good"><b>Copy this code now:</b><br><code style="font-size:18px;letter-spacing:1px">{{new_code}}</code></div>{% endif %}<form class="fields" method="post"><input type="hidden" name="action" value="code_create"><div class="fields-grid"><label>Moderator name / label<input name="code_label" placeholder="McRoy" required></label><label>Valid days<input type="number" min="0" max="365" name="code_days" value="90"><small>Use 0 for no expiry.</small></label></div><button class="purple">Create Personal Access Code</button></form>{% if code_rows %}<div class="top-actions"><form method="post"><input type="hidden" name="action" value="code_clear_revoked"><button class="secondary">Clear Revoked Results</button></form></div>{% endif %}<div class="library">{% for c in code_rows %}<article class="library-card"><h3>🔑 {{c['label']}}</h3><div class="statline"><span class="{{'ok' if c['enabled'] else 'muted'}}">{{'● Active' if c['enabled'] else '○ Revoked'}}</span> · Full Dashboard access<br><span class="muted">Created:</span> {{c['created_text']}}<br><span class="muted">Last used:</span> {{c['last_used_text']}}<br><span class="muted">Expires:</span> {{c['expiry_text']}}</div><form method="post"><input type="hidden" name="action" value="{{'code_revoke' if c['enabled'] else 'code_delete'}}"><input type="hidden" name="code_id" value="{{c['id']}}"><button class="{{'danger' if c['enabled'] else 'secondary'}}">{{'Revoke Code' if c['enabled'] else 'Delete Result'}}</button></form></article>{% endfor %}</div></section><section class="panel"><h2>Authorized Discord Roles</h2><div class="library">{% for r in rows %}<article class="library-card"><h3>@{{r['role_name']}}</h3><div class="statline">Label: {{r['label'] or r['role_name']}}<br>Access: <b>{{r['access_level']}}</b></div><form method="post"><input type="hidden" name="action" value="delete"><input type="hidden" name="role_id" value="{{r['role_id']}}"><button class="danger">Remove Access</button></form></article>{% endfor %}</div></section>"""
    return admin_page("Dashboard Access", body, rows=rows, code_rows=code_rows, new_code=new_code)




@app.route('/research', methods=['GET','POST'])
@login_required
def research_control():
    if session.get('dashboard_role') not in {'owner','admin','council'}:
        abort(403)
    if request.method == 'POST':
        if not hmac.compare_digest(str(session.get('research_csrf','')), request.form.get('csrf','')) or not session.get('research_csrf'):
            abort(403)
        if request.form.get('action') == 'settings':
            return save_economy_form(settings_admin.RESEARCH_KEYS, "research_control",
                values={"tier8_enabled": request.form.get("enabled", ""), "tier8_queue_limit": request.form.get("queue_limit", "")})
        return save_economy_form((), "research_control", technology=request.form.get("code", ""))
    db = get_db()
    try:
        session.setdefault('research_csrf', secrets.token_urlsafe(24))
        rows = db.execute('SELECT * FROM tier8_technologies ORDER BY branch,code').fetchall()
        jobs = db.execute('''SELECT j.*,p.nation_name,t.name FROM tier8_research_jobs j
            LEFT JOIN players p ON p.user_id=j.user_id JOIN tier8_technologies t ON t.code=j.code
            ORDER BY j.id DESC LIMIT 100''').fetchall()
        settings = {r['key']:r['value'] for r in db.execute("SELECT * FROM economy_settings WHERE key IN ('tier8_enabled','tier8_queue_limit')")}
        body = '''<section class="panel"><h2>Research · T8</h2><p>Benefits activate automatically. Queue prices and finish times are saved when confirmed. Costs and durations below are per level: level 2 costs twice the base.</p>
        <form method="post" class="fields"><input type="hidden" name="csrf" value="{{session.research_csrf}}"><input type="hidden" name="action" value="settings">
        <label>Research and benefits enabled (0/1)<input name="enabled" type="number" min="0" max="1" value="{{settings.tier8_enabled}}" required></label>
        <label>Queue limit<input name="queue_limit" type="number" min="1" max="6" value="{{settings.tier8_queue_limit}}" required></label><button>Save settings</button></form></section>
        <section class="panel"><h2>Technology balance</h2><div class="library">{% for r in rows %}<article class="library-card"><h3>{{r.name}}</h3><p>{{r.description}}</p>
        <form method="post" class="fields"><input type="hidden" name="csrf" value="{{session.research_csrf}}"><input type="hidden" name="code" value="{{r.code}}">
        {% for key,label in [('max_level','Maximum level'),('base_cost','Base XC cost'),('seconds','Base duration (seconds)'),('bonus_per_level','Bonus per level (%)'),('bonus_cap','Maximum benefit (%)'),('enabled','Enabled (0/1)')] %}
        <label>{{label}}<input type="number" name="{{key}}" min="{{technology_limits(r.code)[key][0]}}" max="{{technology_limits(r.code)[key][1]}}" value="{{r[key]}}" required><small class="setting-hint">{{technology_limits(r.code)[key][0]}}–{{technology_limits(r.code)[key][1]}} · {{r.name}}. Existing order costs and times stay unchanged.</small></label>{% endfor %}<button>Save technology</button></form></article>{% endfor %}</div></section>
        <section class="panel"><h2>Recent research orders</h2><table><tr><th>Nation</th><th>Research</th><th>Paid XC</th><th>Status</th><th>Ready</th></tr>
        {% for j in jobs %}<tr><td>{{j.nation_name}}</td><td>{{j.name}} · Lv {{j.level}}</td><td>{{j.paid}}</td><td>{{'active benefit' if j.status == 'queued' and j.ready_at <= now else j.status}}</td><td>{{j.ready_at|timestamp}}</td></tr>{% endfor %}</table></section>'''
        return admin_page('Research', body, rows=rows, jobs=jobs, settings=settings, now=int(time.time()), technology_limits=settings_admin.technology_limits)
    finally:
        db.close()


if __name__ == "__main__":
    # Listen on the Tailscale/LAN interface by default so a trusted device on
    # the same tailnet can reach the Dashboard. Set DASHBOARD_HOST=127.0.0.1
    # to restrict it to this machine only, or DASHBOARD_PORT to change 5000.
    app.run(
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", os.getenv("PORT", "5000"))),
        debug=False,
    )
