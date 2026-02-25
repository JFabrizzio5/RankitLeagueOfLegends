import requests
import urllib3
import time
import threading
import os
from flask import Flask, jsonify, make_response

urllib3.disable_warnings()

app = Flask(__name__)

# Estado global
state = {
    "phase": "None",
    "game_result": "",
    "hover": {"id": 0, "name": "Ninguno", "img": ""},
    "picks": [],
    "bans_my": [],
    "bans_enemy": [],
    "game_time": 0,
    "kills": 0,
    "cs": 0,
    "cs_per_min": 0,
    "all_players_stats": [], # Ahora incluiremos el equipo en cada objeto
    "my_summoner_name": "Buscando...",
    "my_puuid": ""
}

# Diccionario local de campeones para traducción rápida
CHAMP_DATA = {}

def update_champ_data():
    global CHAMP_DATA
    try:
        v = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]
        data = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{v}/data/es_MX/champion.json").json()["data"]
        for name, info in data.items():
            CHAMP_DATA[int(info["key"])] = {"name": info["name"], "id": info["id"], "version": v}
    except:
        pass

def get_champ_info(cid):
    if cid in CHAMP_DATA:
        c = CHAMP_DATA[cid]
        return c["name"], f"https://ddragon.leagueoflegends.com/cdn/{c['version']}/img/champion/{c['id']}.png"
    return f"ID: {cid}", ""

# 🔥 Obtener lockfile
def get_lockfile():
    path = r"C:\Riot Games\League of Legends\lockfile"
    if not os.path.exists(path): return None
    with open(path, "r") as f:
        content = f.read().split(":")
        return content[2], content[3]

def request_lcu(port, password, endpoint):
    url = f"https://127.0.0.1:{port}{endpoint}"
    try:
        r = requests.get(url, auth=("riot", password), verify=False, timeout=2)
        return r.json()
    except:
        return None

# 🔁 Loop de actualización
def background_loop():
    update_champ_data()
    while True:
        creds = get_lockfile()
        if not creds:
            time.sleep(5)
            continue

        port, password = creds
        phase = request_lcu(port, password, "/lol-gameflow/v1/gameflow-phase")
        if not phase:
            time.sleep(2)
            continue

        state["phase"] = phase

        # Identidad
        if not state["my_puuid"]:
            me = request_lcu(port, password, "/lol-summoner/v1/current-summoner")
            if me:
                state["my_puuid"] = me.get("puuid", "")
                state["my_summoner_name"] = me.get("gameName", me.get("displayName", "Invocador"))

        # --- Champ Select ---
        if phase == "ChampSelect":
            data = request_lcu(port, password, "/lol-champ-select/v1/session")
            if data:
                state["game_result"] = ""
                state["bans_my"] = [b.get("championId", 0) for b in data.get("bans", {}).get("myTeamBans", [])]
                state["bans_enemy"] = [b.get("championId", 0) for b in data.get("bans", {}).get("theirTeamBans", [])]
                state["picks"] = [m.get("championId", 0) for m in data.get("myTeam", []) if m.get("championId", 0) > 0]
                
                local_cell = data.get("localPlayerCellId")
                for p in data.get("myTeam", []):
                    if p.get("cellId") == local_cell:
                        cid = p.get("championId") or p.get("championPickIntent") or 0
                        name, img = get_champ_info(cid)
                        state["hover"] = {"id": cid, "name": name if cid > 0 else "Ninguno", "img": img}
                        break

        # --- In Progress ---
        elif phase == "InProgress":
            try:
                live = requests.get("https://127.0.0.1:2999/liveclientdata/allgamedata", verify=False, timeout=2).json()
                state["game_time"] = int(live.get("gameData", {}).get("gameTime", 0))
                minutes = max(state["game_time"] / 60.0, 1.0)
                
                players_data = []
                for p in live.get("allPlayers", []):
                    scores = p.get("scores", {})
                    k, d, a = scores.get("kills", 0), scores.get("deaths", 0), scores.get("assists", 0)
                    cs = scores.get("creepScore", 0)
                    
                    is_me = False
                    p_name = p.get("summonerName", "Jugador")
                    active_me = live.get("activePlayer", {}).get("summonerName", "")
                    if p_name == active_me:
                        is_me = True
                        state["kills"], state["cs"], state["cs_per_min"] = k, cs, round(cs/minutes, 2)

                    # Equipo: ORDER suele ser Azul (100), CHAOS Rojo (200)
                    team_raw = p.get("team", "ORDER")
                    team_id = 100 if team_raw == "ORDER" else 200

                    players_data.append({
                        "name": p_name,
                        "champion": p.get("championName", ""),
                        "img": f"https://ddragon.leagueoflegends.com/cdn/14.4.1/img/champion/{p.get('championName')}.png",
                        "kda": f"{k}/{d}/{a}",
                        "cs": cs,
                        "cspm": round(cs/minutes, 2),
                        "is_me": is_me,
                        "team": team_id,
                        "win": None # No se sabe hasta el final
                    })
                state["all_players_stats"] = players_data
            except: pass

        # --- End Of Game ---
        elif phase == "EndOfGame":
            eog = request_lcu(port, password, "/lol-end-of-game/v1/eog-stats-block")
            if eog and "teams" in eog:
                game_length = eog.get("gameLength", 0)
                state["game_time"] = int(game_length / 1000) if game_length > 10000 else int(game_length)
                minutes = max(state["game_time"] / 60.0, 1.0)
                
                players_data = []
                for team in eog.get("teams", []):
                    win = team.get("isWinningTeam", False)
                    t_id = team.get("teamId") # 100 o 200
                    for p in team.get("players", []):
                        cid = p.get("championId", 0)
                        cname, cimg = get_champ_info(cid)
                        
                        stats = p.get("stats", {})
                        k, d, a = stats.get("CHAMPIONS_KILLED", 0), stats.get("NUM_DEATHS", 0), stats.get("ASSISTS", 0)
                        cs = stats.get("MINIONS_KILLED", 0) + stats.get("NEUTRAL_MINIONS_KILLED", 0)
                        
                        full_name = p.get("gameName") or p.get("summonerName") or cname
                        is_me = False
                        if state["my_puuid"] and p.get("puuid") == state["my_puuid"]:
                            is_me = True
                            state["game_result"] = "VICTORIA" if win else "DERROTA"
                            state["kills"], state["cs"], state["cs_per_min"] = k, cs, round(cs/minutes, 2)
                            full_name = state["my_summoner_name"]

                        players_data.append({
                            "name": full_name,
                            "champion": cname,
                            "img": cimg,
                            "kda": f"{k}/{d}/{a}",
                            "cs": cs,
                            "cspm": round(cs/minutes, 2),
                            "is_me": is_me,
                            "team": t_id,
                            "win": win
                        })
                state["all_players_stats"] = players_data

        time.sleep(2)

@app.route("/data")
def data():
    return make_response(jsonify(state))

@app.route("/")
def index():
    return """
<html>
<head>
<title>LoL Tracker Ultimate</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: #0d1117; color: #c9d1d9; margin: 0; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding: 0 10px; }
    .card { background: #161b22; padding: 20px; border-radius: 12px; border: 1px solid #30363d; margin-bottom: 20px; }
    .vic-text { color: #238636; font-weight: bold; }
    .def-text { color: #da3633; font-weight: bold; }
    
    /* Layout de tablas */
    .tables-container { display: flex; gap: 20px; flex-wrap: wrap; }
    .team-column { flex: 1; min-width: 450px; }
    
    .team-header { padding: 10px; border-radius: 8px 8px 0 0; font-weight: bold; display: flex; justify-content: space-between; align-items: center; }
    .team-100 { background: rgba(52, 152, 219, 0.15); border: 1px solid #3498db; color: #3498db; } /* Azul */
    .team-200 { background: rgba(231, 76, 60, 0.15); border: 1px solid #e74c3c; color: #e74c3c; } /* Rojo */
    
    table { width: 100%; border-collapse: collapse; background: #161b22; border-radius: 0 0 8px 8px; overflow: hidden; border: 1px solid #30363d; border-top: none; }
    th, td { padding: 10px; border-bottom: 1px solid #30363d; text-align: left; font-size: 13px; }
    th { background: #0d1117; color: #8b949e; text-transform: uppercase; font-size: 11px; }
    tr.me { background: #1c2128; box-shadow: inset 4px 0 0 #f1c40f; }
    
    .champ-box { display: flex; align-items: center; gap: 8px; }
    .champ-img { width: 28px; height: 28px; border-radius: 50%; border: 1px solid #444; }
    .badge { background: #f1c40f; color: #000; padding: 2px 5px; border-radius: 4px; font-size: 9px; font-weight: bold; }
    .section-title { font-size: 13px; color: #8b949e; text-transform: uppercase; margin-bottom: 8px; border-bottom: 1px solid #30363d; padding-bottom: 4px; }
</style>
<script>
let champMap = {};
let ddragonVersion = "14.4.1";

async function init() {
    try {
        const v = await (await fetch("https://ddragon.leagueoflegends.com/api/versions.json")).json();
        ddragonVersion = v[0];
        const c = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${ddragonVersion}/data/es_MX/champion.json`)).json();
        for (let k in c.data) { champMap[c.data[k].key] = c.data[k]; }
    } catch(e) {}
}

function findChamp(id) {
    return champMap[id] ? champMap[id].name : id;
}

function renderTable(players) {
    if (!players.length) return "<tr><td colspan='4' style='text-align:center'>Esperando datos...</td></tr>";
    return players.map(p => `
        <tr class="${p.is_me ? 'me' : ''}">
            <td>${p.name} ${p.is_me ? '<span class="badge">TÚ</span>' : ''}</td>
            <td><div class="champ-box"><img class="champ-img" src="${p.img}">${p.champion}</div></td>
            <td><b>${p.kda}</b></td>
            <td>${p.cs} <span style="font-size:10px; color:#8b949e">(${p.cspm})</span></td>
        </tr>
    `).join('');
}

async function update() {
    try {
        const data = await (await fetch('/data?t=' + Date.now())).json();
        
        document.getElementById("phase").innerText = data.phase;
        document.getElementById("name").innerText = data.my_summoner_name;
        
        // Hover Info
        document.getElementById("h_name").innerText = data.hover.name;
        document.getElementById("h_img").src = data.hover.img;
        document.getElementById("h_img").style.display = data.hover.img ? "inline" : "none";

        document.getElementById("time").innerText = Math.floor(data.game_time/60) + ":" + (data.game_time%60).toString().padStart(2,'0');
        document.getElementById("kills").innerText = data.kills;
        document.getElementById("cs").innerText = data.cs + " (" + data.cs_per_min + "/m)";

        // Separar por equipos
        const team100 = data.all_players_stats.filter(p => p.team === 100);
        const team200 = data.all_players_stats.filter(p => p.team === 200);

        document.getElementById("tbody100").innerHTML = renderTable(team100);
        document.getElementById("tbody200").innerHTML = renderTable(team200);

        // Mostrar resultado por equipo
        if (data.phase === "EndOfGame" && team100.length > 0) {
            const win100 = team100[0].win;
            document.getElementById("status100").innerText = win100 ? "VICTORIA" : "DERROTA";
            document.getElementById("status100").className = win100 ? "vic-text" : "def-text";
            document.getElementById("status200").innerText = !win100 ? "VICTORIA" : "DERROTA";
            document.getElementById("status200").className = !win100 ? "vic-text" : "def-text";
        } else {
            document.getElementById("status100").innerText = "";
            document.getElementById("status200").innerText = "";
        }
    } catch(e) {}
}
init().then(() => setInterval(update, 2000));
</script>
</head>
<body>
    <div class="header">
        <h1>LoL Tracker Pro</h1>
    </div>
    
    <div class="card">
        <div class="section-title">Información de Sesión</div>
        <h3><span id="name">Cargando...</span> | Fase: <span id="phase" style="color:#58a6ff"></span></h3>
        <p>Selección Actual: <img id="h_img" class="champ-img" style="display:none; vertical-align:middle;"> <span id="h_name" style="color:#f1c40f"></span></p>
    </div>

    <div class="card">
        <div class="section-title">En Partida (Jugador Activo)</div>
        <p>Reloj: <span id="time" style="font-weight:bold"></span> | Kills: <span id="kills" style="color:#2ecc71; font-weight:bold"></span> | CS: <span id="cs" style="color:#f1c40f; font-weight:bold"></span></p>
    </div>

    <div class="tables-container">
        <!-- Equipo Azul (100) -->
        <div class="team-column">
            <div class="team-header team-100">
                <span>EQUIPO AZUL</span>
                <span id="status100"></span>
            </div>
            <table>
                <thead><tr><th>Jugador</th><th>Campeón</th><th>KDA</th><th>CS (m)</th></tr></thead>
                <tbody id="tbody100"></tbody>
            </table>
        </div>

        <!-- Equipo Rojo (200) -->
        <div class="team-column">
            <div class="team-header team-200">
                <span>EQUIPO ROJO</span>
                <span id="status200"></span>
            </div>
            <table>
                <thead><tr><th>Jugador</th><th>Campeón</th><th>KDA</th><th>CS (m)</th></tr></thead>
                <tbody id="tbody200"></tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    thread = threading.Thread(target=background_loop)
    thread.daemon = True
    thread.start()
    app.run(port=5000)