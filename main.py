"""
╔══════════════════════════════════════════════════════════════════╗
║            ✦  AIKO  — VTuber en vivo  ✦                        ║
║         caótica · sarcástica · tuya · digital                   ║
╚══════════════════════════════════════════════════════════════════╝

Orquestador principal: conecta LLM, TTS, Live2D, Twitch, memoria,
eventos y dashboard en un solo pipeline de streaming.
"""

import os, sys, time, threading, webbrowser, signal, random
import yaml

# Forzar codificación en consola de Windows para evitar errores con caracteres ✓ y ✗
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.llm import LLM
from modules.tts import TTS
from modules.tts_emotion import EmotionalTTS
from modules.stt import STT
from modules.chat_reader import ChatReader, MultiChatReader
from modules.tiktok_chat import TikTokChatReader
from modules.live2d_bridge import Live2DBridge
from modules.browser_agent import BrowserAgent
from modules.browser_intelligence import BrowserIntelligence
from modules.prompter import Prompter, update_viewer_after_interaction, build_viewer_context, SessionTracker
from modules.memory import Memory, UserMemory, LoreMemory
from modules.events import StreamEvents
from modules.dashboard import Dashboard, dashboard_state
from modules.heartbeat import HeartbeatSystem
from modules.safety_filter import SafetyFilter

global_safety_filter = SafetyFilter()

# ── Soul / Personalidad ──────────────────────────────────────────────────────
from soul.memory_engine import MemoryEngine
from soul.identity import AikoIdentity
from soul.life_engine import LifeEngine
from soul.autonomy_engine import AutonomyEngine
from content.tribunal import TribunalDelChat
from content.gacha import GachaSimulator

running = True


def load_config(path="config.yaml") -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        print(f"[Main] ✓ Config cargada desde {path}")
        return cfg
    except FileNotFoundError:
        print(f"[Main] ✗ Config no encontrada: {path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[Main] ✗ Error al parsear config: {e}")
        sys.exit(1)


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║            ✦  AIKO  — VTuber en vivo  ✦                    ║
║         caótica · sarcástica · tuya · digital               ║
╚══════════════════════════════════════════════════════════════╝
    """)


import re as _re

# ───────────────────────────────────────────────────────────────────────────────
# STREAMING SPEECH ENGINE
# ───────────────────────────────────────────────────────────────────────────────

_speech_interrupt = threading.Event()


def _split_sentences(text: str) -> list:
    """
    Divide texto en ORACIONES COMPLETAS para TTS natural.
    Chunks de mínimo 20 palabras — asegura que el prefetch cubre la reproducción.
    """
    total_words = len(text.split())
    if total_words <= 30:
        return [text.strip()] if text else []

    raw = _re.split(r'(?<=[.!?])(?:\s+|$)', text)
    result = []
    buf = ""

    for part in raw:
        part = part.strip()
        if not part:
            continue
        buf = (buf + " " + part).strip() if buf else part

        word_count = len(buf.split())
        last_char = buf[-1] if buf else ""

        # 20 palabras mínimo — asegura que RVC del siguiente chunk termina
        # mientras el actual aún se reproduce (chunks cortos generan silencios)
        if last_char in ('.', '?', '!') and word_count >= 20:
            result.append(buf)
            buf = ""

    # Manejar el fragmento restante
    if buf:
        if result and len(buf.split()) <= 6:
            result[-1] = result[-1] + " " + buf
        else:
            result.append(buf)

    return result if result else [text]


def speak_streaming(text: str, etts, vts, mood: str, sentiment: str,
                    interrupt_fn=None) -> bool:
    """
    Reproduce texto como flujo continuo, sin silencio entre oraciones.

    ESTRATEGIA: pre-sintetizar el primer chunk ANTES de lanzar el worker,
    luego el worker procesa el resto en paralelo mientras el primero ya suena.
    Así nunca hay un gap al inicio, y cada siguiente chunk ya está listo.
    """
    import queue
    chunks = _split_sentences(text)
    if not chunks:
        return False

    SENTINEL = "__DONE__"
    audio_queue = queue.Queue()
    stop_synth  = threading.Event()

    def _synth_chunk(chunk):
        """Sintetiza un chunk y lo pone en la cola. Omite room-effect en hot path."""
        try:
            clean = process_visual_actions(chunk, None)
            if not clean.strip():
                return (chunk, None)
            # Sintetizar sin room effect (se aplica solo para respuestas lentas)
            em = sentiment
            orig_speed = etts.base_tts.speed
            etts.base_tts.speed = etts.get_speed_for_emotion(em)
            audio_path = etts.base_tts.synthesize(clean)
            etts.base_tts.speed = orig_speed
            return (chunk, audio_path)
        except Exception:
            return (chunk, None)

    # ── Pre-sintetizar el primer chunk ahora mismo (bloquea brevemente) ──
    first_chunk = chunks[0]
    rest_chunks  = chunks[1:]

    first_result = _synth_chunk(first_chunk)
    audio_queue.put(first_result)   # ya está listo antes de que empiece el worker

    # ── Worker procesa el resto en segundo plano ──
    def _synth_worker():
        for chunk in rest_chunks:
            if stop_synth.is_set():
                break
            audio_queue.put(_synth_chunk(chunk))
        audio_queue.put(SENTINEL)

    if rest_chunks:
        worker = threading.Thread(target=_synth_worker, daemon=True)
        worker.start()
    else:
        audio_queue.put(SENTINEL)   # solo había un chunk

    def _cleanup_queue():
        while not audio_queue.empty():
            item = audio_queue.get()
            if item and isinstance(item, tuple) and item[1]:
                try: os.remove(item[1])
                except: pass

    # ========== PLAYBACK LOOP ==========
    while True:
        if interrupt_fn and interrupt_fn():
            print("[Speech] Interrumpida entre oraciones.")
            stop_synth.set()
            _cleanup_queue()
            return True

        try:
            item = audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        if item == SENTINEL:
            break

        if item and isinstance(item, tuple):
            raw_chunk, audio_path = item

            # Reacciones y subtítulos para ESTA oración
            if raw_chunk and vts:
                process_visual_actions(raw_chunk, vts)
                vts._send({"action": "caption", "text": raw_chunk})

            if audio_path:
                def _on_audio_start():
                    vts.set_talking(True, mood=mood)

                def _on_volume(rms):
                    vts._send({"action": "mouth_volume", "value": rms})

                completed = etts.base_tts.play_audio(
                    audio_path, blocking=True,
                    on_start=_on_audio_start,
                    on_volume=_on_volume,
                )
                vts.set_talking(False, mood=mood)
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

                if not completed:
                    print("[Speech] Interrumpida durante reproducción.")
                    stop_synth.set()
                    _cleanup_queue()
                    return True

    return False



# ─────────────────────────────────────────────────────────────────────────────
# VISUAL ACTION PROCESSOR
# Strips annotation tags from TTS text and fires matching Live2D reactions.
# The LLM may output: "jajaja (risa)", "hola (saluda)", "*se ríe*", etc.
# ─────────────────────────────────────────────────────────────────────────────
_ACTION_PATTERNS = [
    # (regex, reaction_type or None=strip-only)
    # ── Risa ──────────────────────────────────────────────────────────
    (r'\(risa[s]?\)',                                           "laugh"),
    (r'\(laugh\)',                                              "laugh"),
    (r'\(se r[ií]e\)',                                          "laugh"),
    (r'\(r[ií]e\)',                                             "laugh"),
    (r'\*(?:risa[s]?|r[ií]e|se r[ií]e|laughs?|giggles?|chuckles?)\*', "laugh"),
    (r'\((?:sonr[íi][eo]|sonriendo)\)',                         "laugh"),
    # ── Saludo ────────────────────────────────────────────────────────
    (r'\(saluda[s]?\)',                                         "greet"),
    (r'\*(?:saluda?|wavo?|waves?|saludar)\*',                  "greet"),
    # ── Sorpresa ─────────────────────────────────────────────────────
    (r'\(oh[!]?\)',                                             "surprised"),
    (r'\(surprised\)',                                          "surprised"),
    (r'\*(?:sorprend\w+|surprised?|shocked?|gasps?)\*',        "surprised"),
    # ── Guiño ────────────────────────────────────────────────────────
    (r'\(guiña\)',                                              "wink"),
    (r'\*(?:guiña|winks?)\*',                                  "wink"),
    # ── Emocionada ───────────────────────────────────────────────────
    (r'\*(?:emocionad\w+|excit\w+|salta|jumps?|cheers?)\*',   "excited"),
    # ── Tristeza ─────────────────────────────────────────────────────
    (r'\*(?:triste|sad|llora?|cries?|sobs?)\*',               "sad"),
    (r'\(llora\)',                                              "sad"),
    (r'\(triste\)',                                             "sad"),
    (r'\(sad\)',                                                "sad"),
    # ── Pensativa / Duda ─────────────────────────────────────────────
    (r'\*(?:piens[ao]|thinks?|hmm+|considers?|analiz\w+)\*',  "thinking"),
    (r'\*(?:pensando|analizando)\*',                            "thinking"),
    (r'\((?:piensa|pensando|thinking|duda|analiza|analizando)\)', "thinking"),
    # ── Tímida ───────────────────────────────────────────────────────
    (r'\*(?:t[ií]mid\w+|blushing?|se ruboriza|averguenz\w+)\*', "shy"),
    (r'\(t[ií]mida\)',                                          "shy"),
    # ── Asco / Desdén ────────────────────────────────────────────────
    (r'\(asco\)',                                               "disgust"),
    (r'\(disgust\)',                                            "disgust"),
    (r'\*asco\*',                                               "disgust"),
    # ── Enojada / Angry ──────────────────────────────────────────────
    (r'\(angry\)',                                              "angry"),
    (r'\(enojada\)',                                            "angry"),
    (r'\*(?:angry|enojad\w+|furios\w+)\*',                    "angry"),
    # ── Presumida / Smug ─────────────────────────────────────────────
    (r'\(presumida\)',                                          "smug"),
    (r'\(smug\)',                                               "smug"),
    # ── Strip-only: estas NUNCA se pronuncian en voz alta ────────────
    (r'\(suspi[ar][ao]\)',                                      None),
    (r'\*(?:suspira?|sigh[s]?)\*',                              None),
    # Palabras meta que el LLM a veces genera como texto literal
    (r'\(pausa(?: larga)?\)',                                   None),
    (r'\(silencio\)',                                            None),
    (r'\(se detiene\)',                                          None),
    (r'\(se queda callad[ao]\)',                                 None),
    (r'\(interrumpe\)',                                          None),
    (r'\*pausa[s]?\*',                                          None),
    (r'\*silencio\*',                                           None),
    # Metadatos de tono generados por el LLM
    (r'\((?:sarcástica|sarcástico|sarcasmo|iron[íi]a|ir[óo]nica)\)', None),
    (r'\((?:burlona|seria|enojada|molesta|susurra|grita|feliz|alegre|calmada|tranquila)\)', None),
    # Captura general (borrar todas las palabras solas entre paréntesis al principio del texto ej: "(sonriendo) hola")
    (r'^\s*\([A-Za-zñÑáéíóúÁÉÍÓÚ]+\)\s*',                       None),
]


def process_visual_actions(text: str, vts) -> str:
    """
    Strip visual action annotations from text (so TTS doesn't speak them)
    and trigger the matching Live2D reactions.
    Also detects content-based cues: jaja → laugh, hola at start → greet.
    Returns TTS-ready text.
    """
    clean     = text
    triggered = set()

    # Search for known patterns to trigger reactions
    for pattern, reaction in _ACTION_PATTERNS:
        if _re.search(pattern, clean, _re.IGNORECASE):
            if reaction and reaction not in triggered:
                if vts:
                    vts.trigger_react(reaction)
                triggered.add(reaction)

    # ── Content-based triggers (only very obvious cases) ────────────
    if vts:
        # Risa: jaja/jeje/lol → laugh
        if "laugh" not in triggered:
            if any(kw in clean.lower() for kw in ["jaja", "jeje", "jiji", "lol", "xd", "😂", "🤣"]):
                vts.trigger_react("laugh")

        # Saludo SOLO al inicio de la oración o cuando usa la palabra clave
        if "greet" not in triggered:
            low = clean.lower().strip()
            if any(low.startswith(kw) for kw in ["hola", "hey", "buenas", "qué tal"]) or any(kw in low for kw in ["saludo", "saludar", "saludando", "hola "]):
                vts.trigger_react("greet")

        # Sorpresa por expresiones fuertes (no preguntas normales)
        if "surprised" not in triggered:
            low = clean.lower()
            if any(kw in low for kw in ["no mames", "no way", "omg", "😱", "no puede ser"]):
                vts.trigger_react("surprised")

        # Pensativa (solo en expresiones muy claras)
        if "thinking" not in triggered:
            low = clean.lower()
            if any(kw in low for kw in ["hmm", "hmmm", "a ver...", "no sé..."]):
                vts.trigger_react("thinking")

    # ── Failsafe: Remove ALL actions in asterisks and parentheses ──
    # Para asegurar que la voz nunca narre las acciones
    clean = _re.sub(r'\*[^*]+\*', '', clean)
    clean = _re.sub(r'\([^)]+\)', '', clean)

    # Colapsar espacios extra dejados por anotaciones removidas
    clean = _re.sub(r' {2,}', ' ', clean).strip()
    return clean



def response_pipeline(llm, etts, vts, memory, user_memory, config,
                       text="", username=None, is_spontaneous=False,
                       emotion="neutral", spontaneous_trigger="",
                       event_prompt="", greet_regular=False,
                       activity="", mood="", mood_desc="",
                       chat_prompt="",
                       prompter=None,
                       session_anchor="", monologue_thread=None,
                       grounding_context="",
                       viewer_context="",
                       was_interrupted=False,
                       soul_memory=None,
                       lore_memory=None):
    """
    Pipeline completo: LLM → Emoción TTS → animación Live2D → audio.
    """
    system_prompt = config["vtuber"]["personality"]
    vtuber_name = config["vtuber"]["name"]

    # Verificar si está muteada desde el dashboard
    if Dashboard.is_muted():
        print("[Pipeline] Muteada, saltando.")
        return

    try:
        chat_context = memory.get_formatted_context(last_n=20)
        user_context = ""

        # Contexto de interrupción: el LLM sabe que fue cortada
        interrupt_prefix = ""
        if was_interrupted and not is_spontaneous and not event_prompt:
            interrupt_prefix = (
                "Acababan de interrumpirte mientras hablabas. "
                "Reacciona de forma natural — puedes retomar lo que decías "
                "o simplemente seguir con el nuevo tema. "
            )

        # ── Generar respuesta LLM ────────────────────────────
        if event_prompt:
            # Respuesta a evento del stream
            response = llm.generate_event_response(system_prompt, event_prompt, emotion)
            print(f"\n[{vtuber_name}] 🎉 Evento: {response}")

        elif is_spontaneous:
            lore_ctx = lore_memory.get_random_lore() if lore_memory else ""
            response = llm.generate_spontaneous(
                system_prompt, chat_context, emotion, spontaneous_trigger,
                activity=activity, mood=mood, mood_desc=mood_desc,
                session_anchor=session_anchor,
                monologue_thread=monologue_thread or [],
                grounding_context=grounding_context,
                lore_context=lore_ctx,
            )
            # Registrar en el hilo para continuidad natural
            if prompter and response and response != "...":
                prompter.add_to_thread("Aiko", response, is_aiko=True)

        else:
            # Respuesta regular al chat
            if username:
                memory.add_message("user", username, text)
                Dashboard.add_chat_message(username, text)
                if user_memory:
                    user_memory.update_user(username, text)
                    # Combinar contexto dinámico de SQLite con Stats JSON 
                    base_viewer = viewer_context or ""
                    stats_viewer = user_memory.get_user_context(username)
                    viewer_context = f"{stats_viewer} {base_viewer}".strip()

            # Saludo para regulares
            greeting_hint = ""
            if greet_regular and username:
                greeting_hint = f" (Saluda a {username} calurosamente, es un regular!)"

            effective_chat_prompt = (
                interrupt_prefix + (chat_prompt or f"{username}: {text}")
            ) if interrupt_prefix else chat_prompt

            # — Inyectar contexto del navegador si Aiko está viendo algo —
            if prompter:
                _ba = getattr(prompter, '_browser_agent_ref', None)
                if _ba:
                    browser_ctx = _ba.get_current_context()
                    if browser_ctx and effective_chat_prompt:
                        effective_chat_prompt = (
                            f"{browser_ctx}\n{effective_chat_prompt}"
                        )

            lore_ctx = lore_memory.get_random_lore() if lore_memory else ""

            response = llm.generate(
                system_prompt + greeting_hint,
                chat_context, text, username or "chat",
                user_context=viewer_context,
                emotion=emotion,
                activity=activity, mood=mood, mood_desc=mood_desc,
                chat_prompt=effective_chat_prompt,
                grounding_context=grounding_context,
                was_interrupted=was_interrupted,
                lore_context=lore_ctx,
            )

            # Registrar la respuesta de Aiko en el hilo
            if prompter and response and response != "...":
                prompter.add_to_thread("Aiko", response, is_aiko=True)
            # Actualizar memoria del viewer (SQLite) con el intercambio
            if username and response and response != "...":
                update_viewer_after_interaction(
                    username, text, response, soul_memory
                )

        # Saltar respuestas vacías
        if not response or response == "..." or len(response.strip()) < 3:
            return

        # ── Aplicar Peripheral Safety Filter ────────────────────────────
        response = global_safety_filter.filter_response(response)

        if prompter and prompter.session_tracker:
            prompter.session_tracker.log_topic(response)

        # Guardar respuesta
        memory.add_message("assistant", vtuber_name, response)
        Dashboard.add_response(response)

        # Actualizar top viewers
        if user_memory:
            Dashboard.update_top_viewers(user_memory.get_top_viewers(5))

        # ── Strip visual annotations for logging (reactions fire per-sentence in speak_streaming) ──
        response_for_tts = process_visual_actions(response, None)  # None = don't trigger here
        if not response_for_tts or len(response_for_tts.strip()) < 2:
            response_for_tts = response  # fallback to original if stripped empty

        # Log cleaned (after strip so no meta-annotations show)
        if is_spontaneous:
            print(f"\n[{vtuber_name}] (monólogo): {response_for_tts}")
        elif username:
            print(f"\n[{vtuber_name}] → {username}: {response_for_tts}")

        # ── Animar expresión ───────────────────────────────────────────────────
        sentiment = vts.animate_for_response(response, mood=mood)
        etts.set_emotion(sentiment)
        Dashboard.set_emotion(sentiment)

        # ── Sintetizar y reproducir ───────────────────────────────────────
        def _interrupt_check():
            return _speech_interrupt.is_set()

        _speech_interrupt.clear()
        etts.base_tts._interrupt_flag.clear()

        # Notificar al browser que Aiko está hablando (sin condición errada)
        _ba_pipe = getattr(prompter, '_browser_agent_ref', None) if prompter else None
        if _ba_pipe:
            _ba_pipe.set_speaking(True)
        speak_streaming(
            response, etts, vts, mood, sentiment,
            interrupt_fn=_interrupt_check
        )
        # Notificar al browser que terminó
        if _ba_pipe:
            _ba_pipe.set_speaking(False)

    except Exception as e:
        print(f"[Pipeline] Error: {e}")
        vts.set_talking(False)


def main_loop(llm, etts, stt, chat_reader, vts, prompter,
              memory, user_memory, lore_memory, events, config,
              soul_memory, identity, life_engine, autonomy, tribunal, gacha, browser_agent, heartbeat,
              chess_bridge=None):
    """Bucle principal: chat → prompter → pipeline."""
    global running
    vtuber_name = config["vtuber"]["name"]
    print(f"\n[Main] 🎬 ¡{vtuber_name} está EN VIVO!\n")

    # El tema del día se genera en el Prompter. Podemos sobreescribirlo con el LLM
    # si queremos un anchor más rico; usamos el que ya tiene el prompter.
    anchor = prompter.daily_anchor
    print(f"[Main] 🏷️  Tema del día: {anchor}")
    
    # Buffer para mensajes del chat recibidos mientras Aiko habla
    chat_buffer = []

    # ── Chat watcher: lee chat en hilo de fondo mientras Aiko habla ──
    _chat_watcher_active = threading.Event()
    _pending_interrupt_msg = [None]  # list-cell para closure
    _extra_chat_msgs = []  # mensajes extra que llegan durante el habla
    _extra_chat_msgs_lock = threading.Lock()  # protege _extra_chat_msgs (acceso multi-hilo)
    _last_interrupt_time = [0.0]  # cooldown para no interrumpir cada segundo
    INTERRUPT_COOLDOWN = 10.0  # segundos entre interrupciones permitidas

    _actions_since_extraction = 0  # contador para el extractor automático de lore
    
    # ── Timers de inactividad ──
    last_chat_time = time.time()
    last_scroll_time = time.time()

    def _chat_watcher():
        """Hilo que lee el chat mientras response_pipeline se ejecuta.

        Comportamiento:
        - Solo interrumpe si pasaron >10s desde la última interrupción
        - NO corta el audio a mitad de palabra — solo marca _speech_interrupt 
          que se chequea entre oraciones (Aiko termina la oración actual)
        - Si llegan más mensajes durante el habla, los agrupa en _extra_chat_msgs
        """
        while running:
            _chat_watcher_active.wait()  # Dormir hasta que se active
            if not running:
                break
            already_interrupted = False
            while _chat_watcher_active.is_set() and running:
                msg = chat_reader.get_message()
                if msg:
                    now = time.time()
                    
                    # Cooldown dinámico: base 8s + 3s por cada mensaje extra en cola (máx 25s)
                    # Si mucha gente habla, Aiko completará más sus oraciones/monólogos
                    # en lugar de cortarse constantemente.
                    mensajes_en_cola = len(chat_buffer) + len(_extra_chat_msgs)
                    dynamic_cooldown = min(8.0 + (mensajes_en_cola * 3.0), 25.0)

                    can_interrupt = (
                        not already_interrupted
                        and (now - _last_interrupt_time[0]) > dynamic_cooldown
                    )

                    if can_interrupt:
                        # Primer mensaje y fuera de cooldown → interrumpir suavemente
                        _pending_interrupt_msg[0] = msg
                        # Solo setear _speech_interrupt — NO _interrupt_flag
                        # Así Aiko TERMINA su oración actual antes de parar
                        _speech_interrupt.set()
                        _last_interrupt_time[0] = now
                        already_interrupted = True
                        msg_preview = msg.get('message', '')[:40]
                        print(f"[Speech] 💬 Mensaje de {msg['user']} (responderá al terminar oración): {msg_preview}")
                    else:
                        # Ya interrumpida o en cooldown → guardar para después
                        with _extra_chat_msgs_lock:
                            _extra_chat_msgs.append(msg)

                time.sleep(0.15)  # Poll cada 150ms

    watcher_thread = threading.Thread(target=_chat_watcher, daemon=True)
    watcher_thread.start()

    # ── SALUDO INICIAL DEL STREAM ──
    if prompter.SPONTANEOUS_CHANCE > 0.0:
        print("[Main] 🎙️ Generando intro de stream...")
        response_pipeline(
            llm, etts, vts, memory, user_memory, config,
            is_spontaneous=True,
            emotion="hyped",
            spontaneous_trigger=f"[ACCIÓN] Acabas de encender cámara para iniciar tu transmisión en vivo. Saluda a tus espectadores con toda tu personalidad caótica, da la bienvenida, y menciona brevemente de qué tienes ganas de hablar hoy ({anchor}). Máximo 2 oraciones.",
            activity="Iniciando transmisión",
            mood="hyped",
            mood_desc="Recién prendida, con toda la energía para streamear",
            prompter=prompter,
            soul_memory=soul_memory,
            lore_memory=lore_memory,
        )
    else:
        print("[Main] 🎙️ Omitiendo intro general (Modo Gaming activo)")
    last_chat_time = time.time()
    last_scroll_time = time.time()


    while running:
        try:
            # Actualizar estados de módulos en el dashboard
            Dashboard.update_module("llm", llm.connected)
            Dashboard.update_module("twitch", chat_reader.connected)
            Dashboard.update_module("tts", etts.base_tts._piper_available)
            Dashboard.update_module("stt", stt.is_listening if stt.enabled else False)

            Dashboard.update_module("live2d", vts.connected)

            Dashboard.update_module("events", events._running if events else False)

            # ── Drenar cola y Agrupar mensajes por usuario ────
            while True:
                msg_poll = chat_reader.get_message()
                if not msg_poll:
                    break
                chat_buffer.append(msg_poll)
                
            # TikFinity polling eliminado — TikTok ahora usa WebSocket directo
            # Los mensajes de TikTok llegan via MultiChatReader.get_message()

            if chat_buffer:
                grouped_buffer = []
                current_grp = chat_buffer[0]
                for msg_poll in chat_buffer[1:]:
                    if msg_poll['user'] == current_grp['user']:
                        # Un usuario mandó fragmentos muy rápido, los unimos
                        current_grp['message'] += " " + msg_poll['message']
                    else:
                        grouped_buffer.append(current_grp)
                        current_grp = msg_poll
                grouped_buffer.append(current_grp)
                chat_buffer = grouped_buffer

            # ── Si Aiko sigue hablando, esperar ─────────────────────────────
            _ba_ref = getattr(prompter, '_browser_agent_ref', None)
            _browser_is_speaking = _ba_ref._aiko_speaking.is_set() if _ba_ref else False
            if (etts.is_speaking or _browser_is_speaking) and not chat_buffer:
                time.sleep(0.1)
                continue

            now = time.time()

            # ── PRIORIDAD 1: Eventos del stream (Regalos, Subs, Follows) ────
            if events and events.has_events():
                event = events.get_next_event()
                if event:
                    heartbeat.log_event("events_triggered")
                    event_prompt = events.generate_event_prompt(event)
                    event_user = event.get("user", "alguien")
                    
                    # Activar watcher durante la respuesta al evento
                    _pending_interrupt_msg[0] = None
                    _chat_watcher_active.set()
                    
                    # Pasar el evento por el pipeline NORMAL con contexto completo
                    # para que Aiko responda orgánicamente con su personalidad
                    response_pipeline(
                        llm, etts, vts, memory, user_memory, config,
                        text=event_prompt, username=event_user,
                        emotion="excited",
                        mood=prompter.current_mood,
                        mood_desc="",
                        grounding_context=prompter.get_grounding_context(),
                        prompter=prompter,
                        soul_memory=soul_memory,
                        lore_memory=lore_memory,
                    )
                    
                    _chat_watcher_active.clear()
                    with _extra_chat_msgs_lock:
                        if _extra_chat_msgs:
                            chat_buffer.extend(_extra_chat_msgs)
                            _extra_chat_msgs.clear()
                    if _pending_interrupt_msg[0]:
                        chat_buffer.insert(0, _pending_interrupt_msg[0])
                        _pending_interrupt_msg[0] = None
                    continue

            # ── PRIORIDAD 2: STT (entrada de micrófono) ────────────────────
            if stt.enabled and stt.has_transcript():
                transcript = stt.get_transcript()
                if transcript:
                    response_pipeline(
                        llm, etts, vts, memory, user_memory, config,
                        text=transcript["text"], username="Streamer",
                        emotion=prompter.current_mood,
                        soul_memory=soul_memory,
                    )
                    continue

            # ── PRIORIDAD 3: Botón de forzar comentario del dashboard ──────
            if Dashboard.check_force_spontaneous():
                decision = prompter.force_spontaneous()
                response_pipeline(
                    llm, etts, vts, memory, user_memory, config,
                    is_spontaneous=True,
                    emotion=decision.get("emotion", "neutral"),
                    spontaneous_trigger=decision.get("spontaneous_trigger", ""),
                    activity=decision.get("activity", ""),
                    mood=decision.get("mood", ""),
                    mood_desc=decision.get("mood_desc", ""),
                    soul_memory=soul_memory,
                    lore_memory=lore_memory,
                )
                continue

            # Si Aiko está navegando y llega chat, interrumpirla
            if browser_agent.is_browsing and chat_buffer:
                browser_agent.interrupt_browsing()


            # ── PRIORIDAD 5: Leer siguiente mensaje del chat ────
            _from_buffer = False
            if chat_buffer:
                message = chat_buffer.pop(0)
                _from_buffer = True
                heartbeat.log_event("messages_read")
            else:
                message = None

            # Content triggers
            if message and message.get("message", "").startswith("!caso"):
                case_text = message["message"][6:].strip()
                result = tribunal.submit_case(message["user"], case_text)
                print(f"[Tribunal] {result}")

            if message and message.get("message", "").startswith("!gacha"):
                prompt = gacha.start_banner_vote()
                print(f"[Gacha] Votación iniciada")

            if message and message.get("message", "").startswith("!stats"):
                stats_str = gacha.get_stats_summary()
                print(f"[Gacha] Stats: {stats_str}")

            if message and gacha.voting_active and message.get("message", "").strip() in ["1","2","3","4","5"]:
                gacha.register_vote(message["user"], message["message"].strip())

            # ── Procesar eventos de TikTok (regalos, follows, subs) ───────────────────────
            _tiktok_rdr = getattr(prompter, '_tiktok_reader', None)
            if _tiktok_rdr:
                tt_event = _tiktok_rdr.get_event()
                if tt_event:
                    etype = tt_event.get("type")
                    euser = tt_event.get("user", "alguien")

                    if etype == "follow":
                        follow_prompt = (
                            f"{euser} acaba de seguirte en TikTok. "
                            "Reacciona brevemente (máx 1 oración) de forma genuina, "
                            "sin exagerar. Puedes saludar o solo reconocerlo."
                        )
                        response_pipeline(
                            llm, etts, vts, memory, user_memory, config,
                            text="", username=euser, is_spontaneous=True,
                            chat_prompt=follow_prompt,
                            emotion="happy", mood=prompter.current_mood,
                            prompter=prompter, soul_memory=soul_memory,
                            lore_memory=lore_memory,
                        )

                    elif etype == "follow_batch":
                        count = tt_event.get("count", 2)
                        batch_prompt = (
                            f"{count} personas nuevas acaban de seguirte en TikTok. "
                            "Reacciona brevemente, agradece al grupo. Máx 1 oración."
                        )
                        response_pipeline(
                            llm, etts, vts, memory, user_memory, config,
                            text="", username="chat", is_spontaneous=True,
                            chat_prompt=batch_prompt,
                            emotion="happy", mood=prompter.current_mood,
                            prompter=prompter, soul_memory=soul_memory,
                            lore_memory=lore_memory,
                        )

                    elif etype == "gift":
                        gift_name = tt_event.get("gift", "regalo")
                        count = tt_event.get("count", 1)
                        diamonds = tt_event.get("diamonds", 0)
                        gift_prompt = (
                            f"{euser} te mandó {count}x {gift_name} "
                            f"({diamonds} diamantes) en TikTok. "
                            "Reacciona brevemente (máx 1 oración), puede ser sarcasmo, "
                            "gratitud genuina o sorpresa. No exageres."
                        )
                        emotion = "hyped" if diamonds >= 500 else "excited"
                        response_pipeline(
                            llm, etts, vts, memory, user_memory, config,
                            text="", username=euser, is_spontaneous=True,
                            chat_prompt=gift_prompt,
                            emotion=emotion, mood=prompter.current_mood,
                            prompter=prompter, soul_memory=soul_memory,
                            lore_memory=lore_memory,
                        )
                        # Guardar en memoria si es un regalo grande
                        if diamonds >= 100 and soul_memory:
                            soul_memory.remember(
                                type="tiktok_gift",
                                content=f"@{euser} mandó {gift_name} x{count} ({diamonds}💎) en TikTok",
                                emotional_weight=min(1.0, diamonds / 1000),
                                viewer=euser,
                                tags=["tiktok", "regalo"],
                            )

                    elif etype == "subscribe":
                        sub_prompt = (
                            f"{euser} se suscribió en TikTok. "
                            "Reacciona natural y breve. Sin discurso de agradecimiento."
                        )
                        response_pipeline(
                            llm, etts, vts, memory, user_memory, config,
                            text="", username=euser, is_spontaneous=True,
                            chat_prompt=sub_prompt,
                            emotion="hyped", mood=prompter.current_mood,
                            prompter=prompter, soul_memory=soul_memory,
                            lore_memory=lore_memory,
                        )

            # ── Navegación desde el chat: "busca X", "mira X", "abre X" ──
            if message and browser_agent.page is not None:
                msg_text = message.get("message", "").strip()
                msg_low = msg_text.lower()
                search_query = None
                search_platform = "google"

                # Detectar comando de búsqueda
                match = _re.search(
                    r'\b(busca|buscar|mira|abre|search)\s+(.+)',
                    msg_low
                )
                if match:
                    keyword_start = msg_low.index(match.group(1))
                    query_start = keyword_start + len(match.group(1)) + 1
                    search_query = msg_text[query_start:].strip()

                    # Detectar plataforma
                    if "youtube" in search_query.lower():
                        search_platform = "youtube"
                        search_query = (search_query.lower()
                            .replace("en youtube", "")
                            .replace("youtube", "").strip())
                    elif "tiktok" in search_query.lower():
                        search_platform = "tiktok"
                    elif "twitter" in search_query.lower():
                        search_platform = "twitter"
                        search_query = (search_query.lower()
                            .replace("en twitter", "")
                            .replace("twitter", "").strip())

                if search_query:
                    print(f"[Browser] 🔍 {message['user']} pidió buscar: "
                          f"{search_query} en {search_platform}")
                    browser_agent.search_from_chat(
                        search_query, search_platform
                    )
                    message = None  # Ya procesamos este mensaje

            # ── Comandos de Ajedrez ─────────────────────────────────────────
            chess_bridge = shared_stack.get("chess_bridge") if "shared_stack" in dir() else None
            if not chess_bridge:
                chess_bridge = getattr(__builtins__, "_chess_bridge_ref", None)

            if message and chess_bridge is not None:
                _chess_cfg   = config.get("chess", {})
                _chess_cmd   = _chess_cfg.get("chat_command", "!chess").lower()
                _msg_chess   = message.get("message", "").strip()
                _msg_chess_l = _msg_chess.lower()
                _chess_user  = message.get("user", "")

                # ── !chess @oponente ─────────────────────────────────────
                if _msg_chess_l.startswith(_chess_cmd):
                    # Extraer oponente: "!chess @Rival" o "!chess Rival"
                    rest = _msg_chess[len(_chess_cmd):].strip().lstrip("@").strip()
                    if not rest:
                        # Sin oponente → el propio quien escribe desafía a Aiko
                        rest = _chess_user

                    if chess_bridge.is_active:
                        # Ya hay partida — informar
                        response_pipeline(
                            llm, etts, vts, memory, user_memory, config,
                            text="", username=_chess_user, is_spontaneous=True,
                            chat_prompt=(
                                f"{_chess_user} quiere jugar ajedrez pero ya hay una partida "
                                f"activa contra {chess_bridge._opponent}. "
                                "Dile que espere su turno, con actitud."
                            ),
                            emotion="focused", mood=prompter.current_mood,
                            prompter=prompter, soul_memory=soul_memory,
                            lore_memory=lore_memory,
                        )
                    else:
                        result = chess_bridge.start_match(rest, aiko_color="random")
                        if result["ok"]:
                            pass  # chess_bridge habla el intro en _game_loop
                        else:
                            response_pipeline(
                                llm, etts, vts, memory, user_memory, config,
                                text="", username=_chess_user, is_spontaneous=True,
                                chat_prompt=f"No pude iniciar la partida de ajedrez: {result.get('error','error')}. Explícalo brevemente.",
                                emotion="bored", mood=prompter.current_mood,
                                prompter=prompter, soul_memory=soul_memory,
                                lore_memory=lore_memory,
                            )
                    message = None

                # ── !vote e2e4 ───────────────────────────────────────────
                elif _msg_chess_l.startswith("!vote "):
                    uci = _msg_chess_l.replace("!vote", "").strip()
                    vr  = chess_bridge.vote_move(_chess_user, uci)
                    if vr.get("ok"):
                        print(f"[Chess] Voto registrado: {_chess_user} -> {uci} (total: {vr['votes']})")
                    message = None

                # ── !rank ────────────────────────────────────────────────
                elif _msg_chess_l.strip() == "!rank":
                    stats = chess_bridge.scorer.get_stats_summary(_chess_user)
                    response_pipeline(
                        llm, etts, vts, memory, user_memory, config,
                        text="", username=_chess_user, is_spontaneous=True,
                        chat_prompt=(
                            f"El espectador {_chess_user} pregunta su ranking de ajedrez. "
                            f"Sus estadísticas: {stats}. "
                            "Léelas en voz alta brevemente con tu estilo."
                        ),
                        emotion="focused", mood=prompter.current_mood,
                        prompter=prompter, soul_memory=soul_memory,
                        lore_memory=lore_memory,
                    )
                    message = None

                # ── !leaderboard ─────────────────────────────────────────
                elif _msg_chess_l.strip() in ("!leaderboard", "!top", "!ranking"):
                    lb = chess_bridge.scorer.get_leaderboard(5)
                    if lb:
                        lb_txt = " | ".join(
                            f"#{p['rank']} {p['username']} {p['points']}pts"
                            for p in lb
                        )
                    else:
                        lb_txt = "nadie ha jugado aún"
                    response_pipeline(
                        llm, etts, vts, memory, user_memory, config,
                        text="", username=_chess_user, is_spontaneous=True,
                        chat_prompt=(
                            f"El chat pide ver el ranking de ajedrez. "
                            f"Top jugadores: {lb_txt}. "
                            "Anúncialos en voz alta con energía, como presentador de torneo."
                        ),
                        emotion="hyped", mood=prompter.current_mood,
                        prompter=prompter, soul_memory=soul_memory,
                        lore_memory=lore_memory,
                    )
                    message = None

            # Comandos ya procesados — no generar respuesta LLM para ellos
            if message and message.get("message", "").startswith("!"):
                message = None

            # ── Pasar browser_available al Prompter para que decida si navegar ──
            _browser_available = (
                browser_agent.page is not None
                and not browser_agent.is_browsing
                and not etts.is_speaking
                and not chat_buffer
            )

            if message and _from_buffer:
                decision = prompter.should_respond(message, soul_memory)
                # Forzar respuesta si el prompter decidió ignorarlo
                if decision["action"] != "respond":
                    decision = {
                        "action": "respond",
                        "message": message,
                        "emotion": prompter.current_mood,
                        "mood": prompter.current_mood,
                        "mood_desc": "",
                        "grounding_context": prompter.get_grounding_context(),
                        "viewer_context": "",
                    }
            else:
                decision = prompter.should_respond(message, soul_memory,
                                                   browser_available=_browser_available)

            if decision["action"] == "respond" and decision["message"]:
                msg = decision["message"]
                # Pasar el flag was_interrupted para que el LLM reaccione naturalmente
                _was_interrupted = _speech_interrupt.is_set()
                _speech_interrupt.clear()

                # Activar watcher de chat durante el habla
                _pending_interrupt_msg[0] = None
                _chat_watcher_active.set()

                response_pipeline(
                    llm, etts, vts, memory, user_memory, config,
                    text=msg["message"], username=msg["user"],
                    emotion=decision.get("emotion", "neutral"),
                    greet_regular=decision.get("greet_regular", False),
                    activity=decision.get("activity", ""),
                    mood=decision.get("mood", ""),
                    mood_desc=decision.get("mood_desc", ""),
                    chat_prompt=decision.get("chat_prompt", ""),
                    grounding_context=decision.get("grounding_context", ""),
                    viewer_context=decision.get("viewer_context", ""),
                    was_interrupted=_was_interrupted,
                    prompter=prompter,
                    soul_memory=soul_memory,
                    lore_memory=lore_memory,
                )

                # Desactivar watcher y recoger mensajes
                _chat_watcher_active.clear()

                # Recoger mensajes extra que llegaron durante el habla
                with _extra_chat_msgs_lock:
                    if _extra_chat_msgs:
                        chat_buffer.extend(_extra_chat_msgs)
                        _extra_chat_msgs.clear()

                # Si hubo interrupción, procesar el mensaje pendiente inmediatamente
                if _pending_interrupt_msg[0]:
                    chat_buffer.insert(0, _pending_interrupt_msg[0])
                    _pending_interrupt_msg[0] = None

                # Chat recibido → romper modo flujo si estaba activo
                prompter.exit_flow()

            elif decision["action"] == "monologue":
                # Activar watcher de chat durante monólogo
                _pending_interrupt_msg[0] = None
                _chat_watcher_active.set()

                response_pipeline(
                    llm, etts, vts, memory, user_memory, config,
                    is_spontaneous=True,
                    emotion=decision.get("emotion", "neutral"),
                    spontaneous_trigger=decision.get("spontaneous_trigger", ""),
                    activity=decision.get("activity", ""),
                    mood=decision.get("mood", ""),
                    mood_desc=decision.get("mood_desc", ""),
                    prompter=prompter,
                    session_anchor=decision.get("session_anchor", ""),
                    monologue_thread=decision.get("monologue_thread", []),
                    grounding_context=decision.get("grounding_context", ""),
                    soul_memory=soul_memory,
                    lore_memory=lore_memory,
                )

                # Desactivar watcher y recoger mensajes
                _chat_watcher_active.clear()

                # Recoger mensajes extra que llegaron durante el habla
                with _extra_chat_msgs_lock:
                    if _extra_chat_msgs:
                        chat_buffer.extend(_extra_chat_msgs)
                        _extra_chat_msgs.clear()

                # Si fue interrumpida, poner el mensaje en el buffer
                _chat_received = bool(_pending_interrupt_msg[0]) or bool(chat_buffer)
                if _pending_interrupt_msg[0]:
                    chat_buffer.insert(0, _pending_interrupt_msg[0])
                    _pending_interrupt_msg[0] = None
                    _speech_interrupt.clear()
                else:
                    # No fue interrumpida — dejar que el timer normal siga su curso.
                    # on_monologue_done actualiza el modo flujo si corresponde.
                    prompter._last_monologue_time = time.time()

                # Notificar al Prompter para que actualice el modo flujo continuo
                prompter.on_monologue_done(chat_received=_chat_received)

            elif decision["action"] == "browse":
                # Aiko decidió autónomamente navegar por la web
                mood_for_browse = decision.get("mood", prompter.current_mood)

                def _do_browse():
                    browser_agent.start_browsing(mood=mood_for_browse)
                    nonlocal last_chat_time
                    last_chat_time = time.time()

                threading.Thread(
                    target=_do_browse, daemon=True,
                    name="AikoBrowseSession"
                ).start()
                time.sleep(1)   # pequeña pausa antes de volver al loop

            else:
                time.sleep(0.5)

            # ── Extractor Autónomo de Memoria ──
            if decision["action"] in ["respond", "monologue"]:
                _actions_since_extraction += 1
                if _actions_since_extraction >= 15:
                    _actions_since_extraction = 0
                    def _extract_bg():
                        try:
                            ctx = memory.get_formatted_context(last_n=25)
                            mem = llm.extract_self_memory(ctx)
                            if mem:
                                print(f"\n[Lore Engine] 🧠 Nuevo recuerdo autobiográfico asimilado: {mem}\n")
                                lore_memory.add_dynamic_memory(mem)
                        except Exception as e:
                            pass
                    threading.Thread(target=_extract_bg, daemon=True).start()

        except KeyboardInterrupt:
            running = False
            break
        except Exception as e:
            print(f"[Main] Error en bucle: {e}")
            time.sleep(1)



def main(shared_stack=None, gaming_mode=False):
    global running
    try:
        signal.signal(signal.SIGINT, lambda s, f: setattr(
            sys.modules[__name__], 'running', False
        ))
    except ValueError:
        pass  # Ignorar si se llama desde un hilo secundario (GUI)

    print_banner()
    config = load_config()

    if gaming_mode:
        print("\n" + "─" * 50 + "\n  🎮 MODO GAMING (Doki Doki Literature Club) ACTIVADO\n" + "─" * 50)
        config["vtuber"]["personality"] += (
            "\n\n[MODO GAMING ACTIVADO]: Estás jugando 'Doki Doki Literature Club' (una novela visual). "
            "Eres quien está jugando y tomando las decisiones. Haz comentarios sobre los personajes "
            "(Sayori, Natsuki, Yuri, Monika). "
            "Prioriza respuestas cortas y reactivas (gritos, suspiros, risas). "
            "No seas un asistente, eres una jugadora reaccionando a lo que ves en la pantalla."
        )

    # ── Memoria ───────────────────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando Memoria\n" + "─" * 50)
    if shared_stack and "memory" in shared_stack:
        memory = shared_stack["memory"]
        user_memory = shared_stack["user_memory"]
        lore_memory = shared_stack["lore_memory"]
        print("[Main] ✓ Usando Memory desde shared_stack")
    else:
        memory = Memory(config["memory"]["max_messages"], config["memory"]["persist"])
        user_memory = UserMemory()
        lore_memory = LoreMemory()


    # ── LLM (Groq) ────────────────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando LLM (Groq)\n" + "─" * 50)
    groq_cfg = config["groq"]
    
    # Initialize Soul systems
    print("\n" + "─" * 50 + "\n  Inicializando Sistemas de Alma\n" + "─" * 50)
    if shared_stack and "soul_memory" in shared_stack:
        soul_memory = shared_stack["soul_memory"]
        identity = shared_stack["identity"]
        life_engine = shared_stack["life_engine"]
        autonomy = shared_stack["autonomy"]
        tribunal = shared_stack["tribunal"]
        gacha = shared_stack["gacha"]
        print("[Main] ✓ Usando Soul desde shared_stack")
    else:
        soul_memory = MemoryEngine(config.get("soul", {}).get("memory_db", "data/aiko.db"))
        identity = AikoIdentity()
        life_engine = LifeEngine(soul_memory, identity, groq_cfg["api_key"])
        autonomy = AutonomyEngine(soul_memory, identity)
        tribunal = TribunalDelChat(soul_memory, identity, groq_cfg["api_key"])
        gacha = GachaSimulator(soul_memory, identity, groq_cfg["api_key"])
        life_engine.start()

    if shared_stack and "heartbeat" in shared_stack:
        heartbeat = shared_stack["heartbeat"]
        print("[Main] ✓ Usando Heartbeat desde shared_stack")
    else:
        heartbeat = HeartbeatSystem()
        heartbeat.start_stream()

    llm = None
    if shared_stack and "llm" in shared_stack:
        llm = shared_stack["llm"]
        llm.memory_engine = soul_memory
        llm.identity = identity
        print(f"[Main] ✓ Usando LLM desde shared_stack")
    else:
        llm = LLM(
            api_key=groq_cfg["api_key"], model=groq_cfg["model"],
            temperature=groq_cfg["temperature"], max_tokens=groq_cfg["max_tokens"],
            memory_engine=soul_memory, identity=identity
        )
        llm.check_connection()

    llm.inject_heartbeat(heartbeat)

    # ── TTS + TTS Emocional ───────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando TTS\n" + "─" * 50)
    base_tts = None
    etts = None
    if shared_stack and "etts" in shared_stack:
        etts = shared_stack["etts"]
        base_tts = shared_stack["base_tts"]
        print(f"[Main] ✓ Usando TTS desde shared_stack")
    else:
        applio_cfg = config.get("applio", {})
        base_tts = TTS(
            voice_model=config["tts"]["voice_model"],
            speed=config["tts"]["speed"],
            output_device=config["tts"]["output_device"],
            applio_path=applio_cfg.get("path") if applio_cfg.get("enabled") else None,
            rvc_model=applio_cfg.get("model") if applio_cfg.get("enabled") else None,
            rvc_index=applio_cfg.get("index", ""),
            rvc_pitch=applio_cfg.get("pitch", 0),
            rvc_f0_method=applio_cfg.get("f0_method", "rmvpe"),
        )
        etts = EmotionalTTS(base_tts, default_speed=config["tts"]["speed"])

    # ── STT ───────────────────────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando STT\n" + "─" * 50)
    if shared_stack and "stt" in shared_stack:
        stt = shared_stack["stt"]
        print("[Main] ✓ Usando STT desde shared_stack")
    else:
        stt = STT(
            model_size=config["stt"]["model_size"], language=config["stt"]["language"],
            enabled=config["stt"]["enabled"]
        )
        if stt.enabled:
            stt.start_listening()

    # ── Live2D Viewer (Browser-based) ────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando Visor Live2D\n" + "─" * 50)
    vts = None
    live2d_bridge = None
    if shared_stack and "vts" in shared_stack:
        vts = shared_stack["vts"]
        live2d_bridge = vts
        print(f"[Main] ✓ Usando Live2D desde shared_stack")
    else:
        live2d_cfg = config.get("live2d", {})
        vts = Live2DBridge(
            port=live2d_cfg.get("viewer_port", 8765),
            model_dir=live2d_cfg.get("model_path", "live2d_viewer/models"),
            viewer_dir="live2d_viewer",
            http_port=live2d_cfg.get("http_port", 8180),
        )
        vts.start()
        live2d_bridge = vts  # for dashboard compatibility

        # Auto-abrir visor en el navegador
        _http_port_viewer = vts.http_port
        def open_viewer(_port=_http_port_viewer):
            time.sleep(2)
            url = f"http://localhost:{_port}/index.html"
            print(f"[Main] Abriendo visor Live2D: {url}")
            webbrowser.open(url)
        threading.Thread(target=open_viewer, daemon=True).start()

    # ── Chat (Twitch + TikTok) ────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando Chat\n" + "─" * 50)
    if shared_stack and "chat_reader" in shared_stack:
        chat_reader = shared_stack["chat_reader"]
        chat_readers = shared_stack["chat_readers"]
        print("[Main] ✓ Usando Chat desde shared_stack")
    else:
        chat_readers = []

        # Twitch Chat
        if "twitch" in config and config["twitch"].get("enabled", True):
            twitch_reader = ChatReader(
                channel=config["twitch"].get("channel", "vtuberaiko"),
                bot_name=config["twitch"].get("bot_name", "aikobot"),
                token=config["twitch"].get("token", "")
            )
            chat_readers.append(twitch_reader)

        # TikTok Chat (conexión directa via WebSocket, sin TikFinity)
        if "tiktok" in config and config["tiktok"].get("enabled", False):
            tiktok_cfg = config["tiktok"]
            tiktok_reader = TikTokChatReader(
                username=tiktok_cfg.get("username", "vtuberaiko"),
            )
            # Aplicar config de batching
            if tiktok_cfg.get("follow_batch_interval"):
                tiktok_reader._follow_batch_interval = float(
                    tiktok_cfg["follow_batch_interval"]
                )
            chat_readers.append(tiktok_reader)

        chat_reader = MultiChatReader(chat_readers)
        chat_reader.start()

    # ── Browser Agent ─────────────────────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando Navegador Autónomo\n" + "─" * 50)
    if shared_stack and "browser_agent" in shared_stack:
        browser_agent = shared_stack["browser_agent"]
        print("[Main] ✓ Usando Browser Agent desde shared_stack")
    else:
        browser_cfg = config.get("browser", {})
        browser_intelligence = BrowserIntelligence(
            groq_api_key=config["groq"]["api_key"]
        )
        browser_agent = BrowserAgent(
            config=browser_cfg, intelligence=browser_intelligence
        )
        if browser_cfg.get("enabled", True):
            browser_agent.start()

    # ── Eventos ───────────────────────────────────────────────
    if shared_stack and "events" in shared_stack:
        events = shared_stack["events"]
        print("[Main] ✓ Usando Events desde shared_stack")
    else:
        events = None
        events_cfg = config.get("events", {})
        if events_cfg.get("enabled", False):
            print("\n" + "─" * 50 + "\n  Inicializando Eventos de Stream\n" + "─" * 50)
            events = StreamEvents(
                events_file=events_cfg.get("events_file", "events/stream_events.json"),
                poll_interval=events_cfg.get("poll_interval", 3.0)
            )
            events.start()

    # ── Chess Bridge ──────────────────────────────────────────
    chess_bridge = None
    if shared_stack and "chess_bridge" in shared_stack:
        chess_bridge = shared_stack["chess_bridge"]
        print("[Main] ✓ Usando ChessBridge desde shared_stack")
    else:
        chess_cfg = config.get("chess", {})
        if chess_cfg.get("enabled", False):
            print("\n" + "─" * 50 + "\n  Inicializando Módulo de Ajedrez\n" + "─" * 50)
            try:
                from modules.chess_bridge import ChessBridge
                from modules.chess_scorer import ChessScorer
                chess_bridge = ChessBridge(
                    llm=llm, etts=etts, vts=vts, config=config,
                    scorer=ChessScorer(),
                )
                print("[Main] ✓ ChessBridge listo")
                
                # Assign to builtins just in case it's needed globally by commands
                import builtins
                builtins._chess_bridge_ref = chess_bridge
                
            except Exception as e:
                print(f"[Main] ✗ Error iniciando Ajedrez: {e}")

    # ── Dashboard ─────────────────────────────────────────────
    dash_cfg = config.get("dashboard", {})  # siempre disponible para el log final
    if shared_stack and "dashboard" in shared_stack:
        print("[Main] ✓ Usando Dashboard desde shared_stack")
    else:
        if dash_cfg.get("enabled", True):
            print("\n" + "─" * 50 + "\n  Inicializando Dashboard\n" + "─" * 50)
            dashboard = Dashboard(port=dash_cfg.get("port", 5000), chess_bridge=chess_bridge)
            dashboard_state["uptime_start"] = time.time()
            dashboard.start()

    # ── Prompter (Cerebro de Streamer) ────────────────────────
    print("\n" + "─" * 50 + "\n  Inicializando Cerebro de Streamer\n" + "─" * 50)
    if shared_stack and "prompter" in shared_stack:
        prompter = shared_stack["prompter"]
        print("[Main] ✓ Usando Prompter desde shared_stack")
    else:
        streamer_cfg = config.get("streamer", {})
        session_tracker = SessionTracker()
        prompter = Prompter(config=streamer_cfg, session_tracker=session_tracker)
        prompter.set_vtuber_name(config["vtuber"]["name"])

        # Inyectar TikTok reader en el prompter para métricas de actividad en tiempo real
        if "tiktok" in config and config["tiktok"].get("enabled", False):
            for r in chat_readers:
                if hasattr(r, 'chat_is_active'):  # es TikTokChatReader
                    prompter.set_tiktok_reader(r)
                    print("[Prompter] TikTok reader inyectado para métricas de actividad")
                    break

    # ── Conectar browser con TTS y prompter ────────────────────────────

    def _browser_speak_direct(text: str, emotion: str = "neutral"):
        """
        El browser habla DIRECTAMENTE por TTS — sin pasar por el LLM.
        Verifica que el pipeline principal no esté hablando antes de proceder.
        """
        if not text or not text.strip():
            return
        # GUARDIA: no hablar si el pipeline principal ya está usando el TTS
        if etts.is_speaking:
            print(f"[Browser→TTS] Skipped (pipeline hablando): {text[:50]}")
            return
        # Marcar que Aiko va a hablar (bloquea futuras reacciones del browser)
        browser_agent.set_speaking(True)
        try:
            # Doble verificación tras set_speaking (evita race condition)
            if etts.is_speaking:
                return
            mood_now = prompter.current_mood
            sentiment = vts.animate_for_response(text, mood=mood_now)
            etts.set_emotion(sentiment)
            clean = process_visual_actions(text, vts)
            print(f"[Browser→TTS] {clean[:80]}")
            speak_streaming(text, etts, vts, mood_now, sentiment)
        except Exception as e:
            print(f"[Browser→TTS] Error: {e}")
        finally:
            browser_agent.set_speaking(False)

    browser_agent.set_tts_callback(_browser_speak_direct)
    browser_agent.set_prompter_signal(prompter)

    # Guardar referencia al browser en el prompter para que pipeline lo alcance
    prompter._browser_agent_ref = browser_agent

    # ── Game Engine (Modo Gaming) ────────────────────────────
    game_engine = None
    if gaming_mode:
        from modules.game_engine import GameEngine
        print("\n" + "─" * 50 + "\n  🎮 Inicializando Motor de Gaming\n" + "─" * 50)
        
        # 1. Deshabilitar los monólogos estándar y el navegador para que se enfoque en el juego
        prompter.SPONTANEOUS_CHANCE = 0.0
        prompter._should_browse_now = lambda *args, **kwargs: False
        
        game_engine = GameEngine(
            groq_api_key=config["groq"]["api_key"],
            etts=etts, vts=vts, llm=llm,
        )

        def _game_speak(text: str, emotion: str = "neutral",
                        speed_override: float = None):
            """Callback del GameEngine para hablar por TTS."""
            if not text or not text.strip():
                return
            # Esperar a que termine de hablar (máx 15s) en vez de saltar
            wait_start = time.time()
            while etts.is_speaking and (time.time() - wait_start) < 15:
                time.sleep(0.3)
            try:
                mood_now = prompter.current_mood
                sentiment = vts.animate_for_response(text, mood=mood_now)
                etts.set_emotion(sentiment)

                # Aplicar velocidad reducida para narración de juego
                original_speed = etts.default_speed
                if speed_override:
                    etts.default_speed = speed_override

                speak_streaming(text, etts, vts, mood_now, sentiment)

                if speed_override:
                    etts.default_speed = original_speed
            except Exception as e:
                print(f"[Game→TTS] Error: {e}")

        def _game_chat_check() -> bool:
            """Checkea si hay chat pendiente."""
            msg = chat_reader.get_message()
            if msg:
                # Devolver el mensaje al buffer
                # (será procesado por main_loop)
                return True
            return False

        game_engine.set_speak_callback(_game_speak)
        game_engine.set_chat_check_callback(_game_chat_check)
        game_engine.start()
        print("[Main] 🎮 GameEngine iniciado — Aiko está jugando DDLC")

    # ── Inicio Completo ──────────────────────────────────────
    print("\n" + "═" * 50)
    print("  INICIO COMPLETO — esperando conexiones (3s)...")
    print("═" * 50)
    time.sleep(3)

    state = prompter.get_state()
    print("\n  ESTADO DE CONEXIONES:")
    print(f"  Groq LLM:    {'✓ Conectado' if llm.connected else '✗ Revisa API key'}")
    print(f"  Twitch:      {'✓' if any(getattr(r,'connected',False) and not hasattr(r,'chat_is_active') for r in chat_readers) else '✗ (desconectado o no activo)'}")
    tiktok_r = next((r for r in chat_readers if hasattr(r,'chat_is_active')), None)
    if tiktok_r:
        tiktok_status = (
            f"✓ ({tiktok_r.viewer_count} viewers)"
            if tiktok_r.is_connected
            else "○ Esperando live — reconecta automáticamente"
        )
    else:
        tiktok_status = "✗ No habilitado"
    print(f"  TikTok Live: {tiktok_status}")
    print(f"  Live2D:      {'✓' if (live2d_bridge and live2d_bridge.connected) else '○ Esperando navegador'}")
    print(f"  TTS:         {'✓' if base_tts._piper_available else '✗'}")
    print(f"  Browser:     {'✓' if browser_agent._running else '✗'}")
    if game_engine:
        print(f"  🎮 Gaming:   ✓ DDLC activo")
    print(f"  Dashboard:   http://localhost:{dash_cfg.get('port', 5000)}")
    print(f"  Humor:       {state['mood']} ({state['mood_desc'][:40]})")
    print(f"  Actividad:   {'🎮 Jugando DDLC' if gaming_mode else 'libre — Aiko decide'}")
    print(f"  Tribunal:    {len(tribunal.pending_cases)} casos pendientes")
    print(f"  Gacha Pity:  {gacha.pity_counter}")
    print("═" * 50)

    if not llm.connected:
        print("\n⚠ ¡API de Groq no conectada! Revisa tu API key en config.yaml\n"
              "  Obtén una gratis en: https://console.groq.com\n")

    # ── Bucle Principal ───────────────────────────────────────
    try:
        main_loop(llm, etts, stt, chat_reader, vts, prompter,
                  memory, user_memory, lore_memory, events, config,
                  soul_memory, identity, life_engine, autonomy, tribunal, gacha, browser_agent, heartbeat,
                  chess_bridge=locals().get("chess_bridge"))
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[Main] Cerrando...")
        running = False
        heartbeat.stop_stream()
        if game_engine:
            game_engine.stop()
        chat_reader.stop()
        vts.stop()
        browser_agent.stop()
        if stt.enabled:
            stt.stop_listening()
        if events:
            events.stop()
        
        # Shutdown soul systems
        life_engine.set_streaming(False)
        life_engine.stop()
        identity.evolve_from_event("stream_end", {})
        soul_memory.remember(
            type="stream_event",
            content=f"Stream terminado. Humor final: {identity.get_current_mood()}",
            emotional_weight=0.6
        )
        soul_memory.close()
        
        etts.cleanup()
        print("[Main] 👋 ¡Hasta luego!")


if __name__ == "__main__":
    main()
