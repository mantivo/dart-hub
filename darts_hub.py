import os
APP_DB = os.environ.get("DB_PATH", "darts.db")

from flask import Flask, request, redirect, url_for, render_template_string, jsonify
import sqlite3, os, time, datetime

app = Flask(__name__)

# -------------------------
# DB helpers
# -------------------------
def db():
    conn = sqlite3.connect(APP_DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        team_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        PRIMARY KEY (team_id, player_id),
        FOREIGN KEY (team_id) REFERENCES teams(id),
        FOREIGN KEY (player_id) REFERENCES players(id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_type TEXT NOT NULL,      -- "501" (MVP)
        mode TEXT NOT NULL,           -- "ffa" | "teams"
        team_a_id INTEGER,
        team_b_id INTEGER,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        winner_player_id INTEGER,
        winner_team_id INTEGER,
        notes TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS game_players (
        game_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        team_side TEXT,               -- "A" | "B" | NULL
        final_score INTEGER,          -- for 501: remaining points at end
        won INTEGER DEFAULT 0,
        PRIMARY KEY (game_id, player_id),
        FOREIGN KEY (game_id) REFERENCES games(id),
        FOREIGN KEY (player_id) REFERENCES players(id)
    )""")

    conn.commit()
    conn.close()

def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")

def q_all(sql, args=()):
    conn = db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows

def q_one(sql, args=()):
    conn = db()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return row

def exec_sql(sql, args=()):
    conn = db()
    cur = conn.cursor()
    cur.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last

# -------------------------
# In-memory "current game" state
# (Persisted when you hit Finish)
# -------------------------
STATE = {
    "active_game_id": None,     # games.id
    "game_type": None,          # "501"
    "mode": None,               # "ffa" | "teams"
    "players": [],              # list of player dicts {id,name}
    "teamA": None,              # {id,name,members:[{id,name}]}
    "teamB": None,
    "current_turn_idx": 0,      # for ffa: index into players
    "team_turn": "A",           # for teams: A/B
    "team_member_idx": 0,       # rotates within roster
    "scores": {},               # key: player_id or team side
    "start_points": 501,
    "winner": None              # player_id or "A"/"B"
}

def reset_active():
    STATE.update({
        "active_game_id": None,
        "game_type": None,
        "mode": None,
        "players": [],
        "teamA": None,
        "teamB": None,
        "current_turn_idx": 0,
        "team_turn": "A",
        "team_member_idx": 0,
        "scores": {},
        "start_points": 501,
        "winner": None
    })

def ensure_active():
    # If server restarts, active in-memory game is gone.
    # MVP: that’s ok for party use.
    pass

def current_turn_label():
    if not STATE["active_game_id"]:
        return "No active game"
    if STATE["mode"] == "ffa":
        if not STATE["players"]:
            return "—"
        p = STATE["players"][STATE["current_turn_idx"] % len(STATE["players"])]
        return f"Turn: {p['name']}"
    if STATE["mode"] == "teams":
        side = STATE["team_turn"]
        team = STATE["teamA"] if side == "A" else STATE["teamB"]
        members = team["members"]
        idx = STATE["team_member_idx"] % max(1, len(members))
        who = members[idx]["name"] if members else "(no members)"
        return f"Turn: {team['name']} ({who})"
    return "—"

def advance_turn():
    if STATE["mode"] == "ffa":
        STATE["current_turn_idx"] = (STATE["current_turn_idx"] + 1) % max(1, len(STATE["players"]))
        return
    if STATE["mode"] == "teams":
        if STATE["team_turn"] == "A":
            STATE["team_turn"] = "B"
        else:
            STATE["team_turn"] = "A"
            STATE["team_member_idx"] += 1

def apply_501_turn(turn_points: int):
    if not STATE["active_game_id"] or STATE["winner"]:
        return
    turn_points = max(0, min(180, int(turn_points)))

    # Bust rule MVP: if below 0 or exactly 1 => bust (no change)
    if STATE["mode"] == "ffa":
        p = STATE["players"][STATE["current_turn_idx"] % len(STATE["players"])]
        pid = p["id"]
        start_score = STATE["scores"][pid]
        new_score = start_score - turn_points
        bust = (new_score < 0 or new_score == 1)
        if not bust:
            STATE["scores"][pid] = new_score
            if new_score == 0:
                STATE["winner"] = pid
        advance_turn()
        return

    if STATE["mode"] == "teams":
        side = STATE["team_turn"]
        start_score = STATE["scores"][side]
        new_score = start_score - turn_points
        bust = (new_score < 0 or new_score == 1)
        if not bust:
            STATE["scores"][side] = new_score
            if new_score == 0:
                STATE["winner"] = side
        advance_turn()
        return

def finish_game():
    """Persist current game results into DB and clear active state."""
    gid = STATE["active_game_id"]
    if not gid:
        return

    ended = now_iso()
    winner_player_id = None
    winner_team_id = None

    if STATE["mode"] == "ffa":
        if isinstance(STATE["winner"], int):
            winner_player_id = STATE["winner"]

        exec_sql("UPDATE games SET ended_at=?, winner_player_id=? WHERE id=?",
                 (ended, winner_player_id, gid))

        # Write game_players final scores + won
        for p in STATE["players"]:
            pid = p["id"]
            final_score = int(STATE["scores"].get(pid, STATE["start_points"]))
            won = 1 if (winner_player_id == pid) else 0
            exec_sql("""UPDATE game_players SET final_score=?, won=? WHERE game_id=? AND player_id=?""",
                     (final_score, won, gid, pid))

    elif STATE["mode"] == "teams":
        if STATE["winner"] in ("A", "B"):
            # map to team id
            winner_team_id = STATE["teamA"]["id"] if STATE["winner"] == "A" else STATE["teamB"]["id"]

        exec_sql("UPDATE games SET ended_at=?, winner_team_id=? WHERE id=?",
                 (ended, winner_team_id, gid))

        # Each participating player gets a final_score = team remaining
        scoreA = int(STATE["scores"].get("A", STATE["start_points"]))
        scoreB = int(STATE["scores"].get("B", STATE["start_points"]))
        for member in STATE["teamA"]["members"]:
            exec_sql("""UPDATE game_players SET final_score=?, won=? WHERE game_id=? AND player_id=?""",
                     (scoreA, 1 if STATE["winner"] == "A" else 0, gid, member["id"]))
        for member in STATE["teamB"]["members"]:
            exec_sql("""UPDATE game_players SET final_score=?, won=? WHERE game_id=? AND player_id=?""",
                     (scoreB, 1 if STATE["winner"] == "B" else 0, gid, member["id"]))

    reset_active()

# -------------------------
# HTML Templates (minimal)
# -------------------------
BASE_CSS = """
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#0b0f14;color:#e8eef7}
a{color:#9ec5ff;text-decoration:none}
.wrap{padding:16px;max-width:1100px;margin:0 auto}
.card{background:#0f1722;border:1px solid #1d2a3a;border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>*{flex:1 1 auto}
input,select,button{font-size:16px;padding:10px;border-radius:12px;border:1px solid #2a3c52;background:#0b1220;color:#e8eef7}
button{cursor:pointer}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid #2a3c52;background:#101c2b;font-size:13px}
.big{font-size:40px;font-weight:900}
.muted{opacity:.75;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.active{border-color:#6aa3ff;box-shadow:0 0 0 2px rgba(106,163,255,.15) inset}
.win{border-color:#7CFFB0;box-shadow:0 0 0 2px rgba(124,255,176,.12) inset}
table{width:100%;border-collapse:collapse}
td,th{padding:6px 8px;border-bottom:1px solid #1d2a3a;text-align:left}
</style>
"""

# -------------------------
# Pages: Home
# -------------------------
@app.get("/")
def home():
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">Darts Hub</div>
        <div class="muted">Saved players • stats • team names • big display</div>
      </div>

      <div class="card">
        <div class="row">
          <a class="pill" href="/display">/display (monitor)</a>
          <a class="pill" href="/control">/control (phone)</a>
          <a class="pill" href="/players">/players</a>
          <a class="pill" href="/teams">/teams</a>
          <a class="pill" href="/history">/history</a>
        </div>
      </div>
    </div>
    """)

# -------------------------
# Players
# -------------------------
@app.get("/players")
def players_page():
    players = q_all("""
        SELECT p.id, p.name,
               (SELECT MAX(g.started_at)
                FROM game_players gp
                JOIN games g ON g.id = gp.game_id
                WHERE gp.player_id = p.id) AS last_played
        FROM players p
        ORDER BY p.name COLLATE NOCASE
    """)
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">Players</div>
        <div class="muted"><a href="/">Home</a></div>
      </div>

      <div class="card">
        <form method="post" action="/players/add">
          <div class="row">
            <input name="name" placeholder="Add player name (e.g., Alex)" required />
            <button type="submit">Add Player</button>
          </div>
        </form>
      </div>

      <div class="card">
        <table>
          <thead><tr><th>Name</th><th>Last Played</th><th></th></tr></thead>
          <tbody>
            {''.join([f"<tr><td><b>{p['name']}</b></td><td>{p['last_played'] or '—'}</td><td><a class='pill' href='/player/{p['id']}'>View stats</a></td></tr>" for p in players])}
          </tbody>
        </table>
      </div>
    </div>
    """)

@app.post("/players/add")
def players_add():
    name = (request.form.get("name") or "").strip()
    if name:
        try:
            exec_sql("INSERT INTO players(name, created_at) VALUES(?,?)", (name, now_iso()))
        except sqlite3.IntegrityError:
            pass
    return redirect(url_for("players_page"))

@app.get("/player/<int:player_id>")
def player_detail(player_id):
    p = q_one("SELECT id, name, created_at FROM players WHERE id=?", (player_id,))
    if not p:
        return "Not found", 404

    stats = q_one("""
        SELECT
          COUNT(*) AS games_played,
          SUM(CASE WHEN gp.won=1 THEN 1 ELSE 0 END) AS wins,
          MAX(g.started_at) AS last_played
        FROM game_players gp
        JOIN games g ON g.id = gp.game_id
        WHERE gp.player_id=?
    """, (player_id,))

    recent = q_all("""
        SELECT g.id, g.game_type, g.mode, g.started_at, g.ended_at, gp.final_score, gp.won
        FROM game_players gp
        JOIN games g ON g.id = gp.game_id
        WHERE gp.player_id=?
        ORDER BY g.started_at DESC
        LIMIT 15
    """, (player_id,))

    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">{p['name']}</div>
        <div class="muted"><a href="/players">← Players</a></div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="pill">Games played</div>
          <div class="big">{stats['games_played'] or 0}</div>
        </div>
        <div class="card">
          <div class="pill">Wins</div>
          <div class="big">{stats['wins'] or 0}</div>
        </div>
      </div>

      <div class="card">
        <div class="pill">Last played</div>
        <div class="big" style="font-size:22px">{stats['last_played'] or "—"}</div>
      </div>

      <div class="card">
        <div class="pill">Recent games</div>
        <table>
          <thead><tr><th>Date</th><th>Game</th><th>Mode</th><th>Final score</th><th>Result</th></tr></thead>
          <tbody>
            {''.join([f"<tr><td>{r['started_at']}</td><td>{r['game_type']}</td><td>{r['mode']}</td><td>{r['final_score'] if r['final_score'] is not None else '—'}</td><td>{'WIN' if r['won']==1 else ''}</td></tr>" for r in recent])}
          </tbody>
        </table>
      </div>
    </div>
    """)

# -------------------------
# Teams
# -------------------------
@app.get("/teams")
def teams_page():
    teams = q_all("""
        SELECT t.id, t.name,
               (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id=t.id) AS members
        FROM teams t
        ORDER BY t.name COLLATE NOCASE
    """)
    players = q_all("SELECT id, name FROM players ORDER BY name COLLATE NOCASE")
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">Teams</div>
        <div class="muted"><a href="/">Home</a></div>
      </div>

      <div class="card">
        <form method="post" action="/teams/add">
          <div class="row">
            <input name="name" placeholder="New team name (e.g., The Bullseyes)" required />
            <button type="submit">Create Team</button>
          </div>
        </form>
      </div>

      <div class="card">
        <table>
          <thead><tr><th>Team</th><th>Members</th><th></th></tr></thead>
          <tbody>
            {''.join([f"<tr><td><b>{t['name']}</b></td><td>{t['members']}</td><td><a class='pill' href='/team/{t['id']}'>Edit</a></td></tr>" for t in teams])}
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="pill">Tip</div>
        <div class="muted">Create teams once, then in /control you can start Team vs Team using saved names.</div>
      </div>
    </div>
    """, teams=teams, players=players)

@app.post("/teams/add")
def teams_add():
    name = (request.form.get("name") or "").strip()
    if name:
        try:
            exec_sql("INSERT INTO teams(name, created_at) VALUES(?,?)", (name, now_iso()))
        except sqlite3.IntegrityError:
            pass
    return redirect(url_for("teams_page"))

@app.get("/team/<int:team_id>")
def team_detail(team_id):
    t = q_one("SELECT id, name FROM teams WHERE id=?", (team_id,))
    if not t:
        return "Not found", 404
    players = q_all("SELECT id, name FROM players ORDER BY name COLLATE NOCASE")
    members = q_all("""
        SELECT p.id, p.name
        FROM team_members tm
        JOIN players p ON p.id = tm.player_id
        WHERE tm.team_id=?
        ORDER BY p.name COLLATE NOCASE
    """, (team_id,))
    member_ids = {m["id"] for m in members}

    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">{t['name']}</div>
        <div class="muted"><a href="/teams">← Teams</a></div>
      </div>

      <div class="card">
        <form method="post" action="/team/{t['id']}/members">
          <div class="pill">Select members</div>
          <div class="muted">Check players and save.</div>
          <div style="margin-top:10px; display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px;">
            {''.join([
                f"<label class='pill'><input type='checkbox' name='player_id' value='{p['id']}' {'checked' if p['id'] in member_ids else ''}/> {p['name']}</label>"
                for p in players
            ])}
          </div>
          <div style="margin-top:10px" class="row">
            <button type="submit">Save Members</button>
            <a class="pill" href="/control">Go to /control</a>
          </div>
        </form>
      </div>
    </div>
    """, t=t)

@app.post("/team/<int:team_id>/members")
def team_members_save(team_id):
    ids = request.form.getlist("player_id")
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    for pid in ids:
        try:
            cur.execute("INSERT INTO team_members(team_id, player_id) VALUES(?,?)", (team_id, int(pid)))
        except:
            pass
    conn.commit()
    conn.close()
    return redirect(url_for("team_detail", team_id=team_id))

# -------------------------
# Control + Display
# -------------------------
@app.get("/control")
def control():
    players = q_all("SELECT id, name FROM players ORDER BY name COLLATE NOCASE")
    teams = q_all("SELECT id, name FROM teams ORDER BY name COLLATE NOCASE")
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">Control</div>
        <div class="row">
          <a class="pill" href="/display" target="_blank">Open Display</a>
          <a class="pill" href="/players">Players</a>
          <a class="pill" href="/teams">Teams</a>
          <a class="pill" href="/">Home</a>
        </div>
        <div class="pill" style="margin-top:10px;">{current_turn_label()}</div>
      </div>

      <div class="card">
        <div class="pill">Start a 501 game</div>
        <form method="post" action="/start_501_ffa" style="margin-top:10px;">
          <div class="muted">Free-for-all: choose players</div>
          <div style="margin-top:10px; display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px;">
            {''.join([f"<label class='pill'><input type='checkbox' name='player_id' value='{p['id']}'/> {p['name']}</label>" for p in players])}
          </div>
          <div class="row" style="margin-top:10px;">
            <input name="start_points" type="number" value="501" min="101" max="1001"/>
            <button type="submit">Start FFA 501</button>
          </div>
        </form>

        <hr style="border:0;border-top:1px solid #1d2a3a;margin:14px 0;">

        <form method="post" action="/start_501_teams">
          <div class="muted">Teams: pick Team A and Team B (saved team names)</div>
          <div class="row" style="margin-top:10px;">
            <select name="team_a_id" required>
              <option value="">Team A…</option>
              {''.join([f"<option value='{t['id']}'>{t['name']}</option>" for t in teams])}
            </select>
            <select name="team_b_id" required>
              <option value="">Team B…</option>
              {''.join([f"<option value='{t['id']}'>{t['name']}</option>" for t in teams])}
            </select>
          </div>
          <div class="row" style="margin-top:10px;">
            <input name="start_points" type="number" value="501" min="101" max="1001"/>
            <button type="submit">Start Teams 501</button>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="pill">Scoring</div>
        <div class="muted">Enter turn total (0–180). Bust handled automatically.</div>
        <form method="post" action="/turn_501" style="margin-top:10px;">
          <div class="row">
            <input name="turn_points" type="number" min="0" max="180" placeholder="e.g. 60" />
            <button type="submit">Submit Turn</button>
            <button type="submit" formaction="/next_turn">Next Turn</button>
          </div>
        </form>

        <div class="row" style="margin-top:10px;">
          <form method="post" action="/finish" style="flex:1 1 auto;">
            <button type="submit">Finish & Save Game</button>
          </form>
          <form method="post" action="/reset_active" style="flex:1 1 auto;">
            <button type="submit">Reset Active Game</button>
          </form>
        </div>
      </div>
    </div>
    """)

@app.get("/display")
def display():
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="row" style="align-items:center;justify-content:space-between;">
          <div>
            <div class="big" style="font-size:34px;">Darts Display</div>
            <div class="muted">Auto-refreshes • open /control on your phone</div>
          </div>
          <div class="pill">{current_turn_label()}</div>
        </div>
      </div>

      {render_display_body()}
    </div>

    <script>
      setTimeout(()=>location.reload(), 900);
    </script>
    """)

def render_display_body():
    if not STATE["active_game_id"]:
        return "<div class='card'><div class='big'>No game</div><div class='muted'>Start a game from /control</div></div>"

    if STATE["mode"] == "ffa":
        cards = []
        for i, p in enumerate(STATE["players"]):
            pid = p["id"]
            score = STATE["scores"].get(pid, STATE["start_points"])
            cls = "card"
            if i == (STATE["current_turn_idx"] % len(STATE["players"])):
                cls += " active"
            if STATE["winner"] == pid:
                cls += " win"
            cards.append(f"<div class='{cls}'><div class='pill'>{p['name']}</div><div class='big'>{score}</div></div>")
        win_banner = ""
        if isinstance(STATE["winner"], int):
            wname = next((p["name"] for p in STATE["players"] if p["id"] == STATE["winner"]), "Winner")
            win_banner = f"<div class='card win'><div class='big'>{wname} wins ✅</div></div>"
        return win_banner + "<div class='grid'>" + "".join(cards) + "</div>"

    if STATE["mode"] == "teams":
        A = STATE["teamA"]; B = STATE["teamB"]
        scoreA = STATE["scores"].get("A", STATE["start_points"])
        scoreB = STATE["scores"].get("B", STATE["start_points"])
        clsA = "card" + (" active" if STATE["team_turn"] == "A" else "") + (" win" if STATE["winner"] == "A" else "")
        clsB = "card" + (" active" if STATE["team_turn"] == "B" else "") + (" win" if STATE["winner"] == "B" else "")

        banner = ""
        if STATE["winner"] in ("A", "B"):
            wteam = A["name"] if STATE["winner"] == "A" else B["name"]
            banner = f"<div class='card win'><div class='big'>{wteam} wins ✅</div></div>"

        return banner + f"""
        <div class="grid">
          <div class="{clsA}">
            <div class="pill">{A['name']}</div>
            <div class="big">{scoreA}</div>
            <div class="muted">Players: {", ".join([m["name"] for m in A["members"]]) or "—"}</div>
          </div>
          <div class="{clsB}">
            <div class="pill">{B['name']}</div>
            <div class="big">{scoreB}</div>
            <div class="muted">Players: {", ".join([m["name"] for m in B["members"]]) or "—"}</div>
          </div>
        </div>
        """

    return "<div class='card'>Unknown mode</div>"

# -------------------------
# Start game actions
# -------------------------
@app.post("/start_501_ffa")
def start_501_ffa():
    ids = request.form.getlist("player_id")
    start_points = max(101, min(1001, int(request.form.get("start_points") or 501)))
    if len(ids) < 2:
        return redirect(url_for("control"))

    selected = q_all(
        "SELECT id, name FROM players WHERE id IN ({}) ORDER BY name COLLATE NOCASE".format(",".join(["?"] * len(ids))),
        tuple(int(x) for x in ids)
    )

    gid = exec_sql("""
        INSERT INTO games(game_type, mode, started_at) VALUES(?,?,?)
    """, ("501", "ffa", now_iso()))

    # game_players
    for p in selected:
        exec_sql("INSERT INTO game_players(game_id, player_id, team_side) VALUES(?,?,NULL)", (gid, p["id"]))

    reset_active()
    STATE["active_game_id"] = gid
    STATE["game_type"] = "501"
    STATE["mode"] = "ffa"
    STATE["players"] = [{"id": int(p["id"]), "name": p["name"]} for p in selected]
    STATE["start_points"] = start_points
    STATE["scores"] = {int(p["id"]): start_points for p in selected}
    STATE["current_turn_idx"] = 0
    return redirect(url_for("control"))

@app.post("/start_501_teams")
def start_501_teams():
    team_a_id = int(request.form.get("team_a_id") or 0)
    team_b_id = int(request.form.get("team_b_id") or 0)
    start_points = max(101, min(1001, int(request.form.get("start_points") or 501)))
    if team_a_id == 0 or team_b_id == 0 or team_a_id == team_b_id:
        return redirect(url_for("control"))

    A = q_one("SELECT id, name FROM teams WHERE id=?", (team_a_id,))
    B = q_one("SELECT id, name FROM teams WHERE id=?", (team_b_id,))
    if not A or not B:
        return redirect(url_for("control"))

    A_members = q_all("""
        SELECT p.id, p.name FROM team_members tm
        JOIN players p ON p.id=tm.player_id
        WHERE tm.team_id=?
        ORDER BY p.name COLLATE NOCASE
    """, (team_a_id,))
    B_members = q_all("""
        SELECT p.id, p.name FROM team_members tm
        JOIN players p ON p.id=tm.player_id
        WHERE tm.team_id=?
        ORDER BY p.name COLLATE NOCASE
    """, (team_b_id,))

    if len(A_members) == 0 or len(B_members) == 0:
        return redirect(url_for("control"))

    gid = exec_sql("""
        INSERT INTO games(game_type, mode, team_a_id, team_b_id, started_at)
        VALUES(?,?,?,?,?)
    """, ("501", "teams", team_a_id, team_b_id, now_iso()))

    for m in A_members:
        exec_sql("INSERT INTO game_players(game_id, player_id, team_side) VALUES(?,?,?)",
                 (gid, m["id"], "A"))
    for m in B_members:
        exec_sql("INSERT INTO game_players(game_id, player_id, team_side) VALUES(?,?,?)",
                 (gid, m["id"], "B"))

    reset_active()
    STATE["active_game_id"] = gid
    STATE["game_type"] = "501"
    STATE["mode"] = "teams"
    STATE["teamA"] = {"id": int(A["id"]), "name": A["name"], "members": [{"id": int(x["id"]), "name": x["name"]} for x in A_members]}
    STATE["teamB"] = {"id": int(B["id"]), "name": B["name"], "members": [{"id": int(x["id"]), "name": x["name"]} for x in B_members]}
    STATE["start_points"] = start_points
    STATE["scores"] = {"A": start_points, "B": start_points}
    STATE["team_turn"] = "A"
    STATE["team_member_idx"] = 0
    return redirect(url_for("control"))

# -------------------------
# Turn + Finish actions
# -------------------------
@app.post("/turn_501")
def turn_501():
    if not STATE["active_game_id"]:
        return redirect(url_for("control"))
    pts = int(request.form.get("turn_points") or 0)
    apply_501_turn(pts)
    return redirect(url_for("control"))

@app.post("/next_turn")
def next_turn():
    if STATE["active_game_id"]:
        advance_turn()
    return redirect(url_for("control"))

@app.post("/finish")
def finish():
    finish_game()
    return redirect(url_for("control"))

@app.post("/reset_active")
def reset_active_route():
    reset_active()
    return redirect(url_for("control"))

# -------------------------
# History (quick view)
# -------------------------
@app.get("/history")
def history():
    games = q_all("""
        SELECT g.id, g.game_type, g.mode, g.started_at, g.ended_at,
               ta.name AS teamA, tb.name AS teamB,
               wp.name AS winner_player,
               wt.name AS winner_team
        FROM games g
        LEFT JOIN teams ta ON ta.id=g.team_a_id
        LEFT JOIN teams tb ON tb.id=g.team_b_id
        LEFT JOIN players wp ON wp.id=g.winner_player_id
        LEFT JOIN teams wt ON wt.id=g.winner_team_id
        ORDER BY g.started_at DESC
        LIMIT 30
    """)
    return render_template_string(f"""
    {BASE_CSS}
    <div class="wrap">
      <div class="card">
        <div class="big">Game History</div>
        <div class="muted"><a href="/">Home</a></div>
      </div>
      <div class="card">
        <table>
          <thead><tr><th>Date</th><th>Game</th><th>Mode</th><th>Winner</th></tr></thead>
          <tbody>
            {''.join([f"<tr><td>{g['started_at']}</td><td>{g['game_type']}</td><td>{g['mode']}</td><td>{(g['winner_player'] or g['winner_team'] or '—')}</td></tr>" for g in games])}
          </tbody>
        </table>
      </div>
    </div>
    """)

# -------------------------
# Startup
# -------------------------
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
