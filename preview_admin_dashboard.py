"""Local UI acceptance harness. Empty schema copy, sample data, no Discord login.

Run directly for browser QA only. Never imported by the production Dashboard.
The temporary database is removed when the local server exits.
"""
from contextlib import closing
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch


def main():
    from werkzeug.serving import make_server
    from flask import session
    with tempfile.TemporaryDirectory(prefix='xbot-admin-preview-') as folder:
        path = Path(folder) / 'preview.db'
        with closing(sqlite3.connect(f"file:{Path(__file__).with_name('xwar.db').as_posix()}?mode=ro", uri=True)) as source:
            schema = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        with closing(sqlite3.connect(path)) as db:
            for (statement,) in schema:
                db.execute(statement)
            db.commit()
        connect = sqlite3.connect
        def isolated(filename, *args, **kwargs):
            return connect(path if str(filename).endswith('xwar.db') else filename, *args, **kwargs)
        with patch('dotenv.load_dotenv'), patch.dict(os.environ, {
            'DASHBOARD_PASSWORD': 'preview-only', 'DASHBOARD_SECRET': os.urandom(32).hex(),
            'DISCORD_TOKEN': '', 'DISCORD_CLIENT_ID': '', 'DISCORD_CLIENT_SECRET': '',
            'DISCORD_GUILD_ID': '', 'DASHBOARD_OWNER_ID': ''}), patch('sqlite3.connect', isolated):
            dashboard = importlib.import_module('dashboard')
            dashboard.startup_db.close()
        dashboard.DATABASE_PATH = path
        dashboard.discord_server_roles = lambda: []
        dashboard.discord_server_members = lambda: []
        dashboard.discord_server_text_channels = lambda: []
        def local_identity():
            session['dashboard_role'] = 'admin'
            session['discord_user_id'] = 'codex-preview'
            session['discord_name'] = 'LOCAL UI TEST'
        dashboard.app.before_request_funcs[None].insert(0, local_identity)
        with closing(dashboard.get_db()) as db:
            db.execute('INSERT INTO players(user_id,nation_name,display_name,xc,money,bank_xc) VALUES(?,?,?,?,?,?)',
                (990000000000000088, 'Sample Nation', 'Sample Player', 2500, 8000, 12000))
            db.commit()
        server = make_server('127.0.0.1', 0, dashboard.app)
        print(f'LOCAL_PREVIEW=http://127.0.0.1:{server.server_port}/tier6-economy', flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
