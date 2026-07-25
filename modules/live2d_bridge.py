"""
live2d_bridge.py — Full Live2D control via browser-based viewer.

Serves HTML viewer + WebSocket bridge. The browser renders Live2D,
Python controls it remotely via JSON commands. This replaces VTube Studio.

Architecture:
  Python (this file) ──WebSocket──► Browser (index.html)
                                     └── PixiJS + Live2D SDK
                                     └── AnimationEngine (3 layers)
"""

import asyncio
import json
import os
import glob
import threading
import time
import http.server
import socketserver
import functools
import urllib.parse


class Live2DBridge:
    """
    WebSocket bridge for controlling the browser-based Live2D viewer.
    Drop-in replacement for VTubeStudioAPI — all methods compatible.
    """

    WS_PORT   = 8765
    HTTP_PORT = 8180
    VIEWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'live2d_viewer')

    # Reaction name → emotion mapping (from main.py tags)
    REACTION_MAP = {
        "laugh":     "laughing",
        "greet":     "greet",
        "wink":      "wink",
        "surprised": "surprised",
        "excited":   "excited",
        "sad":       "sad",
        "thinking":  "thinking",
        "shy":       "shy",
        "disgust":   "disgust",
        "smug":      "smug",
        "angry":     "angry",
    }

    # Reaction durations
    REACTION_DURATIONS = {
        "laugh":     2.0,
        "greet":     2.5,
        "wink":      1.5,
        "surprised": 1.5,
        "excited":   2.0,
        "sad":       2.5,
        "thinking":  2.0,
        "shy":       2.0,
        "disgust":   1.5,
        "smug":      2.0,
        "angry":     2.0,
    }

    def __init__(self, port=None, model_dir=None, viewer_dir=None,
                 http_port=None, config=None,
                 # Legacy kwargs from VTubeStudioAPI constructor
                 ws_url=None, plugin_name=None,
                 use_live2d_viewer=False, live2d_bridge=None):
        """
        Backwards-compatible constructor.
        Accepts both old Live2DBridge kwargs and VTubeStudioAPI kwargs.
        """
        # Parse from config dict if provided
        cfg = {}
        if config and isinstance(config, dict):
            cfg = config.get("live2d", {})

        self.ws_port   = port or cfg.get("viewer_port", self.WS_PORT)
        self.http_port = http_port or cfg.get("http_port", self.HTTP_PORT)
        self.model_dir = model_dir or cfg.get("model_path", "live2d_viewer/models/")
        self.viewer_dir = viewer_dir or self.VIEWER_DIR

        # Connection state
        self._clients = set()
        self._lock = threading.Lock()
        self._command_queue = []
        self._loop = None
        self._ws_thread = None
        self._http_thread = None
        self._running = False

        self.connected = False
        self.current_emotion = "neutral"
        self.current_mood = "neutral"
        self.is_talking = False

        # Model info received from browser
        self.available_motions = []
        self.available_expressions = []

        # Legacy compatibility
        self.use_live2d_viewer = True
        self.authenticated = True  # always "auth'd" since we control the viewer

        # Auto-detect model
        self._detected_model = None
        self._detect_model()

    def _detect_model(self):
        """Scan models folder for .model3.json"""
        viewer_abs = os.path.abspath(self.viewer_dir)
        patterns = [
            os.path.join(viewer_abs, "models", "**", "*.model3.json"),
            os.path.join(viewer_abs, "models", "*.model3.json"),
        ]
        for pattern in patterns:
            files = glob.glob(pattern, recursive=True)
            if files:
                rel = os.path.relpath(files[0], viewer_abs)
                self._detected_model = rel.replace("\\", "/")
                print(f"[Live2D] Modelo encontrado: {self._detected_model}")
                return
        print("[Live2D] ⚠ No se encontró modelo en models/")

    # ═══════════════════════════════════════════════════════════════════
    # START / STOP
    # ═══════════════════════════════════════════════════════════════════

    def _kill_port(self, port: int):
        """Kill any process holding the given port (Windows)."""
        import subprocess
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f':{port} ' in line and 'LISTENING' in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != os.getpid():
                        subprocess.run(['taskkill', '/F', '/PID', pid],
                                       capture_output=True, timeout=5)
                        time.sleep(0.3)
                        print(f"[Live2D] Puerto {port} liberado (PID {pid})")
        except Exception:
            pass

    def start(self):
        """Start HTTP server + WebSocket server in background threads."""
        if self._running:
            return
        self._running = True

        # Kill stale processes on our ports
        self._kill_port(self.ws_port)

        # Event to signal when WS server is ready
        self._ws_ready = threading.Event()

        # HTTP server (serves viewer files)
        self._http_thread = threading.Thread(
            target=self._run_http_server,
            daemon=True, name="Live2D-HTTP"
        )
        self._http_thread.start()

        # WebSocket server
        self._ws_thread = threading.Thread(
            target=self._run_ws_server,
            daemon=True, name="Live2D-WS"
        )
        self._ws_thread.start()

        # Wait for WS server to actually bind (max 5s)
        self._ws_ready.wait(timeout=5.0)

        print(f"[Live2D] ✓ HTTP → http://localhost:{self.http_port}")
        print(f"[Live2D] ✓ WebSocket → ws://localhost:{self.ws_port}")

    def stop(self):
        """Stop the bridge servers."""
        self._running = False
        self.connected = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        print("[Live2D] Bridge stopped.")

    # ═══════════════════════════════════════════════════════════════════
    # HTTP SERVER (serves live2d_viewer/ files + API)
    # ═══════════════════════════════════════════════════════════════════

    def _run_http_server(self):
        viewer_dir = os.path.abspath(self.viewer_dir)
        bridge = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=viewer_dir, **kwargs)

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                super().end_headers()

            def do_GET(self):
                # API endpoint — returns detected model path
                if self.path == "/api/model_path":
                    path = bridge._detected_model or ""
                    resp = json.dumps({"path": path}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", len(resp))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                super().do_GET()

            def log_message(self, fmt, *args):
                pass  # Silence HTTP logs

        # Use ThreadingHTTPServer so the browser can load 20+ expression
        # files concurrently without the server blocking.
        try:
            server = http.server.ThreadingHTTPServer(("", self.http_port), Handler)
            server.timeout = 1.0
            while self._running:
                server.handle_request()
        except OSError as e:
            if "10048" in str(e) or "address already in use" in str(e).lower():
                print(f"[Live2D] ⚠ Puerto {self.http_port} ocupado, usando {self.http_port + 1}")
                self.http_port += 1
                self._run_http_server()
            else:
                print(f"[Live2D] ✗ Error HTTP: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # WEBSOCKET SERVER
    # ═══════════════════════════════════════════════════════════════════

    def _run_ws_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_ws_server())
            self._ws_ready.set()  # Signal that WS is ready
            self._loop.run_forever()
        except Exception as e:
            self._ws_ready.set()  # Unblock start() even on failure
            if self._running:
                print(f"[Live2D] WebSocket error: {e}")

    async def _start_ws_server(self):
        import websockets
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await websockets.serve(
                    self._handle_client,
                    "0.0.0.0",  # Bind to IPv4 only to avoid dual-stack conflicts
                    self.ws_port,
                    ping_interval=20,
                    ping_timeout=10,
                )
                return  # Success
            except ImportError:
                print("[Live2D] ✗ websockets no instalado. pip install websockets")
                self._running = False
                return
            except OSError as e:
                if attempt < max_retries - 1:
                    self.ws_port += 1
                    print(f"[Live2D] ⚠ Puerto ocupado, reintentando en {self.ws_port}...")
                    await asyncio.sleep(0.5)
                else:
                    print(f"[Live2D] ✗ No se pudo iniciar WebSocket tras {max_retries} intentos: {e}")
                    self._running = False

    async def _handle_client(self, websocket, path=None):
        with self._lock:
            self._clients.add(websocket)
            self.connected = True
        print(f"[Live2D] ✓ Viewer conectado ({len(self._clients)} cliente(s))")

        # Send queued commands
        for cmd in self._command_queue:
            try:
                await websocket.send(json.dumps(cmd))
            except Exception:
                pass
        self._command_queue.clear()

        # Send model path
        if self._detected_model:
            await websocket.send(json.dumps({
                "action": "load_model",
                "path": self._detected_model
            }))

        # Send current mood
        await websocket.send(json.dumps({
            "action": "set_mood",
            "mood": self.current_mood
        }))

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get("type") == "model_info":
                        motions = data.get("motions", [])
                        expressions = data.get("expressions", [])
                        # Only update if we received actual data —
                        # empty arrays from tabs without a loaded model
                        # must NOT overwrite valid data.
                        if motions:
                            self.available_motions = motions
                        if expressions:
                            self.available_expressions = expressions
                        print(f"[Live2D] Motions: {self.available_motions}")
                        print(f"[Live2D] Expresiones: {self.available_expressions}")
                    elif data.get("type") == "viewer_ready":
                        print("[Live2D] Viewer listo.")
                    elif data.get("action"):
                        # Forward action commands to ALL other clients
                        # This allows test scripts to trigger expressions
                        msg = json.dumps(data)
                        for client in list(self._clients):
                            if client != websocket:
                                try:
                                    await client.send(msg)
                                except Exception:
                                    pass
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(websocket)
                self.connected = len(self._clients) > 0
            print(f"[Live2D] Viewer desconectado ({len(self._clients)} cliente(s))")

    def _send(self, data: dict):
        """Send command to all connected viewers."""
        if self._clients and self._loop:
            msg = json.dumps(data)
            async def _broadcast():
                dead = set()
                for client in list(self._clients):
                    try:
                        await client.send(msg)
                    except Exception:
                        dead.add(client)
                for d in dead:
                    self._clients.discard(d)
            # Use call_soon_threadsafe to schedule on the event loop
            # This is more reliable than run_coroutine_threadsafe from other threads
            try:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(_broadcast())
                )
            except RuntimeError:
                # Event loop might be closed
                self._command_queue.append(data)
        else:
            self._command_queue.append(data)
            if len(self._command_queue) > 20:
                self._command_queue = self._command_queue[-10:]

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API — called from main.py (replaces VTubeStudioAPI)
    # ═══════════════════════════════════════════════════════════════════

    def set_emotion(self, emotion: str, duration: float = 0):
        """Smooth emotional state transition."""
        self.current_emotion = emotion
        self._send({
            "action": "set_emotion",
            "emotion": emotion,
            "duration": duration
        })

    def trigger_reaction(self, reaction_name: str, duration: float = 1.5):
        """Wrapper de compatibilidad — usar trigger_react() directamente."""
        self.trigger_react(reaction_name)

    def trigger_react(self, reaction_name: str):
        """
        Called by process_visual_actions in main.py.
        Sends 'react' command to the viewer, which triggers the model's
        native .exp3.json expression (e.g. star eyes, tears, question marks).
        """
        dur = self.REACTION_DURATIONS.get(reaction_name, 1.8)

        # Send react — AnimationEngine.triggerReaction() handles the smooth
        # snap to the emotion preset and auto-return after duration
        self._send({
            "action":   "react",
            "emotion":  reaction_name,
            "duration": dur,
        })

        # Trigger rigged motions for specific reactions (these DON'T conflict
        # because motions control different parameters like arm/body movement)
        motion_map = {
            "greet":     ["HuiShou"],
            "laugh":     ["MeiYan"],
            "laughing":  ["MeiYan"],
        }
        motion_names = motion_map.get(reaction_name, [])
        if self.available_motions:
            for target in motion_names:
                for avail in self.available_motions:
                    if target.lower() in avail.lower():
                        self._send({"action": "motion", "name": avail, "group": avail})
                        break
        elif motion_names:
            self._send({"action": "motion", "name": motion_names[0], "group": motion_names[0]})

        print(f"[Live2D] React: {reaction_name} ({dur}s)")

    def set_talking(self, talking: bool, mood: str = "neutral"):
        """Called when TTS starts/stops."""
        self.is_talking = talking
        if mood != "neutral":
            self.current_mood = mood
        if talking:
            # mouth_speed basado en el humor actual
            speed_map = {
                "hyped": 1.3, "gremlin": 1.2, "excited": 1.35,
                "chill": 0.9, "bored": 0.85, "sad": 0.85,
                "neutral": 1.0, "focused": 1.0, "flustered": 1.1,
            }
            mouth_speed = speed_map.get(self.current_mood, 1.0)
            self._send({
                "action": "talking_start",    # viewer espera 'talking_start'
                "mouth_speed": mouth_speed,
            })
        else:
            self._send({"action": "talking_stop"})  # viewer espera 'talking_stop'

    def set_mood(self, mood: str):
        """Update mood preset in viewer."""
        self.current_mood = mood
        self._send({"action": "set_mood", "mood": mood, "sentiment": mood})

    # NOTE: set_emotion is already defined above (line ~304). Do NOT duplicate.
    # The primary set_emotion sends {action: 'set_emotion', emotion: X}
    # which the viewer handles in AnimationEngine.setEmotion().

    def animate_for_response(self, response_text: str, mood: str = "neutral") -> str:
        """
        Detecta emoción general y aplica el preset de humor en el viewer.
        Retorna la emoción detectada para el TTS.
        Nota: Ya NO lanza la reacción visual (trigger_react) aquí,
        para evitar que las expresiones salten todas al inicio.
        Ahora se lanzan chunk a chunk mediante process_visual_actions.
        """
        self.current_mood = mood
        emotion = self.detect_emotion_from_text(response_text)
        # Aplicar preset de humor
        self._send({"action": "set_mood", "mood": mood, "sentiment": emotion})
        return emotion

    def detect_sentiment(self, text: str) -> str:
        """Legacy wrapper → returns emotion string."""
        return self.detect_emotion_from_text(text)

    def detect_emotion_from_text(self, text: str) -> str:
        """Detect emotion from Aiko's response text."""
        t = text.lower()

        patterns = {
            "laughing":    ["jaja", "jeje", "jiji", "lol", "xd", "me muero", "😂"],
            "surprised":   ["qué!", "no puede ser", "espera", "cómo!", "wow", "omg", "no mames"],
            "annoyed":     ["ugh", "no manches", "odio", "me cae mal", "qué asco"],
            "excited":     ["sí!!", "genial", "increíble", "me encanta"],
            "thinking":    ["hmm", "creo que", "quizás", "a ver", "no sé", "pues"],
            "embarrassed": ["ay no", "qué pena", "bueno—", "me da pena"],
            "smug":        ["obvio", "como siempre", "lo sabía", "claro que"],
            "bored":       ["aburrido", "meh", "whatever", "me da igual"],
            "sad":         ["triste", "qué mal", "lástima", "😢"],
        }

        for emotion, keywords in patterns.items():
            if any(kw in t for kw in keywords):
                return emotion

        if t.count("!") >= 3:
            return "excited"
        if "?" in t and "!" in t:
            return "surprised"

        return "neutral"

    def trigger_expression(self, expression_name: str):
        """Legacy wrapper — maps to trigger_react."""
        self.trigger_react(expression_name)

    # ── Extra actions ──

    def spin(self, times: int = 1):
        """Full body spin animation."""
        self._send({"action": "spin", "times": times})

    def jump(self):
        """Jump animation."""
        self._send({"action": "jump"})

    def play_motion(self, group: str, index: int = 0):
        """Play a rigged motion from the model."""
        self._send({"action": "motion", "name": group, "group": group, "index": index})

    def play_expression(self, name: str):
        """Play a rigged expression."""
        self._send({"action": "expression", "name": name})

    def set_background_color(self, hex_color: str):
        """Legacy — ignored for transparent OBS."""
        pass

    def load_model(self, path: str):
        """Load a specific model by path."""
        self._send({"action": "load_model", "path": path})

    def send_mood(self, mood: str, sentiment: str = "neutral"):
        """Legacy wrapper for set_mood + set_emotion."""
        self.set_mood(mood)
        self.set_emotion(sentiment)

    def send_react(self, motion_group=None, duration_ms=3500, params=None, reaction_name=""):
        """Legacy wrapper for trigger_react."""
        if reaction_name:
            self.trigger_react(reaction_name)

    # ── Properties ──

    @property
    def live2d_bridge(self):
        """For dashboard compatibility — returns self."""
        return self

    @property
    def is_connected(self) -> bool:
        return self.connected and len(self._clients) > 0
