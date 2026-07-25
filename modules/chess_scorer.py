"""
chess_scorer.py — Sistema de Puntos Persistente para el Módulo de Ajedrez.

Almacenamiento: SQLite en data/chess_scores.db
Thread-safe via threading.Lock()

Tabla de eventos y puntos:
  beat_aiko          +100  → Derrotó a Aiko
  lost_to_aiko        -20  → Perdió contra Aiko
  draw                +30  → Empató
  captured_queen      +50  → Capturó la Reina de Aiko
  captured_rook       +15  → Capturó una Torre de Aiko
  gave_check          +10  → Dio jaque a Aiko
  lost_fast           -40  → Perdió en < 15 movimientos
"""

import os
import sqlite3
import threading
import time
from typing import Optional

# ─────────────────────────────────────────────────────────────
#  TABLA DE PUNTOS POR EVENTO
# ─────────────────────────────────────────────────────────────
POINT_TABLE = {
    "win_vs_aiko":       100,   # ganar la partida completa
    "survive_20_moves":   30,   # llegar a jugada 20 sin rendirse
    "capture_queen":      50,   # capturar la reina de Aiko
    "give_check":         15,   # poner en jaque a Aiko (una vez por partida)
    "draw":               20,   # tablas
    "lose_fast":         -10,   # perder en menos de 10 jugadas
    "game_played":         5,   # solo por participar
    "brilliant_move":     25,   # reservado para uso manual desde el dashboard
}

DB_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "chess_scores.db"
)


class ChessScorer:
    """
    Sistema de puntos persistente para el modulo de ajedrez.

    Uso:
        scorer = ChessScorer()
        scorer.award_event("StreamerUser", "win_vs_aiko")
        top = scorer.get_leaderboard(10)
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_DEFAULT_PATH
        self._lock = threading.Lock()
        self._point_table = dict(POINT_TABLE)  # copia mutable
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        print(f"[ChessScorer] OK Base de datos en {self.db_path}")

    def load_point_table_from_yaml(self, personality_path: str):
        """Carga la tabla de puntos desde chess_personality.yaml si existe."""
        try:
            import yaml
            with open(personality_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            yaml_table = data.get("chess", {}).get("point_table", {})
            if yaml_table:
                self._point_table.update(yaml_table)
                print(f"[ChessScorer] Tabla de puntos cargada desde YAML ({len(yaml_table)} eventos)")
        except Exception as e:
            print(f"[ChessScorer] No se pudo cargar tabla desde YAML: {e}")

    # ── Setup ────────────────────────────────────────────────

    def _init_db(self):
        """Crea las tablas si no existen."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    username    TEXT PRIMARY KEY,
                    points      INTEGER DEFAULT 0,
                    wins        INTEGER DEFAULT 0,
                    losses      INTEGER DEFAULT 0,
                    draws       INTEGER DEFAULT 0,
                    games       INTEGER DEFAULT 0,
                    checks_given INTEGER DEFAULT 0,
                    queens_taken INTEGER DEFAULT 0,
                    last_played REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    username    TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    points_delta INTEGER NOT NULL,
                    timestamp   REAL NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ── API Pública ──────────────────────────────────────────

    def award_event(self, username: str, event_type: str) -> int:
        """
        Aplica puntos por un evento de juego.

        Args:
            username:   Nombre del espectador.
            event_type: Clave del POINT_TABLE.

        Returns:
            Puntos aplicados (puede ser negativo). 0 si evento desconocido.
        """
        if not username:
            return 0

        delta = self._point_table.get(event_type, 0)
        now = time.time()

        with self._lock, self._connect() as conn:
            # Upsert del jugador
            conn.execute("""
                INSERT INTO players (username, points, last_played)
                VALUES (?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    points = points + ?,
                    last_played = ?
            """, (username, delta, now, delta, now))

            # Actualizar contadores específicos por evento
            if event_type == "beat_aiko":
                conn.execute("UPDATE players SET wins = wins + 1, games = games + 1 WHERE username = ?", (username,))
            elif event_type == "lost_to_aiko":
                conn.execute("UPDATE players SET losses = losses + 1, games = games + 1 WHERE username = ?", (username,))
            elif event_type == "draw":
                conn.execute("UPDATE players SET draws = draws + 1, games = games + 1 WHERE username = ?", (username,))
            elif event_type == "gave_check":
                conn.execute("UPDATE players SET checks_given = checks_given + 1 WHERE username = ?", (username,))
            elif event_type == "captured_queen":
                conn.execute("UPDATE players SET queens_taken = queens_taken + 1 WHERE username = ?", (username,))
            elif event_type == "game_played":
                conn.execute("UPDATE players SET games = games + 1 WHERE username = ?", (username,))

            # Log del evento
            conn.execute("""
                INSERT INTO event_log (username, event_type, points_delta, timestamp)
                VALUES (?, ?, ?, ?)
            """, (username, event_type, delta, now))

        print(f"[ChessScorer] {username} | {event_type} → {'+' if delta >= 0 else ''}{delta} pts")
        return delta

    def get_player(self, username: str) -> Optional[dict]:
        """Obtiene el perfil completo de un jugador."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE username = ?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def get_leaderboard(self, top_n: int = 10) -> list[dict]:
        """
        Retorna el top N de jugadores ordenado por puntos.

        Returns:
            Lista de dicts: {rank, username, points, wins, losses, draws, queens_taken}
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT username, points, wins, losses, draws, queens_taken, games
                FROM players
                ORDER BY points DESC
                LIMIT ?
            """, (top_n,)).fetchall()

        return [
            {
                "rank":         i + 1,
                "username":     r["username"],
                "points":       r["points"],
                "wins":         r["wins"],
                "losses":       r["losses"],
                "draws":        r["draws"],
                "queens_taken": r["queens_taken"],
                "games":        r["games"],
            }
            for i, r in enumerate(rows)
        ]

    def get_stats_summary(self, username: str) -> str:
        """Devuelve un string legible para TTS."""
        p = self.get_player(username)
        if not p:
            return f"{username} aún no tiene partidas registradas."
        return (
            f"{p['username']}: {p['points']} puntos — "
            f"{p['wins']}G/{p['losses']}D/{p['draws']}E"
        )
