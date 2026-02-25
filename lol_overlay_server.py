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
    "all_players_stats": [], 
    "my_summoner_name": "Buscando...",
    "my_puuid": "",
    "my_team": 100
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
        # Fase desde el LCU (Cliente)
        lcu_phase = request_lcu(port, password, "/lol-gameflow/v1/gameflow-phase")
        
        # Intentar detectar si hay una partida activa por el puerto 2999 (In-Game API)
        # Esto es más fiable para Replays que el LCU
        live_data = None
        try:
            live_res = requests.get("https://127.0.0.1:2999/liveclientdata/allgamedata", verify=False, timeout=1)
            if live_res.status_code == 200:
                live_data = live_res.json()
        except:
            pass

        # Determinar fase final
        if live_data:
            # Si hay datos de la partida, estamos InProgress o Replay
            if lcu_phase in ["WatchInProgress", "Replay"] or not state["my_puuid"]:
                state["phase"] = "Replay"
            else:
                state["phase"] = "InProgress"
        else:
            state["phase"] = lcu_phase if lcu_phase else "None"

        # Identidad
        if not state["my_puuid"] and state["phase"] not in ["Replay", "None"]:
            me = request_lcu(port, password, "/lol-summoner/v1/current-summoner")
            if me:
                state["my_puuid"] = me.get("puuid", "")
                state["my_summoner_name"] = me.get("gameName", me.get("displayName", "Invocador"))

        # --- Champ Select ---
        if state["phase"] == "ChampSelect":
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

        # --- Lógica In-Game (Partida o Replay) ---
        if live_data:
            state["game_time"] = int(live_data.get("gameData", {}).get("gameTime", 0))
            minutes = max(state["game_time"] / 60.0, 1.0)
            
            players_data = []
            active_player = live_data.get("activePlayer")
            active_me = active_player.get("summonerName", "") if (active_player and isinstance(active_player, dict)) else ""

            for p in live_data.get("allPlayers", []):
                scores = p.get("scores", {})
                k, d, a = scores.get("kills", 0), scores.get("deaths", 0), scores.get("assists", 0)
                cs = scores.get("creepScore", 0)
                
                is_me = False
                p_name = p.get("summonerName", "Jugador")
                
                if active_me and p_name == active_me:
                    is_me = True
                    state["kills"], state["cs"], state["cs_per_min"] = k, cs, round(cs/minutes, 2)
                    state["my_team"] = 100 if p.get("team") == "ORDER" else 200

                team_raw = p.get("team", "ORDER")
                team_id = 100 if team_raw == "ORDER" else 200
                pos = p.get("position", {"x": 0, "y": 0, "z": 0})

                players_data.append({
                    "name": p_name,
                    "champion": p.get("championName", ""),
                    "img": f"https://ddragon.leagueoflegends.com/cdn/14.4.1/img/champion/{p.get('championName')}.png",
                    "kda": f"{k}/{d}/{a}",
                    "cs": cs,
                    "cspm": round(cs/minutes, 2),
                    "is_me": is_me,
                    "team": team_id,
                    "pos": pos
                })
            state["all_players_stats"] = players_data

        # --- End Of Game ---
        elif state["phase"] == "EndOfGame":
            eog = request_lcu(port, password, "/lol-end-of-game/v1/eog-stats-block")
            if eog and "teams" in eog:
                game_length = eog.get("gameLength", 0)
                state["game_time"] = int(game_length / 1000) if game_length > 10000 else int(game_length)
                minutes = max(state["game_time"] / 60.0, 1.0)
                
                players_data = []
                for team in eog.get("teams", []):
                    win = team.get("isWinningTeam", False)
                    t_id = team.get("teamId")
                    for p in team.get("players", []):
                        cid = p.get("championId", 0)
                        cname, cimg = get_champ_info(cid)
                        stats = p.get("stats", {})
                        k, d, a = stats.get("CHAMPIONS_KILLED", 0), stats.get("NUM_DEATHS", 0), stats.get("ASSISTS", 0)
                        cs = stats.get("MINIONS_KILLED", 0) + stats.get("NEUTRAL_MINIONS_KILLED", 0)
                        
                        full_name = p.get("gameName") or p.get("summonerName") or cname
                        is_me = (state["my_puuid"] and p.get("puuid") == state["my_puuid"])

                        if is_me:
                            state["game_result"] = "VICTORIA" if win else "DERROTA"
                            state["kills"], state["cs"], state["cs_per_min"] = k, cs, round(cs/minutes, 2)
                            state["my_team"] = t_id

                        players_data.append({
                            "name": full_name,
                            "champion": cname,
                            "img": cimg,
                            "kda": f"{k}/{d}/{a}",
                            "cs": cs,
                            "cspm": round(cs/minutes, 2),
                            "is_me": is_me,
                            "team": t_id,
                            "win": win,
                            "pos": {"x": 0, "z": 0}
                        })
                state["all_players_stats"] = players_data

        time.sleep(1)

@app.route("/data")
def data():
    return make_response(jsonify(state))

@app.route("/")
def index():
    return """
<html>
<head>
<title>LoL Smart Tracker</title>
<style>
    :root {
        --bg: #0d1117;
        --card: #161b22;
        --border: #30363d;
        --text: #c9d1d9;
        --blue: #3498db;
        --red: #e74c3c;
        --yellow: #f1c40f;
        --green: #238636;
    }
    body { font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: var(--bg); color: var(--text); margin: 0; }
    .container { max-width: 1200px; margin: 0 auto; }
    .card { background: var(--card); padding: 20px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 20px; }
    
    #live-ui, #replay-ui, #champ-ui, #eog-ui, #idle-ui { display: none; }
    .split-layout { display: flex; gap: 20px; flex-wrap: wrap; }
    .main-col { flex: 1; min-width: 300px; }
    .side-col { width: 400px; }

    .minimap { position: relative; width: 400px; height: 400px; background-image: url('https://ddragon.leagueoflegends.com/cdn/14.4.1/img/map/map11.png'); background-size: cover; border-radius: 8px; border: 2px solid var(--border); overflow: hidden; margin: 0 auto; }
    .player-dot { position: absolute; width: 26px; height: 26px; border-radius: 50%; border: 2px solid white; transform: translate(-50%, -50%); transition: all 0.8s linear; z-index: 10; object-fit: cover; }
    .team-100 { border-color: var(--blue); box-shadow: 0 0 8px var(--blue); }
    .team-200 { border-color: var(--red); box-shadow: 0 0 8px var(--red); }
    .is-me { border-color: var(--yellow); z-index: 20; border-width: 3px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px; border-bottom: 1px solid var(--border); text-align: left; }
    th { color: #8b949e; font-size: 11px; text-transform: uppercase; }
    .champ-img { width: 24px; height: 24px; border-radius: 50%; vertical-align: middle; margin-right: 8px; }
    
    .stat-hero { font-size: 32px; font-weight: bold; color: var(--yellow); }
    .stat-label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
    .vic { color: var(--green); font-size: 24px; font-weight: bold; }
    .def { color: var(--red); font-size: 24px; font-weight: bold; }
    
    .team-header { padding: 10px; font-weight: bold; font-size: 14px; border-radius: 8px 8px 0 0; }
    .th-blue { background: rgba(52, 152, 219, 0.2); color: var(--blue); }
    .th-red { background: rgba(231, 76, 60, 0.2); color: var(--red); }
</style>
<script>
async function update() {
    try {
        const res = await fetch('/data?t=' + Date.now());
        const data = await res.json();
        
        const views = ["live-ui", "replay-ui", "champ-ui", "eog-ui", "idle-ui"];
        views.forEach(v => document.getElementById(v).style.display = "none");

        if (data.phase === "None") {
            document.getElementById("idle-ui").style.display = "block";
        } else if (data.phase === "ChampSelect") {
            document.getElementById("champ-ui").style.display = "block";
            renderChampSelect(data);
        } else if (data.phase === "EndOfGame") {
            document.getElementById("eog-ui").style.display = "block";
            renderEOG(data);
        } else if (data.phase === "Replay") {
            document.getElementById("replay-ui").style.display = "block";
            renderReplay(data);
        } else {
            document.getElementById("live-ui").style.display = "block";
            renderPlaying(data);
        }

    } catch(e) { console.error(e); }
}

function renderChampSelect(data) {
    document.getElementById("cs-hover-name").innerText = data.hover.name;
    document.getElementById("cs-hover-img").src = data.hover.img;
    document.getElementById("cs-hover-img").style.display = data.hover.img ? "inline" : "none";
}

function renderPlaying(data) {
    document.getElementById("p-time").innerText = formatTime(data.game_time);
    document.getElementById("p-kills").innerText = data.kills;
    document.getElementById("p-cs").innerText = data.cs;
    document.getElementById("p-name").innerText = data.my_summoner_name;
    
    const allyTeam = data.all_players_stats.filter(p => p.team === data.my_team);
    document.getElementById("p-team-title").innerText = data.my_team === 100 ? "Tu Equipo (Azul)" : "Tu Equipo (Rojo)";

    document.getElementById("p-table-ally").innerHTML = allyTeam.map(p => `
        <tr style="${p.is_me ? 'background:rgba(241,196,15,0.1)' : ''}">
            <td><img class="champ-img" src="${p.img}">${p.name} ${p.is_me ? '<b>(TÚ)</b>' : ''}</td>
            <td><b>${p.kda}</b></td>
            <td>${p.cs}</td>
        </tr>`).join('');
        
    renderDots("minimap-live", data.all_players_stats);
}

function renderEOG(data) {
    document.getElementById("e-result").innerText = data.game_result;
    document.getElementById("e-result").className = data.game_result === "VICTORIA" ? "vic" : "def";
    
    const team100 = data.all_players_stats.filter(p => p.team === 100);
    const team200 = data.all_players_stats.filter(p => p.team === 200);

    document.getElementById("e-table-100").innerHTML = team100.map(p => `
        <tr style="${p.is_me ? 'background:rgba(241,196,15,0.1)' : ''}"><td><img class="champ-img" src="${p.img}">${p.name}</td><td>${p.kda}</td><td>${p.cs}</td></tr>`).join('');
    document.getElementById("e-table-200").innerHTML = team200.map(p => `
        <tr style="${p.is_me ? 'background:rgba(241,196,15,0.1)' : ''}"><td><img class="champ-img" src="${p.img}">${p.name}</td><td>${p.kda}</td><td>${p.cs}</td></tr>`).join('');
}

function renderReplay(data) {
    document.getElementById("r-time").innerText = formatTime(data.game_time);
    renderDots("minimap-replay", data.all_players_stats);
    
    const team100 = data.all_players_stats.filter(p => p.team === 100);
    const team200 = data.all_players_stats.filter(p => p.team === 200);

    document.getElementById("r-table-100").innerHTML = team100.map(p => `<tr><td><img class="champ-img" src="${p.img}">${p.name}</td><td>${p.kda}</td><td>${p.cs}</td></tr>`).join('');
    document.getElementById("r-table-200").innerHTML = team200.map(p => `<tr><td><img class="champ-img" src="${p.img}">${p.name}</td><td>${p.kda}</td><td>${p.cs}</td></tr>`).join('');
}

function renderDots(mapId, players) {
    const map = document.getElementById(mapId);
    players.forEach(p => {
        let dotId = mapId + "-dot-" + btoa(unescape(encodeURIComponent(p.name)));
        let dot = document.getElementById(dotId);
        if (!dot) {
            dot = document.createElement("img");
            dot.id = dotId;
            dot.className = "player-dot " + (p.team === 100 ? "team-100" : "team-200") + (p.is_me ? " is-me" : "");
            dot.src = p.img;
            map.appendChild(dot);
        }
        const mapSize = (mapId === "minimap-replay") ? 400 : 360;
        const gameSize = 15000;
        dot.style.left = (p.pos.x / gameSize * mapSize) + "px";
        dot.style.top = (mapSize - (p.pos.z / gameSize * mapSize)) + "px";
        dot.style.opacity = (p.pos.x === 0 && p.pos.z === 0) ? "0" : "1";
    });
}

function formatTime(s) { return Math.floor(s/60) + ":" + (s%60).toString().padStart(2,'0'); }

setInterval(update, 1000);
</script>
</head>
<body>
    <div class="container">
        
        <div id="idle-ui">
            <div class="card" style="text-align:center; padding: 100px 0;">
                <h1 style="color:var(--yellow)">Esperando Partida...</h1>
                <p>Abre el cliente de League of Legends o una Repetición para empezar.</p>
            </div>
        </div>

        <div id="champ-ui">
            <div class="card" style="text-align:center">
                <h1>Selección de Campeón</h1>
                <img id="cs-hover-img" style="width:120px; border-radius:50%; border: 4px solid var(--yellow); display:none">
                <h2 id="cs-hover-name" style="color:var(--yellow)"></h2>
                <p>Configura tus runas, la partida empezará pronto.</p>
            </div>
        </div>

        <div id="live-ui">
            <div class="header"><h1>Modo Juego: <span id="p-name"></span></h1></div>
            <div class="split-layout">
                <div class="main-col">
                    <div class="card" style="display:flex; justify-content:space-around; text-align:center">
                        <div><div class="stat-label">Reloj</div><div class="stat-hero" id="p-time">0:00</div></div>
                        <div><div class="stat-label">Kills</div><div class="stat-hero" style="color:var(--green)" id="p-kills">0</div></div>
                        <div><div class="stat-label">Tu CS</div><div class="stat-hero" id="p-cs">0</div></div>
                    </div>
                    <div class="card">
                        <h3 id="p-team-title">Tu Equipo</h3>
                        <table><thead><tr><th>Jugador</th><th>KDA</th><th>CS</th></tr></thead><tbody id="p-table-ally"></tbody></table>
                    </div>
                </div>
                <div class="side-col">
                    <div class="card" style="padding:15px">
                        <div class="minimap" id="minimap-live" style="width:360px; height:360px"></div>
                        <p style="font-size:11px; margin-top:10px; color:#8b949e; text-align:center">Mapa táctico activado.</p>
                    </div>
                </div>
            </div>
        </div>

        <div id="replay-ui">
            <div class="header"><h1>Consola de Análisis (Replay) - <span id="r-time"></span></h1></div>
            <div class="split-layout">
                <div class="main-col">
                    <div class="card" style="border-left: 4px solid var(--blue)">
                        <h3 style="color:var(--blue)">Equipo Azul</h3>
                        <table><thead><tr><th>Campeón</th><th>KDA</th><th>CS</th></tr></thead><tbody id="r-table-100"></tbody></table>
                    </div>
                    <div class="card" style="border-left: 4px solid var(--red)">
                        <h3 style="color:var(--red)">Equipo Rojo</h3>
                        <table><thead><tr><th>Campeón</th><th>KDA</th><th>CS</th></tr></thead><tbody id="r-table-200"></tbody></table>
                    </div>
                </div>
                <div class="side-col">
                    <div class="card" style="padding:10px"><div class="minimap" id="minimap-replay"></div></div>
                </div>
            </div>
        </div>

        <div id="eog-ui">
            <div class="header" style="text-align:center">
                <h1>Resumen de Partida: <span id="e-result"></span></h1>
            </div>
            <div class="split-layout">
                <div class="main-col">
                    <div class="team-header th-blue">EQUIPO AZUL</div>
                    <div class="card" style="border-radius: 0 0 12px 12px;">
                        <table><thead><tr><th>Jugador</th><th>KDA</th><th>CS</th></tr></thead><tbody id="e-table-100"></tbody></table>
                    </div>
                </div>
                <div class="main-col">
                    <div class="team-header th-red">EQUIPO ROJO</div>
                    <div class="card" style="border-radius: 0 0 12px 12px;">
                        <table><thead><tr><th>Jugador</th><th>KDA</th><th>CS</th></tr></thead><tbody id="e-table-200"></tbody></table>
                    </div>
                </div>
            </div>
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