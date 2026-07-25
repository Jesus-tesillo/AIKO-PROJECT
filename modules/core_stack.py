"""
core_stack.py — Inicializador compartido del stack de Aiko.

Carga LLM, TTS/RVC, Live2D Bridge, Soul, Memory, Chat y Heartbeat
una sola vez.  Usado por aiko.py (interfaz maestra) para compartir
instancias entre los paneles de Stream, Video, Social y Tester.

También se puede usar desde main.py o video_mode.py para evitar
duplicar la inicialización.
"""

import os
import sys
import time
import threading
import webbrowser
import yaml


import shutil

def load_config(path="config.yaml") -> dict:
    """Carga y valida la configuración desde YAML."""
    if not os.path.exists(path) and os.path.exists("config.example.yaml"):
        print(f"[CoreStack] ⚠️  {path} no encontrado. Creando automáticamente desde config.example.yaml...")
        shutil.copy("config.example.yaml", path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        print(f"[CoreStack] ✓ Config cargada desde {path}")
        
        # Validar Groq API Key
        groq_key = cfg.get("groq", {}).get("api_key", "")
        if not groq_key or "YOUR_GROQ_API_KEY" in groq_key:
            print("\n" + "═" * 60)
            print("  ⚠️  CONFIGURACIÓN INCOMPLETA — GROQ API KEY REQUERIDA")
            print("═" * 60)
            print("  Aiko requiere una API Key de Groq (Gratuita) para pensar y responder.")
            print("\n  Pasos rápidos:")
            print("    1. Regístrate gratis en: https://console.groq.com")
            print("    2. Crea tu API Key en la sección 'API Keys'")
            print(f"    3. Pega la key en {path} en la línea 'api_key'")
            print("═" * 60 + "\n")
            
        return cfg
    except FileNotFoundError:
        print(f"[CoreStack] ✗ Config no encontrada: {path}. Crea {path} basándote en config.example.yaml")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[CoreStack] ✗ Error de formato YAML en {path}: {e}")
        sys.exit(1)



def init_core_stack(config: dict, open_viewer: bool = True,
                    on_status: callable = None,
                    full: bool = False) -> dict:
    """
    Inicializa los módulos base compartidos.

    Args:
        config:      Dict de configuración (desde load_config).
        open_viewer: Si True, abre el visor Live2D en el navegador.
        on_status:   Callback opcional (str) para reportar progreso.
        full:        Si True, inicializa también Soul, Memory, Chat,
                     Heartbeat y SafetyFilter (necesario para Stream).

    Returns:
        dict con llm, etts, vts, base_tts, config y (si full=True)
        soul_memory, identity, life_engine, autonomy, tribunal, gacha,
        memory, user_memory, lore_memory, chat_reader, chat_readers,
        heartbeat, safety_filter, stt, browser_agent.
    """
    def _status(msg):
        print(msg)
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    result = {"config": config}

    # ══════════════════════════════════════════════════════
    #  LLM (Groq)
    # ══════════════════════════════════════════════════════
    _status("[CoreStack] Conectando LLM...")
    from modules.llm import LLM
    groq_cfg = config["groq"]
    llm = LLM(
        api_key=groq_cfg["api_key"],
        model=groq_cfg["model"],
        temperature=groq_cfg.get("temperature", 0.92),
        max_tokens=groq_cfg.get("max_tokens", 150),
    )
    llm.check_connection()
    result["llm"] = llm
    _status(f"[CoreStack] ✓ LLM {'conectado' if llm.connected else 'FALLO'}")

    # ══════════════════════════════════════════════════════
    #  TTS + RVC
    # ══════════════════════════════════════════════════════
    _status("[CoreStack] Iniciando Motor de Voz (TTS)...")
    from modules.tts import TTS
    from modules.tts_emotion import EmotionalTTS

    applio_cfg = config.get("applio", {})
    gpt_sovits_cfg = config.get("gpt_sovits", {})

    base_tts = TTS(
        voice_model=config["tts"]["voice_model"],
        speed=config["tts"]["speed"],
        output_device=config["tts"]["output_device"],
        applio_path=applio_cfg.get("path") if applio_cfg.get("enabled") else None,
        rvc_model=applio_cfg.get("model") if applio_cfg.get("enabled") else None,
        rvc_index=applio_cfg.get("index", ""),
        rvc_pitch=applio_cfg.get("pitch", 0),
        rvc_f0_method=applio_cfg.get("f0_method", "rmvpe"),
        gpt_sovits_cfg=gpt_sovits_cfg,
    )
    etts = EmotionalTTS(base_tts, default_speed=config["tts"]["speed"])
    # Configurar dispositivo de monitoreo (para escuchar en auriculares mientras OBS usa CABLE)
    monitor_dev = config["tts"].get("monitor_device", "")
    if monitor_dev:
        base_tts.set_monitor_device(monitor_dev)
    result["base_tts"] = base_tts
    result["etts"] = etts
    _status("[CoreStack] ✓ TTS listo")

    # ══════════════════════════════════════════════════════
    #  Live2D
    # ══════════════════════════════════════════════════════
    _status("[CoreStack] Iniciando Live2D...")
    from modules.live2d_bridge import Live2DBridge
    live2d_cfg = config.get("live2d", {})
    vts = Live2DBridge(
        port=live2d_cfg.get("viewer_port", 8765),
        model_dir=live2d_cfg.get("model_path", "live2d_viewer/models"),
        viewer_dir="live2d_viewer",
        http_port=live2d_cfg.get("http_port", 8180),
    )
    vts.start()
    result["vts"] = vts

    if open_viewer:
        http_port = vts.http_port
        def _open():
            time.sleep(2)
            url = f"http://localhost:{http_port}/index.html"
            _status(f"[CoreStack] Abriendo visor: {url}")
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    # ══════════════════════════════════════════════════════
    #  FULL MODE: Soul, Memory, Chat, Heartbeat, etc.
    # ══════════════════════════════════════════════════════
    if full:
        # ── Safety Filter ─────────────────────────────────
        from modules.safety_filter import SafetyFilter
        result["safety_filter"] = SafetyFilter()

        # ── Memory ────────────────────────────────────────
        _status("[CoreStack] Iniciando Memory...")
        from modules.memory import Memory, UserMemory, LoreMemory
        result["memory"] = Memory(
            config["memory"]["max_messages"],
            config["memory"]["persist"],
        )
        result["user_memory"] = UserMemory()
        result["lore_memory"] = LoreMemory()

        # ── Soul Systems ──────────────────────────────────
        _status("[CoreStack] Iniciando Soul...")
        from soul.memory_engine import MemoryEngine
        from soul.identity import AikoIdentity
        from soul.life_engine import LifeEngine
        from soul.autonomy_engine import AutonomyEngine
        from content.tribunal import TribunalDelChat
        from content.gacha import GachaSimulator

        soul_memory = MemoryEngine(
            config.get("soul", {}).get("memory_db", "data/aiko.db")
        )
        identity = AikoIdentity()
        life_engine = LifeEngine(soul_memory, identity, groq_cfg["api_key"])
        autonomy = AutonomyEngine(soul_memory, identity)
        tribunal = TribunalDelChat(soul_memory, identity, groq_cfg["api_key"])
        gacha = GachaSimulator(soul_memory, identity, groq_cfg["api_key"])

        # Inyectar soul en LLM
        llm.memory_engine = soul_memory
        llm.identity = identity

        life_engine.start()

        result["soul_memory"] = soul_memory
        result["identity"] = identity
        result["life_engine"] = life_engine
        result["autonomy"] = autonomy
        result["tribunal"] = tribunal
        result["gacha"] = gacha

        # ── Heartbeat ─────────────────────────────────────
        _status("[CoreStack] Iniciando Heartbeat...")
        from modules.heartbeat import HeartbeatSystem
        heartbeat = HeartbeatSystem()
        heartbeat.start_stream()
        llm.inject_heartbeat(heartbeat)
        result["heartbeat"] = heartbeat

        # ── STT ───────────────────────────────────────────
        _status("[CoreStack] Iniciando STT...")
        from modules.stt import STT
        stt = STT(
            model_size=config["stt"]["model_size"],
            language=config["stt"]["language"],
            enabled=config["stt"]["enabled"],
        )
        if stt.enabled:
            stt.start_listening()
        result["stt"] = stt

        # ── Chat (Twitch + TikTok) ────────────────────────
        _status("[CoreStack] Iniciando Chat...")
        from modules.chat_reader import ChatReader, MultiChatReader
        from modules.tiktok_chat import TikTokChatReader

        chat_readers = []

        if "twitch" in config and config["twitch"].get("enabled", True):
            twitch_reader = ChatReader(
                channel=config["twitch"].get("channel", "vtuberaiko"),
                bot_name=config["twitch"].get("bot_name", "aikobot"),
                token=config["twitch"].get("token", ""),
            )
            chat_readers.append(twitch_reader)

        if "tiktok" in config and config["tiktok"].get("enabled", False):
            tiktok_cfg = config["tiktok"]
            tiktok_reader = TikTokChatReader(
                username=tiktok_cfg.get("username", "vtuberaiko"),
            )
            if tiktok_cfg.get("follow_batch_interval"):
                tiktok_reader._follow_batch_interval = float(
                    tiktok_cfg["follow_batch_interval"]
                )
            chat_readers.append(tiktok_reader)

        chat_reader = MultiChatReader(chat_readers)
        chat_reader.start()

        result["chat_readers"] = chat_readers
        result["chat_reader"] = chat_reader

        # ── Browser Agent ─────────────────────────────────
        _status("[CoreStack] Iniciando Browser Agent...")
        from modules.browser_agent import BrowserAgent
        from modules.browser_intelligence import BrowserIntelligence

        browser_cfg = config.get("browser", {})
        browser_intelligence = BrowserIntelligence(
            groq_api_key=config["groq"]["api_key"]
        )
        browser_agent = BrowserAgent(
            config=browser_cfg, intelligence=browser_intelligence
        )
        if browser_cfg.get("enabled", True):
            browser_agent.start()
        result["browser_agent"] = browser_agent

        # ── Events ────────────────────────────────────────
        events = None
        events_cfg = config.get("events", {})
        if events_cfg.get("enabled", False):
            _status("[CoreStack] Iniciando Eventos...")
            from modules.events import StreamEvents
            events = StreamEvents(
                events_file=events_cfg.get("events_file",
                                           "events/stream_events.json"),
                poll_interval=events_cfg.get("poll_interval", 3.0),
            )
            events.start()
        result["events"] = events

        # ── Dashboard ─────────────────────────────────────
        dash_cfg = config.get("dashboard", {})
        chess_bridge = None

        # ── Módulo de Ajedrez (inicializar ANTES del dashboard para inyectar endpoints) ──
        chess_cfg = config.get("chess", {})
        if chess_cfg.get("enabled", False):
            _status("[CoreStack] Iniciando módulo de Ajedrez...")
            try:
                from modules.chess_bridge import ChessBridge
                from modules.chess_scorer import ChessScorer
                chess_bridge = ChessBridge(
                    llm=result["llm"],
                    etts=result["etts"],
                    vts=result["vts"],
                    config=config,
                    scorer=ChessScorer(),
                )
                result["chess_bridge"] = chess_bridge
                _status("[CoreStack] ✓ ChessBridge listo")
            except Exception as e:
                _status(f"[CoreStack] ✗ Error iniciando Ajedrez: {e}")

        if dash_cfg.get("enabled", True):
            _status("[CoreStack] Iniciando Dashboard...")
            from modules.dashboard import Dashboard, dashboard_state
            dashboard = Dashboard(
                port=dash_cfg.get("port", 5000),
                chess_bridge=chess_bridge,   # None si ajedrez desactivado
            )
            dashboard_state["uptime_start"] = time.time()
            dashboard.start()
            result["dashboard"] = dashboard

        # ── Prompter ──────────────────────────────────────
        _status("[CoreStack] Iniciando Prompter...")
        from modules.prompter import Prompter, SessionTracker
        streamer_cfg = config.get("streamer", {})
        session_tracker = SessionTracker()
        prompter = Prompter(config=streamer_cfg,
                            session_tracker=session_tracker)
        prompter.set_vtuber_name(config["vtuber"]["name"])

        # Inyectar TikTok reader en prompter
        for r in chat_readers:
            if hasattr(r, 'chat_is_active'):
                prompter.set_tiktok_reader(r)
                print("[CoreStack] TikTok reader inyectado en Prompter")
                break

        result["prompter"] = prompter
        result["session_tracker"] = session_tracker

    time.sleep(3)
    _status("[CoreStack] ✓ Stack listo")
    return result
