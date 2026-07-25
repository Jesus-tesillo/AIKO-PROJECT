"""
video_mode.py — Modo Video de Aiko (TikTok / Shorts).

Arranca el mismo stack que main.py (TTS, RVC, Live2D, LLM)
pero SIN conectar Twitch ni iniciar el loop de stream.

Uso:
    python video_mode.py
    python video_mode.py "Opina sobre los animes de temporada"
    python video_mode.py "Haz un video de lo que quieras" --mood chill --countdown 5
"""

import os
import sys
import time
import threading
import signal
import webbrowser
import argparse
import yaml

# Codificación UTF-8 para Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════
#  CARGA DE CONFIG
# ═══════════════════════════════════════════════════════════

def load_config(path="config.yaml") -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        print(f"[VideoMode] ✓ Config cargada desde {path}")
        return cfg
    except FileNotFoundError:
        print(f"[VideoMode] ✗ Config no encontrada: {path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[VideoMode] ✗ Error en config: {e}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════
#  INICIALIZACIÓN DEL STACK MÍNIMO
# ═══════════════════════════════════════════════════════════

def init_stack(config: dict):
    """
    Inicializa los módulos necesarios para el modo video
    usando core_stack compartido.
    LLM → TTS → RVC → Live2D
    No inicia: Twitch, TikTok chat, Dashboard, Browser, Prompter de stream.
    """
    print("\n" + "═" * 60)
    print("  ✦  AIKO — MODO VIDEO CORTO")
    print("═" * 60)

    from modules.core_stack import init_core_stack
    stack = init_core_stack(config, open_viewer=True)

    print("\n[VideoMode] ✓ Stack listo.\n")
    return stack["llm"], stack["etts"], stack["vts"]


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Aiko — Generador de Videos Cortos (TikTok / Shorts)"
    )
    parser.add_argument(
        "topic", nargs="?", default="",
        help="Tema del video (vacío = Aiko elige libremente)"
    )
    parser.add_argument(
        "--mood", "-m",
        choices=["hyped", "chill", "bored", "gremlin", "flustered", "focused", "neutral"],
        default=None,
        help="Humor de Aiko para este video"
    )
    parser.add_argument(
        "--countdown", "-c", type=int, default=None,
        help="Segundos de cuenta regresiva antes de hablar (para OBS)"
    )
    parser.add_argument(
        "--batch", "-b", action="store_true",
        help="Modo batch: no pide más videos, termina tras el primero"
    )
    args = parser.parse_args()

    # Señal de salida limpia
    def _sig_handler(sig, frame):
        print("\n[VideoMode] Saliendo...")
        sys.exit(0)
    signal.signal(signal.SIGINT, _sig_handler)

    config = load_config()

    # Valores por defecto desde config o args
    mood = args.mood or config.get("vtuber", {}).get("default_mood", "neutral")
    countdown = (
        args.countdown
        if args.countdown is not None
        else config.get("video_mode", {}).get("countdown_seconds", 3)
    )

    llm, etts, vts = init_stack(config)

    from modules.tiktok_video_mode import TikTokVideoMode, interactive_video_session

    if args.batch or args.topic:
        # Modo directo: un solo video y termina
        engine = TikTokVideoMode(
            llm=llm, etts=etts, vts=vts,
            countdown_seconds=countdown,
        )
        result = engine.run(topic=args.topic, mood=mood)
        if result["success"]:
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        # Modo GUI: lanza Video Studio
        from gui_video_studio import launch_studio
        launch_studio(llm=llm, etts=etts, vts=vts)

    # Cleanup
    try:
        etts.cleanup()
    except Exception:
        pass


if __name__ == "__main__":
    main()
