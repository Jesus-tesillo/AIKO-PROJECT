"""
dashboard.py - Panel de Control Web para monitorear la VTuber.
Interfaz oscura estilo anime en http://localhost:5000.
Muestra estado de módulos, chat, respuestas, viewers y controles.
"""
import json
import queue
import threading
import time
from datetime import datetime


# Estado global compartido — otros módulos escriben en este dict
dashboard_state = {
    "modules": {
        "llm": False, "tts": False, "live2d": False,
        "twitch": False, "stt": False, "events": False,
        "chess": False,
    },
    "emotion": "neutral",
    "chat_log": [],        # últimos 20 mensajes
    "responses": [],       # últimas 5 respuestas
    "top_viewers": [],     # top 5 más activos
    "muted": False,
    "force_spontaneous": False,
    "uptime_start": time.time(),
    "messages_processed": 0,
    "tikfinity_queue": queue.Queue(),  # Cola de mensajes de TikFinity
    "chess": {"active": False},        # Estado de ajedrez
}

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aiko VTuber — Panel de Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-main: #0b0f19;
    --bg-card: #111827;
    --bg-card-hover: #1f2937;
    --bg-subtle: #1e293b;
    --border-color: #1f2937;
    --border-light: #334155;
    --text-main: #f1f5f9;
    --text-muted: #94a3b8;
    --text-dark: #64748b;
    --accent-indigo: #6366f1;
    --accent-indigo-light: #818cf8;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --accent-amber: #f59e0b;
  }

  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg-main);
    color: var(--text-main);
    min-height: 100vh;
    font-size: 14px;
    line-height: 1.5;
  }

  /* Header */
  .header {
    background: var(--bg-card);
    border-bottom: 1px solid var(--border-color);
    padding: 16px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .header h1 {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-main);
    letter-spacing: -0.02em;
  }
  .live-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(16, 185, 129, 0.1);
    color: var(--accent-green);
    border: 1px solid rgba(16, 185, 129, 0.2);
    padding: 3px 10px;
    border-radius: 9999px;
    font-size: 12px;
    font-weight: 600;
  }
  .pulse-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.9); }
    100% { opacity: 1; transform: scale(1); }
  }
  .header-metrics {
    display: flex;
    align-items: center;
    gap: 20px;
    color: var(--text-muted);
    font-size: 13px;
  }
  .metric-item strong {
    color: var(--text-main);
  }

  /* Layout Grid */
  .container {
    max-width: 1280px;
    margin: 0 auto;
    padding: 24px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
  }
  @media (max-width: 900px) {
    .grid { grid-template-columns: 1fr; }
  }

  /* Card Component */
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-color);
  }
  .card-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  /* Status Grid */
  .status-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .status-pill {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg-subtle);
    border: 1px solid var(--border-color);
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 13px;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .status-dot.on { background: var(--accent-green); box-shadow: 0 0 6px rgba(16, 185, 129, 0.4); }
  .status-dot.off { background: var(--text-dark); }

  /* Emotion & Persona Pill */
  .emotion-container {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--border-color);
  }
  .emotion-badge {
    background: rgba(99, 102, 241, 0.15);
    color: var(--accent-indigo-light);
    border: 1px solid rgba(99, 102, 241, 0.3);
    padding: 4px 12px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
  }

  /* Control Buttons */
  .controls-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .btn {
    padding: 10px 16px;
    border: 1px solid transparent;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .btn-primary {
    background: var(--accent-indigo);
    color: #ffffff;
  }
  .btn-primary:hover {
    background: #4f46e5;
  }
  .btn-danger {
    background: rgba(239, 68, 68, 0.15);
    color: var(--accent-red);
    border-color: rgba(239, 68, 68, 0.3);
  }
  .btn-danger:hover {
    background: rgba(239, 68, 68, 0.25);
  }
  .btn-success {
    background: rgba(16, 185, 129, 0.15);
    color: var(--accent-green);
    border-color: rgba(16, 185, 129, 0.3);
  }
  .btn-success:hover {
    background: rgba(16, 185, 129, 0.25);
  }
  .btn-warning {
    background: rgba(245, 158, 11, 0.15);
    color: var(--accent-amber);
    border-color: rgba(245, 158, 11, 0.3);
  }
  .btn-warning:hover {
    background: rgba(245, 158, 11, 0.25);
  }

  /* List & Feed Boxes */
  .feed-box {
    max-height: 260px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border-light) transparent;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .feed-box::-webkit-scrollbar { width: 4px; }
  .feed-box::-webkit-scrollbar-thumb { background: var(--border-light); border-radius: 4px; }

  .chat-item {
    background: var(--bg-subtle);
    border: 1px solid var(--border-color);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
  }
  .chat-user {
    color: var(--accent-indigo-light);
    font-weight: 600;
  }
  .chat-msg {
    color: var(--text-main);
  }
  .chat-time {
    color: var(--text-dark);
    font-size: 11px;
    float: right;
  }

  .response-item {
    background: var(--bg-subtle);
    border-left: 3px solid var(--accent-indigo);
    padding: 10px 12px;
    border-radius: 0 6px 6px 0;
    font-size: 13px;
    color: var(--text-main);
  }

  .viewer-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-subtle);
    border-radius: 6px;
    border: 1px solid var(--border-color);
    font-size: 13px;
  }
  .viewer-name { font-weight: 500; color: var(--text-main); }
  .viewer-count { color: var(--accent-indigo-light); font-weight: 600; }

  /* Full-width sections */
  .full-width { grid-column: 1 / -1; }

  /* Chess Panel Layout */
  .chess-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 20px;
  }
  @media (max-width: 1024px) {
    .chess-grid { grid-template-columns: 1fr; }
  }
  .chess-info-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid var(--border-color);
    font-size: 13px;
  }
  .chess-info-row .label { color: var(--text-muted); }
  .chess-info-row .val { color: var(--text-main); font-weight: 600; }

  .chess-board-ascii {
    font-family: 'Cascadia Code', 'Fira Code', monospace;
    font-size: 12px;
    line-height: 1.5;
    background: var(--bg-main);
    border: 1px solid var(--border-color);
    padding: 12px;
    border-radius: 8px;
    color: var(--accent-indigo-light);
    white-space: pre;
    overflow: hidden;
  }

  .chess-input {
    width: 100%;
    padding: 9px 12px;
    border-radius: 6px;
    background: var(--bg-subtle);
    border: 1px solid var(--border-color);
    color: var(--text-main);
    font-size: 13px;
    outline: none;
    transition: border-color 0.15s;
  }
  .chess-input:focus { border-color: var(--accent-indigo); }

  .badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-active { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }
  .badge-idle { background: rgba(100, 116, 139, 0.15); color: var(--text-muted); }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="header-left">
    <h1>✦ Aiko VTuber</h1>
    <span class="live-indicator"><span class="pulse-dot"></span> EN VIVO</span>
  </div>
  <div class="header-metrics">
    <div class="metric-item">Uptime: <strong id="uptime">00:00:00</strong></div>
    <div class="metric-item">Mensajes: <strong id="msg-count">0</strong></div>
  </div>
</div>

<!-- Main Container -->
<div class="container">
  <div class="grid">

    <!-- Card 1: System Status -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Estado del Sistema</span>
      </div>
      <div class="status-grid" id="modules"></div>
      <div class="emotion-container">
        <span style="color: var(--text-muted); font-size:13px;">Humor Activo:</span>
        <span class="emotion-badge" id="emotion">neutral</span>
      </div>
    </div>

    <!-- Card 2: Interactive Controls -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Acciones Directas</span>
      </div>
      <div class="controls-row">
        <button class="btn btn-primary" onclick="forceSpontaneous()">💬 Comentario Espontáneo</button>
        <button class="btn btn-danger" id="mute-btn" onclick="toggleMute()">🔇 Mutear Voz</button>
      </div>
      <p style="margin-top: 14px; font-size: 12px; color: var(--text-dark);">
        Permite disparar monólogos inmediatamente o alternar el silencio del canal de audio.
      </p>
    </div>

    <!-- Card 3: Chat Stream -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Chat de Transmisión</span>
      </div>
      <div class="feed-box" id="chat-log"></div>
    </div>

    <!-- Card 4: Recent AI Responses -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Últimas Respuestas</span>
      </div>
      <div class="feed-box" id="responses"></div>
    </div>

    <!-- Card 5: Top Viewers -->
    <div class="card full-width">
      <div class="card-header">
        <span class="card-title">Audiencia Activa</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px;" id="top-viewers"></div>
    </div>

    <!-- Card 6: Chess Bridge (Collapsible/Dynamic) -->
    <div class="card full-width" id="chess-card" style="display:none;">
      <div class="card-header">
        <span class="card-title">♿︎ Módulo Ajedrez en Vivo</span>
        <span id="chess-status-badge" class="badge badge-idle">Inactivo</span>
      </div>
      <div class="chess-grid">
        <!-- Col 1: Partida -->
        <div>
          <div class="chess-info-row"><span class="label">Oponente</span><span class="val" id="chess-opponent">—</span></div>
          <div class="chess-info-row"><span class="label">Color Aiko</span><span class="val" id="chess-color">—</span></div>
          <div class="chess-info-row"><span class="label">Movimientos</span><span class="val" id="chess-move-count">0</span></div>
          <div class="chess-info-row"><span class="label">Turno</span><span class="val" id="chess-turn">—</span></div>
          <div class="chess-info-row"><span class="label">Último Evento</span><span class="val" id="chess-event">—</span></div>
          <div style="margin-top: 14px;">
            <input id="chess-opponent-input" class="chess-input" placeholder="Nombre de usuario en Lichess...">
            <div class="controls-row" style="margin-top: 10px;">
              <button class="btn btn-success" onclick="chessStart()">▶ Iniciar</button>
              <button class="btn btn-danger" onclick="chessStop()">⏹ Detener</button>
              <button class="btn btn-warning" onclick="chessSkip()">⏭ Saltar Turno</button>
            </div>
          </div>
        </div>

        <!-- Col 2: Tablero Visual ASCII -->
        <div>
          <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;">Estado del Tablero</p>
          <div class="chess-board-ascii" id="chess-board-ascii">Sin partida activa</div>
        </div>

        <!-- Col 3: Leaderboard -->
        <div>
          <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;">Top Jugadores</p>
          <div id="chess-lb" style="display:flex; flex-direction:column; gap:6px;"></div>
        </div>
      </div>
    </div>

  </div>
</div>

<script>
function formatTime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sc = Math.floor(s % 60);
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sc).padStart(2,'0')}`;
}

async function refresh() {
  try {
    const r = await fetch("/api/state");
    const d = await r.json();
    
    // Status Modules
    let mhtml = "";
    const labels = {
      llm: "Groq LLM", tts: "Motor TTS", live2d: "Live2D Bridge",
      twitch: "Chat Twitch", stt: "Reconocimiento STT", events: "Eventos Stream", chess: "Ajedrez"
    };
    for (const [k, v] of Object.entries(d.modules)) {
      mhtml += `<div class="status-pill"><div class="status-dot ${v ? 'on' : 'off'}"></div>${labels[k] || k}</div>`;
    }
    document.getElementById("modules").innerHTML = mhtml;
    document.getElementById("emotion").textContent = d.emotion;
    document.getElementById("msg-count").textContent = d.messages_processed;
    
    // Uptime
    const up = Date.now() / 1000 - d.uptime_start;
    document.getElementById("uptime").textContent = formatTime(up);
    
    // Chat Feed
    let chtml = "";
    for (const m of d.chat_log.slice(-20)) {
      chtml += `<div class="chat-item"><span class="chat-time">${m.time || ''}</span><span class="chat-user">${m.user}</span>: <span class="chat-msg">${m.message}</span></div>`;
    }
    document.getElementById("chat-log").innerHTML = chtml || '<p style="color: var(--text-dark); font-size:13px;">Sin mensajes recientes en el chat.</p>';
    
    // Responses
    let rhtml = "";
    for (const r2 of d.responses.slice(-5)) {
      rhtml += `<div class="response-item">${r2}</div>`;
    }
    document.getElementById("responses").innerHTML = rhtml || '<p style="color: var(--text-dark); font-size:13px;">Sin respuestas generadas.</p>';
    
    // Top Viewers
    let vhtml = "";
    for (const v2 of d.top_viewers) {
      vhtml += `<div class="viewer-item"><span class="viewer-name">${v2.name}</span><span class="viewer-count">${v2.count} msgs</span></div>`;
    }
    document.getElementById("top-viewers").innerHTML = vhtml || '<p style="color: var(--text-dark); font-size:13px;">No hay audiencia registrada aún.</p>';
    
    // Mute Button State
    const mb = document.getElementById("mute-btn");
    mb.textContent = d.muted ? "🔊 Desmutear Voz" : "🔇 Mutear Voz";
    mb.className = "btn " + (d.muted ? "btn-success" : "btn-danger");
  } catch (e) {
    console.error("Error actualizando estado:", e);
  }
}

async function forceSpontaneous() {
  try { await fetch("/api/force_spontaneous", { method: "POST" }); } catch(e) {}
}
async function toggleMute() {
  try { await fetch("/api/toggle_mute", { method: "POST" }); } catch(e) {}
}

// Chess Functions
async function chessStart() {
  const opp = document.getElementById('chess-opponent-input').value.trim();
  if (!opp) { alert('Ingresa el usuario de Lichess'); return; }
  try {
    const r = await fetch('/api/chess/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opponent: opp, color: 'random' })
    });
    const d = await r.json();
    if (!d.ok) alert('Error: ' + (d.error || 'No se pudo iniciar'));
  } catch(e) { alert('Error de conexión con el módulo de ajedrez'); }
}

async function chessStop() {
  if (!confirm('¿Deseas detener la partida actual?')) return;
  try { await fetch('/api/chess/stop', { method: 'POST' }); } catch(e) {}
}
async function chessSkip() {
  try { await fetch('/api/chess/skip_turn', { method: 'POST' }); } catch(e) {}
}

async function refreshChess() {
  try {
    const r = await fetch('/api/chess/state');
    const d = await r.json();
    document.getElementById('chess-card').style.display = 'block';
    
    const badge = document.getElementById('chess-status-badge');
    if (d.status === 'playing' || d.status === 'starting') {
      badge.textContent = 'JUGANDO';
      badge.className = 'badge badge-active';
    } else {
      badge.textContent = 'Inactivo';
      badge.className = 'badge badge-idle';
    }
    
    document.getElementById('chess-opponent').textContent = d.opponent || '—';
    document.getElementById('chess-color').textContent = d.aiko_color === 'white' ? 'Blancas' : d.aiko_color === 'black' ? 'Negras' : '—';
    document.getElementById('chess-move-count').textContent = d.move_count || 0;
    document.getElementById('chess-turn').textContent = d.turn === 'aiko' ? 'Aiko' : 'Oponente';
    
    const ev = d.last_event || '—';
    document.getElementById('chess-event').textContent = ev.length > 35 ? ev.substring(0, 35) + '...' : ev;
    document.getElementById('chess-board-ascii').textContent = d.board_ascii || 'Sin partida activa';
  } catch(e) {}
  
  try {
    const r2 = await fetch('/api/chess/leaderboard?n=5');
    const lb = await r2.json();
    let h = '';
    lb.forEach((p) => {
      h += `<div class="viewer-item"><span class="viewer-name">#${p.rank} ${p.username}</span><span class="viewer-count">${p.points} pts</span></div>`;
    });
    document.getElementById('chess-lb').innerHTML = h || '<p style="color: var(--text-dark); font-size:13px;">Sin jugadores registrados.</p>';
  } catch(e) {}
}

setInterval(refresh, 2000);
setInterval(refreshChess, 2500);
refresh();
refreshChess();
</script>
</body>
</html>"""



class Dashboard:
    """Dashboard web basado en Flask para Aiko."""

    def __init__(self, port: int = 5000, chess_bridge=None):
        self.port = port
        self._thread = None
        self._app = None
        self._chess_bridge = chess_bridge   # inyectado desde core_stack
        print(f"[Dashboard] Se ejecutará en http://localhost:{self.port}")

    def start(self):
        """Iniciar el dashboard en un hilo de fondo."""
        self._thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="Dashboard"
        )
        self._thread.start()

    def _run_server(self):
        """Ejecutar el servidor Flask."""
        try:
            from flask import Flask, jsonify, request, Response

            app = Flask(__name__)
            app.logger.disabled = True

            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)

            @app.after_request
            def add_cors_headers(response):
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
                return response

            @app.route("/")
            def index():
                return Response(DASHBOARD_HTML, mimetype="text/html")

            @app.route("/api/state")
            def api_state():
                return jsonify(dashboard_state)

            @app.route("/api/force_spontaneous", methods=["POST"])
            def api_force():
                dashboard_state["force_spontaneous"] = True
                return jsonify({"ok": True})

            @app.route("/api/toggle_mute", methods=["POST"])
            def api_mute():
                dashboard_state["muted"] = not dashboard_state["muted"]
                return jsonify({"muted": dashboard_state["muted"]})

            @app.route("/api/tikfinity", methods=["POST", "GET"])
            def api_tikfinity():
                """TikFinity reemplazado por conexión directa via TikTokLive.
                Endpoint mantenido para evitar errores 404 si TikFinity
                sigue configurado apuntando aquí."""
                return jsonify({"status": "ok", "note": "TikFinity deprecated — using TikTokLive direct"}), 200

            self._app = app

            # ── Endpoints opcionales de módulos extra ──────────────────────
            if self._chess_bridge is not None:
                try:
                    from modules.chess_bridge import register_chess_endpoints
                    register_chess_endpoints(app, self._chess_bridge)
                    dashboard_state["modules"]["chess"] = True
                except Exception as e:
                    print(f"[Dashboard] Error registrando endpoints de ajedrez: {e}")

            print(f"[Dashboard] ✓ Corriendo en http://localhost:{self.port}")
            app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)

        except ImportError:
            print("[Dashboard] ✗ Flask no instalado. Ejecuta: pip install flask")
        except Exception as e:
            print(f"[Dashboard] Error: {e}")

    # ── Métodos auxiliares (llamados desde main.py) ────────────

    @staticmethod
    def update_module(name: str, status: bool):
        dashboard_state["modules"][name] = status

    @staticmethod
    def set_emotion(emotion: str):
        dashboard_state["emotion"] = emotion

    @staticmethod
    def add_chat_message(user: str, message: str):
        dashboard_state["chat_log"].append({
            "user": user, "message": message,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        # Mantener últimos 20
        if len(dashboard_state["chat_log"]) > 20:
            dashboard_state["chat_log"] = dashboard_state["chat_log"][-20:]
        dashboard_state["messages_processed"] += 1

    @staticmethod
    def add_response(text: str):
        dashboard_state["responses"].append(text)
        if len(dashboard_state["responses"]) > 5:
            dashboard_state["responses"] = dashboard_state["responses"][-5:]

    @staticmethod
    def update_top_viewers(viewer_data: list):
        """viewer_data: lista de {"name": str, "count": int}"""
        dashboard_state["top_viewers"] = viewer_data[:5]

    @staticmethod
    def check_force_spontaneous() -> bool:
        if dashboard_state["force_spontaneous"]:
            dashboard_state["force_spontaneous"] = False
            return True
        return False

    @staticmethod
    def is_muted() -> bool:
        return dashboard_state["muted"]

    @staticmethod
    def get_tikfinity_message() -> dict:
        """Get the next TikFinity chat message from the queue."""
        try:
            return dashboard_state["tikfinity_queue"].get_nowait()
        except queue.Empty:
            return None
