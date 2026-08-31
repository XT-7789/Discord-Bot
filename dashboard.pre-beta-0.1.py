import os
import hmac
import sqlite3
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template_string,
    request,
    session,
    url_for
)

load_dotenv()

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
DATABASE_PATH = Path(__file__).resolve().parent / "xwar.db"

if not DASHBOARD_PASSWORD:
    raise ValueError(
        "DASHBOARD_PASSWORD was not found. Add it to your .env file."
    )

app = Flask(__name__)
app.secret_key = os.urandom(32)


def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


LOGIN_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>X War Admin Login</title>
<style>
body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #0c1220; color: #edf3ff; font-family: Arial, sans-serif;
}
.box {
    width: min(380px, 88%); padding: 30px; border-radius: 14px;
    background: #141f33; border: 1px solid #294567;
}
h1 { margin-top: 0; }
input {
    width: 100%; margin: 8px 0 16px; padding: 12px; box-sizing: border-box;
    color: white; background: #0c1220; border: 1px solid #38587d;
    border-radius: 8px;
}
button {
    width: 100%; padding: 12px; border: 0; border-radius: 8px;
    background: #3976d2; color: white; font-weight: bold; cursor: pointer;
}
.error { color: #ff8d8d; }
</style>
</head>
<body>
<div class="box">
    <h1>⚔️ X War Admin</h1>
    <p>Admin Dashboard Login</p>
    {% with messages = get_flashed_messages() %}
        {% for message in messages %}
            <p class="error">{{ message }}</p>
        {% endfor %}
    {% endwith %}
    <form method="post">
        <label>Dashboard Password</label>
        <input type="password" name="password" required autofocus>
        <button type="submit">Log In</button>
    </form>
</div>
</body>
</html>
"""

DASHBOARD_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>X War Admin Dashboard</title>
<style>
* { box-sizing: border-box; }
body {
    margin: 0; background: #0c1220; color: #edf3ff;
    font-family: Arial, sans-serif;
}
header {
    padding: 26px 7%; background: #152947; border-bottom: 1px solid #345476;
    display: flex; justify-content: space-between; align-items: center;
}
h1 { margin: 0; }
a { color: #9ec9ff; text-decoration: none; }
main { width: min(1250px, 92%); margin: 28px auto; }
.stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px; margin-bottom: 24px;
}
.card, section {
    background: #141f33; border: 1px solid #294567; border-radius: 12px;
}
.card { padding: 18px; }
.card span { color: #9bb1ce; font-size: 13px; }
.card strong { display: block; font-size: 26px; margin-top: 8px; }
section { overflow: hidden; }
h2 { margin: 0; padding: 18px; border-bottom: 1px solid #294567; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 13px; text-align: left; border-bottom: 1px solid #233852; }
th { color: #9bb1ce; font-size: 12px; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
button {
    padding: 8px 12px; border: 0; border-radius: 6px; cursor: pointer;
    color: white; background: #3976d2;
}
.conquered { color: #ff8585; }
.active { color: #58e69a; }
</style>
</head>
<body>
<header>
    <div>
        <h1>⚔️ X War Admin Dashboard</h1>
        <p>Player and economy management</p>
    </div>
    <a href="{{ url_for('logout') }}">Log out</a>
</header>

<main>
    <div class="stats">
        <div class="card"><span>Total Nations</span><strong>{{ total_nations }}</strong></div>
        <div class="card"><span>Total Land</span><strong>{{ total_land }}</strong></div>
        <div class="card"><span>Total Power</span><strong>{{ total_power }}</strong></div>
        <div class="card"><span>Active Capitals</span><strong>{{ active_capitals }}</strong></div>
    </div>

    <section>
        <h2>Nation Management</h2>
        <table>
            <thead>
                <tr>
                    <th>Nation</th>
                    <th>Alliance</th>
                    <th>Land</th>
                    <th>Credits</th>
                    <th>Power</th>
                    <th>Capital</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {% for player in players %}
                <tr>
                    <td>{{ player["nation_name"] }}</td>
                    <td>
                        {% if player["alliance_name"] %}
                            [{{ player["alliance_tag"] }}] {{ player["alliance_name"] }}
                        {% else %}
                            None
                        {% endif %}
                    </td>
                    <td>{{ player["land"] }}</td>
                    <td>${{ player["money"] }}</td>
                    <td>{{ player["power"] }}</td>
                    <td>
                        {% if player["capital_health"] > 0 %}
                            <span class="active">{{ player["capital_health"] }} HP</span>
                        {% else %}
                            <span class="conquered">Conquered</span>
                        {% endif %}
                    </td>
                    <td>
                        <a href="{{ url_for('edit_player', user_id=player['user_id']) }}">
                            <button>Edit</button>
                        </a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </section>
</main>
</body>
</html>
"""

EDIT_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Nation · X War Admin</title>
<style>
* { box-sizing: border-box; }
body {
    margin: 0; background: #0c1220; color: #edf3ff;
    font-family: Arial, sans-serif;
}
header { padding: 24px 7%; background: #152947; border-bottom: 1px solid #345476; }
main { width: min(900px, 92%); margin: 28px auto; }
form {
    padding: 24px; background: #141f33; border: 1px solid #294567;
    border-radius: 12px;
}
.grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
}
label { display: block; color: #aebfd8; font-size: 13px; }
input {
    width: 100%; margin-top: 7px; padding: 10px; color: white;
    background: #0c1220; border: 1px solid #38587d; border-radius: 7px;
}
button {
    margin-top: 24px; padding: 11px 18px; border: 0; border-radius: 7px;
    background: #3976d2; color: white; font-weight: bold; cursor: pointer;
}
a { color: #9ec9ff; }
.message { color: #58e69a; }
</style>
</head>
<body>
<header>
    <a href="{{ url_for('home') }}">← Back to Dashboard</a>
    <h1>Edit: {{ player["nation_name"] }}</h1>
</header>

<main>
    {% with messages = get_flashed_messages() %}
        {% for message in messages %}
            <p class="message">{{ message }}</p>
        {% endfor %}
    {% endwith %}

    <form method="post">
        <div class="grid">
            <label>Nation Name
                <input name="nation_name" value="{{ player['nation_name'] }}" required>
            </label>

            <label>Capital Name
                <input name="capital_name" value="{{ player['capital_name'] }}" required>
            </label>

            <label>War Credits
                <input type="number" min="0" name="money" value="{{ player['money'] }}" required>
            </label>

            <label>Land
                <input type="number" min="0" name="land" value="{{ player['land'] }}" required>
            </label>

            <label>Land Army
                <input type="number" min="0" name="land_army" value="{{ player['land_army'] }}" required>
            </label>

            <label>Air Army
                <input type="number" min="0" name="air_army" value="{{ player['air_army'] }}" required>
            </label>

            <label>Navy
                <input type="number" min="0" name="navy" value="{{ player['navy'] }}" required>
            </label>

            <label>Capital Health (0–100)
                <input type="number" min="0" max="100" name="capital_health"
                       value="{{ player['capital_health'] }}" required>
            </label>

            <label>Iron
                <input type="number" min="0" name="iron" value="{{ player['iron'] }}" required>
            </label>

            <label>Gold
                <input type="number" min="0" name="gold" value="{{ player['gold'] }}" required>
            </label>

            <label>Oil
                <input type="number" min="0" name="oil" value="{{ player['oil'] }}" required>
            </label>

            <label>Iron Mines
                <input type="number" min="0" name="iron_mines" value="{{ player['iron_mines'] }}" required>
            </label>

            <label>Gold Mines
                <input type="number" min="0" name="gold_mines" value="{{ player['gold_mines'] }}" required>
            </label>

            <label>Oil Mines
                <input type="number" min="0" name="oil_mines" value="{{ player['oil_mines'] }}" required>
            </label>
        </div>

        <button type="submit">Save Changes</button>
    </form>
</main>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if hmac.compare_digest(password, DASHBOARD_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("home"))

        flash("Incorrect password.")

    return render_template_string(LOGIN_PAGE)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    connection = get_db()

    players = connection.execute("""
        SELECT
            players.*,
            alliances.name AS alliance_name,
            alliances.tag AS alliance_tag,
            (players.land_army + players.air_army * 3 + players.navy * 5) AS power
        FROM players
        LEFT JOIN alliance_members
            ON players.user_id = alliance_members.user_id
        LEFT JOIN alliances
            ON alliance_members.alliance_id = alliances.id
        ORDER BY power DESC, players.land DESC
    """).fetchall()

    connection.close()

    return render_template_string(
        DASHBOARD_PAGE,
        players=players,
        total_nations=len(players),
        total_land=sum(player["land"] for player in players),
        total_power=sum(player["power"] for player in players),
        active_capitals=sum(
            1 for player in players if player["capital_health"] > 0
        )
    )


@app.route("/player/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_player(user_id):
    connection = get_db()
    player = connection.execute(
        "SELECT * FROM players WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if player is None:
        connection.close()
        return "Player not found.", 404

    if request.method == "POST":
        try:
            nation_name = request.form["nation_name"].strip()
            capital_name = request.form["capital_name"].strip()

            if not 3 <= len(nation_name) <= 30:
                raise ValueError("Nation name must be 3–30 characters.")

            if not 3 <= len(capital_name) <= 30:
                raise ValueError("Capital name must be 3–30 characters.")

            values = {
                "money": max(0, int(request.form["money"])),
                "land": max(0, int(request.form["land"])),
                "land_army": max(0, int(request.form["land_army"])),
                "air_army": max(0, int(request.form["air_army"])),
                "navy": max(0, int(request.form["navy"])),
                "capital_health": min(
                    100,
                    max(0, int(request.form["capital_health"]))
                ),
                "iron": max(0, int(request.form["iron"])),
                "gold": max(0, int(request.form["gold"])),
                "oil": max(0, int(request.form["oil"])),
                "iron_mines": max(0, int(request.form["iron_mines"])),
                "gold_mines": max(0, int(request.form["gold_mines"])),
                "oil_mines": max(0, int(request.form["oil_mines"]))
            }

            connection.execute("""
                UPDATE players
                SET nation_name = ?,
                    capital_name = ?,
                    money = ?,
                    land = ?,
                    land_army = ?,
                    air_army = ?,
                    navy = ?,
                    capital_health = ?,
                    iron = ?,
                    gold = ?,
                    oil = ?,
                    iron_mines = ?,
                    gold_mines = ?,
                    oil_mines = ?
                WHERE user_id = ?
            """, (
                nation_name,
                capital_name,
                values["money"],
                values["land"],
                values["land_army"],
                values["air_army"],
                values["navy"],
                values["capital_health"],
                values["iron"],
                values["gold"],
                values["oil"],
                values["iron_mines"],
                values["gold_mines"],
                values["oil_mines"],
                user_id
            ))

            connection.commit()
            flash("Player data saved.")

        except ValueError as error:
            flash(str(error))

        player = connection.execute(
            "SELECT * FROM players WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    connection.close()
    return render_template_string(EDIT_PAGE, player=player)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)