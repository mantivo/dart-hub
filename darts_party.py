from flask import Flask, request, jsonify, render_template_string
import time, random

app = Flask(__name__)

CRICKET_NUMS = [20, 19, 18, 17, 16, 15, "BULL"]

STATE = {
    "mode": "ffa",          # "ffa" | "teams" | "championship"
    "game": None,           # "501" | "cricket" | "atc" | "leaderboard" | "match"
    "players": [],          # used for ffa
    "current": 0,
    "started": False,
    "created_at": time.time(),
    "settings": {
        "501_start": 501,
        "501_double_out": False,
        "match_start": 301,         # used for championship matches
    },
    "teams": {              # used for teams mode
        "A": [],
        "B": [],
        "team_current": 0,  # 0..(max roster-1) to rotate A[i],B[i]
        "team_turn": "A",   # "A" or "B"
    },
    "tournament": {         # used for championship mode
        "players": [],
        "round": 1,
        "matches": [],      # list of {"p1","p2","winner":None}
        "current_match": 0,
        "champion": None,
    },
    "data": {}              # per-game data
}

# ---------------------------
# Helpers
# ---------------------------
def reset_state():
    STATE["mode"] = "ffa"
    STATE["game"] = None
    STATE["players"] = []
    STATE["current"] = 0
    STATE["started"] = False
    STATE["settings"] = {"501_start": 501, "501_double_out": False, "match_start": 301}
    STATE["teams"] = {"A": [], "B": [], "team_current": 0, "team_turn": "A"}
    STATE["tournament"] = {"players": [], "round": 1, "matches": [], "current_match": 0, "champion": None}
    STATE["data"] = {}
    STATE["created_at"] = time.time()

def safe_int(val, default=0):
    try: return int(val)
    except: return default

def ensure_players():
    if STATE["mode"] == "ffa":
        if not STATE["players"]:
            STATE["players"] = ["Player 1", "Player 2"]
    elif STATE["mode"] == "teams":
        if not STATE["teams"]["A"]:
            STATE["teams"]["A"] = [f"A{i+1}" for i in range(10)]
        if not STATE["teams"]["B"]:
            STATE["teams"]["B"] = [f"B{i+1}" for i in range(10)]
    elif STATE["mode"] == "championship":
        if not STATE["tournament"]["players"]:
            STATE["tournament"]["players"] = [f"P{i+1}" for i in range(8)]

def current_player_label():
    ensure_players()
    if STATE["mode"] == "ffa":
        return STATE["players"][STATE["current"] % len(STATE["players"])]
    if STATE["mode"] == "teams":
        A = STATE["teams"]["A"]; B = STATE["teams"]["B"]
        idx = STATE["teams"]["team_current"] % max(len(A), len(B))
        turn = STATE["teams"]["team_turn"]
        roster = A if turn == "A" else B
        if idx >= len(roster):  # if uneven rosters, wrap
            idx = idx % len(roster)
        return f"Team {turn}: {roster[idx]}"
    # championship shows current match
    t = STATE["tournament"]
    if t["champion"]:
        return f"Champion: {t['champion']}"
    if not t["matches"]:
        return "No matches"
    m = t["matches"][t["current_match"]]
    return f"Match: {m['p1']} vs {m['p2']}"

def advance_turn():
    if STATE["mode"] == "ffa":
        if STATE["players"]:
            STATE["current"] = (STATE["current"] + 1) % len(STATE["players"])
        return

    if STATE["mode"] == "teams":
        # alternate A/B each turn, advance player index after both have played
        turn = STATE["teams"]["team_turn"]
        if turn == "A":
            STATE["teams"]["team_turn"] = "B"
        else:
            STATE["teams"]["team_turn"] = "A"
            STATE["teams"]["team_current"] = STATE["teams"]["team_current"] + 1
        return

def init_game(game):
    ensure_players()
    STATE["game"] = game
    STATE["started"] = True
    STATE["data"] = {}

    if STATE["mode"] == "ffa":
        players = STATE["players"]
        if game == "501":
            start = int(STATE["settings"].get("501_start", 501))
            STATE["data"] = {"scores": {p: start for p in players}, "last": {p: None for p in players}, "winner": None}
        elif game == "leaderboard":
            STATE["data"] = {"points": {p: 0 for p in players}}
        elif game == "atc":
            STATE["data"] = {"target": {p: 1 for p in players}, "winner": None}
        elif game == "cricket":
            STATE["data"] = {
                "marks": {p: {str(n): 0 for n in CRICKET_NUMS} for p in players},
                "points": {p: 0 for p in players},
                "winner": None
            }

    if STATE["mode"] == "teams":
        if game == "501":
            start = int(STATE["settings"].get("501_start", 501))
            STATE["data"] = {"team_scores": {"A": start, "B": start}, "last": {"A": None, "B": None}, "winner": None}
        elif game == "leaderboard":
            STATE["data"] = {"team_points": {"A": 0, "B": 0}}
        elif game == "atc":
            STATE["data"] = {"team_target": {"A": 1, "B": 1}, "winner": None}
        elif game == "cricket":
            STATE["data"] = {
                "marks": {"A": {str(n): 0 for n in CRICKET_NUMS}, "B": {str(n): 0 for n in CRICKET_NUMS}},
                "points": {"A": 0, "B": 0},
                "winner": None
            }
        STATE["teams"]["team_current"] = 0
        STATE["teams"]["team_turn"] = "A"

    if STATE["mode"] == "championship":
        # championship uses a special internal "match" 501-style subtract (match_start default 301)
        start = int(STATE["settings"].get("match_start", 301))
        STATE["game"] = "match"
        build_round_if_needed()
        init_match_scores(start)

# ---------------------------
# Championship logic
# ---------------------------
def build_round_if_needed():
    t = STATE["tournament"]
    if t["champion"]:
        return
    if not t["matches"]:
        # create first round from t["players"]
        players = t["players"][:]
        random.shuffle(players)
        # if odd, add a BYE
        if len(players) % 2 == 1:
            players.append("BYE")
        matches = []
        for i in range(0, len(players), 2):
            matches.append({"p1": players[i], "p2": players[i+1], "winner": None})
        t["matches"] = matches
        t["current_match"] = 0
        t["round"] = 1

def init_match_scores(start):
    t = STATE["tournament"]
    m = t["matches"][t["current_match"]]
    p1, p2 = m["p1"], m["p2"]
    # BYE auto-advance
    if p1 == "BYE" and p2 != "BYE":
        m["winner"] = p2
        advance_match()
        return
    if p2 == "BYE" and p1 != "BYE":
        m["winner"] = p1
        advance_match()
        return

    STATE["data"] = {
        "match_start": start,
        "scores": {p1: start, p2: start},
        "turn": p1,  # alternates within match
        "winner": None,
        "last": {p1: None, p2: None}
    }

def current_match_players():
    t = STATE["tournament"]
    if not t["matches"]:
        return None, None
    m = t["matches"][t["current_match"]]
    return m["p1"], m["p2"]

def advance_match():
    t = STATE["tournament"]
    # move to next match in round
    t["current_match"] += 1
    if t["current_match"] >= len(t["matches"]):
        # round complete -> build next round from winners
        winners = [m["winner"] for m in t["matches"] if m["winner"]]
        if len(winners) == 1:
            t["champion"] = winners[0]
            STATE["data"] = {"winner": winners[0]}
            return
        t["round"] += 1
        t["current_match"] = 0
        # pair winners
        if len(winners) % 2 == 1:
            winners.append("BYE")
        new_matches = []
        for i in range(0, len(winners), 2):
            new_matches.append({"p1": winners[i], "p2": winners[i+1], "winner": None})
        t["matches"] = new_matches

    # init next match
    start = int(STATE["settings"].get("match_start", 301))
    init_match_scores(start)

def match_add(score):
    if STATE["tournament"]["champion"]:
        return
    d = STATE["data"]
    if d.get("winner"):
        return

    p = d["turn"]
    score = max(0, min(180, int(score)))
    start_score = d["scores"][p]
    new_score = start_score - score

    bust = (new_score < 0 or new_score == 1)
    if new_score == 0 and STATE["settings"].get("501_double_out", False):
        if not (score == 50 or score % 2 == 0):
            bust = True

    if bust:
        d["last"][p] = f"BUST (tried {score})"
    else:
        d["scores"][p] = new_score
        d["last"][p] = f"-{score} → {new_score}"
        if new_score == 0:
            d["winner"] = p
            # set match winner
            t = STATE["tournament"]
            t["matches"][t["current_match"]]["winner"] = p

    # alternate turn within match
    p1, p2 = current_match_players()
    d["turn"] = p2 if p == p1 else p1

# ---------------------------
# Game logic (FFA + Teams)
# ---------------------------
def ffa_current_player():
    return STATE["players"][STATE["current"] % len(STATE["players"])]

def teams_current_team():
    return STATE["teams"]["team_turn"]  # "A" or "B"

def handle_501_add(score):
    if STATE["data"].get("winner"):
        return
    score = max(0, min(180, int(score)))

    if STATE["mode"] == "ffa":
        p = ffa_current_player()
        start_score = STATE["data"]["scores"][p]
        new_score = start_score - score

        bust = (new_score < 0 or new_score == 1)
        if new_score == 0 and STATE["settings"].get("501_double_out", False):
            if not (score == 50 or score % 2 == 0):
                bust = True

        if bust:
            STATE["data"]["last"][p] = f"BUST (tried {score})"
        else:
            STATE["data"]["scores"][p] = new_score
            STATE["data"]["last"][p] = f"-{score} → {new_score}"
            if new_score == 0:
                STATE["data"]["winner"] = p

        advance_turn()
        return

    if STATE["mode"] == "teams":
        team = teams_current_team()
        start_score = STATE["data"]["team_scores"][team]
        new_score = start_score - score

        bust = (new_score < 0 or new_score == 1)
        if new_score == 0 and STATE["settings"].get("501_double_out", False):
            if not (score == 50 or score % 2 == 0):
                bust = True

        if bust:
            STATE["data"]["last"][team] = f"BUST (tried {score})"
        else:
            STATE["data"]["team_scores"][team] = new_score
            STATE["data"]["last"][team] = f"-{score} → {new_score}"
            if new_score == 0:
                STATE["data"]["winner"] = f"Team {team}"

        advance_turn()
        return

def cricket_hit(number, hits):
    if STATE["data"].get("winner"):
        return
    num_key = str(number).upper()
    hits = max(0, min(3, int(hits)))

    if STATE["mode"] == "ffa":
        p = ffa_current_player()
        marks = STATE["data"]["marks"]
        pts = STATE["data"]["points"]
        if num_key not in marks[p]:
            return

        for _ in range(hits):
            if marks[p][num_key] < 3:
                marks[p][num_key] += 1
            else:
                not_closed = any(marks[o][num_key] < 3 for o in STATE["players"] if o != p)
                if not_closed:
                    val = 25 if num_key == "BULL" else int(num_key)
                    pts[p] += val

        all_closed = all(marks[p][str(n)] >= 3 for n in CRICKET_NUMS)
        if all_closed:
            max_other = max(pts[o] for o in STATE["players"] if o != p) if len(STATE["players"]) > 1 else 0
            if pts[p] >= max_other:
                STATE["data"]["winner"] = p

        advance_turn()
        return

    if STATE["mode"] == "teams":
        team = teams_current_team()
        marks = STATE["data"]["marks"]
        pts = STATE["data"]["points"]
        opp = "B" if team == "A" else "A"
        if num_key not in marks[team]:
            return

        for _ in range(hits):
            if marks[team][num_key] < 3:
                marks[team][num_key] += 1
            else:
                if marks[opp][num_key] < 3:
                    val = 25 if num_key == "BULL" else int(num_key)
                    pts[team] += val

        all_closed = all(marks[team][str(n)] >= 3 for n in CRICKET_NUMS)
        if all_closed and pts[team] >= pts[opp]:
            STATE["data"]["winner"] = f"Team {team}"

        advance_turn()
        return

def atc_hit(success):
    if STATE["data"].get("winner"):
        return

    if STATE["mode"] == "ffa":
        p = ffa_current_player()
        if success:
            t = STATE["data"]["target"][p]
            if t <= 20:
                STATE["data"]["target"][p] = t + 1
            else:
                STATE["data"]["winner"] = p
        advance_turn()
        return

    if STATE["mode"] == "teams":
        team = teams_current_team()
        if success:
            t = STATE["data"]["team_target"][team]
            if t <= 20:
                STATE["data"]["team_target"][team] = t + 1
            else:
                STATE["data"]["winner"] = f"Team {team}"
        advance_turn()
        return

def leaderboard_add(points):
    points = safe_int(points, 0)
    if STATE["mode"] == "ffa":
        p = ffa_current_player()
        STATE["data"]["points"][p] += points
        advance_turn()
        return
    if STATE["mode"] == "teams":
        team = teams_current_team()
        STATE["data"]["team_points"][team] += points
        advance_turn()
        return

# ---------------------------
# UI
# ---------------------------
DISPLAY_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Darts Party Display</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; background: #0b0f14; color: #e8eef7; }
    .top { padding: 18px 22px; display:flex; align-items:center; justify-content:space-between; border-bottom: 1px solid #1d2a3a;}
    .game { font-size: 22px; font-weight: 800; letter-spacing: 0.4px;}
    .meta { opacity: 0.85; font-size: 14px;}
    .wrap { padding: 18px 22px; }
    .card { background:#0f1722; border:1px solid #1d2a3a; border-radius: 14px; padding: 16px; margin-bottom: 14px; }
    .grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .pname { font-size: 18px; font-weight: 800; margin-bottom: 6px; }
    .big { font-size: 44px; font-weight: 900; }
    .small { opacity: 0.85; }
    .pill { display:inline-block; padding: 6px 10px; border-radius: 999px; border:1px solid #2a3c52; background:#101c2b; font-size: 13px; }
    .active { border-color: #6aa3ff; box-shadow: 0 0 0 2px rgba(106,163,255,0.15) inset; }
    .winner { border-color:#7CFFB0; box-shadow: 0 0 0 2px rgba(124,255,176,0.12) inset;}
    table { width:100%; border-collapse: collapse; }
    td, th { padding: 6px 8px; border-bottom: 1px solid #1d2a3a; text-align:left; }
    .muted { opacity:0.7; }
  </style>
</head>
<body>
  <div class="top">
    <div>
      <div class="game" id="title">Darts Party</div>
      <div class="meta" id="meta">Loading…</div>
    </div>
    <div class="pill" id="turn">Turn: —</div>
  </div>
  <div class="wrap" id="content"></div>

<script>
async function refresh(){
  const s = await (await fetch('/state')).json();
  document.getElementById('title').textContent =
    (s.mode ? s.mode.toUpperCase() : "FFA") + (s.game ? (" • " + s.game.toUpperCase()) : "");

  document.getElementById('meta').textContent =
    s.mode === "teams"
      ? ("Team A: " + s.teams.A.length + " players • Team B: " + s.teams.B.length + " players")
      : (s.mode === "championship"
          ? ("Round " + s.tournament.round + " • Match " + (s.tournament.current_match+1) + "/" + (s.tournament.matches.length||0))
          : ("Players: " + (s.players.join(", ") || "—")));

  document.getElementById('turn').textContent = s.turn_label;

  const c = document.getElementById('content');
  c.innerHTML = "";

  if (!s.started) {
    c.innerHTML = `<div class="card"><div class="pname">Not started</div><div class="small">Go to /control to set mode + start.</div></div>`;
    return;
  }

  // Championship winner
  if (s.mode === "championship" && s.tournament.champion) {
    c.innerHTML = `<div class="card winner"><div class="pname">Champion</div><div class="big">${s.tournament.champion}</div></div>`;
    return;
  }

  if (s.game === "501") {
    if (s.mode === "teams") {
      const w = s.data.winner;
      c.innerHTML = `
        ${w ? `<div class="card winner"><div class="pname">Winner</div><div class="big">${w}</div></div>` : ""}
        <div class="grid">
          <div class="card ${s.teams.team_turn==="A" ? "active" : ""} ${w==="Team A" ? "winner" : ""}">
            <div class="pname">Team A</div>
            <div class="big">${s.data.team_scores.A}</div>
            <div class="small">${s.data.last.A || ""}</div>
          </div>
          <div class="card ${s.teams.team_turn==="B" ? "active" : ""} ${w==="Team B" ? "winner" : ""}">
            <div class="pname">Team B</div>
            <div class="big">${s.data.team_scores.B}</div>
            <div class="small">${s.data.last.B || ""}</div>
          </div>
        </div>`;
      return;
    }

    // FFA
    const winner = s.data.winner;
    let html = winner ? `<div class="card winner"><div class="pname">Winner</div><div class="big">${winner}</div></div>` : "";
    html += `<div class="grid">` + s.players.map(p => `
      <div class="card ${(s.turn_label.includes(p) ? "active" : "")} ${(winner===p ? "winner":"")}">
        <div class="pname">${p}</div>
        <div class="big">${s.data.scores[p]}</div>
        <div class="small">${s.data.last[p]||""}</div>
      </div>`).join("") + `</div>`;
    c.innerHTML = html;
    return;
  }

  if (s.game === "leaderboard") {
    if (s.mode === "teams") {
      c.innerHTML = `
        <div class="grid">
          <div class="card ${s.teams.team_turn==="A" ? "active" : ""}">
            <div class="pname">Team A</div>
            <div class="big">${s.data.team_points.A}</div>
          </div>
          <div class="card ${s.teams.team_turn==="B" ? "active" : ""}">
            <div class="pname">Team B</div>
            <div class="big">${s.data.team_points.B}</div>
          </div>
        </div>`;
      return;
    } else {
      const rows = Object.entries(s.data.points).sort((a,b)=>b[1]-a[1]).map(([p,pt],i)=>(
        `<tr><td>${i+1}</td><td>${p}</td><td><b>${pt}</b></td></tr>`
      )).join("");
      c.innerHTML = `
        <div class="card">
          <div class="pname">Leaderboard</div>
          <table><thead><tr><th>#</th><th>Player</th><th>Points</th></tr></thead><tbody>${rows}</tbody></table>
        </div>`;
      return;
    }
  }

  if (s.game === "atc") {
    if (s.mode === "teams") {
      const w = s.data.winner;
      c.innerHTML = `
        ${w ? `<div class="card winner"><div class="pname">Winner</div><div class="big">${w}</div></div>` : ""}
        <div class="grid">
          <div class="card ${s.teams.team_turn==="A" ? "active" : ""} ${w==="Team A" ? "winner":""}">
            <div class="pname">Team A</div>
            <div class="big">Hit ${Math.min(s.data.team_target.A,20)}${s.data.team_target.A>20?" • BULL":""}</div>
          </div>
          <div class="card ${s.teams.team_turn==="B" ? "active" : ""} ${w==="Team B" ? "winner":""}">
            <div class="pname">Team B</div>
            <div class="big">Hit ${Math.min(s.data.team_target.B,20)}${s.data.team_target.B>20?" • BULL":""}</div>
          </div>
        </div>`;
      return;
    }
  }

  if (s.game === "cricket") {
    if (s.mode === "teams") {
      const w = s.data.winner;
      const nums = ["20","19","18","17","16","15","BULL"];
      function disp(v){ return v>=3 ? "✔✔✔" : ("•".repeat(v) + "—".repeat(3-v)); }
      const rowA = nums.map(n=>`<td>${disp(s.data.marks.A[n])}</td>`).join("");
      const rowB = nums.map(n=>`<td>${disp(s.data.marks.B[n])}</td>`).join("");
      c.innerHTML = `
        ${w ? `<div class="card winner"><div class="pname">Winner</div><div class="big">${w}</div></div>` : ""}
        <div class="grid">
          <div class="card ${s.teams.team_turn==="A" ? "active" : ""} ${w==="Team A" ? "winner":""}">
            <div class="pname">Team A Points</div><div class="big">${s.data.points.A}</div>
          </div>
          <div class="card ${s.teams.team_turn==="B" ? "active" : ""} ${w==="Team B" ? "winner":""}">
            <div class="pname">Team B Points</div><div class="big">${s.data.points.B}</div>
          </div>
        </div>
        <div class="card">
          <div class="pname">Cricket Marks (Teams)</div>
          <table>
            <thead><tr><th>Team</th><th>20</th><th>19</th><th>18</th><th>17</th><th>16</th><th>15</th><th>BULL</th><th>Pts</th></tr></thead>
            <tbody>
              <tr><td><b>A</b></td>${rowA}<td><b>${s.data.points.A}</b></td></tr>
              <tr><td><b>B</b></td>${rowB}<td><b>${s.data.points.B}</b></td></tr>
            </tbody>
          </table>
        </div>`;
      return;
    }
  }

  if (s.game === "match") {
    // championship match display
    const d = s.data;
    const t = s.tournament;
    const m = t.matches[t.current_match] || null;
    if (!m) { c.innerHTML = `<div class="card">No match</div>`; return; }
    const p1 = m.p1, p2 = m.p2;
    const w = t.champion || (m.winner ? m.winner : null);

    let html = `
      <div class="card">
        <div class="pname">Round ${t.round} • Match ${t.current_match+1}/${t.matches.length}</div>
        <div class="big">${p1} vs ${p2}</div>
      </div>`;

    if (m.winner) {
      html += `<div class="card winner"><div class="pname">Match Winner</div><div class="big">${m.winner}</div></div>`;
    }

    html += `
      <div class="grid">
        <div class="card ${d.turn===p1 ? "active":""}">
          <div class="pname">${p1}</div>
          <div class="big">${d.scores ? d.scores[p1] : ""}</div>
          <div class="small">${d.last ? (d.last[p1]||"") : ""}</div>
        </div>
        <div class="card ${d.turn===p2 ? "active":""}">
          <div class="pname">${p2}</div>
          <div class="big">${d.scores ? d.scores[p2] : ""}</div>
          <div class="small">${d.last ? (d.last[p2]||"") : ""}</div>
        </div>
      </div>`;

    // show bracket list
    const rows = t.matches.map((mm,i)=>`<tr><td>${i+1}</td><td>${mm.p1}</td><td>${mm.p2}</td><td><b>${mm.winner||""}</b></td></tr>`).join("");
    html += `
      <div class="card">
        <div class="pname">Current Round Bracket</div>
        <table><thead><tr><th>#</th><th>P1</th><th>P2</th><th>Winner</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;

    if (t.champion) {
      html = `<div class="card winner"><div class="pname">Champion</div><div class="big">${t.champion}</div></div>` + html;
    }

    c.innerHTML = html;
    return;
  }

  c.innerHTML = `<div class="card"><div class="pname">Started</div><div class="small">Use /control to enter scores.</div></div>`;
}

setInterval(refresh, 700);
refresh();
</script>
</body>
</html>
"""

CONTROL_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Darts Party Control</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; background: #0b0f14; color: #e8eef7; }
    .wrap { padding: 16px; max-width: 880px; margin: 0 auto;}
    .card { background:#0f1722; border:1px solid #1d2a3a; border-radius: 14px; padding: 14px; margin-bottom: 12px; }
    input, select, button { font-size: 16px; padding: 10px; border-radius: 12px; border: 1px solid #2a3c52; background:#0b1220; color:#e8eef7; }
    button { cursor: pointer; }
    .row { display:flex; gap:10px; flex-wrap:wrap; }
    .row > * { flex: 1 1 auto; }
    .title { font-size: 18px; font-weight: 900; margin-bottom: 10px; }
    .muted { opacity:0.75; font-size: 13px; }
    .pill { display:inline-block; padding: 6px 10px; border-radius: 999px; border:1px solid #2a3c52; background:#101c2b; font-size: 13px; }
    .danger { border-color: #ff6a6a; }
    .ok { border-color: #7CFFB0; }
    .btnrow button { flex: 1 1 120px; }
    .spacer { height: 6px; }
    a { color:#9ec5ff; text-decoration:none; }
    textarea { width:100%; min-height:72px; border-radius:12px; border:1px solid #2a3c52; background:#0b1220; color:#e8eef7; padding:10px; font-size:15px;}
  </style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="title">Quick Links</div>
    <div class="muted">Display: <a href="/display" target="_blank">/display</a></div>
    <div class="pill" id="turnPill">Turn: —</div>
  </div>

  <div class="card">
    <div class="title">1) Mode</div>
    <div class="row">
      <select id="modeSel">
        <option value="ffa">Free-for-all</option>
        <option value="teams">2 Teams</option>
        <option value="championship">Championships</option>
      </select>
      <button onclick="setMode()">Set Mode</button>
    </div>
    <div class="spacer"></div>
    <button class="danger" onclick="resetAll()">Reset Everything</button>
  </div>

  <div class="card" id="ffaCard">
    <div class="title">2A) Players (FFA)</div>
    <div class="muted">Comma-separated names.</div>
    <div class="row">
      <input id="players" placeholder="Alex, Sheila, ..." />
      <button onclick="setPlayers()">Set Players</button>
    </div>
  </div>

  <div class="card" id="teamsCard" style="display:none;">
    <div class="title">2B) Teams (2 Teams)</div>
    <div class="muted">One name per line.</div>
    <div class="spacer"></div>
    <div class="row">
      <div style="flex:1 1 320px;">
        <div class="pill">Team A</div>
        <textarea id="teamA" placeholder="A1\\nA2\\n..."></textarea>
      </div>
      <div style="flex:1 1 320px;">
        <div class="pill">Team B</div>
        <textarea id="teamB" placeholder="B1\\nB2\\n..."></textarea>
      </div>
    </div>
    <div class="spacer"></div>
    <button onclick="setTeams()">Set Teams</button>
    <button onclick="nextTurn()">Next Turn</button>
  </div>

  <div class="card" id="champCard" style="display:none;">
    <div class="title">2C) Championships</div>
    <div class="muted">One player per line (20 is fine). App will shuffle + pair matches.</div>
    <textarea id="tourPlayers" placeholder="Player 1\\nPlayer 2\\n..."></textarea>
    <div class="spacer"></div>
    <div class="row">
      <input id="mstart" type="number" value="301" min="101" max="501"/>
      <button onclick="saveMatch()">Save Match Start</button>
      <button class="ok" onclick="startGame()">Start Championships</button>
    </div>
    <div class="spacer"></div>
    <button onclick="nextMatch()">Next Match</button>
  </div>

  <div class="card">
    <div class="title">3) Start a Game</div>
    <div class="row">
      <select id="gameSel">
        <option value="501">501</option>
        <option value="cricket">Cricket</option>
        <option value="atc">Around the Clock</option>
        <option value="leaderboard">Leaderboard</option>
      </select>
      <button class="ok" onclick="startGame()">Start / Restart</button>
    </div>

    <div class="spacer"></div>
    <div class="muted"><b>501 Settings</b></div>
    <div class="row">
      <input id="s501" type="number" value="501" min="101" max="1001"/>
      <label class="pill"><input id="dout" type="checkbox" /> Double-out (simplified)</label>
      <button onclick="save501()">Save 501 Settings</button>
    </div>
  </div>

  <div class="card">
    <div class="title">4) Score Entry</div>
    <div id="controls">Loading…</div>
  </div>

  <div class="card">
    <div class="title">Status</div>
    <pre id="status" class="muted" style="white-space:pre-wrap;"></pre>
  </div>
</div>

<script>
async function post(path, data) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data||{})});
  return await r.json();
}

async function refresh() {
  const s = await (await fetch('/state')).json();
  document.getElementById('status').textContent = JSON.stringify(s, null, 2);
  document.getElementById('turnPill').textContent = s.turn_label;

  document.getElementById('modeSel').value = s.mode;
  document.getElementById('ffaCard').style.display = (s.mode === "ffa") ? "" : "none";
  document.getElementById('teamsCard').style.display = (s.mode === "teams") ? "" : "none";
  document.getElementById('champCard').style.display = (s.mode === "championship") ? "" : "none";

  const c = document.getElementById('controls');

  if (!s.started) {
    c.innerHTML = `<div class="muted">Set mode + players, then start a game.</div>`;
    return;
  }

  // Championship controls
  if (s.mode === "championship") {
    if (s.tournament.champion) {
      c.innerHTML = `<div class="pill ok">Champion: ${s.tournament.champion}</div>`;
      return;
    }
    if (s.game !== "match") {
      c.innerHTML = `<div class="muted">Press “Start Championships”.</div>`;
      return;
    }
    c.innerHTML = `
      <div class="muted">Enter turn total (0–180). Alternates between the two players.</div>
      <div class="row">
        <input id="vm" type="number" min="0" max="180" placeholder="e.g. 60" />
        <button class="ok" onclick="matchAdd()">Submit Turn</button>
      </div>
      <div class="spacer"></div>
      <div class="row btnrow">
        <button onclick="matchQuick(0)">0</button>
        <button onclick="matchQuick(60)">60</button>
        <button onclick="matchQuick(100)">100</button>
        <button onclick="matchQuick(140)">140</button>
        <button onclick="matchQuick(180)">180</button>
      </div>
      <div class="spacer"></div>
      <button onclick="nextMatch()">Next Match</button>
    `;
    return;
  }

  // Normal game score entry
  if (s.game === "501") {
    c.innerHTML = `
      <div class="muted">Enter turn total (0–180). Bust handled automatically.</div>
      <div class="row">
        <input id="v501" type="number" min="0" max="180" placeholder="e.g., 60 or 100" />
        <button class="ok" onclick="add501()">Submit Turn</button>
        <button onclick="nextTurn()">Next Turn</button>
      </div>
      <div class="spacer"></div>
      <div class="row btnrow">
        <button onclick="quick501(0)">0</button>
        <button onclick="quick501(60)">60</button>
        <button onclick="quick501(100)">100</button>
        <button onclick="quick501(140)">140</button>
        <button onclick="quick501(180)">180</button>
      </div>
    `;
    return;
  }

  if (s.game === "leaderboard") {
    c.innerHTML = `
      <div class="muted">Add points for the current turn.</div>
      <div class="row">
        <input id="vLB" type="number" placeholder="points" />
        <button class="ok" onclick="addLB()">Add</button>
        <button onclick="nextTurn()">Next Turn</button>
      </div>
      <div class="spacer"></div>
      <div class="row btnrow">
        <button onclick="quickLB(1)">+1</button>
        <button onclick="quickLB(5)">+5</button>
        <button onclick="quickLB(10)">+10</button>
        <button onclick="quickLB(25)">+25</button>
      </div>
    `;
    return;
  }

  if (s.game === "atc") {
    c.innerHTML = `
      <div class="muted">Did they hit their target?</div>
      <div class="row btnrow">
        <button class="ok" onclick="atc(true)">Hit ✅</button>
        <button class="danger" onclick="atc(false)">Miss ❌</button>
        <button onclick="nextTurn()">Next Turn</button>
      </div>
    `;
    return;
  }

  if (s.game === "cricket") {
    const nums = ["20","19","18","17","16","15","BULL"];
    const opts = nums.map(n => `<option value="${n}">${n}</option>`).join("");
    c.innerHTML = `
      <div class="muted">Select number and hits (1–3).</div>
      <div class="row">
        <select id="cNum">${opts}</select>
        <select id="cHits">
          <option value="1">1 hit (single)</option>
          <option value="2">2 hits (double)</option>
          <option value="3">3 hits (triple)</option>
        </select>
        <button class="ok" onclick="cricketHit()">Apply</button>
        <button onclick="nextTurn()">Next Turn</button>
      </div>
      <div class="spacer"></div>
      <div class="row btnrow">
        <button onclick="crQ('20',3)">T20</button>
        <button onclick="crQ('19',3)">T19</button>
        <button onclick="crQ('18',3)">T18</button>
        <button onclick="crQ('BULL',2)">Bull x2</button>
      </div>
    `;
    return;
  }

  c.innerHTML = `<div class="muted">No controls available.</div>`;
}

async function setMode() {
  const mode = document.getElementById('modeSel').value;
  await post('/action', {type:'set_mode', mode});
  await refresh();
}

async function setPlayers() {
  const raw = document.getElementById('players').value || "";
  const players = raw.split(",").map(x=>x.trim()).filter(Boolean);
  await post('/action', {type:'set_players', players});
  await refresh();
}

async function setTeams() {
  const A = (document.getElementById('teamA').value || "").split("\\n").map(x=>x.trim()).filter(Boolean);
  const B = (document.getElementById('teamB').value || "").split("\\n").map(x=>x.trim()).filter(Boolean);
  await post('/action', {type:'set_teams', A, B});
  await refresh();
}

async function save501() {
  const start = parseInt(document.getElementById('s501').value || "501");
  const doubleOut = document.getElementById('dout').checked;
  await post('/action', {type:'set_501_settings', start, doubleOut});
  await refresh();
}

async function saveMatch() {
  const start = parseInt(document.getElementById('mstart').value || "301");
  await post('/action', {type:'set_match_start', start});
  await refresh();
}

async function startGame() {
  const mode = document.getElementById('modeSel').value;
  if (mode === "championship") {
    const players = (document.getElementById('tourPlayers').value || "")
      .split("\\n").map(x=>x.trim()).filter(Boolean);
    await post('/action', {type:'set_tournament_players', players});
    await post('/action', {type:'start_game', game:'match'});
    await refresh();
    return;
  }
  const game = document.getElementById('gameSel').value;
  await post('/action', {type:'start_game', game});
  await refresh();
}

async function resetAll() { await post('/action', {type:'reset'}); await refresh(); }
async function nextTurn() { await post('/action', {type:'next'}); await refresh(); }

async function add501() {
  const v = parseInt(document.getElementById('v501').value || "0");
  await post('/action', {type:'501_add', score:v});
  await refresh();
}
async function quick501(v) { await post('/action', {type:'501_add', score:v}); await refresh(); }

async function addLB() {
  const v = parseInt(document.getElementById('vLB').value || "0");
  await post('/action', {type:'lb_add', points:v});
  await refresh();
}
async function quickLB(v) { await post('/action', {type:'lb_add', points:v}); await refresh(); }

async function atc(success) { await post('/action', {type:'atc_hit', success}); await refresh(); }

async function cricketHit() {
  const number = document.getElementById('cNum').value;
  const hits = parseInt(document.getElementById('cHits').value);
  await post('/action', {type:'cricket_hit', number, hits});
  await refresh();
}
async function crQ(number, hits) { await post('/action', {type:'cricket_hit', number, hits}); await refresh(); }

async function matchAdd() {
  const v = parseInt(document.getElementById('vm').value || "0");
  await post('/action', {type:'match_add', score:v});
  await refresh();
}
async function matchQuick(v) { await post('/action', {type:'match_add', score:v}); await refresh(); }
async function nextMatch() { await post('/action', {type:'next_match'}); await refresh(); }

setInterval(refresh, 900);
refresh();
</script>
</body>
</html>
"""

# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def home():
    return "Running. Use /display (monitor) and /control (phone)."

@app.get("/display")
def display():
    return render_template_string(DISPLAY_HTML)

@app.get("/control")
def control():
    return render_template_string(CONTROL_HTML)

@app.get("/state")
def state():
    ensure_players()
    return jsonify({
        "mode": STATE["mode"],
        "game": STATE["game"],
        "players": STATE["players"],
        "current": STATE["current"],
        "started": STATE["started"],
        "settings": STATE["settings"],
        "teams": STATE["teams"],
        "tournament": STATE["tournament"],
        "data": STATE["data"],
        "turn_label": current_player_label()
    })

@app.post("/action")
def action():
    payload = request.get_json(force=True, silent=True) or {}
    t = payload.get("type")

    if t == "reset":
        reset_state()
        return jsonify({"ok": True})

    if t == "set_mode":
        mode = payload.get("mode", "ffa")
        if mode not in ["ffa", "teams", "championship"]:
            return jsonify({"ok": False, "error": "Unknown mode"}), 400
        STATE["mode"] = mode
        STATE["started"] = False
        STATE["game"] = None
        STATE["data"] = {}
        return jsonify({"ok": True})

    if t == "set_players":
        players = payload.get("players") or []
        players = [p.strip() for p in players if isinstance(p, str) and p.strip()]
        if not players:
            players = ["Player 1", "Player 2"]
        STATE["players"] = players
        if STATE["started"] and STATE["mode"] == "ffa" and STATE["game"] in ["501","cricket","atc","leaderboard"]:
            init_game(STATE["game"])
        return jsonify({"ok": True})

    if t == "set_teams":
        A = payload.get("A") or []
        B = payload.get("B") or []
        A = [x.strip() for x in A if isinstance(x, str) and x.strip()]
        B = [x.strip() for x in B if isinstance(x, str) and x.strip()]
        if not A: A = [f"A{i+1}" for i in range(10)]
        if not B: B = [f"B{i+1}" for i in range(10)]
        STATE["teams"]["A"] = A
        STATE["teams"]["B"] = B
        STATE["teams"]["team_current"] = 0
        STATE["teams"]["team_turn"] = "A"
        if STATE["started"] and STATE["mode"] == "teams" and STATE["game"] in ["501","cricket","atc","leaderboard"]:
            init_game(STATE["game"])
        return jsonify({"ok": True})

    if t == "set_tournament_players":
        players = payload.get("players") or []
        players = [p.strip() for p in players if isinstance(p, str) and p.strip()]
        if len(players) < 2:
            players = [f"P{i+1}" for i in range(8)]
        STATE["tournament"] = {"players": players, "round": 1, "matches": [], "current_match": 0, "champion": None}
        return jsonify({"ok": True})

    if t == "set_match_start":
        start = safe_int(payload.get("start"), 301)
        start = max(101, min(501, start))
        STATE["settings"]["match_start"] = start
        return jsonify({"ok": True})

    if t == "set_501_settings":
        start = safe_int(payload.get("start"), 501)
        start = max(101, min(1001, start))
        doubleOut = bool(payload.get("doubleOut"))
        STATE["settings"]["501_start"] = start
        STATE["settings"]["501_double_out"] = doubleOut
        if STATE["started"] and STATE["game"] == "501":
            init_game("501")
        return jsonify({"ok": True})

    if t == "start_game":
        game = payload.get("game")
        if STATE["mode"] == "championship":
            init_game("match")
            return jsonify({"ok": True})

        if game not in ["501", "cricket", "atc", "leaderboard"]:
            return jsonify({"ok": False, "error": "Unknown game"}), 400
        init_game(game)
        return jsonify({"ok": True})

    if t == "next":
        if STATE["mode"] in ["ffa", "teams"]:
            advance_turn()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Not applicable"}), 400

    if t == "next_match":
        if STATE["mode"] == "championship":
            advance_match()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Not in championship mode"}), 400

    # Scoring actions
    if t == "501_add" and STATE["game"] == "501":
        handle_501_add(payload.get("score", 0))
        return jsonify({"ok": True})

    if t == "cricket_hit" and STATE["game"] == "cricket":
        cricket_hit(payload.get("number", "20"), payload.get("hits", 1))
        return jsonify({"ok": True})

    if t == "atc_hit" and STATE["game"] == "atc":
        atc_hit(bool(payload.get("success")))
        return jsonify({"ok": True})

    if t == "lb_add" and STATE["game"] == "leaderboard":
        leaderboard_add(payload.get("points", 0))
        return jsonify({"ok": True})

    if t == "match_add" and STATE["mode"] == "championship" and STATE["game"] == "match":
        match_add(payload.get("score", 0))
        # if match winner set, you can press "Next Match"
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Action not valid for current mode/game"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
