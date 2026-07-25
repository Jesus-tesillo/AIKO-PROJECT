"""
game_engine.py - Motor de Gaming Autonomo para Aiko.

Sistema hibrido: OCR local (gratis) para leer dialogos +
Groq Vision (esporadico) para entender la escena visual.

Disenado para visual novels (Doki Doki Literature Club).
"""

import base64
import io
import json
import random
import re
import threading
import time
from typing import Optional, Callable

import numpy as np
import mss
import pyautogui
import pygetwindow as gw
from PIL import Image
from groq import Groq

# Seguridad de pyautogui
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

# Nombres conocidos de DDLC
DDLC_CHARACTERS = {"Sayori", "Monika", "Yuri", "Natsuki", "MC", "Player",
                    "Aiko", "???", "Sayo", "Mon"}


class GameEngine:
    """Motor autonomo para que Aiko juegue visual novels."""

    VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
    FAST_MODEL = "llama-3.1-8b-instant"

    SCENE_EMOTION_MAP = {
        "happy": "excited", "romantic": "shy", "sad": "sad",
        "scary": "surprised", "horror": "surprised", "funny": "laugh",
        "tense": "thinking", "awkward": "shy", "shocking": "surprised",
        "cute": "excited", "neutral": None, "dramatic": "surprised",
        "creepy": "surprised", "emotional": "sad", "angry": "angry",
    }

    def __init__(self, groq_api_key: str, etts=None, vts=None, llm=None):
        self._groq = Groq(api_key=groq_api_key)
        self.etts = etts
        self.vts = vts
        self.llm = llm

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._speak_callback: Optional[Callable] = None
        self._chat_check_callback: Optional[Callable] = None
        self._game_window_title = "Doki Doki Literature Club!"

        # Historia acumulada
        self._story_log: list[str] = []
        self._max_story_log = 20
        self._last_scene_text = ""
        self._last_narrated_text = ""
        self._scene_count = 0

        # Timing
        self._last_speak_time = 0
        self._min_speak_gap = 1.0
        self._last_opinion_time = 0
        self._opinion_cooldown = 15
        self._is_speaking = False

        # API budget
        self._consecutive_errors = 0
        self._max_backoff = 15
        self._dialogues_since_vision = 0
        self._vision_interval = 5  # Vision cada N dialogos nuevos
        self._last_vision_result = {}  # Cache del ultimo analisis visual
        self._last_emotion = "neutral"

        # OCR
        self._ocr_reader = None
        self._init_ocr()

        print("[GameEngine] OK - Motor de gaming inicializado")

    # ==============================================================
    #  OCR LOCAL (GRATIS E ILIMITADO)
    # ==============================================================

    def _init_ocr(self):
        """Inicializa EasyOCR para lectura local de texto."""
        try:
            import easyocr
            self._ocr_reader = easyocr.Reader(
                ['en'], gpu=False, verbose=False
            )
            print("[GameEngine] OCR local listo (easyocr, CPU)")
        except Exception as e:
            print(f"[GameEngine] WARN: OCR no disponible: {e}")
            print("[GameEngine] Se usara solo Vision API (mas costoso)")

    def _read_dialogue_ocr(self, screenshot_b64: str) -> dict:
        """
        Lee el texto del dialogo usando OCR local.
        Retorna: {text, character, has_text}
        """
        if not self._ocr_reader:
            return {"text": "", "character": "", "has_text": False}

        try:
            img_data = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_data))
            w, h = img.size

            # --- Crop del area del nombre (arriba de la caja de dialogo) ---
            name_crop = img.crop((
                int(w * 0.02), int(h * 0.65),
                int(w * 0.35), int(h * 0.74)
            ))
            name_np = np.array(name_crop)
            name_results = self._ocr_reader.readtext(name_np)

            character = ""
            for bbox, text, conf in name_results:
                cleaned = text.strip().title()
                # Buscar coincidencia con nombres conocidos
                for name in DDLC_CHARACTERS:
                    if name.lower() in cleaned.lower() or cleaned.lower() in name.lower():
                        character = name
                        break
                if character:
                    break

            # --- Crop de la caja de dialogo ---
            dialogue_crop = img.crop((
                int(w * 0.05), int(h * 0.74),
                int(w * 0.95), int(h * 0.97)
            ))
            dialogue_np = np.array(dialogue_crop)
            dialogue_results = self._ocr_reader.readtext(dialogue_np)

            # Extraer lineas con confianza > 25%
            lines = [r[1] for r in dialogue_results if r[2] > 0.25]
            full_text = " ".join(lines).strip()

            has_text = len(full_text) > 3
            return {
                "text": full_text,
                "character": character,
                "has_text": has_text,
            }

        except Exception as e:
            print(f"[GameEngine] Error OCR: {e}")
            return {"text": "", "character": "", "has_text": False}

    # ==============================================================
    #  TRADUCCION (LLM texto, muy barato)
    # ==============================================================

    def _translate(self, english_text: str) -> str:
        """Traduce texto ingles a espanol usando LLM rapido (barato)."""
        if not english_text or len(english_text) < 3:
            return ""
        try:
            resp = self._groq.chat.completions.create(
                model=self.FAST_MODEL,
                messages=[
                    {"role": "system", "content":
                     "Traduce el siguiente texto de un videojuego del ingles al espanol. "
                     "Responde UNICAMENTE con la traduccion, sin explicaciones, "
                     "sin comillas, sin caracteres japoneses ni chinos. "
                     "Si el texto no tiene sentido o esta vacio, responde con un guion."},
                    {"role": "user", "content": english_text},
                ],
                max_tokens=150,
                temperature=0.3,
            )
            result = resp.choices[0].message.content.strip()
            result = self._sanitize_for_tts(result)
            if result == "-" or len(result) < 2:
                return ""
            return result
        except Exception as e:
            print(f"[GameEngine] Error traduccion: {e}")
            return english_text

    def _sanitize_for_tts(self, text: str) -> str:
        """Limpia texto para evitar crashes de GPT-SoVITS con caracteres especiales."""
        if not text:
            return ""
        # Eliminar caracteres no-latin que causan charmap errors
        import unicodedata
        cleaned = []
        for ch in text:
            cat = unicodedata.category(ch)
            # Mantener: letras, numeros, puntuacion comun, espacios
            if cat.startswith(('L', 'N', 'P', 'Z', 'S')):
                try:
                    ch.encode('cp1252')
                    cleaned.append(ch)
                except UnicodeEncodeError:
                    # Reemplazar con equivalente o saltar
                    if ch in '\u3002\u3001\uff01\uff1f':
                        cleaned.append('.' if ch == '\u3002' else ',')
                    # Otros caracteres raros: saltar
        return ''.join(cleaned).strip()

    # ==============================================================
    #  VISION API (ESPORADICA - solo para entender escena)
    # ==============================================================

    def _analyze_scene_vision(self, screenshot_b64: str) -> dict:
        """
        Analisis visual COMPLETO via Groq Vision.
        Se usa solo cada N dialogos o en pantallas sin texto.
        """
        system_prompt = """Analiza esta captura de Doki Doki Literature Club.
Responde SOLO con JSON valido:
{
    "screen_type": "dialogue|choice|menu|transition|poem|glitch|text_input|other",
    "scene_emotion": "happy|sad|scary|funny|romantic|tense|shocking|cute|neutral|dramatic|creepy|emotional|angry|horror|awkward",
    "choices": ["opcion 1", "opcion 2"] or null,
    "input_prompt": "que pide escribir el juego, o null",
    "click_x": 0.5,
    "click_y": 0.8,
    "visual_description": "descripcion breve de lo que se ve en pantalla (personajes, fondo, expresiones)"
}

REGLAS:
- click_x/click_y: coordenadas relativas (0.0-1.0) del punto donde hacer click para avanzar.
- Para menu: apuntar a "New Game". Para dialogo: centro de la caja de texto.
- Para choices: apuntar a la primera opcion.
- screen_type "glitch" = pantalla rota/corrupta de DDLC.
- visual_description: describe lo que ves (ej. "Sayori sonriendo en el salon de clases")."""

        try:
            resp = self._groq.chat.completions.create(
                model=self.VISION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Analiza esta captura de DDLC:"},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_b64}",
                        }}
                    ]},
                ],
                max_tokens=300,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                data = json.loads(m.group())
                self._consecutive_errors = 0
                print(f"[GameEngine] Vision: {data.get('screen_type')} | "
                      f"emo: {data.get('scene_emotion')} | "
                      f"{data.get('visual_description','')[:40]}")
                return data
            print(f"[GameEngine] Vision sin JSON: {raw[:60]}")
        except json.JSONDecodeError as e:
            print(f"[GameEngine] Vision JSON invalido: {e}")
        except Exception as e:
            self._consecutive_errors += 1
            print(f"[GameEngine] Vision error: {e}")
        return {"screen_type": "other"}

    # ==============================================================
    #  DECIDIR ELECCION
    # ==============================================================

    def _decide_choice(self, choices: list) -> tuple[int, str]:
        """Elige una opcion y genera la reaccion de Aiko. Retorna (indice, reaccion)."""
        if not choices:
            return 0, ""
        history = "\n".join(self._story_log[-10:])
        system = (
            "Eres Aiko, VTuber sarcastica y expresiva jugando Doki Doki Literature Club.\n"
            "Te dan opciones de dialogo. Debes:\n"
            "1. Elegir la opcion (responde el NUMERO)\n"
            "2. Decir POR QUE la eliges, como streamer en vivo (1-2 oraciones cortas)\n\n"
            "Formato EXACTO de respuesta:\n"
            "NUMERO: [tu numero]\n"
            "RAZON: [tu comentario en espanol]\n\n"
            f"Historia reciente del juego:\n{history}"
        )
        opts = "\n".join(f"{i+1}. {c}" for i, c in enumerate(choices))
        try:
            r = self._groq.chat.completions.create(
                model=self.FAST_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Opciones:\n{opts}"},
                ],
                max_tokens=80, temperature=0.8,
            )
            text = r.choices[0].message.content.strip()
            num = re.search(r'NUMERO:\s*(\d+)', text)
            reason = re.search(r'RAZON:\s*(.+)', text)
            idx = 0
            reaction = ""
            if num:
                idx = int(num.group(1)) - 1
                if not (0 <= idx < len(choices)):
                    idx = 0
            if reason:
                reaction = self._sanitize_for_tts(reason.group(1).strip())
            return idx, reaction
        except Exception as e:
            print(f"[GameEngine] Error decidiendo: {e}")
        return random.randint(0, len(choices) - 1), ""

    def _generate_streamer_narration(self, character: str, translation: str,
                                       emotion: str) -> str:
        """
        Genera una narracion estilo streamer para el dialogo actual.
        Se basa UNICAMENTE en el texto real del dialogo.
        """
        # Si el texto es muy corto, solo devolver el texto directo
        if len(translation) < 15:
            if character and character.lower() not in ("narrator", "mc", "player", ""):
                return f"{character} esta como, {translation}"
            return translation

        history = "\n".join(self._story_log[-6:])
        try:
            r = self._groq.chat.completions.create(
                model=self.FAST_MODEL,
                messages=[
                    {"role": "system", "content":
                     "Eres Aiko, VTuber latina narrando un juego en stream.\n"
                     "Te dan un dialogo del juego ya traducido. Tu trabajo:\n"
                     "- Parafrasearlo para tu audiencia en 1-2 oraciones CORTAS.\n"
                     "- DEBES basarte en el texto que te dan. NO inventes cosas.\n"
                     "- Si hay un personaje, menciona quien habla naturalmente.\n"
                     "- Habla casual, como gamer en vivo.\n"
                     "- NO uses comillas.\n"
                     "- NO digas cosas que no esten en el dialogo.\n\n"
                     f"Contexto previo:\n{history}"},
                    {"role": "user", "content":
                     f"[{character or 'Narrador'}]: {translation}\n"
                     "Narra:"},
                ],
                max_tokens=60, temperature=0.7,
            )
            result = r.choices[0].message.content.strip()
            result = self._sanitize_for_tts(result)
            if result and len(result) > 5:
                return result
        except Exception as e:
            print(f"[GameEngine] Error narrar: {e}")
        # Fallback simple y directo
        if character and character.lower() not in ("narrator", "mc", "player", ""):
            return f"entonces {character} dice que {translation}"
        return translation

    def _should_comment_now(self) -> bool:
        """Decide si Aiko deberia hacer un comentario adicional."""
        # Cada 2-3 dialogos, generar comentario
        if self._scene_count % random.randint(2, 3) == 0:
            return True
        # Forzar si lleva mucho callada
        if time.time() - self._last_opinion_time > 20:
            return True
        return False

    # ==============================================================
    #  MANEJAR INPUT DE TEXTO
    # ==============================================================

    def _handle_text_input(self, prompt_text: str):
        self._speak("oh, me pide un nombre... le pongo Aiko, obviamente",
                     emotion="happy")
        time.sleep(2)
        win = self._get_game_window()
        if win:
            try:
                win.activate()
            except Exception:
                pass
            self._click_at(0.5, 0.5)
            time.sleep(0.3)
        pyautogui.write("Aiko", interval=0.12)
        time.sleep(0.4)
        pyautogui.press("enter")
        print(f"[GameEngine] Tipeado 'Aiko'")
        time.sleep(1.5)

    # ==============================================================
    #  CONTROL DEL MOUSE
    # ==============================================================

    def _get_game_window(self):
        try:
            # Busqueda flexible: primero titulo exacto, luego parcial
            wins = gw.getWindowsWithTitle(self._game_window_title)
            if not wins:
                # Buscar parcialmente
                for w in gw.getAllWindows():
                    if "doki" in w.title.lower() or "ddlc" in w.title.lower():
                        wins = [w]
                        break
            for w in (wins or []):
                if w.width > 100 and w.height > 100 and w.left > -10000:
                    return w
        except Exception:
            pass
        return None

    def _capture_screen(self) -> Optional[str]:
        try:
            win = self._get_game_window()
            if win:
                # Verificar que la ventana del juego este visible
                try:
                    if win.isMinimized:
                        print("[GameEngine] Juego minimizado, esperando...")
                        return None
                except Exception:
                    pass

            with mss.mss() as sct:
                if win:
                    mon = {"top": win.top, "left": win.left,
                           "width": win.width, "height": win.height}
                else:
                    print("[GameEngine] Ventana del juego no encontrada")
                    monitors = sct.monitors
                    mon = monitors[1] if len(monitors) > 1 else monitors[0]
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                if img.width > 1024:
                    r = 1024 / img.width
                    img = img.resize((1024, int(img.height * r)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=65)
                return base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            print(f"[GameEngine] Error captura: {e}")
            return None

    def _click_at(self, x_pct: float, y_pct: float):
        """Click en posicion relativa DENTRO del area de contenido del juego."""
        try:
            screen_w, screen_h = pyautogui.size()
            win = self._get_game_window()
            if win:
                # Compensar barra de titulo (~32px en Windows)
                title_bar_h = 32
                content_top = win.top + title_bar_h
                content_h = win.height - title_bar_h
                # Bordes laterales (~8px)
                border_w = 8
                content_left = win.left + border_w
                content_w = win.width - (border_w * 2)

                x = content_left + int(content_w * x_pct)
                y = content_top + int(content_h * y_pct)
            else:
                x = int(screen_w * x_pct)
                y = int(screen_h * y_pct)
            x = max(5, min(screen_w - 5, x))
            y = max(5, min(screen_h - 5, y))
            dur = random.uniform(0.12, 0.30)
            pyautogui.moveTo(x, y, duration=dur, tween=pyautogui.easeInOutQuad)
            time.sleep(random.uniform(0.03, 0.08))
            pyautogui.click(x, y)
            print(f"[GameEngine] Click ({x}, {y})")
        except Exception as e:
            print(f"[GameEngine] Error click: {e}")

    def _click_choice_by_index(self, idx: int, total: int):
        if total == 2:
            y_pcts = [0.42, 0.55]
        elif total == 3:
            y_pcts = [0.35, 0.48, 0.61]
        else:
            sp = 0.13
            base = 0.5 - (total * sp / 2)
            y_pcts = [base + i * sp for i in range(total)]
        y = y_pcts[idx] if idx < len(y_pcts) else 0.5
        self._click_at(0.5, y)

    # ==============================================================
    #  HABLAR / EXPRESIONES
    # ==============================================================

    def _speak(self, text: str, emotion: str = "neutral",
               speed: float = None, is_narration: bool = False):
        if not text or not text.strip():
            return
        # Sanitizar para evitar crashes de TTS
        text = self._sanitize_for_tts(text)
        if not text or len(text) < 2:
            return
        elapsed = time.time() - self._last_speak_time
        if elapsed < self._min_speak_gap:
            time.sleep(self._min_speak_gap - elapsed)

        self._is_speaking = True
        if self._speak_callback:
            try:
                self._speak_callback(
                    text, emotion,
                    speed or (0.85 if is_narration else None)
                )
                self._last_speak_time = time.time()
            except Exception as e:
                print(f"[GameEngine] Error TTS: {e}")
        else:
            print(f"[GameEngine] (sin TTS): {text[:80]}")
        self._is_speaking = False

    def _trigger_expression(self, scene_emotion: str):
        if not self.vts:
            return
        reaction = self.SCENE_EMOTION_MAP.get(scene_emotion)
        if reaction:
            self.vts.trigger_react(reaction)

    def _log_story(self, character: str, text: str):
        if not text:
            return
        entry = f"{character}: {text}" if character else text
        self._story_log.append(entry)
        if len(self._story_log) > self._max_story_log:
            self._story_log = self._story_log[-self._max_story_log:]

    # ==============================================================
    #  CONFIGURACION
    # ==============================================================

    def set_speak_callback(self, cb: Callable):
        self._speak_callback = cb

    def set_chat_check_callback(self, cb: Callable):
        self._chat_check_callback = cb

    # ==============================================================
    #  GAME LOOP PRINCIPAL
    # ==============================================================

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._game_loop, daemon=True, name="GameEngine"
        )
        self._thread.start()
        print("[GameEngine] [GAME] Game loop iniciado")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[GameEngine] Game loop detenido")

    def _game_loop(self):
        print("[GameEngine] Esperando 3s para que el juego este listo...")
        time.sleep(3)

        self._speak(
            random.choice([
                "aver aver, Doki Doki Literature Club, esto va a estar bueno",
                "listo chat, hoy jugamos Doki Doki, preparen sus corazones",
                "bueno, hoy le entramos al Doki Doki, a ver si me hago llorar sola",
            ]),
            emotion="excited"
        )
        time.sleep(2)

        # Frases de Aiko con personalidad
        MENU_LINES = [
            "oka, menu principal, vamos a darle",
            "perfecto, le doy a nueva partida y arrancamos",
            "aqui estamos, dale dale nueva partida",
            "listo, iniciemos esta aventura",
        ]
        GLITCH_LINES = [
            "que rayos fue eso, la pantalla se murio",
            "eeeh, que paso ahi, eso no es normal",
            "ok eso fue raro, que acaba de pasar",
            "wey la pantalla se rompio, esto me da miedo",
        ]

        empty_count = 0
        needs_vision = True  # Primera vez siempre usa vision

        while self._running:
            try:
                # == NO CAPTURAR MIENTRAS HABLA ==
                if self._is_speaking:
                    time.sleep(0.5)
                    continue

                # == BACKOFF si hay errores de API ==
                if self._consecutive_errors > 2:
                    wait = min(2 ** self._consecutive_errors, self._max_backoff)
                    print(f"[GameEngine] Backoff: {wait}s (errores: {self._consecutive_errors})")
                    time.sleep(wait)

                # -- Capturar pantalla --
                shot = self._capture_screen()
                if not shot:
                    time.sleep(1)
                    continue

                # ==========================================================
                # PASO 1: OCR LOCAL (gratis) - leer texto del dialogo
                # ==========================================================
                ocr = self._read_dialogue_ocr(shot)
                ocr_text = ocr.get("text", "")
                ocr_char = ocr.get("character", "")
                has_text = ocr.get("has_text", False)

                if has_text:
                    print(f"[GameEngine] OCR: [{ocr_char}] {ocr_text[:60]}")

                # ==========================================================
                # PASO 2: VISION API (solo cuando es necesario)
                # ==========================================================
                scene = self._last_vision_result
                cx = scene.get("click_x")
                cy = scene.get("click_y")

                if needs_vision or (not has_text and empty_count < 2):
                    # Sin texto = probablemente menu/transition/choice
                    scene = self._analyze_scene_vision(shot)
                    self._last_vision_result = scene
                    self._dialogues_since_vision = 0
                    needs_vision = False
                    cx = scene.get("click_x")
                    cy = scene.get("click_y")

                    stype = scene.get("screen_type", "other")
                    self._last_emotion = scene.get("scene_emotion", "neutral")
                    print(f"[GameEngine] Vision coords: click_x={cx}, click_y={cy}, type={stype}")

                    # -- Text Input --
                    if stype == "text_input":
                        self._handle_text_input(scene.get("input_prompt", ""))
                        self._last_scene_text = ""
                        continue

                    # -- Glitch --
                    if stype == "glitch":
                        self._trigger_expression("horror")
                        self._speak(random.choice(GLITCH_LINES),
                                    emotion="surprised")
                        time.sleep(2)
                        self._click_at(cx or 0.5, cy or 0.75)
                        time.sleep(1)
                        continue

                    # -- Menu -- (coordenadas FIJAS para DDLC con retry)
                    if stype == "menu":
                        self._menu_attempts = getattr(self, '_menu_attempts', 0) + 1
                        if self._menu_attempts == 1:
                            self._speak(random.choice(MENU_LINES),
                                         emotion="happy")

                        # Intentar distintas posiciones si la primera falla
                        menu_positions = [
                            (0.09, 0.60),  # New Game - intento 1
                            (0.09, 0.63),  # Ligeramente mas abajo
                            (0.09, 0.57),  # Ligeramente mas arriba
                            (0.12, 0.60),  # Mas a la derecha
                        ]
                        pos_idx = min(self._menu_attempts - 1, len(menu_positions) - 1)
                        mx, my = menu_positions[pos_idx]
                        print(f"[GameEngine] Menu intento #{self._menu_attempts} -> ({mx}, {my})")
                        self._click_at(mx, my)

                        if self._menu_attempts >= 4:
                            # Hablar para que no este callada
                            self._speak("hmm no me deja entrar, a ver...",
                                        emotion="thinking")
                            self._menu_attempts = 0  # Resetear

                        empty_count += 1
                        time.sleep(2.5)
                        continue

                    # -- Choices --
                    if stype == "choice" and scene.get("choices"):
                        choices = scene["choices"]
                        opts_es = ", ".join(choices)
                        self._speak(f"a ver, tenemos opciones: {opts_es}",
                                    emotion="thinking", is_narration=True)
                        time.sleep(0.5)

                        idx, reaction = self._decide_choice(choices)
                        chosen = choices[idx] if idx < len(choices) else choices[0]

                        if reaction:
                            self._speak(reaction, emotion="excited")
                        else:
                            self._speak(f"le doy a esta: {chosen}", emotion="excited")

                        self._log_story("Aiko eligio", chosen)
                        time.sleep(0.5)
                        self._click_choice_by_index(idx, len(choices))
                        time.sleep(2)
                        needs_vision = True
                        continue

                    # -- Poem --
                    if stype == "poem":
                        self._speak("oh es el mini juego de palabras",
                                    emotion="thinking")
                        self._click_at(cx or 0.5, cy or 0.5)
                        time.sleep(1.5)
                        needs_vision = True
                        continue

                    # -- Transition --
                    if stype == "transition":
                        time.sleep(1.5)
                        needs_vision = True
                        continue

                # ==========================================================
                # PASO 3: PROCESAR TEXTO DETECTADO POR OCR
                # ==========================================================

                if not has_text or ocr_text == self._last_scene_text:
                    # Sin texto nuevo
                    empty_count += 1
                    if empty_count > 4:
                        # Click en centro de pantalla para avanzar
                        self._click_at(0.5, 0.85)
                        needs_vision = True
                    time.sleep(2)
                    continue

                # -- Texto nuevo detectado! --
                empty_count = 0
                self._last_scene_text = ocr_text
                self._scene_count += 1
                self._dialogues_since_vision += 1

                # Pedir vision cada N dialogos para actualizar emocion
                if self._dialogues_since_vision >= self._vision_interval:
                    needs_vision = True

                # Expresion facial
                self._trigger_expression(self._last_emotion)

                # -- Traducir (LLM texto, barato) --
                translation = self._translate(ocr_text)

                if translation and translation != self._last_narrated_text:
                    self._last_narrated_text = translation
                    character = ocr_char or "narrator"
                    self._log_story(character, translation)

                    # Generar narracion estilo streamer
                    narration = self._generate_streamer_narration(
                        character, translation, self._last_emotion
                    )

                    # BLOQUEA hasta que TTS termine
                    self._speak(narration, emotion=self._last_emotion,
                                is_narration=True)

                    # -- Comentario extra cada 2-3 dialogos --
                    if self._should_comment_now():
                        history = "\n".join(self._story_log[-6:])
                        try:
                            r = self._groq.chat.completions.create(
                                model=self.FAST_MODEL,
                                messages=[
                                    {"role": "system", "content":
                                     "Eres Aiko, VTuber jugando DDLC en stream.\n"
                                     "Haz un comentario CORTO (1 oracion) sobre lo que esta pasando.\n"
                                     "Puedes: opinar de un personaje, predecir que pasara, \n"
                                     "quejarte, emocionarte, asustarte, etc.\n"
                                     "NO repitas lo que dijiste antes. Se creativa.\n\n"
                                     f"Historia reciente:\n{history}"},
                                    {"role": "user", "content": "Comenta:"},
                                ],
                                max_tokens=50, temperature=0.9,
                            )
                            comment = self._sanitize_for_tts(
                                r.choices[0].message.content.strip())
                            if comment and len(comment) > 5:
                                time.sleep(0.3)
                                self._speak(comment, emotion=self._last_emotion)
                                self._last_opinion_time = time.time()
                        except Exception:
                            pass

                # -- Avanzar dialogo (SIEMPRE caja de texto, no coords de vision) --
                time.sleep(random.uniform(0.8, 1.5))
                self._click_at(0.5, 0.85)

                # -- Chat check --
                if self._scene_count % 4 == 0 and self._chat_check_callback:
                    try:
                        if self._chat_check_callback():
                            print("[GameEngine] [CHAT] Chat pendiente, pausa breve...")
                            time.sleep(3)
                    except Exception:
                        pass

                # Pausa entre escenas
                time.sleep(random.uniform(2.0, 3.5))

            except Exception as e:
                print(f"[GameEngine] Error en loop: {e}")
                self._consecutive_errors += 1
                time.sleep(3)

            # == SAFETY NET: si lleva mucho callada, decir algo ==
            finally:
                silence = time.time() - self._last_speak_time
                if silence > 20 and self._running:
                    self._speak(
                        random.choice([
                            "a ver, que esta pasando aqui",
                            "hmm dejame ver que hace el juego",
                            "ok ok, seguimos",
                            "bueno, vamos a ver que pasa",
                        ]),
                        emotion="thinking"
                    )
                    # Click para intentar avanzar
                    self._click_at(0.5, 0.85)

    # ==============================================================
    #  PROPIEDADES
    # ==============================================================

    @property
    def is_playing(self) -> bool:
        return self._running

    @property
    def scene_count(self) -> int:
        return self._scene_count
