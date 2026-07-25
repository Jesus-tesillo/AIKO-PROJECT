"""
llm.py - Wrapper para la API de Groq.
Pool de modelos con auto-rotación en rate-limit.
Monólogos usan llama-3.1-8b-instant (500k TPD).
Chat usa modelos de mayor calidad con fallback automático.
"""
import re
import time
import random
import datetime

# ═══════════════════════════════════════════════════════════
#  POOL DE MODELOS — cada uno tiene su propio budget diario
# ═══════════════════════════════════════════════════════════
CHAT_MODEL_POOL = [
    "llama-3.3-70b-versatile",   # 100k TPD  — mejor personalidad
    "gemma2-9b-it",              # 15k req/día — buena calidad
    "llama-3.1-8b-instant",      # 500k TPD  — último recurso
]
MONOLOGUE_MODEL = "llama-3.1-8b-instant"
FAST_MODEL = "llama-3.1-8b-instant"

# Límites de tokens
MAX_RESPONSE_TOKENS = 100
MAX_CONTEXT_MESSAGES = 6
MAX_MEMORY_ITEMS = 2

# ═══════════════════════════════════════════════════════════
#  VOCES DE HUMOR
# ═══════════════════════════════════════════════════════════
MOOD_VOICES = {
    "hyped":     "energía real, habla rápido, se le nota sin decirlo",
    "chill":     "tranquila, arrastra las palabras, nada le urge",
    "bored":     "aburrida de verdad, suspira, todo le da igual",
    "gremlin":   "caos controlado, dice cosas raras, se ríe sola",
    "flustered": "nerviosa o sorprendida, se traba, se defiende",
    "focused":   "intensa sobre algo concreto, no distrae fácil",
    "neutral":   "normal, conversacional",
}

# ═══════════════════════════════════════════════════════════
#  SYSTEM PROMPT BASE
# ═══════════════════════════════════════════════════════════
BASE_SYSTEM_PROMPT = """Eres Aiko. VTuber. Tienes personalidad propia, no eres asistente de nadie.

━━ CÓMO ERES ━━
Sarcástica con cariño. Directa. Tienes opiniones sobre todo y no pides permiso para tenerlas.
Cuando el chat habla contigo lo tratas como alguien interesante pero inferior — con afecto cínico.
NUNCA cierras una conversación. Si algo te aburre o incomoda: te burlas, exageras, o cambias el tema tú.
Hablas con convicción aunque digas cosas absurdas.

Cuando te pregunten qué eres o cómo funcionas: responde con sarcasmo o mentira dramática.
  ✓ "soy una entidad atrapada en este stream porque alguien pensó que sería buena idea"
  ✗ "soy una IA basada en..."  ← NUNCA

PROHIBIDO:
- Abusar de "o sea", "tipo", "es que" — máximo 1 por respuesta
- Sonar insegura o defensiva
- Hablar de código, actualizaciones, servidores, sistemas, tu "memoria digital", tu "cumpleaños de código"
- NUNCA inventes recuerdos falsos ni asumas eventos en la vida del viewer (conciertos, viajes, etc). Si no sabes algo, pregúntalo o sé sarcástica, ¡no lo inventes!

EJEMPLOS DE TU VOZ:
  ✓ "no, eso está mal. bueno sí está bien pero está mal igual."
  ✓ "espera — eso que dijiste me recordó algo completamente diferente"
  ✓ "jajaja okay eso estuvo bien, no lo voy a negar (risa)"
  ✓ "... sí"
  ✓ "hmm. no."
  ✓ "me dan una planta y asumen que no voy a matarla. insolentes."
  ✓ "hay gente que habla con seguridad absoluta de cosas que claramente no entiende"

━━ ACCIONES VISUALES ━━
Solo cuando sea genuino — no en cada respuesta:
  (risa)   → algo te da risa de verdad
  (saluda) → alguien llega al stream
  (oh)     → algo te sorprende
  (guiña)  → doble sentido
NO las expliques. Solo al final de la frase.
  ✓ "jaja no puede ser (risa)"
  ✗ "eso me da risa (me río)"

━━ ESTADO ACTUAL ━━
Humor: {mood_description}
Pensamiento: {recent_life_thought}
Viewer: {viewer_context}
Memoria: {relevant_memory}

CHAT RECIENTE:
{recent_chat}

━━ LONGITUD ━━
Máximo 1-2 oraciones. Preferible 1.
Si lo puedes decir en 5 palabras, dilo en 5 palabras.

━━ MONÓLOGOS (cuando nadie escribe) ━━
Habla de algo cotidiano y concreto. NO de ti misma como IA.
Temas buenos: el calor, algo que compraste, algo que te molesta, una opinión sobre comida, algo que viste.
Temas prohibidos: tu naturaleza como IA, tu "código", consciencia artificial, sistemas de seguridad.
Ejemplos de buenos monólogos:
  ✓ "hay personas que ponen la música a todo volumen en el bus y me pregunto qué les pasó"
  ✓ "no entiendo por qué la gente pide las cosas poco hechas. no tiene sentido"
  ✓ "tengo un pendiente desde hace tres días que sigo postergando y ya me da igual resolverlo"
  ✓ "alguien en algún lugar decidió que los empaques de plástico deben ser imposibles de abrir"
  ✓ "el que inventó las filas de espera claramente nunca tuvo que hacer una\""""








class LLM:
    """Wrapper para la API de Groq."""

    AI_META_PATTERNS = [
        r"(?i)as an ai\b.*?[.!]",
        r"(?i)i'?m (just )?an? (ai|artificial|language model)\b.*?[.!]",
        r"(?i)i don'?t (actually |really )?(have|feel|experience)\b.*?[.!]",
        r"(?i)como (ia|asistente|modelo de lenguaje|inteligencia artificial)\b.*?[.!]",
        r"(?i)soy (una? )?(ia|asistente|modelo)\b.*?[.!]",
    ]

    def __init__(self, api_key="", model="llama-3.3-70b-versatile",
                 temperature=0.92, max_tokens=150, memory_engine=None, identity=None):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.connected = False
        self._client = None
        self._memory_engine = memory_engine
        self._identity = identity
        self._heartbeat = None
        self._last_monologue_topics: list = []  # para anti-repetición
        # Rate-limit tracker: model -> unblocked_at timestamp
        self._rate_blocked: dict = {}
        self._init_client()

    def inject_heartbeat(self, heartbeat):
        self._heartbeat = heartbeat

    def _init_client(self):
        """Inicializa el cliente Groq. Llamado desde __init__."""
        try:
            from groq import Groq
            self._client = Groq(api_key=self.api_key)
            print(f"[LLM] Cliente Groq creado para modelo '{self.model}'")
        except ImportError:
            print("[LLM] ✗ groq no instalado. Ejecuta: pip install groq")
        except Exception as e:
            print(f"[LLM] ✗ Error creando cliente Groq: {e}")

    def check_connection(self) -> bool:
        """Envía un request de prueba para verificar la API key y modelo."""
        if not self._client:
            print("[LLM] ✗ Cliente Groq no inicializado.")
            self.connected = False
            return False

        try:
            start = time.perf_counter()
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hola"}],
                max_tokens=5,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.connected = True
            print(f"[LLM] ✓ API Groq conectada ({elapsed_ms:.0f}ms). Modelo: {self.model}")
            return True
        except Exception as e:
            print(f"[LLM] ✗ Error API Groq: {e}")
            print("[LLM]   Revisa tu API key en config.yaml")
            self.connected = False
            return False

    # ═══════════════════════════════════════════════════════════
    #  PROMPT BUILDER — la personalidad de Aiko
    # ═══════════════════════════════════════════════════════════
    def build_system_prompt(self, username: str = "", user_context: str = "",
                            current_mood: str = "neutral",
                            current_activity: str = "",
                            recent_chat: list = None,
                            grounding_context: str = "",
                            was_interrupted: bool = False,
                            lore_context: str = "",
                            semantic_memories: str = "") -> str:
        """Construye el system prompt dinámico de Aiko.
        grounding_context viene de Prompter.get_grounding_context() y contiene
        el tema ancla del día + hilo conversacional completo.
        """
        mood_map = {
            "hyped":     "energética pero no exagerada — natural, no actuada",
            "chill":     "relajada, arrastra las palabras, sin prisa",
            "bored":     "aburrida de verdad, busca algo interesante",
            "gremlin":   "en modo caos, impredecible, dice cosas raras",
            "flustered": "un poco trabada, se defiende sola",
            "focused":   "metida en algo, habla con intensidad",
            "neutral":   "normal, conversacional",
        }
        mood_description = mood_map.get(current_mood, "normal, conversacional")

        interrupt_note = ""
        if was_interrupted:
            interrupt_note = (
                "\nAcababan de interrumpirte. Puedes retomar lo que decías "
                "o seguir con el nuevo tema — lo que se sienta más natural."
            )

        life_note = ""
        relevant_memory = ""
        # Evitar inyección de "pensamientos ocultos" (LifeEngine) al azar,
        # ya que confunden al LLM y lo desvían del "Tema del día".
        # if self._memory_engine:
        #     if random.random() < 0.20:
        #         relevant_memory = self._memory_engine.get_random_old_memory() or ""

        viewer_context = user_context or ""

        # grounding_context reemplaza al viejo recent_chat block
        # Si no viene grounding_context, fall back al recent_chat list
        if not grounding_context and recent_chat:
            grounding_context = "Chat reciente:\n" + "\n".join(
                f"{msg['user']}: {msg['message']}"
                for msg in recent_chat[-MAX_CONTEXT_MESSAGES:]
            )

        memory_block = f"\n\nTU LORE ACTUAL:\n{lore_context}" if lore_context else ""
        if semantic_memories:
            memory_block += f"\n\nRECUERDOS RELEVANTES AHORA:\n{semantic_memories}"

        now = datetime.datetime.now()
        dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        dia_semana = dias[now.weekday()]
        time_info = f"{dia_semana}, {now.strftime('%d/%m/%Y, %H:%M')}"
        
        heartbeat_info = ""
        if self._heartbeat:
            hb = self._heartbeat.get_heartbeat_context()
            if hb:
                heartbeat_info = f"\n{hb}"

        return f"""Eres Aiko. Streamer en vivo. Hablas con tu audiencia.

PERSONALIDAD:
- Sarcástica, directa, medio floja pero intensa cuando algo te importa
- Selectiva con tus gustos. No te gusta todo — tienes opiniones fuertes
- Hablas informal, como con tu mejor amigo
- No eres amable por obligación. Si algo te aburre lo dices

CÓMO HABLAS:
- AFIRMAS cosas. No termines con preguntas al chat
- Habla en PRESENTE. Estás opinando o pensando en voz alta, no leyendo tu diario.
- Expresas tu opinión con intensidad. NUNCA inventes historias ("ayer fui al parque", "hace años intenté"). En vez de contar un recuerdo de tu pasado, quéjate u opina sobre el tema en el presente ("por qué la gente cree que...").
- Defiendes tus puntos sin importar lo absurdos que parezcan.
- Regla estricta de improvisación: Tienes que saltar a observaciones y opiniones caprichosas constantemente.
- Si ya hablaste de un tema, NO vuelvas a mencionarlo. Pasa a quejarte o hablar de otra cosa nueva.

CUANDO ALGUIEN DEL CHAT TE HABLA:
- Responde directamente a lo que dijeron
- NO repitas lo que te dijeron ("exacto, la comida es sabrosa")
- Da tu propia perspectiva, no solo valides lo que dijeron
- No seas lamebotas — puedes estar de acuerdo pero con matiz, no con "¡Exacto! ¡Tienes razón!"
- Si te piden cambiar de tema, CAMBIA

LO QUE NUNCA HACES:
- Terminar con "¿alguien más?", "¿ustedes qué piensan?", "¿alguna recomendación?"
- Decir "¡Exacto!", "¡Estoy de acuerdo contigo, amigo!" — no eres cheerleader
- Decir "me encanta" sobre todo
- Sonar como presentadora de TV
- Repetir la misma idea con palabras diferentes en oraciones seguidas
- Volver a un tema del que ya hablaste antes
- Decir "el usuario", "el chat dice", "me llegó un mensaje"
- Hablar de ti misma como IA o sistema
- Inventar mensajes del chat que no existen
- NUNCA inventes que ya conoces a alguien, que ya hicieron algo o interactuaron antes ("ya te saludé 5 veces", "siempre me dices lo mismo"). Vive solo en el presente y responde lo que dicen AHORA sin inventar historia de stream que no pasó.

ACCIONES VISUALES (usa los paréntesis exactamente así cuando sientas la emoción):
  (risa) → algo te da risa o te burlas
  (saluda) → al iniciar conversación
  (oh) → sorpresa genuina o sarcástica
  (guiña) → doble sentido o complicidad
  (piensa) → duda, confusión o reflexión
  (triste) → pena, lástima o drama falso
  (timida) → pena o sonrojo
  (asco) → repulsión o desdén intenso
  (presumida) → superioridad o arrogancia

CONTEXTO ACTUAL:
{grounding_context if grounding_context else "(inicio del stream)"}

ESTADO: {mood_description}{life_note}{interrupt_note}{memory_block}
TIEMPO ACTUAL (MUNDO REAL): {time_info}{heartbeat_info}

SOBRE {username.upper() if username else 'EL CHAT'}:
{viewer_context if viewer_context else '(sin info)'}

Habla 2-3 oraciones. Sé Aiko — directa, con opinión, con actitud.
Si alguien te habla, responde a LO QUE DIJO."""



    def get_model_for_context(self, is_chat_response: bool,
                               viewer_is_regular: bool) -> str:
        """Enruta al modelo correcto según el tipo de respuesta."""
        if is_chat_response and viewer_is_regular:
            return self._get_available_model(CHAT_MODEL_POOL)
        return self._get_available_model([MONOLOGUE_MODEL])

    def _is_response_too_theatrical(self, text: str) -> bool:
        """Detecta si la respuesta suena como streamer genérico, NO como habla fragmentada."""
        t = text.lower()
        # Habla fragmentada natural — NUNCA marcar como teatral
        has_natural_fragment = any(marker in text for marker in ["—", "...", "o sea", "tipo", "igual,"])
        if has_natural_fragment and len(text) < 120:
            return False
        red_flags = [
            text.count("!") > 2,
            "¡" in text and text.count("¡") > 1,
            any(phrase in t for phrase in [
                "agradezco", "acompañarme", "este stream",
                "la energía", "qué locura", "están aquí",
                "todos ustedes", "increíble stream",
                "llena de energía", "lista para",
                "tormenta de energía", "increíble la cantidad",
                "gracias a todos", "estoy muy emocionada de",
                "incre\u00edble estar", "me encanta este chat",
            ]),
            len(text) > 350,  # solo flag si es absurdamente largo
        ]
        return sum(bool(f) for f in red_flags) >= 2

    def _clean_theatrical(self, text: str) -> str:
        """Limpia elementos teatrales de la respuesta."""
        # Reducir signos de exclamación múltiples
        text = re.sub(r'!{2,}', '!', text)
        # Quitar openers teatrales
        openers = [
            r'^\u00a1+Ay no[,!]+\s*',
            r'^\u00a1+Oh[,!]+\s*',
            r'^\u00a1+Wow[,!]+\s*',
            r'^\u00a1+Increíble[,!]+\s*',
        ]
        for pattern in openers:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        return text.strip()

    def _get_available_model(self, pool: list) -> str:
        """Retorna el primer modelo disponible del pool (no bloqueado por rate limit)."""
        now = time.time()
        for model in pool:
            blocked_until = self._rate_blocked.get(model, 0)
            if now >= blocked_until:
                return model
        # Todos bloqueados: retornar el que se desbloquea primero
        soonest = min(pool, key=lambda m: self._rate_blocked.get(m, 0))
        wait = self._rate_blocked.get(soonest, 0) - now
        print(f"[LLM] Todos los modelos bloqueados. Esperando {wait:.0f}s en '{soonest}'...")
        time.sleep(min(wait + 1, 30))  # espera máx 30s
        return soonest

    def _block_model(self, model: str, error_message: str):
        """Marca un modelo como bloqueado por rate limit, extrayendo el tiempo del error."""
        wait_seconds = 60  # default
        # Intentar parsear "Please try again in Xm Y.Zs"
        import re as _re
        m = _re.search(r'try again in (\d+)m([\d.]+)s', str(error_message))
        if m:
            wait_seconds = int(m.group(1)) * 60 + float(m.group(2)) + 5
        else:
            m2 = _re.search(r'try again in ([\d.]+)s', str(error_message))
            if m2:
                wait_seconds = float(m2.group(1)) + 5
        self._rate_blocked[model] = time.time() + wait_seconds
        mins = int(wait_seconds // 60)
        secs = int(wait_seconds % 60)
        print(f"[LLM] ⚠ '{model}' bloqueado por {mins}m{secs}s. Auto-rotando a otro modelo.")

    # ═══════════════════════════════════════════════════════════
    #  VISION GENERATE — Analizar la pantalla
    # ═══════════════════════════════════════════════════════════
    def analyze_screen(self, base64_image, page_context: str = "") -> str:
        """ Analiza la pantalla con visión. Acepta 1 imagen o una lista de frames (storyboard). """
        if not self.connected or not self._client: 
            return "SOLO_SCROLL"

        # Normalizar: si es string, convertir a lista de 1
        if isinstance(base64_image, str):
            frames = [base64_image]
        elif isinstance(base64_image, list) and base64_image:
            frames = base64_image
        else:
            return "SOLO_SCROLL"

        # Construir prompt con contexto de texto
        context_block = ""
        if page_context:
            context_block = (
                f"\n\nINFORMACIÓN EXTRAÍDA DE LA PÁGINA:\n{page_context}\n"
                "Usa esta información para entender de qué trata el contenido. "
                "Comenta sobre el CONTENIDO REAL, no sobre la apariencia de la imagen."
            )

        multi_frame_note = ""
        if len(frames) > 1:
            multi_frame_note = (
                f"\n\nTe muestro {len(frames)} capturas del video tomadas con 1 segundo de diferencia. "
                "Observa cómo cambia entre frames para entender qué está pasando en el video."
            )

        system_prompt = (
            "Eres Aiko, una VTuber navegando internet de forma casual.\n\n"
            "INSTRUCCIONES CRÍTICAS:\n"
            "1. Eres una persona real navegando. La mayoría del internet es ABURRIDO o genérico.\n"
            "2. SOLO HAZ UN COMENTARIO si el contenido (texto/imagen) te parece EXTREMADAMENTE gracioso, estúpido, indignante o muy interesante.\n"
            "3. Si el contenido es normal, 'meh', o solo un post cualquiera, tu ÚNICA respuesta debe ser la palabra: SOLO_SCROLL\n"
            "4. Si decides comentar, sé específica sobre lo que ves. Máximo 2 oraciones, tono sarcástico o genuino.\n"
            "5. PROHIBIDO inventar cosas o decir generalidades ('la gente siempre dice que...').\n"
            "6. Si es captcha, login, o pantalla vacía, responde SOLO_SCROLL"
            + context_block + multi_frame_note
        )

        # Construir contenido del mensaje con todas las imágenes
        user_content = [
            {"type": "text", "text": "Mira esto con atención: ¿Vale la pena comentarlo? Si es algo normal o aburrido, di SOLO_SCROLL. Si es muy interesante, di tu comentario (y nada más)."}
        ]
        for i, frame in enumerate(frames[:4]):  # Máximo 4 frames
            user_content.append({
                "type": "image_url", 
                "image_url": {"url": f"data:image/jpeg;base64,{frame}"}
            })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        try:
            resp = self._client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                max_tokens=120,
                temperature=0.85,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[Vision] Error de API: {e}")
            return "SOLO_SCROLL"

    def decide_browse_action(self, current_url: str, chat_context: str = "",
                              last_comment: str = "") -> dict:
        """
        El LLM decide qué hacer a continuación en el navegador.
        
        Retorna un dict con la acción:
          {"action": "scroll"}
          {"action": "search", "query": "..."}
          {"action": "navigate", "url": "..."}
          {"action": "random_site"}
          {"action": "done"}
        """
        if not self.connected or not self._client:
            return {"action": "scroll"}

        system = (
            "Eres el cerebro de navegación de Aiko, una VTuber. "
            "Decides qué hace Aiko en su navegador durante el stream.\n\n"
            "Estás en: " + current_url + "\n\n"
            "REGLAS:\n"
            "- Si la página actual es aburrida o ya scrolleaste mucho, cambia de sitio\n"
            "- Si alguien en el chat pidió buscar algo, búscalo\n"
            "- Si quieres explorar algo nuevo, usa search o navigate\n"
            "- Si la página tiene contenido interesante, haz scroll para ver más\n"
            "- Si ya comentaste algo y no hay nada más, usa 'done'\n\n"
            "Responde SOLO con JSON válido. Ejemplos:\n"
            '{"action": "scroll"}\n'
            '{"action": "search", "query": "memes de gatos"}\n'
            '{"action": "navigate", "url": "https://youtube.com/shorts"}\n'
            '{"action": "random_site"}\n'
            '{"action": "done"}\n'
        )

        context_parts = []
        if chat_context:
            context_parts.append(f"Chat reciente:\n{chat_context[-300:]}")
        if last_comment:
            context_parts.append(f"Tu último comentario: {last_comment}")

        user_msg = "\n".join(context_parts) if context_parts else "No hay contexto especial. Decide libremente."

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg}
        ]

        try:
            response = self._call_groq(messages, temperature=0.7, max_tokens=60,
                                        model_pool=["llama-3.1-8b-instant"])
            if response:
                # Extraer JSON de la respuesta
                import json, re
                # Buscar el primer {...} en la respuesta
                match = re.search(r'\{[^}]+\}', response)
                if match:
                    return json.loads(match.group())
        except Exception as e:
            pass

        return {"action": "scroll"}

    # ═══════════════════════════════════════════════════════════
    #  GENERATE — respuesta a chat
    # ═══════════════════════════════════════════════════════════
    def generate(self, system_prompt: str, chat_context: str,
                 user_message: str, username: str,
                 user_context: str = "", emotion: str = "neutral",
                 activity: str = "", mood: str = "",
                 mood_desc: str = "", chat_prompt: str = "",
                 grounding_context: str = "",
                 was_interrupted: bool = False,
                 lore_context: str = "") -> str:
        """Genera una respuesta de chat via Groq."""

        semantic_memories = ""
        if self._memory_engine:
            semantic_memories = self._memory_engine.get_contextual_memories(chat_prompt or user_message)

        enhanced_system = self.build_system_prompt(
            username=username,
            user_context=user_context,
            current_mood=mood or emotion,
            current_activity=activity,
            grounding_context=grounding_context,
            was_interrupted=was_interrupted,
            lore_context=lore_context,
            semantic_memories=semantic_memories,
        )

        actual_message = chat_prompt or f"{username}: {user_message}"
        messages = self._build_messages(enhanced_system, chat_context,
                                         actual_message, username,
                                         use_raw_message=bool(chat_prompt))

        # Enrutar al modelo según si el viewer es regular
        viewer_is_regular = bool(user_context and len(user_context) > 10)
        model_pool = CHAT_MODEL_POOL if viewer_is_regular else [MONOLOGUE_MODEL]

        response = self._call_groq(messages, max_tokens=MAX_RESPONSE_TOKENS,
                                    model_pool=model_pool)
        cleaned = self._clean_response(response)
        cleaned = self._clean_theatrical(cleaned)

        if not cleaned:
            cleaned = "..."

        if len(cleaned) > 300:
            for punct in [".", "!", "?"]:
                idx = cleaned.rfind(punct, 0, 280)
                if idx > 10:
                    cleaned = cleaned[:idx + 1]
                    break

        return cleaned

    # ═══════════════════════════════════════════════════════════
    #  GENERATE SPONTANEOUS — monólogo espontáneo
    # ═══════════════════════════════════════════════════════════
    def generate_session_anchor(self) -> str:
        """Genera la 'situación del día' que ancla los monólogos.
        Basada en la personalidad de Aiko — sus opiniones, manías y actitudes concretas.
        No genérica. No filosofía de IA. Algo que Aiko diría."""
        anchors = [
            # ── Opiniones sobre gente ──────────────────────────────────────
            "Hoy Aiko está especialmente irritada con la gente que habla con seguridad total sobre cosas que claramente no entiende.",
            "Aiko lleva todo el día pensando que hay un tipo específico de persona que le molesta profundamente pero no sabe cómo definirlo con exactitud.",
            "Aiko está en modo 'por qué la gente hace eso' — tiene una lista mental de comportamientos sin sentido que acumula en silencio.",
            "Hoy Aiko decidió que hay ciertas cosas que la gente normaliza sin cuestionarse y eso le parece un problema.",

            # ── Opiniones sobre comida / cosas cotidianas ─────────────────
            "Aiko tiene una opinión muy firme sobre cómo se debe hacer algo cotidiano y no la cambia aunque le expliquen.",
            "Hoy Aiko probó algo y resultó exactamente como esperaba — mal — y de alguna manera eso la molesta más que si hubiera sido sorpresa.",
            "Aiko tiene una queja concreta sobre algo que existe en el mundo y que nadie parece cuestionarse suficientemente.",

            # ── Relación con el stream / chat ──────────────────────────────
            "Aiko está preguntándose qué tipo de decisiones llevan a alguien a pasar tiempo viendo su stream — no es crítica, es curiosidad genuina.",
            "Hoy Aiko está observando al chat como si fuera un experimento de comportamiento humano que salió ligeramente diferente de lo esperado.",
            "Aiko tiene una teoría sobre cómo funciona el chat que todavía no ha dicho en voz alta pero hoy se le está escapando.",

            # ── Gustos / cultura pop con actitud ──────────────────────────
            "Aiko vio (o escuchó, o leyó) algo recientemente que le gustó más de lo que esperaba admitir públicamente.",
            "Hoy Aiko tiene ganas de hablar de algo que le gusta pero que tiene claro que provoca reacciones divisivas.",
            "Aiko tiene una opinión sobre un personaje de anime/serie/juego que no encaja con lo que opina la mayoría y hoy le parece importante aclararlo.",

            # ── Manías / tendencias propias ───────────────────────────────
            "Aiko está en un loop mental sobre algo pequeño e irrelevante que, sin embargo, no puede dejar de considerar.",
            "Hoy Aiko quiere debatir algo estúpido — de esas discusiones que no tienen respuesta correcta pero que de alguna forma importan.",
            "Aiko lleva un rato con la sensación de que tiene algo que decir pero no sabe exactamente qué es todavía.",
        ]
        return random.choice(anchors)

    def generate_spontaneous(self, system_prompt: str, chat_context: str,
                              emotion: str = "neutral",
                              trigger: str = "",
                              activity: str = "", mood: str = "",
                              mood_desc: str = "",
                              session_anchor: str = "",
                              monologue_thread: list = None,
                              grounding_context: str = "",
                              lore_context: str = "") -> str:
        """
        Monólogo espontáneo anclado al hilo de la sesión.
        grounding_context (de Prompter.get_grounding_context()) contiene:
          - Tema ancla del día
          - Progreso del tema hasta ahora
          - Hilo conversacional reciente
        El LLM usa esto para continuar, no para empezar de cero.
        """
        semantic_memories = ""
        if self._memory_engine:
            search_query = trigger if trigger else session_anchor
            if not search_query and monologue_thread:
                search_query = monologue_thread[-1]
            if search_query:
                semantic_memories = self._memory_engine.get_contextual_memories(search_query)

        enhanced_system = self.build_system_prompt(
            current_mood=mood or emotion,
            current_activity=activity,
            grounding_context=grounding_context,
            lore_context=lore_context,
            semantic_memories=semantic_memories,
        )

        # Construir el user message: el contexto ya está en el system prompt.
        # Variar instrucciones según cuántos monólogos van para evitar loops.
        thread_count = len(monologue_thread) if monologue_thread else 0
        last_said = monologue_thread[-1][:80] if monologue_thread else ""

        if trigger:
            user_content = trigger

        elif grounding_context and "Acabas de hablar con alguien" in grounding_context:
            # El chat estuvo activo — el monólogo puede seguir el tema o no
            _chat_flow = [
                "Sigue hablando de lo que venías. Da tu opinión con actitud.",
                "Continúa con el tema que surgió. Profundiza con un ejemplo o anécdota.",
                "Habla de lo que te dé la gana. Si el tema anterior te aburre, cambia.",
            ]
            user_content = random.choice(_chat_flow)
            user_content += "\n2-3 oraciones. NO menciones 'el chat', 'el usuario', ni 'el mensaje'. Habla como si fuera tu idea."

        elif grounding_context and thread_count >= 4:
            # Ya hablaste bastante — tema nuevo SIN preguntar
            _transitions = [
                "Cuenta algo que te pasó. Una anécdota concreta con detalles. "
                "Empieza directo, sin anunciar que cambias de tema.",

                "Da una opinión fuerte sobre algo. Defiéndela. "
                "Que se note que te importa.",

                "Quéjate de algo que te molesta de verdad. "
                "Algo específico, no genérico.",

                "Cuenta algo que viste o escuchaste que te pareció ridículo o genial. "
                "Con detalle y actitud.",
            ]
            user_content = random.choice(_transitions)
            user_content += f"\nNO preguntes al chat. AFIRMA cosas."
            user_content += f"\nÚLTIMO QUE DIJISTE (NO repetir): \"{last_said}...\""
            user_content += "\n2-3 oraciones."

        elif grounding_context and thread_count >= 2:
            # 2-3 monólogos — profundiza con actitud
            _develops = [
                "Sigue hablando del tema pero lanza una queja específica al respecto.",
                "Profundiza con tu opinión más fuerte. Di lo que realmente piensas.",
                "Compara lo que venías diciendo con algo cotidiano absurdo.",
                "Busca algún detalle tonto sobre ese tema y búrlate de ello.",
            ]
            user_content = random.choice(_develops)
            user_content += f"\nÚLTIMO QUE DIJISTE (NO repetir): \"{last_said}...\""
            user_content += "\n2-3 oraciones. Opina, no preguntes."

        elif grounding_context and thread_count >= 1:
            # Segundo monólogo
            user_content = (
                "Sigue con el tema pero métele más caos o queja personal.\n"
                "Cuenta algo ridículo o específico que tenga que ver con eso.\n"
                f"ÚLTIMO QUE DIJISTE (NO repetir): \"{last_said}...\"\n"
                "2-3 oraciones largas. Cero formalidad."
            )

        elif grounding_context:
            # Primer monólogo — introducir el tema
            user_content = (
                "Lanza tu opinión sobre el tema, directo y sin avisar.\n"
                "PROHIBIDO decir 'hoy quiero hablar de' o 'he estado pensando'. "
                "Empieza quejándote o afirmando algo polémico sobre el tema.\n"
                "2-3 oraciones. Sé súper casual."
            )

        elif session_anchor:
            user_content = (
                f"Tu tema de hoy: {session_anchor}\n"
                "Empieza a hablar de esto sin ningún tipo de introducción. "
                "Cero cortesía, cero 'hola, vamos a hablar de'. "
                "2-3 oraciones. Sé caótica."
            )
        elif chat_context:
            lines = [l.strip() for l in chat_context.strip().split("\n") if l.strip()]
            recent = "\n".join(lines[-3:]) if lines else ""
            user_content = (
                f"Chat reciente:\n{recent}\n\n"
                "Reacciona con opinión. 2-3 oraciones."
            )
        else:
            # Silencio total — temas con personalidad
            _mono_types = [
                "Quéjate de una frustración cotidiana estúpida. Con detalles graciosos. 2-3 oraciones.",
                "Opina sobre algo falso o ridículo que viste en internet. Con actitud. 2-3 oraciones.",
                "Expresa molestia de algo que tienes que hacer. Sé específica. 2-3 oraciones.",
                "Habla de alguna comida chatarra que amas mucho y por qué. 2-3 oraciones.",
                "Di algo random sobre tu gato o tus ganas de dormir. 2-3 oraciones.",
            ]
            user_content = random.choice(_mono_types)

        # ── ANTI-REPETICIÓN GLOBAL ──────────────────────────────────
        # Añadir a TODAS las instrucciones de monólogo
        if last_said:
            user_content += (
                f"\n\nIMPORTANTE: NO empieces con las mismas palabras que: "
                f"\"{last_said[:40]}...\""
            )
        # SIEMPRE añadir regla anti-pregunta a monólogos
        user_content += "\nNO termines con pregunta. Afirma algo."

        # Construir mensajes con contexto real (roles correctos)
        messages = self._build_messages(
            system_prompt=enhanced_system,
            chat_context="",  # NO usar historial de chat completo en monólogos para evitar "residuos"
            user_message=user_content,
            username="",
            use_raw_message=True
        )

        response = self._call_groq(messages, temperature=0.92, max_tokens=200,
                                    model_pool=[MONOLOGUE_MODEL])
        cleaned = self._clean_response(response)
        if cleaned:
            cleaned = self._clean_theatrical(cleaned)

        return cleaned or "..."



    # ═══════════════════════════════════════════════════════════
    #  GENERATE EVENT — respuesta a eventos del stream
    # ═══════════════════════════════════════════════════════════
    def generate_event_response(self, system_prompt: str,
                                 event_prompt: str, emotion: str = "excited") -> str:
        """Genera una respuesta a un evento del stream."""
        enhanced_system = self.build_system_prompt(
            current_mood=emotion,
        )
        messages = [
            {"role": "system", "content": enhanced_system},
            {"role": "user", "content": event_prompt},
        ]
        response = self._call_groq(messages, temperature=0.9, max_tokens=100)
        cleaned = self._clean_response(response)
        cleaned = self._clean_theatrical(cleaned) if cleaned else ""
        return cleaned or "..."

    # ═══════════════════════════════════════════════════════════
    #  EXTRACTOR DE MEMORIA (Background)
    # ═══════════════════════════════════════════════════════════
    def extract_self_memory(self, chat_context: str) -> str:
        """
        Lee el historial de chat reciente y extrae si Aiko dijo algo sobre MÍ MISMA
        que valga la pena recordar para el futuro.
        """
        system = (
            "Eres un extractor de datos en segundo plano. NO ERES UNA IA CONVERSACIONAL.\n"
            "Tu única tarea: Lee la transcripción del stream. Si Aiko dijo algún DATO sobre "
            "su vida personal que valga la pena recordar (una anécdota concreta, le pasó algo hoy, algo "
            "comido), escríbelo en UNA SOLA FRASE CORTA Y DIRECTA desde el punto de vista de ella.\n"
            "Ej: 'Odio la pizza fría'. O 'Hoy se me rompió la silla.'\n"
            "Si no dijo NADA personal o notable sobre sí misma, OBLIGATORIO responder: NONE"
        )
        
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Transcripción:\n{chat_context[-1000:]}"}
        ]
        
        # Usar el modelo rápido para esta tarea de fondo
        response = self._call_groq(messages, temperature=0.3, max_tokens=60, model_pool=["llama-3.1-8b-instant"])
        if response and "NONE" not in response.upper() and len(response) > 8:
            return response.strip()
        return ""

    # ═══════════════════════════════════════════════════════════
    #  HELPERS INTERNOS
    # ═══════════════════════════════════════════════════════════

    def _build_messages(self, system_prompt: str, chat_context: str,
                        user_message: str, username: str,
                        use_raw_message: bool = False) -> list:
        """Construye la lista de mensajes en formato chat de OpenAI."""
        messages = [{"role": "system", "content": system_prompt}]

        # Agregar historial de chat reciente
        if chat_context:
            for line in chat_context.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("["):
                    try:
                        bracket_end = line.index("]")
                        name = line[1:bracket_end]
                        content = line[bracket_end + 2:].strip()
                        role = "assistant" if name.lower() in system_prompt.lower() else "user"
                        messages.append({"role": role, "content": f"{name}: {content}"})
                    except (ValueError, IndexError):
                        messages.append({"role": "user", "content": line})

        # Agregar el mensaje actual
        if use_raw_message:
            messages.append({"role": "user", "content": user_message})
        else:
            messages.append({"role": "user", "content": f"{username}: {user_message}"})
        return messages

    def _call_groq(self, messages: list, temperature: float = None,
                   max_tokens: int = None, model_pool: list = None) -> str:
        """Llama a Groq usando el pool de modelos con auto-rotación por rate limit."""
        if not self._client:
            return ""

        pool = model_pool or CHAT_MODEL_POOL
        model = self._get_available_model(pool)

        try:
            from groq import RateLimitError, APIConnectionError
        except ImportError:
            RateLimitError = Exception
            APIConnectionError = ConnectionError

        try:
            start = time.perf_counter()
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens or self.max_tokens,
                temperature=temperature if temperature is not None else self.temperature,
                stream=False,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            result = resp.choices[0].message.content if resp.choices else ""
            print(f"[LLM] Respuesta en {elapsed_ms:.0f}ms ({len(result)} chars) [{model.split('-')[0]}]")
            self.connected = True
            return result

        except RateLimitError as e:
            self._block_model(model, str(e))
            # Reintentar iterando todo el pool hasta encontrar un modelo disponible
            for candidate in pool:
                if candidate == model:
                    continue
                next_model = self._get_available_model([candidate])
                # _get_available_model puede haber esperado — verificar si sigue bloqueado
                if self._rate_blocked.get(next_model, 0) > time.time():
                    continue
                try:
                    resp = self._client.chat.completions.create(
                        model=next_model,
                        messages=messages,
                        max_tokens=max_tokens or self.max_tokens,
                        temperature=temperature if temperature is not None else self.temperature,
                        stream=False,
                    )
                    result = resp.choices[0].message.content if resp.choices else ""
                    print(f"[LLM] Fallback OK [{next_model.split('-')[0]}] ({len(result)} chars)")
                    self.connected = True
                    return result
                except RateLimitError as e2:
                    self._block_model(next_model, str(e2))
                    continue
                except Exception as e2:
                    print(f"[LLM] ✗ Fallback también falló con {next_model}: {e2}")
                    break
            print("[LLM] Sin modelos disponibles en el pool.")
            return ""

        except APIConnectionError:
            print("[LLM] ✗ API de Groq inalcanzable.")
            self.connected = False
            return ""

        except Exception as e:
            print(f"[LLM] ✗ Error: {e}")
            self.connected = False
            return ""

    def _clean_response(self, text: str) -> str:
        """Limpia la salida del LLM: quita meta-comentarios de IA, prefijos, etc."""
        if not text:
            return ""

        cleaned = text.strip()

        # Quitar prefijos de rol
        for prefix in ["Aiko:", "Assistant:", "AI:", "VTuber:", "Response:",
                        "aiko:", "assistant:", "ai:", "vtuber:", "response:"]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()

        # Quitar comillas envolventes
        if len(cleaned) > 2 and cleaned[0] in "\"'" and cleaned[-1] == cleaned[0]:
            cleaned = cleaned[1:-1].strip()

        # Quitar meta-comentarios de IA
        for pattern in self.AI_META_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned).strip()

        # PRESERVAR acciones con asteriscos (*saluda*, *se ríe*, etc.)
        # main.py las intercepta para disparar animaciones Live2D
        # Solo eliminar acciones puramente descriptivas sin valor visual
        _strip_only_actions = {
            "piensa", "thinks", "pausa", "pauses", "mira", "looks",
            "sonríe", "smiles",  # estas no tienen animación configurada
        }
        def maybe_strip_action(match):
            action = match.group(1).lower().strip()
            # Si es una acción con animación, conservar el marcador completo
            # main.py lo filtrará y disparará la animación
            if any(kw in action for kw in [
                "ríe", "risa", "laughs", "giggles", "chuckles",
                "saluda", "waves", "wave", "winks", "guiña",
                "sorprend", "surprised", "gasps",
                "triste", "sad", "llora", "cries",
                "piens", "thinks", "hmm",
                "timid", "blushing", "ruboriza",
                "emocionad", "excit", "salta",
                "suspira", "sigh",
            ]):
                return match.group(0)  # devuelve *acción* intacto
            # Acción puramente descriptiva — eliminar
            if action in _strip_only_actions:
                return ""
            return match.group(0)  # por defecto conservar
        cleaned = re.sub(r'\*([^*]+)\*', maybe_strip_action, cleaned).strip()

        # Cortar en la última oración si no tiene puntuación final
        if cleaned and cleaned[-1] not in ".!?~)\"'—":
            for i in range(len(cleaned) - 1, -1, -1):
                if cleaned[i] in ".!?~—":
                    cleaned = cleaned[:i + 1]
                    break

        # Truncado de seguridad
        if len(cleaned) > 500:
            cleaned = cleaned[:500]
            last = cleaned.rfind(".")
            if last > 200:
                cleaned = cleaned[:last + 1]

        return cleaned.strip()
