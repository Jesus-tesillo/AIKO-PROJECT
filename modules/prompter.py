"""
prompter.py - Cerebro de Streamer.

Grounding system: Aiko tiene un tema ancla diario que desarrolla
de forma natural. El chat se clasifica por prioridad y el LLM
recibe contexto de memoria del viewer para respuestas personalizadas.
"""
import json
import random
import time
import threading
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════
#  HUMORES — afecta personalidad, velocidad TTS y estilo
# ═══════════════════════════════════════════════════════════════
MOODS = {
    "hyped":     {"tts_speed_mod": 1.08, "desc": "energía alta, se emociona"},
    "chill":     {"tts_speed_mod": 0.97, "desc": "relajada, tranquila"},
    "bored":     {"tts_speed_mod": 0.95, "desc": "aburrida, busca drama"},
    "flustered": {"tts_speed_mod": 1.03, "desc": "nerviosa, se traba"},
    "gremlin":   {"tts_speed_mod": 1.05, "desc": "caos controlado, se ríe"},
    "focused":   {"tts_speed_mod": 1.0,  "desc": "metida en algo, intensa"},
}

# ═══════════════════════════════════════════════════════════════
#  DAILY ANCHORS — temas ancla variados para el stream
#  Aiko elige uno y lo desarrolla naturalmente
# ═══════════════════════════════════════════════════════════════
DAILY_ANCHORS = [
    # ── Vida cotidiana / historias ──────────────────────────────
    "Aiko está contando cómo fue su semana y qué cosas le pasaron",
    "Aiko quiere hablar de una experiencia rara que tuvo recientemente",
    "Aiko está pensando en sus hábitos y por qué hace ciertas cosas",
    "Aiko quiere contar sobre algo que la hizo reír muchísimo",

    # ── Opiniones / debates ─────────────────────────────────────
    "Aiko tiene una opinión fuerte sobre algo y quiere debatirlo con el chat",
    "Aiko está pensando en cosas que todo el mundo hace pero nadie habla",
    "Aiko quiere hablar de costumbres que le parecen absurdas",
    "Aiko está cuestionando por qué la gente actúa de cierta forma",

    # ── Cultura pop / entretenimiento ──────────────────────────
    "Aiko quiere recomendar algo que vio o escuchó recientemente",
    "Aiko está procesando algo que vio en una serie o película",
    "Aiko quiere hablar de música y qué ha estado escuchando",
    "Aiko vio algo en internet que la dejó pensando",

    # ── Comida / gustos ─────────────────────────────────────────
    "Aiko tiene una opinión polémica sobre comida",
    "Aiko quiere hablar de qué cocinó o qué quiere comer",

    # ── Internet / tecnología ──────────────────────────────────
    "Aiko quiere hablar de algo que está de moda en internet",
    "Aiko tiene una teoría sobre redes sociales y comportamiento",

    # ── Personal / reflexivo ───────────────────────────────────
    "Aiko está reflexionando sobre algo que le preocupa de forma ligera",
    "Aiko quiere hablar de sus planes o de algo que quiere hacer",
]


# ═══════════════════════════════════════════════════════════════
#  ESTILOS DE RESPUESTA AL CHAT
# ═══════════════════════════════════════════════════════════════
CHAT_RESPONSE_STYLES = [
    "{username} dijo '{message}' — responde como Aiko, natural y breve.",
    "El chat ({username}): '{message}'. Reacciona.",
    "{username}: '{message}'. Lo escuchaste. Di lo que piensas.",
    "Responde a {username} que dijo: '{message}'. Sin preámbulos.",
    "{username} preguntó o comentó: '{message}'. Reacciona como si fuera una conversación real.",
]


# ═══════════════════════════════════════════════════════════════
#  CHAT INTERACTION MANAGER
#  Clasifica mensajes y determina el mejor estilo de respuesta
# ═══════════════════════════════════════════════════════════════
class ChatInteractionManager:
    """Clasifica mensajes del chat y genera instrucciones de estilo para el LLM."""

    LAUGH_TOKENS = {"jaja", "jajaja", "jeje", "lol", "xd", "haha", "lmao", "kek", "kkk"}
    GREETING_TOKENS = {"hola", "hi", "hey", "ola", "holi", "holaa", "holiii", "buenas", "saludos",
                       "hello", "que tal", "qué tal", "buenas noches", "buenas tardes",
                       "buenos días", "buenos dias", "sup", "yo", "eyyy", "eyy", "ey"}
    FILLER_MAX_LEN = 3  # Solo "si", "no", "ok" — "hola" (4) ya no cae aquí

    def classify_message(self, message: str, username: str,
                          memory_engine) -> dict:
        """
        Clasifica un mensaje para decidir prioridad y chance de respuesta.
        Retorna un dict con: priority, response_chance, is_regular, should_respond.
        """
        msg_lower = message.lower().strip()

        # Viewer data
        viewer = memory_engine.recall_viewer(username) if memory_engine else {}
        is_regular = (viewer.get("times_chatted", 0) > 10) if viewer else False

        # Clasificar
        if any(w in msg_lower for w in ["aiko", "@aiko", "oye aiko", "ey aiko"]):
            priority = "direct_mention"
            response_chance = 1.0

        elif "?" in message:
            priority = "question"
            response_chance = 0.97

        elif msg_lower in self.GREETING_TOKENS or any(
            msg_lower.startswith(g) for g in ["hola", "buenas", "hello", "hey", "hi "]
        ):
            priority = "greeting"
            response_chance = 0.95

        elif msg_lower in self.LAUGH_TOKENS:
            priority = "reaction"
            response_chance = 0.40

        elif len(message.strip()) <= self.FILLER_MAX_LEN:
            priority = "filler"
            response_chance = 0.50

        elif is_regular:
            priority = "regular_viewer"
            response_chance = 0.95

        else:
            priority = "normal"
            response_chance = 0.90

        return {
            "priority": priority,
            "response_chance": response_chance,
            "is_regular": is_regular,
            "should_respond": random.random() < response_chance,
        }

    def build_response_style(self, classification: dict, thread: list) -> str:
        """
        Retorna una instrucción de estilo para el LLM basada en la
        clasificación del mensaje y el estado del hilo conversacional.
        """
        # Hilo activo = al menos 2 mensajes con el último hace <30s
        has_active_thread = (
            len(thread) >= 2
            and time.time() - thread[-1].get("timestamp", 0) < 30
        )

        styles = {
            "direct_mention":
                "Te están hablando directamente. Responde con atención pero sin "
                "exagerar — como cuando alguien te llama en medio de algo.",

            "question":
                "Te hicieron una pregunta. Respóndela desde tu perspectiva, con "
                "tu opinión real. No des la respuesta 'correcta' — da la respuesta de Aiko.",

            "greeting":
                "Te saludaron. Devuelve el saludo de forma natural y breve, "
                "como si alguien entrara a tu cuarto. Puedes preguntar algo.",

            "reaction":
                "El chat está reaccionando. Puedes reconocerlo brevemente o "
                "simplemente continuar — no tienes que responder cada risa.",

            "regular_viewer":
                "Es alguien que aparece seguido. Puedes ser más familiar, hacer "
                "referencias a cosas pasadas si las recuerdas, tratarlo como "
                "alguien que ya conoces.",

            "filler":
                "Mensaje corto o filler. Puedes ignorarlo, responder algo "
                "breve, o aprovecharlo para seguir con lo tuyo.",

            "normal":
                "Mensaje normal del chat. Decide si quieres responderlo o "
                "simplemente continuar con lo tuyo.",
        }

        base_style = styles.get(classification.get("priority", "normal"),
                                styles["normal"])

        thread_note = ""
        if has_active_thread:
            thread_note = (
                " Hay un hilo activo — si tiene sentido, conecta tu respuesta "
                "con lo que ya se estaba hablando."
            )

        return base_style + thread_note


# ═══════════════════════════════════════════════════════════════
#  VIEWER CONTEXT BUILDER
#  Genera contexto rico de memoria para el LLM
# ═══════════════════════════════════════════════════════════════
def build_viewer_context(username: str, memory_engine) -> str:
    """
    Genera contexto de memoria del viewer para inyectar en el prompt del LLM.
    Incluye: nivel de relación, notas de personalidad, momentos memorables, chistes.
    """
    if not memory_engine:
        return f"{username}: sin datos de memoria disponibles."

    viewer = memory_engine.recall_viewer(username)

    if not viewer:
        return f"{username} es nuevo — primera vez que aparece."

    chats = viewer.get("times_chatted", 0)
    notes = viewer.get("personality_notes", "")
    moments_raw = viewer.get("memorable_moments", "[]")
    jokes_raw = viewer.get("inside_jokes", "[]")

    # Parsear JSON con fallback seguro
    try:
        moments = json.loads(moments_raw) if moments_raw else []
    except (json.JSONDecodeError, TypeError):
        moments = []

    try:
        jokes = json.loads(jokes_raw) if jokes_raw else []
    except (json.JSONDecodeError, TypeError):
        jokes = []

    context_parts = []

    # Nivel de relación por número de chats
    if chats == 1:
        context_parts.append(f"{username} es nuevo hoy.")
    elif chats < 5:
        context_parts.append(f"{username} ha aparecido {chats} veces.")
    elif chats < 20:
        context_parts.append(f"{username} aparece seguido ({chats} mensajes).")
    else:
        context_parts.append(
            f"{username} es un regular ({chats} mensajes) — lo conoces bien."
        )

    if notes:
        context_parts.append(f"Lo que sabes de él/ella: {notes}")

    if moments:
        context_parts.append(f"Momento memorable: {moments[-1]}")

    if jokes:
        context_parts.append(f"Chiste interno: {jokes[-1]}")

    return " ".join(context_parts)


# ═══════════════════════════════════════════════════════════════
#  POST-RESPONSE MEMORY UPDATER
# ═══════════════════════════════════════════════════════════════
def update_viewer_after_interaction(username: str, message: str,
                                     aiko_response: str,
                                     memory_engine):
    """
    Actualiza lo que Aiko sabe del viewer después de cada interacción.
    - Siempre actualiza stats básicos (times_chatted, last_seen)
    - Detecta tópicos relevantes en el mensaje
    - Registra intercambios memorables (mensajes largos, opiniones fuertes, preguntas)
    """
    if not memory_engine:
        return

    # 1. Actualizar stats básicos
    memory_engine.update_viewer(username, message)

    # 2. Detectar tópicos notables (para futuros usos de personality_notes)
    notable_keywords = {
        "anime":    ["anime", "manga", "opening", "ending", "filler", "op", "ova"],
        "gaming":   ["juego", "game", "ps5", "pc", "steam", "switch", "xbox"],
        "opinion":  ["odio", "amo", "me gusta", "no me gusta", "creo que", "pienso"],
        "personal": ["trabajo", "escuela", "familia", "me pasó", "ayer", "hoy"],
    }

    found_topics = []
    msg_lower = message.lower()
    for category, keywords in notable_keywords.items():
        if any(kw in msg_lower for kw in keywords):
            found_topics.append(category)

    # 3. Determinar si el intercambio vale la pena guardar
    is_memorable = (
        len(message) > 50
        or "?" in message
        or any(w in msg_lower for w in
               ["siempre", "nunca", "odio", "amo", "mejor", "peor",
                "favorito", "favorita", "jamás", "igual"])
    )

    if is_memorable:
        try:
            exchange_text = f"{message[:80]} → {aiko_response[:60]}"
            memory_engine.conn.execute("""
                UPDATE viewers SET
                memorable_moments = json_insert(
                    COALESCE(memorable_moments, '[]'),
                    '$[#]',
                    ?
                )
                WHERE username = ?
            """, (exchange_text, username))
            memory_engine.conn.commit()
        except Exception:
            pass  # No bloquear el pipeline por un error de DB


# ═══════════════════════════════════════════════════════════════
#  SESSION COHERENCE TRACKER
# ═══════════════════════════════════════════════════════════════
class SessionTracker:
    """
    Rastreador de coherencia de sesión.
    Mantiene un historial de los temas discutidos en la sesión actual
    para evitar repeticiones a largo plazo (más allá del hilo inmediato).
    """
    def __init__(self):
        self.discussed_topics = []
        self.current_topic = None

    def log_topic(self, topic: str):
        if not topic or len(topic.strip()) < 5:
            return
        # Solo guardar un resumen de las primeras palabras
        short_topic = " ".join(topic.strip().split()[:8])
        if short_topic not in self.discussed_topics:
            self.discussed_topics.append(short_topic)
            self.current_topic = short_topic
            if len(self.discussed_topics) > 15:
                self.discussed_topics.pop(0)

    def get_tracker_context(self) -> str:
        if not self.discussed_topics:
            return ""
        topics_str = " / ".join(self.discussed_topics[-5:])
        return f"\n[SESSION TRACKER - Temas ya cerrados hoy: {topics_str}. NO repitas estos temas.]"


# ═══════════════════════════════════════════════════════════════
#  PROMPTER — clase principal
# ═══════════════════════════════════════════════════════════════
class Prompter:
    """Cerebro de Streamer — humores, monólogos, filtrado de chat y grounding."""

    # Probabilidad de que Aiko hable cuando el timer lo permite.
    SPONTANEOUS_CHANCE = 0.90

    def __init__(self, config: dict = None, session_tracker=None):
        cfg = config or {}

        # ── Señales compartidas (otros módulos escriben aquí) ──────────
        self._signals = {}
        self.session_tracker = session_tracker

        # ── Tiempos ────────────────────────────────────────────────────
        self.monologue_min = cfg.get("monologue_interval_min", 15)
        self.monologue_max = cfg.get("monologue_interval_max", 35)
        self.chat_response_chance = cfg.get("chat_response_chance", 0.90)
        self.mood_change_min = cfg.get("mood_change_interval_min", 120)
        self.mood_change_max = cfg.get("mood_change_interval_max", 300)

        # ── Estado de humor ──────────────────────────────────────────
        self.current_mood = cfg.get("initial_mood", "hyped")
        self.last_response_time = time.time()

        # ── Grounding: tema ancla y hilo conversacional ───────────────
        self.daily_anchor: str = random.choice(DAILY_ANCHORS)
        self.anchor_progress: list = []
        self.current_thread: list = []   # {user, content, is_aiko, timestamp}

        # ── Interaction manager ───────────────────────────────────────
        self._interaction_mgr = ChatInteractionManager()

        self._lock = threading.Lock()

        # ── Timers ────────────────────────────────────────────────────
        now = time.time()
        # Primer monólogo sale rápido (5-10s tras arrancar)
        self._last_monologue_time = now - self.monologue_min + random.uniform(5, 10)
        self._next_monologue_delay = random.uniform(5, 10)
        self._last_mood_change = now
        self._next_mood_delay = random.uniform(self.mood_change_min, self.mood_change_max)

        # ── Timer de navegación ──────────────────────────────────────
        # Aiko empieza a considerar navegar solo tras un tiempo razonable de stream
        self._last_browse_time = now                  # evita navegar al arrancar
        self._browse_cooldown = random.uniform(120, 240)  # primera ventana en 2-4 min

        # ── Cooldowns por usuario ────────────────────────────────
        self._user_last_responded = defaultdict(float)
        # Escala dinámicamente con la velocidad del chat
        self._user_cooldown_base = 3.0   # chat quieto (<5 mpm)
        self._user_cooldown_max  = 12.0  # chat caliente (>15 mpm)

        # ── Tracking de actividad del chat ─────────────────────────
        self._recent_message_times: list = []
        self._messages_per_minute = 0

        # Referencia externa al TikTokChatReader (inyectada en main.py)
        self._tiktok_reader = None

        # ── Keywords de nombre de VTuber ────────────────────
        self.priority_keywords = ["vtuber", "ai", "aiko"]

        # ── Estadísticas ────────────────────────────────────
        self.messages_ignored = 0
        self.messages_answered = 0
        self.monologues_delivered = 0

        # ── Modo Flujo Continuo ─────────────────────────────────────
        # Cuando no hay chat, Aiko puede encadenar monólogos con pausas cortas
        # dando la sensación de habla natural continua (2-5s entre ideas).
        self._in_flow_mode = False         # True cuando está en flujo activo
        self._flow_monologue_count = 0    # cuántos monólogos seguidos van
        self.FLOW_CONTINUATION_CHANCE = 0.72  # probabilidad de continuar el flujo
        self.FLOW_MAX_CHAIN = 5           # máximo de ideas encadenadas antes de pausa larga

        print(f"[Prompter] Cerebro de Streamer inicializado")
        print(f"[Prompter]   Monólogo cada {self.monologue_min}-{self.monologue_max}s (primer monólogo en ~8s)")
        print(f"[Prompter]   Probabilidad de respuesta al chat: {self.chat_response_chance:.0%}")
        print(f"[Prompter]   Probabilidad de monólogo espontáneo: {self.SPONTANEOUS_CHANCE:.0%}")
        print(f"[Prompter]   Humor: {self.current_mood}")

    # ── Señales compartidas ────────────────────────────────────
    def set(self, key: str, value):
        """Otros módulos escriben señales aquí (ej: browsing=True)."""
        self._signals[key] = value

    def get(self, key: str, default=None):
        """Lee una señal compartida."""
        return self._signals.get(key, default)

    # ── Público: set nombre para detección de menciones ──────
    def set_vtuber_name(self, name: str):
        n = name.lower()
        self.priority_keywords = [
            n, f"hey {n}", f"hi {n}", f"hola {n}", f"@{n}",
            f"oye {n}", f"{n}!", "vtuber", "ai", "aiko",
        ]

    # ── Público: obtener estado actual para display ──────────
    def get_state(self) -> dict:
        return {
            "mood": self.current_mood,
            "mood_desc": MOODS.get(self.current_mood, {}).get("desc", ""),
            "activity": "libre — Aiko decide",
            "tts_speed_mod": MOODS.get(self.current_mood, {}).get("tts_speed_mod", 1.0),
            "messages_ignored": self.messages_ignored,
            "messages_answered": self.messages_answered,
            "monologues": self.monologues_delivered,
            "msgs_per_min": self._messages_per_minute,
        }

    # ═══════════════════════════════════════════════════════════
    #  GROUNDING CONTEXT
    # ═══════════════════════════════════════════════════════════
    def get_grounding_context(self) -> str:
        """
        Retorna el contexto de grounding para inyectar en el LLM.
        Para MONÓLOGOS: solo muestra lo que Aiko dijo, no el chat raw.
        Para CHAT RESPONSES: el chat context va por otro canal (chat_prompt).
        """
        # Extraer temas ya cubiertos de TODOS los monólogos recientes
        aiko_msgs = [m for m in self.current_thread[-8:] if m['is_aiko']]
        # Extraer las primeras palabras de cada mensaje como "temas cubiertos"
        covered_topics = []
        for m in aiko_msgs:
            # Tomar las primeras 6 palabras como resumen del tema
            words = m['content'].split()[:6]
            covered_topics.append(" ".join(words))
        
        no_repetir = ""
        if covered_topics:
            topics_str = " / ".join(covered_topics[-4:])  # últimos 4 temas
            no_repetir = f"\n(TEMAS YA CUBIERTOS - no vuelvas a estos: {topics_str})"

        # Detectar si el chat estuvo activo recientemente
        recent_chat = [m for m in self.current_thread[-4:] if not m['is_aiko']]
        chat_was_active = len(recent_chat) > 0

        dead_time = self._seconds_since_last_chat()
        silence_note = ""
        if dead_time > 120 and self.monologues_delivered > 2:
            mins = int(dead_time // 60)
            silence_note = f"\n[AMBIENTE: El chat lleva {mins} minutos en completo silencio. Nadie ha escrito. Decide orgánicamente si lo mencionas, asumes que escuchan, o lo ignoras.]"

        if chat_was_active and recent_chat:
            last_chat_topic = recent_chat[-1]['content'][:60]
            return (
                f"Acabas de hablar con alguien del chat sobre: \"{last_chat_topic}\"\n"
                f"Puedes seguir con ese tema o hablar de otra cosa.{no_repetir}{silence_note}"
            )
            
        if self.monologues_delivered >= 4:
            return (
                f"Estás en modo libre. El tema inicial ya pasó.\n"
                f"Habla de lo que quieras o sigue el hilo actual."
                f"{no_repetir}{silence_note}"
            )
        
        anchor_progress_text = (
            " → ".join(self.anchor_progress[-3:])
            if self.anchor_progress
            else "sin desarrollar todavía"
        )
        
        tracker_note = self.session_tracker.get_tracker_context() if self.session_tracker else ""

        return (
            f"TEMA DE HOY: {self.daily_anchor}\n"
            f"PROGRESO: {anchor_progress_text}"
            f"{no_repetir}{silence_note}{tracker_note}"
        )



    def add_to_thread(self, user: str, content: str, is_aiko: bool):
        """Registra un mensaje en el hilo conversacional (máx 10 entradas)."""
        if not content or not content.strip() or content.strip() == "...":
            return
        self.current_thread.append({
            "user": user if not is_aiko else "Aiko",
            "content": content.strip(),
            "is_aiko": is_aiko,
            "timestamp": time.time(),
        })
        self.current_thread = self.current_thread[-10:]

        if is_aiko:
            beat = content.strip()[:60]
            self.anchor_progress.append(beat)
            self.anchor_progress = self.anchor_progress[-6:]

    def _chat_active_recently(self, seconds: float = 15.0) -> bool:
        """True si hubo mensajes de chat en los últimos `seconds` segundos."""
        now = time.time()
        return any(
            (now - m["timestamp"]) < seconds
            for m in self.current_thread
            if not m["is_aiko"]
        )

    # ═══════════════════════════════════════════════════════════
    #  MOTOR DE DECISIÓN PRINCIPAL
    # ═══════════════════════════════════════════════════════════
    def should_respond(self, message: dict = None,
                        user_memory=None,
                        browser_available: bool = False) -> dict:
        """
        Decide qué hace Aiko ahora.
        PRIORIDAD: chat > monólogo > navegar > esperar.
        Ahora usa ChatInteractionManager para clasificar mensajes.
        """
        now = time.time()
        self._maybe_change_mood(now)
        self._update_chat_rate(now)

        with self._lock:
            if message:
                self._recent_message_times.append(now)
                username = message.get("user", "").lower()
                text = message.get("message", "")

                # Registrar en hilo conversacional
                self.add_to_thread(username, text, is_aiko=False)

                # Clasificar el mensaje con el interaction manager
                classification = self._interaction_mgr.classify_message(
                    text, username, user_memory
                )

                # Decidir si responder basado en la clasificación
                if not classification["should_respond"]:
                    self.messages_ignored += 1
                    return self._wait(
                        f"Ignorando ({classification['priority']}, "
                        f"p={classification['response_chance']:.0%})"
                    )

                # Cooldown por usuario — escala con la actividad del chat
                user_elapsed = now - self._user_last_responded.get(username, 0)
                cooldown = self._dynamic_user_cooldown()
                if user_elapsed < cooldown:
                    return self._wait("Cooldown de usuario")

                # Cooldown global mínimo
                elapsed = now - self.last_response_time
                if elapsed < 2.0:
                    return self._wait("Cooldown global")

                # Responder
                self.last_response_time = now
                self._user_last_responded[username] = now
                self.messages_answered += 1

                # Style instruction para el LLM
                response_style = self._interaction_mgr.build_response_style(
                    classification, self.current_thread
                )

                # Viewer context desde memoria
                viewer_ctx = build_viewer_context(username, user_memory)

                chat_prompt = self._build_chat_prompt_with_style(
                    text, username, response_style
                )

                return {
                    "action": "respond",
                    "message": message,
                    "reason": f"Respondiendo a {username} ({classification['priority']})",
                    "emotion": self.current_mood,
                    "chat_prompt": chat_prompt,
                    "activity": "",
                    "mood": self.current_mood,
                    "mood_desc": MOODS[self.current_mood]["desc"],
                    "grounding_context": self.get_grounding_context(),
                    "viewer_context": viewer_ctx,
                    "classification": classification,
                    "is_regular": classification["is_regular"],
                }

            # Sin mensaje — revisar timer de monólogo / navegación
            return self._check_monologue(now, browser_available=browser_available)

    # ═══════════════════════════════════════════════════════════
    #  FILTRADO DE CHAT (mantiene prioridades hard-coded para mentions)
    # ═══════════════════════════════════════════════════════════
    def _should_respond_to_chat(self, message: str, username: str) -> bool:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in self.priority_keywords):
            return True
        if "?" in message:
            return True
        if "@" in message:
            return True
        if len(message) > 20:
            return random.random() < 0.92
        return random.random() < self.chat_response_chance

    def _build_chat_prompt_with_style(self, message: str, username: str,
                                       style_instruction: str) -> str:
        """Construye el prompt de chat incluyendo contexto de lo que Aiko decía."""
        base = random.choice(CHAT_RESPONSE_STYLES).format(
            username=username, message=message
        )

        # Añadir contexto de lo que Aiko estaba hablando (solo referencia)
        last_aiko_msgs = [m for m in self.current_thread[-4:] if m["is_aiko"]]
        if last_aiko_msgs:
            last_said = last_aiko_msgs[-1]["content"][:100]
            base += (
                f"\n\n(Contexto: antes de esto estabas hablando de: \"{last_said}...\")"
            )

        base += (
            f"\n\nPRIORIDAD: Responde a lo que {username} dijo. "
            "Si te preguntaron algo, responde ESO."
        )

        return f"{base}\n\nESTILO DE RESPUESTA: {style_instruction}"



    # ═══════════════════════════════════════════════════════════
    #  MOTOR DE MONÓLOGOS
    # ═══════════════════════════════════════════════════════════
    def enter_flow(self):
        self._in_flow_mode = True

    def exit_flow(self):
        self._in_flow_mode = False
        self._flow_monologue_count = 0

    def on_monologue_done(self, chat_received: bool):
        """
        Llamar desde main_loop tras completar un monólogo.
        Decide si entrar/continuar modo flujo o romperlo.
        """
        if chat_received or self._chat_active_recently(seconds=10) or self._chat_is_hot():
            # El chat interrumpió o hay actividad reciente → salir del flujo
            self._in_flow_mode = False
            self._flow_monologue_count = 0
        else:
            # Silencio → posible continuación de flujo
            self._flow_monologue_count += 1
            if (self._flow_monologue_count >= 2
                    and self._flow_monologue_count < self.FLOW_MAX_CHAIN
                    and random.random() < self.FLOW_CONTINUATION_CHANCE):
                self._in_flow_mode = True
                print(f"[Prompter] 🌊 Modo flujo activo "
                      f"(idea #{self._flow_monologue_count}/{self.FLOW_MAX_CHAIN})")
            elif self._flow_monologue_count >= self.FLOW_MAX_CHAIN:
                # Pausa larga tras mucho flujo
                self._in_flow_mode = False
                self._flow_monologue_count = 0
                print("[Prompter] ⏸️ Pausa larga tras flujo continuo")

    def _dynamic_user_cooldown(self) -> float:
        """
        Cooldown de usuario que se adapta a la velocidad del chat.
        Chat quieto (0-5 mpm)  → 3s   (responde rápido a cada uno)
        Chat activo (5-15 mpm) → 6s   (evita responder a spam)
        Chat caliente (>15 mpm)→ 10s  (prioriza solo menciones directas)
        """
        mpm = self._messages_per_minute
        if mpm <= 5:
            return self._user_cooldown_base          # 3s
        elif mpm <= 15:
            return 6.0
        else:
            return self._user_cooldown_max           # 12s

    def set_tiktok_reader(self, reader):
        """Inyecta referencia al TikTokChatReader para métricas en tiempo real."""
        self._tiktok_reader = reader

    def _chat_is_hot(self) -> bool:
        """
        True si el chat está muy activo (>10 mpm).
        En modo caliente Aiko no monologa — prioriza responder.
        """
        return self._messages_per_minute > 10

    def should_do_monologue(self) -> bool:
        """Decide si tiene sentido hablar ahora (adaptativo al chat)."""
        # No monologuear mientras navega
        if self._signals.get('browsing', False):
            return False
        # No monologuear si el chat está muy caliente (>10 mpm)
        # — priorizar escuchar y responder
        if self._chat_is_hot():
            return False
        # No interrumpir conversaciones activas del chat (últimos 15s)
        if self._chat_active_recently(seconds=15):
            return False
        if random.random() > self.SPONTANEOUS_CHANCE:
            return False
        return True

    # Probabilidad base de navegar según humor
    _BROWSE_CHANCE_BY_MOOD = {
        "bored":     0.55,   # aburrida → ¡necesita algo que ver!
        "chill":     0.40,   # relajada → puede explorar tranquila
        "focused":   0.30,   # metida en algo → podría buscar información
        "gremlin":   0.25,   # caótica → puede querer encontrar drama
        "flustered": 0.15,   # nerviosa → prefiere hablar
        "hyped":     0.10,   # muy activa → energía va al chat
    }

    def _should_browse_now(self, browser_available: bool) -> bool:
        """
        Decide si Aiko quiere navegar en este momento.
        Nunca navega si el chat está activo o si ya está navegando.
        """
        if not browser_available:
            return False
        if self._signals.get('browsing', False):
            return False
        if self._chat_is_hot():
            return False
        if self._chat_active_recently(seconds=30):   # espera 30s de silencio mínimo
            return False

        # Cooldown de navegación
        now = time.time()
        if (now - self._last_browse_time) < self._browse_cooldown:
            return False

        chance = self._BROWSE_CHANCE_BY_MOOD.get(self.current_mood, 0.20)
        return random.random() < chance

    def _check_monologue(self, now: float, browser_available: bool = False) -> dict:
        elapsed = now - self._last_monologue_time

        # Si el chat está activo, romper el modo flujo automáticamente
        if self._in_flow_mode and (self._chat_is_hot() or self._chat_active_recently(seconds=12)):
            self._in_flow_mode = False
            self._flow_monologue_count = 0

        # Intervalo adaptativo: cuando el chat es activo, esperar más
        # Quieto: min-max base / Activo: base*1.5 / Caliente: base*2.5
        mpm = self._messages_per_minute
        if mpm > 10:
            eff_min = self.monologue_min * 2.5
            eff_max = self.monologue_max * 2.5
        elif mpm > 5:
            eff_min = self.monologue_min * 1.5
            eff_max = self.monologue_max * 1.5
        else:
            eff_min = self.monologue_min
            eff_max = self.monologue_max

        # ── Modo flujo: delays muy cortos entre ideas ──────────────────────────
        if self._in_flow_mode:
            eff_min = 2.0
            eff_max = 5.0

        next_delay = self._next_monologue_delay

        if elapsed >= next_delay:
            # ── Ventana de decisión abierta ─────────────────────────────────
            # Primero: ¿Aiko quiere navegar? (nunca en modo flujo)
            if not self._in_flow_mode and self._should_browse_now(browser_available):
                self._last_browse_time = now
                # Cooldown del siguiente browse: 3-8 minutos
                self._browse_cooldown = random.uniform(180, 480)
                # Resetear también el timer de monólogo para que no dispare inmediatamente después
                self._last_monologue_time = now
                self._next_monologue_delay = random.uniform(eff_min, eff_max)
                mood = self.current_mood
                print(f"[Prompter] Aiko decidio navegar (humor: {mood})")
                return {
                    "action":  "browse",
                    "message": None,
                    "reason":  f"Aiko quiere explorar la web ({mood})",
                    "mood":    mood,
                    "emotion": mood,
                }

            # Segundo: ¿Quiere hacer un monólogo?
            if not self.should_do_monologue():
                self._next_monologue_delay = elapsed + random.uniform(5, 10)
                return self._wait("Timer OK pero no es buen momento")

            self._last_monologue_time = now
            self._next_monologue_delay = random.uniform(eff_min, eff_max)

            self.last_response_time = now
            self.monologues_delivered += 1

            trigger = ""

            return {
                "action":            "monologue",
                "message":           None,
                "reason":            "Timer de monologo + condicion OK",

                "emotion":           self.current_mood,
                "spontaneous_trigger": trigger,
                "activity":          "",
                "mood":              self.current_mood,
                "mood_desc":         MOODS[self.current_mood]["desc"],
                "session_anchor":    self.daily_anchor,
                "monologue_thread":  [m["content"] for m in self.current_thread
                                      if m.get("is_aiko")][-6:],
                "grounding_context": self.get_grounding_context(),
            }

        return self._wait("Esperando timer de monologo")

    def _seconds_since_last_chat(self) -> float:
        """Tiempo en segundos desde el último mensaje de chat humano."""
        now = time.time()
        # Usar el reader de TikTok si está disponible para mayor precisión
        if self._tiktok_reader is not None:
            try:
                if self._tiktok_reader.chat_is_active(window_secs=9999):
                    pass  # solo actualiza la ventana interna
            except Exception:
                pass
        chat_msgs = [m for m in self.current_thread if not m.get("is_aiko", False)]
        if chat_msgs:
            return now - chat_msgs[-1]["timestamp"]
        return 999.0



    def force_spontaneous(self) -> dict:
        now = time.time()
        self._last_monologue_time = now
        self.last_response_time = now
        self.monologues_delivered += 1
        return {
            "action": "monologue",
            "message": None,
            "reason": "Monólogo forzado",
            "emotion": self.current_mood,
            "spontaneous_trigger": "",
            "activity": "",
            "mood": self.current_mood,
            "mood_desc": MOODS[self.current_mood]["desc"],
            "session_anchor": self.daily_anchor,
            "monologue_thread": self.monologue_thread,
            "grounding_context": self.get_grounding_context(),
        }

    # ═══════════════════════════════════════════════════════════
    #  SISTEMA DE HUMOR
    # ═══════════════════════════════════════════════════════════
    def _maybe_change_mood(self, now: float):
        elapsed = now - self._last_mood_change
        if elapsed >= self._next_mood_delay:
            old_mood = self.current_mood
            mood_list = list(MOODS.keys())

            if self._messages_per_minute > 3:
                weights = [3 if m in ("hyped", "gremlin", "flustered") else 1
                           for m in mood_list]
            else:
                weights = [3 if m in ("bored", "chill", "focused") else 1
                           for m in mood_list]

            self.current_mood = random.choices(mood_list, weights=weights, k=1)[0]
            self._last_mood_change = now
            self._next_mood_delay = random.uniform(self.mood_change_min, self.mood_change_max)

            if self.current_mood != old_mood:
                print(f"[Prompter] Humor → {self.current_mood} "
                      f"({MOODS[self.current_mood]['desc'][:35]}...)")

    def _update_chat_rate(self, now: float):
        self._recent_message_times = [
            t for t in self._recent_message_times if now - t < 60
        ]
        self._messages_per_minute = len(self._recent_message_times)

    # ═══════════════════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════════════════
    def _wait(self, reason: str) -> dict:
        return {
            "action": "wait",
            "message": None,
            "reason": reason,
            "emotion": self.current_mood,
        }

    # ── Session anchor API (compatibilidad con main.py) ─────────────
    @property
    def session_anchor(self) -> str:
        return self.daily_anchor

    @session_anchor.setter
    def session_anchor(self, value: str):
        if value:
            self.daily_anchor = value
            self.anchor_progress.clear()
            self.current_thread.clear()

    @property
    def monologue_thread(self) -> list:
        return [m["content"] for m in self.current_thread if m["is_aiko"]][-4:]

    def add_to_thread_legacy(self, line: str):
        """Compatibilidad con llamadas de un solo argumento."""
        self.add_to_thread("Aiko", line, is_aiko=True)

    @property
    def current_activity(self):
        if self.anchor_progress:
            return f"desarrollando: {self.daily_anchor[:50]}..."
        return self.daily_anchor[:60]
