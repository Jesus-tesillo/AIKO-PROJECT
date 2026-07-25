"""
chess_bridge.py -- Motor Principal del Modulo de Ajedrez para Aiko VTuber.

Conecta:
  - Lichess API  (via berserk -- cliente oficial)
  - python-chess (validacion de tablero, representacion textual)
  - LLM (Groq)   (decisiones de jugada via system_prompt + reacciones LLM)
  - ChessScorer  (puntos persistentes SQLite)
  - speak_streaming / VTS (voz + expresiones Live2D)
  - Dashboard Flask (endpoints inyectados en el servidor existente)

Cambios arquitectonicos vs version anterior:
  - Reacciones a eventos generadas por LLM (reaction_prompt) en lugar de strings estaticos.
  - Comentarios idle generados por LLM (idle_prompt) en lugar de strings estaticos.
  - Mensajes de fin de partida generados por LLM (game_end_prompt).
  - System prompt con variables extendidas: situacion, capturas, historial rival, etc.
  - Calculo de situacion de juego (calc_game_situation).
  - Rastreo de piezas capturadas por ambos lados.
  - Timer de thinking del rival para idle comments graduales.
  - get_state_dict ampliado con todos los campos del overlay.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import yaml
from typing import Optional, Callable


# ─────────────────────────────────────────────────────────────
#  CONSTANTES
# ─────────────────────────────────────────────────────────────

PIECE_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0}

PIECE_NAMES_ES = {1: "peon", 2: "caballo", 3: "alfil", 4: "torre", 5: "reina", 6: "rey"}

# Mapeo evento -> clave reaction_events del YAML
CAPTURE_EVENT_MAP_AIKO_LOST = {
    1: "captured_pawn",
    2: "captured_knight",
    3: "captured_bishop",
    4: "captured_rook",
    5: "captured_queen",
}
CAPTURE_EVENT_MAP_OPPONENT_LOST = {
    1: "opponent_captured_pawn",
    2: "opponent_captured_knight",
    3: "opponent_captured_bishop",
    4: "opponent_captured_rook",
    5: "opponent_captured_queen",
}

LLM_MAX_RETRIES      = 3
OPPONENT_TIMEOUT_SEC = 120
POLLING_INTERVAL     = 0.5

# Intervalos para idle comments (segundos sin que el rival mueva)
IDLE_COMMENT_INTERVALS = [30, 60, 90, 120]

_THIS_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONALITY_PATH = os.path.join(_THIS_DIR, "chess_personality.yaml")


# ─────────────────────────────────────────────────────────────
#  CHESS BRIDGE
# ─────────────────────────────────────────────────────────────

class ChessBridge:
    """
    Puente central que une Lichess, python-chess, LLM, TTS y el Dashboard.

    Estados del ciclo de vida:
        idle     -> sin partida activa
        starting -> esperando confirmacion de Lichess
        playing  -> partida en curso
        finished -> partida terminada (transitorio)
    """

    def __init__(self,
                 llm,
                 etts,
                 vts,
                 config: dict,
                 scorer=None,
                 speak_fn: Optional[Callable] = None):
        self.llm    = llm
        self.etts   = etts
        self.vts    = vts
        self.config = config

        if scorer is None:
            from modules.chess_scorer import ChessScorer
            self.scorer = ChessScorer()
        else:
            self.scorer = scorer

        self._speak_fn = speak_fn

        # Estado de partida
        self._state = "idle"
        self._lock  = threading.Lock()

        # Datos de la partida actual
        self._opponent:         Optional[str]  = None
        self._aiko_color:       Optional[str]  = None
        self._game_id:          Optional[str]  = None
        self._board                            = None
        self._move_count                       = 0
        self._last_event                       = ""
        self._last_comment                     = ""

        # Tracking de piezas capturadas
        self._aiko_captured:     list = []   # piezas de Aiko capturadas por el rival
        self._opponent_captured: list = []   # piezas del rival capturadas por Aiko

        # Tracking de ultima jugada del rival
        self._last_opponent_move_san = "ninguna"
        self._last_piece_lost        = "ninguna"

        # Timer de thinking del rival (para idle comments)
        self._rival_thinking_start: Optional[float] = None
        self._idle_fired: set = set()   # intervalos ya disparados para no repetir

        # Votacion de la audiencia
        self._vote_pool:  dict = {}
        self._votes_open        = False

        # Flags de control
        self._stop_flag  = threading.Event()
        self._skip_flag  = threading.Event()
        self._game_thread: Optional[threading.Thread] = None

        # Lichess client (lazy init)
        self._lichess = None

        # Personalidad
        self._personality = self._load_personality()

        print("[ChessBridge] Modulo de ajedrez inicializado")

    # ─────────────────────────────────────────────────────────
    #  PERSONALIDAD
    # ─────────────────────────────────────────────────────────

    def _load_personality(self) -> dict:
        """Carga chess_personality.yaml. Permite hot-reload."""
        try:
            with open(PERSONALITY_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("chess", {})
        except FileNotFoundError:
            print(f"[ChessBridge] {PERSONALITY_PATH} no encontrado. Usando defaults.")
            return {}
        except Exception as e:
            print(f"[ChessBridge] Error cargando personalidad: {e}")
            return {}

    def _get_event_line(self, event_key: str, **kwargs) -> str:
        """Fallback: devuelve un mensaje estatico aleatorio del evento especificado."""
        lines = self._personality.get("event_lines", {}).get(event_key, [])
        if not lines:
            return ""
        template = random.choice(lines)
        try:
            return template.format(**kwargs)
        except KeyError:
            return template

    # ─────────────────────────────────────────────────────────
    #  GENERACION LLM PARA REACCIONES / IDLE / FIN DE PARTIDA
    # ─────────────────────────────────────────────────────────

    def _llm_quick_text(self, prompt: str, max_tokens: int = 80) -> str:
        """
        Llama al LLM y espera texto libre (sin JSON).
        Usado para reacciones, idle comments y mensajes de fin de partida.
        Retorna "" si el LLM falla.
        """
        try:
            raw = self.llm._call_groq(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.85,
            )
            if raw:
                # Limpiar posibles comillas extra o saltos de linea del LLM
                return raw.strip().strip('"').strip()
            return ""
        except Exception as e:
            print(f"[ChessBridge] LLM error en texto libre: {e}")
            return ""

    def _get_llm_reaction(self, event_type: str, piece_type: int) -> str:
        """
        Genera en tiempo real una reaccion al evento usando reaction_prompt del YAML.
        Si el LLM falla, devuelve el ejemplo del YAML como fallback.
        """
        piece_name = PIECE_NAMES_ES.get(piece_type, "pieza")
        template   = self._personality.get("reaction_prompt", "")
        tone       = (self._personality
                      .get("reaction_events", {})
                      .get(event_type, {})
                      .get("tone", "neutral"))

        if not template:
            # Fallback directo al ejemplo del YAML
            example = (self._personality
                       .get("reaction_events", {})
                       .get(event_type, {})
                       .get("example", ""))
            return example.format(
                opponent_name=self._opponent or "rival",
                piece_name=piece_name,
            ) if example else ""

        material_diff = self._calc_material_diff()
        mat_str = (f"+{material_diff}" if material_diff > 0
                   else f"{material_diff}" if material_diff < 0 else "0")

        # Construir el prompt de forma segura
        try:
            prompt_text = template.format(
                event_type=event_type,
                piece_name=piece_name,
                opponent_name=self._opponent or "rival",
                material_advantage=mat_str,
            )
            prompt_text += f"\nTono sugerido: {tone}"
        except KeyError:
            prompt_text = template

        result = self._llm_quick_text(prompt_text, max_tokens=60)

        if not result:
            # Fallback al ejemplo del YAML
            example = (self._personality
                       .get("reaction_events", {})
                       .get(event_type, {})
                       .get("example", ""))
            if example:
                try:
                    result = example.format(
                        opponent_name=self._opponent or "rival",
                        piece_name=piece_name,
                    )
                except KeyError:
                    result = example

        return result

    def _get_idle_comment(self, seconds_waiting: int) -> str:
        """
        Genera un comentario mientras el rival tarda en mover.
        Usa idle_prompt del YAML para dar contexto temporal al LLM.
        """
        template = self._personality.get("idle_prompt", "")
        if not template:
            return ""

        minutes_waiting = seconds_waiting // 60
        try:
            prompt_text = template.format(
                seconds_waiting=seconds_waiting,
                minutes_waiting=minutes_waiting,
                opponent_name=self._opponent or "rival",
            )
        except KeyError:
            prompt_text = template

        # Agregar ejemplos de tono como contexto adicional
        examples = self._personality.get("idle_examples", [])
        if examples:
            sample = random.choice(examples)
            try:
                sample = sample.format(
                    opponent_name=self._opponent or "rival",
                    minutes_waiting=minutes_waiting,
                )
            except KeyError:
                pass
            prompt_text += f"\nEjemplo de tono (no copiar literal): {sample}"

        return self._llm_quick_text(prompt_text, max_tokens=60)

    def _get_game_end_comment(self, result: str, points_awarded: int) -> str:
        """
        Genera el mensaje de cierre de partida via game_end_prompt del YAML.
        """
        template = self._personality.get("game_end_prompt", "")
        if not template:
            return ""

        try:
            prompt_text = template.format(
                result=result,
                opponent_name=self._opponent or "rival",
                total_moves=self._move_count,
                points_awarded=points_awarded,
            )
        except KeyError:
            prompt_text = template

        # Agregar ejemplo de tono
        examples_block = self._personality.get("game_end_examples", {})
        examples_list  = examples_block.get(result, [])
        if examples_list:
            sample = random.choice(examples_list)
            try:
                sample = sample.format(opponent_name=self._opponent or "rival")
            except KeyError:
                pass
            prompt_text += f"\nEjemplo de tono: {sample}"

        result_text = self._llm_quick_text(prompt_text, max_tokens=80)

        if not result_text and examples_list:
            # Fallback al ejemplo del YAML
            try:
                result_text = random.choice(examples_list).format(
                    opponent_name=self._opponent or "rival"
                )
            except KeyError:
                result_text = random.choice(examples_list)

        return result_text

    # ─────────────────────────────────────────────────────────
    #  LICHESS -- CONEXION
    # ─────────────────────────────────────────────────────────

    def _init_lichess(self) -> bool:
        """Inicializa el cliente berserk con el token del config."""
        try:
            import berserk
        except ImportError:
            print("[ChessBridge] berserk no instalado. Ejecuta: pip install berserk")
            return False

        chess_cfg = self.config.get("chess", {})
        token     = chess_cfg.get("lichess_token", "")

        if not token:
            print("[ChessBridge] lichess_token no configurado en config.yaml")
            return False

        try:
            session       = berserk.TokenSession(token)
            self._lichess = berserk.Client(session=session)
            account       = self._lichess.account.get()
            print(f"[ChessBridge] Lichess conectado como: {account['username']}")
            return True
        except Exception as e:
            print(f"[ChessBridge] Error conectando a Lichess: {e}")
            return False

    # ─────────────────────────────────────────────────────────
    #  API PUBLICA -- Control de partida
    # ─────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in ("starting", "playing")

    def start_match(self, opponent: str, aiko_color: str = "random") -> dict:
        with self._lock:
            if self.is_active:
                return {"ok": False, "error": "Ya hay una partida en curso"}
            if not opponent or not opponent.strip():
                return {"ok": False, "error": "Nombre de oponente invalido"}

            if aiko_color == "random":
                aiko_color = random.choice(["white", "black"])

            self._opponent   = opponent.strip()
            self._aiko_color = aiko_color
            self._stop_flag.clear()
            self._skip_flag.clear()
            self._state = "starting"

        if self._lichess is None:
            if not self._init_lichess():
                print("[ChessBridge] Modo LOCAL activado (sin Lichess)")

        self._game_thread = threading.Thread(
            target=self._game_loop, daemon=True, name="ChessGame"
        )
        self._game_thread.start()

        color_str = "blancas" if aiko_color == "white" else "negras"
        print(f"[ChessBridge] Partida iniciada vs {opponent} | Aiko: {color_str}")
        return {"ok": True, "game_id": self._game_id or "local"}

    def stop_match(self, reason: str = "admin_cancel") -> dict:
        if not self.is_active:
            return {"ok": False, "error": "No hay partida activa"}

        self._stop_flag.set()
        self._state = "idle"

        if self._lichess and self._game_id:
            try:
                self._lichess.board.resign_game(self._game_id)
            except Exception:
                pass

        print(f"[ChessBridge] Partida cancelada ({reason})")
        return {"ok": True}

    def skip_turn(self) -> dict:
        if self._state != "playing":
            return {"ok": False, "error": "No hay partida activa"}
        self._skip_flag.set()
        return {"ok": True}

    def get_state_dict(self) -> dict:
        """Devuelve el estado completo serializable (para /api/chess/state)."""
        board     = self._board
        top_votes = sorted(self._vote_pool.items(), key=lambda x: -x[1])[:3] if self._vote_pool else []

        material_diff = self._calc_material_diff() if board else 0
        mat_str = (f"+{material_diff}" if material_diff > 0
                   else f"{material_diff}" if material_diff < 0 else "0")

        # Tiempo que lleva pensando el rival
        rival_thinking = 0
        if self._rival_thinking_start is not None:
            rival_thinking = int(time.time() - self._rival_thinking_start)

        return {
            # Estado basico
            "game_status":              self._state,
            "opponent_name":            self._opponent or "",
            "aiko_color":               self._aiko_color or "",
            "game_id":                  self._game_id or "",
            "move_count":               self._move_count,
            "last_event":               self._last_event,
            "last_comment":             self._last_comment,

            # Tablero
            "fen":                      board.fen() if board else "",
            "board_ascii":              str(board) if board else "",
            "move_history_san":         self._get_move_history_san(),
            "turn":                     "aiko" if (board and self._is_aiko_turn(board)) else "opponent",

            # Material
            "material_advantage":       mat_str,
            "your_captured":            ", ".join(self._aiko_captured) or "ninguna",
            "opponent_captured":        ", ".join(self._opponent_captured) or "ninguna",

            # Rival
            "last_opponent_move":       self._last_opponent_move_san,
            "rival_thinking_seconds":   rival_thinking,

            # Votacion
            "top_votes":                [{"move": m, "votes": v} for m, v in top_votes],
            "votes_open":               self._votes_open,

            # Situacion de juego
            "game_situation":           self._calc_game_situation() if board else "normal",

            # Campos del overlay
            "opponent_time_remaining":  0,   # actualizado desde Lichess en modo real
            "ai_time_remaining":        0,
        }

    def vote_move(self, username: str, uci: str) -> dict:
        if not self._votes_open or self._state != "playing":
            return {"ok": False, "error": "No hay ventana de votacion activa"}
        if not self._board:
            return {"ok": False, "error": "Sin partida activa"}

        try:
            import chess
            move = chess.Move.from_uci(uci.lower().strip())
            if move not in self._board.legal_moves:
                return {"ok": False, "error": "Movimiento ilegal"}
        except Exception:
            return {"ok": False, "error": "Formato UCI invalido"}

        uci = uci.lower().strip()
        self._vote_pool[uci] = self._vote_pool.get(uci, 0) + 1
        total = self._vote_pool[uci]
        print(f"[ChessBridge] Voto: {username} -> {uci} (total: {total})")
        return {"ok": True, "move": uci, "votes": total}

    # ─────────────────────────────────────────────────────────
    #  LOOP PRINCIPAL DE PARTIDA
    # ─────────────────────────────────────────────────────────

    def _game_loop(self):
        """Hilo de la partida: maneja turno a turno hasta que termina."""
        import chess

        self._board                  = chess.Board()
        self._move_count             = 0
        self._last_event             = "inicio"
        self._last_comment           = ""
        self._aiko_captured          = []
        self._opponent_captured      = []
        self._last_opponent_move_san = "ninguna"
        self._last_piece_lost        = "ninguna"
        self._vote_pool              = {}
        self._votes_open             = False
        self._rival_thinking_start   = None
        self._idle_fired             = set()

        intro = self._get_event_line("game_start", opponent=self._opponent)
        if intro:
            self._speak(intro, emotion="hyped")

        self._state = "playing"

        if self._lichess and self._game_id:
            self._lichess_game_loop()
        else:
            self._local_game_loop()

    def _is_aiko_turn(self, board) -> bool:
        import chess
        if self._aiko_color == "white":
            return board.turn == chess.WHITE
        return board.turn == chess.BLACK

    # ─────────────────────────────────────────────────────────
    #  MODO LICHESS
    # ─────────────────────────────────────────────────────────

    def _lichess_game_loop(self):
        try:
            for event in self._lichess.board.stream_game_state(self._game_id):
                if self._stop_flag.is_set():
                    break

                event_type = event.get("type", "")

                if event_type == "gameFull":
                    self._process_game_full(event)
                elif event_type == "gameState":
                    self._process_game_state(event)

        except Exception as e:
            print(f"[ChessBridge] Error en Lichess loop: {e}")
        finally:
            self._state = "idle"

    def _process_game_full(self, event: dict):
        import chess
        initial_fen = event.get("initialFen", chess.STARTING_FEN)
        self._board = chess.Board(initial_fen)

        state     = event.get("state", {})
        moves_str = state.get("moves", "")
        if moves_str:
            for uci_move in moves_str.split():
                self._board.push_uci(uci_move)
                self._move_count += 1

        if self._is_aiko_turn(self._board):
            self._aiko_turn()

    def _process_game_state(self, event: dict):
        import chess

        status = event.get("status", "started")

        if status not in ("started", "created"):
            self._on_game_end(status, event.get("winner", ""))
            return

        moves_str = event.get("moves", "")
        if moves_str:
            all_moves  = moves_str.split()
            board_temp = chess.Board()
            prev_count = self._move_count

            for m in all_moves:
                board_temp.push_uci(m)

            # Extraer la ultima jugada del rival en SAN
            if len(all_moves) > prev_count:
                try:
                    tmp = chess.Board()
                    for m in all_moves[:-1]:
                        tmp.push_uci(m)
                    last_uci = all_moves[-1]
                    self._last_opponent_move_san = tmp.san(chess.Move.from_uci(last_uci))
                except Exception:
                    self._last_opponent_move_san = all_moves[-1] if all_moves else "ninguna"

            self._board      = board_temp
            self._move_count = len(all_moves)

        self._detect_and_react()
        self._rival_thinking_start = None  # empezo turno de Aiko

        if self._is_aiko_turn(self._board) and not self._stop_flag.is_set():
            self._aiko_turn()
        else:
            # Empieza el timer de thinking del rival
            self._rival_thinking_start = time.time()
            self._idle_fired = set()

    # ─────────────────────────────────────────────────────────
    #  MODO LOCAL (sin Lichess)
    # ─────────────────────────────────────────────────────────

    def _local_game_loop(self):
        import chess

        self._opponent_move_queue: list = []
        print("[ChessBridge] Modo LOCAL: el oponente envia jugadas via /api/chess/move")

        while not self._stop_flag.is_set() and not self._board.is_game_over():
            if self._is_aiko_turn(self._board):
                self._rival_thinking_start = None
                move_uci = self._aiko_turn()
                if not move_uci:
                    break
                # Iniciar timer para el rival
                self._rival_thinking_start = time.time()
                self._idle_fired = set()
                time.sleep(0.5)
            else:
                # Esperar jugada del rival con idle comments por tiempo
                deadline      = time.time() + OPPONENT_TIMEOUT_SEC
                move_received = False

                while time.time() < deadline and not self._stop_flag.is_set():
                    if self._skip_flag.is_set():
                        self._skip_flag.clear()
                        self._last_event = "timeout_rival"
                        msg = self._get_event_line("opponent_timeout", opponent=self._opponent)
                        if msg:
                            self._speak(msg)
                        break

                    # Idle comments graduales
                    if self._rival_thinking_start is not None:
                        elapsed = int(time.time() - self._rival_thinking_start)
                        for threshold in IDLE_COMMENT_INTERVALS:
                            if elapsed >= threshold and threshold not in self._idle_fired:
                                self._idle_fired.add(threshold)
                                idle_text = self._get_idle_comment(elapsed)
                                if idle_text:
                                    self._speak(idle_text, emotion="bored")
                                break

                    if self._opponent_move_queue:
                        uci = self._opponent_move_queue.pop(0)
                        try:
                            import chess as _chess
                            move = _chess.Move.from_uci(uci)
                            if move in self._board.legal_moves:
                                # Calcular SAN antes de empujar
                                try:
                                    self._last_opponent_move_san = self._board.san(move)
                                except Exception:
                                    self._last_opponent_move_san = uci

                                self._board.push(move)
                                self._move_count += 1
                                self._detect_and_react()
                                move_received = True
                                self._rival_thinking_start = None
                                break
                            else:
                                print(f"[ChessBridge] Jugada ilegal recibida: {uci}")
                        except Exception as e:
                            print(f"[ChessBridge] Error procesando jugada rival: {e}")

                    time.sleep(POLLING_INTERVAL)

                if not move_received and not self._stop_flag.is_set():
                    self._last_event = "timeout_rival"
                    msg = self._get_event_line("opponent_timeout", opponent=self._opponent)
                    if msg:
                        self._speak(msg)

        if self._board.is_game_over():
            result = self._board.result()
            self._on_game_end_local(result)

        self._state = "idle"
        print("[ChessBridge] Partida local terminada")

    def push_opponent_move(self, uci: str) -> dict:
        if self._state != "playing":
            return {"ok": False, "error": "No hay partida activa"}
        if self._is_aiko_turn(self._board):
            return {"ok": False, "error": "Es el turno de Aiko, no del oponente"}

        self._opponent_move_queue = getattr(self, "_opponent_move_queue", [])
        self._opponent_move_queue.append(uci)
        return {"ok": True}

    # ─────────────────────────────────────────────────────────
    #  TURNO DE AIKO -- LLM
    # ─────────────────────────────────────────────────────────

    def _aiko_turn(self) -> Optional[str]:
        import chess

        legal_moves = [m.uci() for m in self._board.legal_moves]
        if not legal_moves:
            return None

        # Ventana de votos
        self._vote_pool  = {}
        self._votes_open = True
        time.sleep(0.8)
        self._votes_open = False

        prompt_system = self._build_system_prompt(legal_moves)

        for attempt in range(LLM_MAX_RETRIES):
            try:
                raw = self.llm._call_groq(
                    [{"role": "system", "content": prompt_system},
                     {"role": "user",   "content": "Elige tu jugada ahora."}],
                    max_tokens=140,
                    temperature=0.75,
                )

                if not raw:
                    continue

                data = self._parse_llm_json(raw)
                if not data:
                    print(f"[ChessBridge] JSON invalido (intento {attempt+1}): {raw[:80]}")
                    continue

                move_uci = data.get("move", "").strip().lower()
                comment  = data.get("comment", "").strip()

                try:
                    move = chess.Move.from_uci(move_uci)
                except Exception:
                    print(f"[ChessBridge] UCI invalido: {move_uci}")
                    continue

                if move not in self._board.legal_moves:
                    print(f"[ChessBridge] Jugada ilegal: {move_uci} (intento {attempt+1})")
                    continue

                # Jugada valida
                self._last_comment = comment

                # Detectar captura ANTES de empujar
                captured_piece = self._board.piece_at(move.to_square)

                self._board.push(move)
                self._move_count += 1

                gave_check = self._board.is_check()

                if self._lichess and self._game_id:
                    try:
                        self._lichess.board.make_move(self._game_id, move_uci)
                    except Exception as e:
                        print(f"[ChessBridge] Error enviando jugada a Lichess: {e}")

                if comment:
                    self._speak(comment, emotion="focused")

                # Reaccion por captura de pieza rival
                if captured_piece:
                    self._on_aiko_captured(captured_piece.piece_type)

                # Reaccion por jaque
                if gave_check:
                    self._last_event = "jaque"
                    reaction = self._get_llm_reaction("gave_check", 0)
                    if reaction:
                        time.sleep(0.3)
                        self._speak(reaction, emotion="hyped")

                print(f"[ChessBridge] Aiko jugo: {move_uci} | {comment[:50]}")
                return move_uci

            except Exception as e:
                print(f"[ChessBridge] Error en turno de Aiko (intento {attempt+1}): {e}")
                time.sleep(1)

        # Fallback: jugada aleatoria
        fallback = random.choice(legal_moves)
        print(f"[ChessBridge] Fallback a jugada aleatoria: {fallback}")
        move = chess.Move.from_uci(fallback)
        self._board.push(move)
        self._move_count += 1
        self._speak("Estrategia experimental. Confien en el proceso.", emotion="gremlin")
        return fallback

    # ─────────────────────────────────────────────────────────
    #  CONSTRUCCION DEL PROMPT
    # ─────────────────────────────────────────────────────────

    def _build_system_prompt(self, legal_moves: list) -> str:
        import chess

        board     = self._board
        color_str = "blancas" if self._aiko_color == "white" else "negras"

        # Historial en SAN
        history_san = self._get_move_history_san()

        # Diferencia material
        material_diff = self._calc_material_diff()
        mat_str = (f"+{material_diff} (ventaja Aiko)" if material_diff > 0
                   else f"{material_diff} (desventaja Aiko)" if material_diff < 0
                   else "0 (equilibrio)")

        # Puntos del rival
        opponent_pts = 0
        if self._opponent:
            try:
                p = self.scorer.get_player(self._opponent)
                opponent_pts = p["points"] if p else 0
            except Exception:
                pass

        # Historial contra este rival
        rival_history = "Primera partida"
        if self._opponent:
            try:
                p = self.scorer.get_player(self._opponent)
                if p and p.get("games", 0) > 0:
                    rival_history = (
                        f"{p['wins']}V/{p['losses']}D/{p['draws']}E en "
                        f"{p['games']} partidas"
                    )
            except Exception:
                pass

        # Situacion del juego
        game_situation = self._calc_game_situation()

        # Tiempo que lleva pensando el rival
        rival_thinking = 0
        if self._rival_thinking_start:
            rival_thinking = int(time.time() - self._rival_thinking_start)

        template = self._personality.get("system_prompt", "")
        if not template:
            template = (
                "Eres Aiko, jugando ajedrez. Movimientos legales: {legal_moves}. "
                "Responde SOLO JSON: {{\"move\": \"UCI\", \"comment\": \"texto\"}}"
            )

        try:
            prompt = template.format(
                opponent_name             = self._opponent or "rival",
                opponent_points           = opponent_pts,
                rival_history             = rival_history,
                turn_number               = self._move_count + 1,
                your_color                = color_str,
                last_opponent_move        = self._last_opponent_move_san,
                opponent_just_captured    = self._last_piece_lost,
                board_ascii               = str(board),
                fen                       = board.fen(),
                legal_moves               = ", ".join(legal_moves[:25]),
                move_history_san          = history_san,
                your_captured             = ", ".join(self._aiko_captured) or "ninguna",
                opponent_captured         = ", ".join(self._opponent_captured) or "ninguna",
                material_advantage        = mat_str,
                game_situation            = game_situation,
                opponent_time_remaining   = 0,
                rival_thinking_seconds    = rival_thinking,
            )
        except KeyError as e:
            print(f"[ChessBridge] Variable de template no encontrada: {e}")
            prompt = template

        return prompt

    def _get_move_history_san(self) -> str:
        """Reconstruye las ultimas 10 jugadas del historial en notacion SAN."""
        if not self._board:
            return "Inicio de partida"
        import chess

        history_moves = []
        temp_board    = chess.Board()
        try:
            for m in self._board.move_stack:
                try:
                    history_moves.append(temp_board.san(m))
                    temp_board.push(m)
                except Exception:
                    break
        except Exception:
            pass

        return " ".join(history_moves[-10:]) if history_moves else "Inicio de partida"

    def _calc_material_diff(self) -> int:
        """Diferencia material (positivo = Aiko tiene mas)."""
        if not self._board:
            return 0
        import chess

        aiko_color_chess = chess.WHITE if self._aiko_color == "white" else chess.BLACK
        opponent_color   = chess.BLACK if self._aiko_color == "white" else chess.WHITE

        aiko_val     = sum(PIECE_VALUES.get(p.piece_type, 0)
                           for p in self._board.piece_map().values()
                           if p.color == aiko_color_chess)
        opponent_val = sum(PIECE_VALUES.get(p.piece_type, 0)
                           for p in self._board.piece_map().values()
                           if p.color == opponent_color)

        return aiko_val - opponent_val

    def _calc_game_situation(self) -> str:
        """
        Clasifica la situacion actual del tablero en una de las categorias
        definidas en el system_prompt para que el LLM ajuste su tono.
        """
        if not self._board:
            return "normal"

        board = self._board

        # Jaque sobre Aiko
        if board.is_check() and self._is_aiko_turn(board):
            return "check_on_you"

        # Jaque al rival
        if board.is_check() and not self._is_aiko_turn(board):
            return "check_on_rival"

        mat = self._calc_material_diff()

        # Ventaja/desventaja clara (mas de 5 puntos de material)
        if mat >= 5:
            return "you_winning_clearly"
        if mat <= -5:
            return "rival_winning_clearly"

        # Endgame: menos de 10 piezas en total (sin reyes)
        piece_count = len(self._board.piece_map()) - 2
        if piece_count <= 8:
            # Nearly mate: si hay pocos movimientos legales para el rey
            import chess
            king_sq = board.king(chess.WHITE if self._aiko_color == "white" else chess.BLACK)
            if king_sq is not None:
                legal_count = sum(1 for m in board.legal_moves
                                  if m.from_square == king_sq)
                if legal_count <= 2:
                    return "nearly_mate"
            return "endgame"

        return "normal"

    @staticmethod
    def _parse_llm_json(raw: str) -> Optional[dict]:
        """Extrae el JSON del output del LLM, tolerante a texto extra."""
        match = re.search(r'\{[^{}]*"move"[^{}]*"comment"[^{}]*\}', raw, re.DOTALL)
        if not match:
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None

    # ─────────────────────────────────────────────────────────
    #  DETECCION Y REACCION A EVENTOS
    # ─────────────────────────────────────────────────────────

    def _detect_and_react(self):
        """
        Detecta eventos del tablero tras la jugada del rival y reacciona.
        Actualiza self._last_event para el siguiente prompt del LLM.
        """
        if not self._board:
            return

        import chess

        board = self._board

        # Jaque al rey de Aiko
        if board.is_check() and self._is_aiko_turn(board):
            self._last_event = "el rival me pone en jaque"
            reaction = self._get_llm_reaction("received_check", 0)
            if reaction:
                self._speak(reaction, emotion="scared")
            return

        self._last_event = "turno normal"

    def _on_aiko_captured(self, piece_type: int):
        """Aiko capturo una pieza del rival. Reaccion LLM + registro."""
        piece_name = PIECE_NAMES_ES.get(piece_type, "pieza")
        self._opponent_captured.append(piece_name)

        event_key = CAPTURE_EVENT_MAP_OPPONENT_LOST.get(piece_type)
        if event_key:
            reaction = self._get_llm_reaction(event_key, piece_type)
            if reaction:
                self._speak(reaction, emotion="hyped")

        # Punto en el scorer si se capturo la reina
        if piece_type == 5 and self._opponent:  # QUEEN
            self.scorer.award_event(self._opponent, "capture_queen")

    def _on_opponent_captured_aiko_piece(self, piece_type: int):
        """El rival capturo una pieza de Aiko. Scorer + reaccion LLM."""
        import chess as _chess

        piece_name = PIECE_NAMES_ES.get(piece_type, "pieza")
        self._aiko_captured.append(piece_name)
        self._last_piece_lost = piece_name

        # Puntos al rival
        scorer_map = {
            _chess.QUEEN:  "capture_queen",
        }
        event_scorer = scorer_map.get(piece_type)
        if event_scorer and self._opponent:
            self.scorer.award_event(self._opponent, event_scorer)

        event_key = CAPTURE_EVENT_MAP_AIKO_LOST.get(piece_type)
        if event_key:
            reaction = self._get_llm_reaction(event_key, piece_type)
            if reaction:
                self._speak(reaction, emotion="angry")

    # ─────────────────────────────────────────────────────────
    #  FIN DE PARTIDA
    # ─────────────────────────────────────────────────────────

    def _on_game_end(self, status: str, winner: str):
        """Procesa el fin de partida (Lichess mode)."""
        try:
            aiko_username = self._lichess.account.get()["username"] if self._lichess else "aiko"
        except Exception:
            aiko_username = "aiko"

        if status in ("mate", "resign", "timeout"):
            if winner == aiko_username:
                self._handle_aiko_wins()
            else:
                self._handle_aiko_loses()
        elif status == "draw":
            self._handle_draw()
        else:
            print(f"[ChessBridge] Partida terminada: {status}")

        self._state = "idle"

    def _on_game_end_local(self, result: str):
        """Procesa el fin de partida (modo local)."""
        if result == "1-0":
            if self._aiko_color == "white":
                self._handle_aiko_wins()
            else:
                self._handle_aiko_loses()
        elif result == "0-1":
            if self._aiko_color == "black":
                self._handle_aiko_wins()
            else:
                self._handle_aiko_loses()
        else:
            self._handle_draw()

    def _handle_aiko_wins(self):
        opponent      = self._opponent or ""
        points_given  = 0

        if opponent:
            self.scorer.award_event(opponent, "game_played")
            # Puntos por sobrevivir mas de 20 movimientos
            if self._move_count >= 40:  # 40 half-moves = 20 movimientos completos
                self.scorer.award_event(opponent, "survive_20_moves")
                points_given += 30

        line = self._get_game_end_comment("aiko_wins", points_given)
        if line:
            self._speak(line, emotion="hyped")

        print(f"[ChessBridge] Aiko gano vs {opponent}")

    def _handle_aiko_loses(self):
        opponent     = self._opponent or ""
        points_given = 100

        if opponent:
            pts = self.scorer.award_event(opponent, "win_vs_aiko")
            self.scorer.award_event(opponent, "game_played")
            points_given = pts

            # Penalizacion si el rival perdio rapido (lo que aqui significa
            # que Aiko gano rapidamente... esto es derrota del rival en <10 jugadas)
            if self._move_count < 20:  # 20 half-moves = 10 movimientos completos
                self.scorer.award_event(opponent, "lose_fast")

        line = self._get_game_end_comment("rival_wins", points_given)
        if line:
            self._speak(line, emotion="sad")

        print(f"[ChessBridge] Aiko perdio vs {opponent}")

    def _handle_draw(self):
        opponent     = self._opponent or ""
        points_given = 20

        if opponent:
            self.scorer.award_event(opponent, "draw")
            self.scorer.award_event(opponent, "game_played")

        line = self._get_game_end_comment("draw", points_given)
        if line:
            self._speak(line, emotion="bored")

        print(f"[ChessBridge] Empate vs {opponent}")

    # ─────────────────────────────────────────────────────────
    #  TTS HELPER
    # ─────────────────────────────────────────────────────────

    def _speak(self, text: str, emotion: str = "neutral"):
        """Envia texto a TTS de forma segura (no bloquea el game loop)."""
        if not text:
            return
        try:
            if self._speak_fn is None:
                try:
                    from main import speak_streaming
                    self._speak_fn = speak_streaming
                except ImportError:
                    pass

            if self._speak_fn:
                self._speak_fn(text, self.etts, self.vts, emotion, emotion)
            else:
                print(f"[ChessBridge] (TTS no disponible) {text}")
        except Exception as e:
            print(f"[ChessBridge] Error TTS: {e}")


# ─────────────────────────────────────────────────────────────
#  ENDPOINTS FLASK -- se inyectan en el Dashboard existente
# ─────────────────────────────────────────────────────────────

def register_chess_endpoints(app, bridge: ChessBridge):
    """
    Registra los endpoints de ajedrez en la app Flask del Dashboard.

    Endpoints:
        GET  /api/chess/state         estado completo del tablero
        POST /api/chess/start         {\"opponent\": str, \"color\": str}
        POST /api/chess/stop          cancela la partida
        POST /api/chess/skip_turn     salta turno del oponente
        GET  /api/chess/leaderboard   top 10 jugadores
        POST /api/chess/move          {\"move\": \"e7e5\"} (testing/local)
        GET  /api/chess/last_comment  ultimo comentario generado
        POST /api/chess/vote          {\"username\": str, \"move\": str}
        GET  /api/chess/audience      estado de la votacion activa
    """
    from flask import request, jsonify

    @app.route("/api/chess/state", methods=["GET"])
    def chess_state():
        return jsonify(bridge.get_state_dict())

    @app.route("/api/chess/start", methods=["POST"])
    def chess_start():
        data     = request.get_json(force=True, silent=True) or {}
        opponent = data.get("opponent", "").strip()
        color    = data.get("color", "random").lower()
        if color not in ("white", "black", "random"):
            color = "random"
        result = bridge.start_match(opponent, color)
        return jsonify(result), 200 if result["ok"] else 400

    @app.route("/api/chess/stop", methods=["POST"])
    def chess_stop():
        return jsonify(bridge.stop_match(reason="admin_cancel"))

    @app.route("/api/chess/skip_turn", methods=["POST"])
    def chess_skip():
        return jsonify(bridge.skip_turn())

    @app.route("/api/chess/leaderboard", methods=["GET"])
    def chess_leaderboard():
        top_n = int(request.args.get("n", 10))
        return jsonify(bridge.scorer.get_leaderboard(top_n))

    @app.route("/api/chess/move", methods=["POST"])
    def chess_move():
        data = request.get_json(force=True, silent=True) or {}
        uci  = data.get("move", "").strip().lower()
        if not uci:
            return jsonify({"ok": False, "error": "Campo 'move' requerido"}), 400
        return jsonify(bridge.push_opponent_move(uci))

    @app.route("/api/chess/last_comment", methods=["GET"])
    def chess_last_comment():
        return jsonify({"comment": bridge._last_comment})

    @app.route("/api/chess/vote", methods=["POST"])
    def chess_vote():
        data     = request.get_json(force=True, silent=True) or {}
        username = data.get("username", "anonimo").strip()
        uci      = data.get("move", "").strip()
        if not uci:
            return jsonify({"ok": False, "error": "Campo 'move' requerido"}), 400
        return jsonify(bridge.vote_move(username, uci))

    @app.route("/api/chess/audience", methods=["GET"])
    def chess_audience():
        votes = sorted(bridge._vote_pool.items(), key=lambda x: -x[1]) if bridge._vote_pool else []
        return jsonify({
            "votes_open": bridge._votes_open,
            "vote_pool":  [{"move": m, "votes": v} for m, v in votes],
        })

    print("[ChessBridge] OK: Endpoints /api/chess/* registrados en Dashboard")
