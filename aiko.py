"""
aiko.py — Punto de Entrada Único de Aiko VTuber.

Panel central con menú lateral para Stream, Video, Social y Tester.
Unifica todos los comandos en uno solo.

Uso:
    python aiko.py              → GUI completo (Stream + Video + Social + Tester)
    python aiko.py --stream     → Solo stream CLI (= python main.py)
    python aiko.py --video "t"  → Solo video batch CLI
"""

import customtkinter as ctk
import tkinter as tk
import threading
import queue
import time
import os
import sys
import argparse
import io

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Colores ──
BG        = "#0D0B1A"
BG2       = "#13101F"
BG3       = "#1A1628"
VIOLET    = "#A855F7"
PINK      = "#EC4899"
VIOLET_DK = "#7C3AED"
PINK_DK   = "#BE185D"
TEXT      = "#F0EAFF"
TEXT_DIM  = "#7A6E94"
GREEN     = "#22D3A5"
RED       = "#F43F5E"
ORANGE    = "#FB923C"
CYAN      = "#22D3EE"

# Páginas del menú lateral
PAGES = [
    ("stream",  "🔴  Stream",      "Iniciar transmisión en vivo"),
    ("video",   "🎬  Video",       "Crear videos cortos"),
    ("social",  "📱  Social",      "Posts y respuestas"),
    ("tester",  "🎭  Tester",      "Probar expresiones Live2D"),
]


# ─────────────────────────────────────────────────────────
#  STDOUT INTERCEPTOR — redirige prints al panel de consola
# ─────────────────────────────────────────────────────────

class ConsoleRedirector(io.TextIOBase):
    """Redirige stdout a un callback + terminal real."""
    def __init__(self, callback, original_stdout):
        super().__init__()
        self._callback = callback
        self._original = original_stdout

    def write(self, text):
        if text and text.strip():
            try:
                self._callback(text)
            except Exception:
                pass
        if self._original:
            try:
                self._original.write(text)
                self._original.flush()
            except Exception:
                pass
        return len(text) if text else 0

    def flush(self):
        if self._original:
            try:
                self._original.flush()
            except Exception:
                pass


class AikoApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("✦ Aiko — Control Panel")
        self.geometry("1340x800")
        self.minsize(1100, 700)
        self.configure(fg_color=BG)

        self._ui_queue = queue.Queue()
        self._stack = None  # se llena al cargar
        self._stream_thread = None
        self._stream_running = False
        self._current_page = None
        self._pages = {}

        self._build_layout()
        self.after(100, self._process_queue)
        self.after(200, self._init_stack_bg)

    # ═══════════════════════════════════════════════════════
    #  LAYOUT
    # ═══════════════════════════════════════════════════════

    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Sidebar ──
        sidebar = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0, width=220)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(10, weight=1)

        # Logo
        ctk.CTkLabel(sidebar, text="✦ AIKO",
                     font=ctk.CTkFont("Segoe UI", 22, "bold"),
                     text_color=VIOLET).grid(row=0, column=0, padx=20, pady=(24, 4), sticky="w")
        ctk.CTkLabel(sidebar, text="Control Panel",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=TEXT_DIM).grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        # Status
        self._status_lbl = ctk.CTkLabel(sidebar, text="⏳ Cargando...",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=ORANGE)
        self._status_lbl.grid(row=2, column=0, padx=20, pady=(0, 16), sticky="w")

        # Botones de navegación
        self._nav_btns = {}
        for i, (key, label, tooltip) in enumerate(PAGES):
            btn = ctk.CTkButton(
                sidebar, text=label, height=44, corner_radius=10,
                fg_color="transparent", hover_color=BG3,
                text_color=TEXT_DIM, anchor="w",
                font=ctk.CTkFont("Segoe UI", 13, "bold"),
                command=lambda k=key: self._show_page(k))
            btn.grid(row=3 + i, column=0, sticky="ew", padx=10, pady=3)
            self._nav_btns[key] = btn

        # Espacio flexible
        ctk.CTkFrame(sidebar, fg_color="transparent").grid(row=10, column=0, sticky="nsew")

        # Connection indicators (bottom)
        self._indicators = ctk.CTkFrame(sidebar, fg_color=BG3, corner_radius=10)
        self._indicators.grid(row=11, column=0, sticky="ew", padx=10, pady=(4, 14))
        self._ind_labels = {}
        for i, (name, icon) in enumerate([
            ("LLM", "🧠"), ("TTS", "🔊"), ("Live2D", "🎭"),
            ("Twitch", "📺"), ("TikTok", "🎵"),
        ]):
            f = ctk.CTkFrame(self._indicators, fg_color="transparent")
            f.grid(row=i, column=0, sticky="ew", padx=8, pady=2)
            ctk.CTkLabel(f, text=f"{icon} {name}",
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=TEXT_DIM).pack(side="left")
            dot = ctk.CTkLabel(f, text="●",
                               font=ctk.CTkFont("Segoe UI", 10),
                               text_color=RED)
            dot.pack(side="right", padx=4)
            self._ind_labels[name] = dot

        # ── Content area ──
        self._content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # Loading page
        self._loading_frame = ctk.CTkFrame(self._content, fg_color=BG)
        self._loading_frame.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self._loading_frame, text="⏳ Iniciando módulos...",
                     font=ctk.CTkFont("Segoe UI", 18, "bold"),
                     text_color=VIOLET).place(relx=0.5, rely=0.45, anchor="center")
        self._loading_sub = ctk.CTkLabel(self._loading_frame, text="Cargando LLM, TTS, Live2D...",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=TEXT_DIM)
        self._loading_sub.place(relx=0.5, rely=0.52, anchor="center")

    # ═══════════════════════════════════════════════════════
    #  INIT STACK EN BACKGROUND
    # ═══════════════════════════════════════════════════════

    def _init_stack_bg(self):
        def _worker():
            try:
                from modules.core_stack import load_config, init_core_stack
                config = load_config()
                def _on_status(msg):
                    self._ui_queue.put(("status", msg))
                stack = init_core_stack(
                    config, open_viewer=True,
                    on_status=_on_status, full=True,
                )
                self._ui_queue.put(("stack_ready", stack))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._ui_queue.put(("stack_error", str(e)))
        threading.Thread(target=_worker, daemon=True, name="StackInit").start()

    def _on_stack_ready(self, stack):
        self._stack = stack
        self._status_lbl.configure(text="✓ Listo", text_color=GREEN)

        llm = stack["llm"]
        self._ind_labels["LLM"].configure(
            text_color=GREEN if llm.connected else RED)
        self._ind_labels["TTS"].configure(text_color=GREEN)
        self._ind_labels["Live2D"].configure(text_color=GREEN)

        # Chat indicators
        chat_readers = stack.get("chat_readers", [])
        for r in chat_readers:
            if hasattr(r, 'chat_is_active'):
                self._ind_labels["TikTok"].configure(text_color=GREEN)
            elif hasattr(r, 'connected'):
                self._ind_labels["Twitch"].configure(text_color=GREEN)

        # Build pages
        self._build_pages()
        self._loading_frame.destroy()
        self._show_page("stream")  # default: la página más usada

    def _build_pages(self):
        s = self._stack
        # ── Stream Panel ──
        self._pages["stream"] = self._build_stream_page()
        # ── Video Studio ──
        self._pages["video"] = self._build_video_page()
        # ── Social Studio ──
        self._pages["social"] = self._build_social_page()
        # ── Tester ──
        from modules.gui_tester import Live2DTesterPanel
        tester = Live2DTesterPanel(self._content, vts=s["vts"])
        self._pages["tester"] = tester

    # ═══════════════════════════════════════════════════════
    #  PAGE NAVIGATION
    # ═══════════════════════════════════════════════════════

    def _show_page(self, key):
        if not self._stack and key != "tester":
            return
        if self._current_page == key:
            return
        # Hide all
        for k, frame in self._pages.items():
            frame.grid_forget()
        # Show selected
        if key in self._pages:
            self._pages[key].grid(row=0, column=0, sticky="nsew",
                                   in_=self._content)
        # Update nav buttons
        for k, btn in self._nav_btns.items():
            if k == key:
                btn.configure(fg_color=VIOLET_DK, text_color=TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_DIM)
        self._current_page = key

    # ═══════════════════════════════════════════════════════
    #  STREAM PAGE
    # ═══════════════════════════════════════════════════════

    def _build_stream_page(self):
        frame = ctk.CTkFrame(self._content, fg_color=BG)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=12)
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))

        ctk.CTkLabel(hdr, text="🔴 STREAM EN VIVO",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=VIOLET).pack(side="left", padx=18, pady=12)

        self._stream_btn = ctk.CTkButton(
            hdr, text="▶ Iniciar Stream", height=40, width=180,
            fg_color=GREEN, hover_color="#16a085",
            text_color="white",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            command=self._toggle_stream)
        self._stream_btn.pack(side="right", padx=18, pady=12)

        # Gaming Mode Toggle
        self._gaming_mode_var = ctk.BooleanVar(value=False)
        self._gaming_switch = ctk.CTkSwitch(
            hdr, text="🎮 Modo Doki Doki (Gaming)", variable=self._gaming_mode_var,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=PINK, progress_color=PINK_DK, button_color=VIOLET_DK)
        self._gaming_switch.pack(side="right", padx=20)

        # Status bar
        self._stream_status = ctk.CTkLabel(
            frame, text="Stream detenido. Presiona 'Iniciar' para conectar.",
            font=ctk.CTkFont("Segoe UI", 12), text_color=TEXT_DIM)
        self._stream_status.grid(row=1, column=0, sticky="w", padx=20, pady=4)

        # Console output
        self._stream_console = ctk.CTkTextbox(
            frame, fg_color=BG3, text_color=TEXT_DIM, corner_radius=10,
            font=ctk.CTkFont("Consolas", 11), state="disabled")
        self._stream_console.grid(row=2, column=0, sticky="nsew",
                                   padx=14, pady=(4, 14))
        return frame

    def _toggle_stream(self):
        if self._stream_running:
            self._stop_stream()
        else:
            self._start_stream()

    def _start_stream(self):
        self._stream_running = True
        self._stream_btn.configure(text="⏹ Detener Stream",
                                    fg_color=RED, hover_color="#c0392b")
        self._stream_status.configure(
            text="Conectando a Twitch y módulos de stream...",
            text_color=ORANGE)
        self._stream_log("Iniciando stream...\n")

        def _worker():
            try:
                import importlib
                import main as main_mod
                importlib.reload(main_mod)
                s = self._stack
                self._stream_log("Stream iniciado. Usa el botón 'Detener' para parar.\n")
                self._ui_queue.put(("stream_status", "🟢 Stream activo"))
                is_gaming = self._gaming_mode_var.get()

                # Redirigir stdout al panel de consola
                original_stdout = sys.stdout
                sys.stdout = ConsoleRedirector(
                    lambda text: self._ui_queue.put(("stream_log", text)),
                    original_stdout,
                )
                try:
                    main_mod.main(shared_stack=s, gaming_mode=is_gaming)
                finally:
                    sys.stdout = original_stdout
            except Exception as e:
                self._stream_log(f"Error: {e}\n")
            finally:
                self._ui_queue.put(("stream_stopped", None))

        self._stream_thread = threading.Thread(
            target=_worker, daemon=True, name="StreamThread")
        self._stream_thread.start()

    def _stop_stream(self):
        self._stream_running = False
        import main as main_mod
        main_mod.running = False
        self._stream_btn.configure(text="▶ Iniciar Stream",
                                    fg_color=GREEN, hover_color="#16a085")
        self._stream_status.configure(text="Stream detenido.",
                                       text_color=TEXT_DIM)

    def _stream_log(self, msg):
        def _do():
            self._stream_console.configure(state="normal")
            self._stream_console.insert("end", msg if msg.endswith("\n") else msg + "\n")
            # Limitar a ~500 líneas: usar índice de texto en lugar de leer todo el contenido
            line_count = int(self._stream_console.index("end-1c").split(".")[0])
            if line_count > 500:
                excess = line_count - 500
                self._stream_console.delete("1.0", f"{excess + 1}.0")
            self._stream_console.see("end")
            self._stream_console.configure(state="disabled")
        # Thread-safe: usar after()
        try:
            self.after(0, _do)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    #  VIDEO PAGE
    # ═══════════════════════════════════════════════════════

    def _build_video_page(self):
        s = self._stack
        try:
            from gui_video_studio import VideoStudioApp
            # Embed the studio app as a frame
            frame = VideoStudioApp(master=self._content, llm=s["llm"], etts=s["etts"], vts=s["vts"])
            return frame
        except Exception as e:
            print(f"[Aiko] Error cargando Video Studio: {e}")
            err_frame = ctk.CTkFrame(self._content, fg_color=BG)
            ctk.CTkLabel(err_frame, text=f"Error: {e}").pack()
            return err_frame

    # ═══════════════════════════════════════════════════════
    #  SOCIAL PAGE
    # ═══════════════════════════════════════════════════════

    def _build_social_page(self):
        s = self._stack
        frame = ctk.CTkFrame(self._content, fg_color=BG)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        hdr = ctk.CTkFrame(frame, fg_color=BG2, corner_radius=12)
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        ctk.CTkLabel(hdr, text="📱 SOCIAL STUDIO",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=VIOLET).pack(side="left", padx=18, pady=12)

        btn = ctk.CTkButton(
            hdr, text="Abrir Social Studio ↗", height=40, width=200,
            fg_color=VIOLET, hover_color=PINK,
            text_color="white",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            command=self._open_social_studio)
        btn.pack(side="right", padx=18, pady=12)

        info = ctk.CTkLabel(
            frame,
            text="Genera descripciones de TikTok, posts de X/Twitter\n"
                 "y respuestas a comentarios con la personalidad de Aiko.",
            font=ctk.CTkFont("Segoe UI", 13),
            text_color=TEXT_DIM, justify="center")
        info.grid(row=1, column=0, pady=60)
        frame.grid_rowconfigure(1, weight=1)

        return frame

    def _open_social_studio(self):
        """Abre el Social Studio como ventana secundaria."""
        s = self._stack
        try:
            from modules.gui_social_studio import SocialStudioWindow
            SocialStudioWindow(self, s["llm"], "", "", "neutral")
        except Exception as e:
            print(f"[Aiko] Error abriendo Social Studio: {e}")

    # ═══════════════════════════════════════════════════════
    #  UI QUEUE PROCESSOR
    # ═══════════════════════════════════════════════════════

    def _process_queue(self):
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                kind = msg[0]
                data = msg[1]
                if kind == "stack_ready":
                    self._on_stack_ready(data)
                elif kind == "stack_error":
                    self._status_lbl.configure(
                        text=f"✗ Error: {data[:40]}", text_color=RED)
                elif kind == "status":
                    self._loading_sub.configure(text=data)
                elif kind == "stream_status":
                    self._stream_status.configure(
                        text=data, text_color=GREEN)
                elif kind == "stream_stopped":
                    self._stream_running = False
                    self._stream_btn.configure(
                        text="▶ Iniciar Stream",
                        fg_color=GREEN, hover_color="#16a085")
                    self._stream_status.configure(
                        text="Stream terminado.", text_color=TEXT_DIM)
                elif kind == "stream_log":
                    self._stream_log(data)
        except queue.Empty:
            pass
        self.after(100, self._process_queue)


# ═══════════════════════════════════════════════════════════
#  CLI MODE HANDLERS
# ═══════════════════════════════════════════════════════════

def run_stream_cli(gaming_mode=False):
    """Ejecuta el stream directamente en CLI (= python main.py)."""
    import main as main_mod
    main_mod.main(gaming_mode=gaming_mode)


def run_video_cli(topic="", mood=None, countdown=None, batch=False):
    """Ejecuta modo video en CLI (= python video_mode.py)."""
    from modules.core_stack import load_config
    config = load_config()

    mood = mood or config.get("vtuber", {}).get("default_mood", "neutral")
    countdown = (
        countdown if countdown is not None
        else config.get("video_mode", {}).get("countdown_seconds", 3)
    )

    from video_mode import init_stack
    llm, etts, vts = init_stack(config)

    from modules.tiktok_video_mode import TikTokVideoMode
    engine = TikTokVideoMode(
        llm=llm, etts=etts, vts=vts,
        countdown_seconds=countdown,
    )
    result = engine.run(topic=topic, mood=mood)
    sys.exit(0 if result["success"] else 1)


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="✦ Aiko VTuber — Control Panel Unificado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python aiko.py                     GUI completo
  python aiko.py --stream            Stream CLI (= python main.py)
  python aiko.py --stream --gaming   Stream CLI + modo gaming
  python aiko.py --video "tema"      Video batch CLI
  python aiko.py --video --mood chill  Video interactivo
        """
    )
    parser.add_argument(
        "--stream", "-s", action="store_true",
        help="Modo stream directo (CLI, sin GUI)"
    )
    parser.add_argument(
        "--video", "-v", nargs="?", const="", default=None,
        help="Modo video corto (CLI). Opcionalmente pasa un tema."
    )
    parser.add_argument(
        "--gaming", "-g", action="store_true",
        help="Activar modo gaming (Doki Doki) en stream"
    )
    parser.add_argument(
        "--mood", "-m",
        choices=["hyped", "chill", "bored", "gremlin", "flustered", "focused", "neutral"],
        default=None,
        help="Humor de Aiko (para modo video)"
    )
    parser.add_argument(
        "--countdown", "-c", type=int, default=None,
        help="Segundos de countdown antes del video (para OBS)"
    )
    args = parser.parse_args()

    # ── CLI: Stream ──
    if args.stream:
        run_stream_cli(gaming_mode=args.gaming)
        return

    # ── CLI: Video ──
    if args.video is not None:
        run_video_cli(
            topic=args.video,
            mood=args.mood,
            countdown=args.countdown,
        )
        return

    # ── GUI: Panel completo ──
    app = AikoApp()
    app.mainloop()


if __name__ == "__main__":
    main()
