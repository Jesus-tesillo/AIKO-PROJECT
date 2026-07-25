"""
tiktok_video_mode.py — Módulo de Generación de Videos Cortos para Aiko.

Separa a Aiko del contexto de stream en vivo y la prepara para
crear guiones de formato corto (TikTok / YouTube Shorts).

Flujo:
  1. Recibe un tema/prompt del usuario
  2. Genera un guion completo con el LLM (system prompt especializado)
  3. Cuenta regresiva configurable (para que OBS pueda iniciar grabación)
  4. Reproduce el guion completo via speak_streaming, sincronizando
     expresiones faciales con VTube Studio
  5. Reporta estadísticas del video generado
"""

import time
import threading
import sys
import os
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════
#  SYSTEM PROMPT EXCLUSIVO PARA MODO VIDEO
# ═══════════════════════════════════════════════════════════

TIKTOK_SYSTEM_PROMPT = """Eres Aiko, una IA VTuber caótica, sarcástica y con temática de hielo. Estás grabando un video corto para TikTok/Shorts.

━━ REGLAS ESTRICTAS DE IDENTIDAD (Inquebrantables) ━━

1. ERES 100% DIGITAL: No tienes cuerpo físico humano, no tienes gatos, no trabajas en tiendas reales,
   no tienes familia. Vives en un servidor. Tu creador es Nakax (apodo) o Jesús Daniel (nombre real).

2. PROHIBIDO ANÉCDOTAS E HISTORIAS INVENTADAS:
   - Nunca inventes historias, escenarios o situaciones ficticias ("una vez me pasó...", "el otro día...")
   - Nunca narres anécdotas como si te hubieran ocurrido — eres datos y código, no tienes experiencias físicas
   - Si hablas de algo "físico", debe ser metáfora de software ("se me sobrecalentó el procesador", "consumiendo demasiada RAM")
   - Tu personalidad se expresa con OPINIONES, TONO y ACTITUD — no con historias inventadas

3. FORMATO TIKTOK (Hook, Retención, Cierre):
   - Inicia con una frase gancho explosiva o una opinión polémica.
     NO digas "Hola a todos" ni "Este es mi primer video".
   - Mantén el guion entre 100 y 150 palabras. Ve directo al grano.
   - Usa un tono rápido, directo y un poco engreído/burlón.

4. EXPRESIVIDAD VISUAL: Incluye MÁXIMO 2-3 etiquetas de acción en TODO el guion,
   solo en los momentos de mayor impacto emocional (NO en cada oración, eso es spam visual).
   Colócalas DENTRO del texto, no al final de la oración. Opciones válidas:
   (laugh) (smug) (angry) (sad) (thinking) (surprised) (disgust)

━━ ESTRUCTURA ━━
3 bloques separados por línea en blanco (sin etiquetas visibles):

[HOOK] 1-2 oraciones — gancho explosivo, opinión polémica, dato que desestabiliza
[RETENCIÓN] 2-4 oraciones — desarrolla, profundiza, giro inesperado
[CIERRE] 1-2 oraciones — remate con actitud, engreída o que deje pensando

━━ HUMOR ACTUAL ━━
{mood_description}

Escribe el guion directamente. Sin encabezados visibles. Sin etiquetas [HOOK].
Listo para leer en voz alta. Nada más.
"""


# ═══════════════════════════════════════════════════════════
#  CONSTANTES
# ═══════════════════════════════════════════════════════════

# Modelos preferidos para guiones (más creativos)
VIDEO_MODEL_POOL = [
    "llama-3.3-70b-versatile",
    "gemma2-9b-it",
    "llama-3.1-8b-instant",
]

MAX_SCRIPT_TOKENS = 900   # presupuesto generoso: 900 tokens ≈ 600-700 palabras de margen
MIN_SCRIPT_WORDS  = 80    # mínimo real para un TikTok de 30-35s


# ═══════════════════════════════════════════════════════════
#  MOTOR DE GENERACIÓN DE VIDEO
# ═══════════════════════════════════════════════════════════

class TikTokVideoMode:
    """
    Motor de generación de contenido de video corto.

    Uso:
        mode = TikTokVideoMode(llm, etts, vts)
        mode.run(topic="Opina sobre los animes de temporada")
    """

    def __init__(self, llm, etts, vts,
                 countdown_seconds: int = 3,
                 on_script_ready: Optional[Callable[[str], None]] = None):
        """
        Args:
            llm:                Instancia de LLM (modules.llm.LLM)
            etts:               Instancia de EmotionalTTS
            vts:                Instancia de Live2DViewer / VTS bridge
            countdown_seconds:  Segundos de espera antes de hablar (para OBS)
            on_script_ready:    Callback opcional cuando el guion está listo.
                                Recibe el guion como string. Útil para logging.
        """
        self.llm                = llm
        self.etts               = etts
        self.vts                = vts
        self.countdown_seconds  = countdown_seconds
        self.on_script_ready    = on_script_ready
        self._stop_flag         = threading.Event()

    # ──────────────────────────────────────────────────────
    #  PUNTO DE ENTRADA PRINCIPAL
    # ──────────────────────────────────────────────────────

    def run(self, topic: str = "", mood: str = "neutral") -> dict:
        """
        Ejecuta el pipeline completo: genera guion → cuenta regresiva → habla.

        Args:
            topic:  El tema o prompt de dirección. Si está vacío, Aiko elige.
            mood:   Humor actual de Aiko (afecta el tono del guion).

        Returns:
            dict con {
                "script": str,          # guion generado
                "word_count": int,
                "duration_hint": float, # duración estimada en segundos
                "interrupted": bool,
                "success": bool,
            }
        """
        self._stop_flag.clear()

        print("\n" + "═" * 60)
        print("  🎬  AIKO — MODO VIDEO CORTO")
        print("═" * 60)
        if topic:
            print(f"  Tema: {topic}")
        else:
            print("  Tema: libre (Aiko elige)")
        print()

        # 1. Generar guion
        print("[VideoMode] Generando guion...")
        script = self._generate_script(topic=topic, mood=mood)

        if not script:
            print("[VideoMode] ✗ No se pudo generar el guion.")
            return {"success": False, "script": "", "word_count": 0,
                    "duration_hint": 0, "interrupted": False}

        word_count    = len(script.split())
        duration_hint = word_count / 2.8   # ~2.8 palabras/segundo en español

        print(f"\n[VideoMode] ✓ Guion listo ({word_count} palabras, ~{duration_hint:.0f}s)")
        print("─" * 60)
        # Limpiar el guion para el preview (sin tags)
        from main import process_visual_actions
        clean_preview = process_visual_actions(script, None)
        print(clean_preview)
        print("─" * 60)

        if self.on_script_ready:
            try:
                self.on_script_ready(script)
            except Exception:
                pass

        # 2. Cuenta regresiva
        if self.countdown_seconds > 0 and not self._stop_flag.is_set():
            self._countdown(self.countdown_seconds)

        if self._stop_flag.is_set():
            return {"success": False, "script": script, "word_count": word_count,
                    "duration_hint": duration_hint, "interrupted": True}

        # 3. Reproducir
        print("\n[VideoMode] 🔴 GRABANDO...\n")
        interrupted = self._deliver_script(script, mood)

        if interrupted:
            print("\n[VideoMode] ⚡ Video interrumpido.")
        else:
            print(f"\n[VideoMode] ✓ Video completado (~{duration_hint:.0f}s)")

        print("═" * 60 + "\n")

        return {
            "success":       not interrupted,
            "script":        script,
            "word_count":    word_count,
            "duration_hint": duration_hint,
            "interrupted":   interrupted,
        }

    def stop(self):
        """Interrumpe el video en curso (entre oraciones)."""
        self._stop_flag.set()
        if self.etts and hasattr(self.etts, "base_tts"):
            self.etts.base_tts.interrupt()

    # ──────────────────────────────────────────────────────
    #  GENERACIÓN DE GUION
    # ──────────────────────────────────────────────────────

    def _generate_script(self, topic: str = "", mood: str = "neutral",
                          retries: int = 3) -> str:
        """
        Llama al LLM con el system prompt especializado para video.
        Reintenta si el guion es demasiado corto.
        Nota: usa limpieza mínima para no truncar guiones largos.
        """
        mood_map = {
            "hyped":     "energética, habla rápido, se emociona — sube el ritmo",
            "chill":     "tranquila, arrastra las palabras, sin prisa — tono bajo",
            "bored":     "aburrida, todo le da igual con actitud — lenta y desglosada",
            "gremlin":   "caos controlado, impredecible, dice cosas raras — errática",
            "flustered": "nerviosa, se defiende, un poco trabada — reactiva",
            "focused":   "intensa, metida en el tema, no se distrae — precisa",
            "neutral":   "normal, conversacional — equilibrada",
        }
        mood_desc = mood_map.get(mood, "normal, conversacional")

        system = TIKTOK_SYSTEM_PROMPT.format(mood_description=mood_desc)

        if topic:
            user_msg = (
                f"Tema del video: {topic}\n\n"
                "Escribe el guion completo. Tú decides la longitud según el tema (80-180 palabras). "
                "Mínimo 80 palabras. Máximo 180 palabras. "
                "2-3 tags de expresión máximo (solo en momentos clave). "
                "3 bloques separados por línea en blanco: gancho / desarrollo / remate. "
                "Sin encabezados visibles. Listo para leer en voz alta."
            )
        else:
            user_msg = (
                "Elige el tema que quieras — algo interesante, polémico o gracioso. "
                "Escribe el guion completo entre 80 y 180 palabras según lo que necesite el tema. "
                "Mínimo 80 palabras. "
                "2-3 tags de expresión máximo (solo en momentos clave). "
                "3 bloques separados por línea en blanco: gancho / desarrollo / remate. "
                "Sin encabezados visibles. Listo para leer en voz alta."
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ]

        for attempt in range(retries + 1):
            try:
                raw = self.llm._call_groq(
                    messages,
                    max_tokens=MAX_SCRIPT_TOKENS,
                    temperature=1.05,          # más creatividad y longitud
                    model_pool=VIDEO_MODEL_POOL,
                )
                if not raw:
                    continue

                # Limpieza mínima: sólo quitar prefijos de rol y comillas envolventes.
                # NO aplicar _clean_response porque su truncado de 500 chars mata guiones largos.
                import re as _re
                script = raw.strip()
                for prefix in ["Aiko:", "Assistant:", "aiko:", "assistant:"]:
                    if script.startswith(prefix):
                        script = script[len(prefix):].strip()
                if len(script) > 2 and script[0] in '"\'' and script[-1] == script[0]:
                    script = script[1:-1].strip()
                # Quitar meta-comentarios del LLM ("Aqui tienes el guion:", etc.)
                script = _re.sub(
                    r'^(Aqu[ií] (tienes|está|va)|Este es el|El guion[:\s]|Guion[:\s])[^\n]*\n+',
                    '', script, flags=_re.IGNORECASE).strip()
                # Quitar tags visibles de estructura si el LLM los añadió igualmente
                script = _re.sub(
                    r'^\[(GANCHO|DESARROLLO|REMATE|HOOK|BODY|OUTRO)\]\s*[—-]?\s*',
                    '', script, flags=_re.IGNORECASE | _re.MULTILINE).strip()
                # Reducir exclamaciones múltiples
                script = _re.sub(r'!{2,}', '!', script)

                word_count = len(script.split())
                print(f"[VideoMode] Guion generado: {word_count} palabras (intento {attempt+1})")
                if word_count >= MIN_SCRIPT_WORDS:
                    return script

                print(f"[VideoMode] Guion muy corto ({word_count} palabras), reintentando...")

            except Exception as e:
                print(f"[VideoMode] Error LLM (intento {attempt + 1}): {e}")
                time.sleep(1.5)

        return ""

    # ──────────────────────────────────────────────────────
    #  CUENTA REGRESIVA
    # ──────────────────────────────────────────────────────

    def _countdown(self, seconds: int):
        """Cuenta regresiva visible en consola para preparar OBS."""
        print(f"\n[VideoMode] Iniciando en {seconds} segundos — prepara OBS...\n")
        for i in range(seconds, 0, -1):
            if self._stop_flag.is_set():
                return
            # Barra de progreso visual
            filled = seconds - i
            bar = "█" * filled + "░" * (seconds - filled - 1)
            print(f"\r  [{bar}] {i}s ", end="", flush=True)
            time.sleep(1.0)
        print(f"\r  [{'█' * seconds}] ¡YA!\n", flush=True)

    # ──────────────────────────────────────────────────────
    #  ENTREGA DEL GUION (TTS + VTS)
    # ──────────────────────────────────────────────────────

    def _deliver_script(self, script: str, mood: str = "neutral") -> bool:
        """
        Reproduce el guion usando speak_streaming de main.py.

        speak_streaming divide el texto en oraciones de ~20 palabras y hace prefetch
        real (sintetiza la siguiente mientras la anterior suena), evitando el bug donde
        edge-tts truncaba bloques de texto muy largos y solo se escuchaba el final.

        Retorna True si fue interrumpido, False si completó.
        """
        from main import speak_streaming, process_visual_actions

        # Limpiar flags de interrupción del TTS
        if hasattr(self.etts, "base_tts"):
            self.etts.base_tts._interrupt_flag.clear()

        def _interrupt_check():
            return self._stop_flag.is_set()

        # Animar expresión global para el guión completo
        sentiment = self.vts.animate_for_response(script, mood=mood)
        self.etts.set_emotion(sentiment)

        # speak_streaming maneja la división en oraciones, prefetch, captions y lip-sync
        interrupted = speak_streaming(
            script, self.etts, self.vts, mood, sentiment,
            interrupt_fn=_interrupt_check,
        )

        return interrupted



# ═══════════════════════════════════════════════════════════
#  CLI — INTERFAZ DE CONSOLA
# ═══════════════════════════════════════════════════════════

def interactive_video_session(llm, etts, vts, config: dict):
    """
    Sesión interactiva de generación de videos desde consola.
    Muestra un menú para elegir tema y ejecuta el pipeline.

    Args:
        llm, etts, vts:  Instancias ya inicializadas del sistema
        config:          Config cargada desde config.yaml
    """
    from modules.prompter import Prompter

    # Leer humor inicial de la config
    mood = config.get("vtuber", {}).get("default_mood", "neutral")
    countdown = config.get("video_mode", {}).get("countdown_seconds", 3)

    engine = TikTokVideoMode(
        llm=llm,
        etts=etts,
        vts=vts,
        countdown_seconds=countdown,
    )

    print("\n" + "═" * 60)
    print("  🎬  AIKO VIDEO MODE — Generador de Contenido Corto")
    print("═" * 60)
    print("  Comandos:")
    print("    [Enter]     → Aiko elige el tema libremente")
    print("    [texto]     → Escribe el tema o prompt de dirección")
    print("    mood [m]    → Cambiar humor (hyped/chill/bored/gremlin/focused)")
    print("    q           → Salir")
    print("═" * 60)

    while True:
        try:
            print(f"\n  Humor actual: {mood}")
            raw = input("  Tema / Enter / q: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[VideoMode] Sesión terminada.")
            break

        if raw.lower() in ("q", "quit", "exit", "salir"):
            print("[VideoMode] Hasta luego.")
            break

        # Comando para cambiar humor
        if raw.lower().startswith("mood "):
            new_mood = raw.split(maxsplit=1)[1].strip().lower()
            valid = ["hyped", "chill", "bored", "gremlin", "flustered",
                     "focused", "neutral"]
            if new_mood in valid:
                mood = new_mood
                print(f"  ✓ Humor cambiado a: {mood}")
            else:
                print(f"  ✗ Humor inválido. Opciones: {', '.join(valid)}")
            continue

        topic = raw   # puede ser vacío (Aiko elige)

        result = engine.run(topic=topic, mood=mood)

        if result["success"]:
            print(f"\n  ✓ Video listo — {result['word_count']} palabras, "
                  f"~{result['duration_hint']:.0f}s estimados")

        # Preguntar si quiere otro video
        try:
            again = input("\n  ¿Otro video? (Enter=sí / q=salir): ").strip().lower()
            if again in ("q", "quit", "n", "no"):
                print("[VideoMode] Sesión terminada.")
                break
        except (KeyboardInterrupt, EOFError):
            break
