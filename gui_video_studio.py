"""
gui_video_studio.py — Aiko VTuber · Video Studio GUI
Dark premium UI · customtkinter 5.2.2
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import font as tkfont
import threading
import queue
import time
import math
import os
import sys
from datetime import datetime
from typing import Optional

# ── Colores ──────────────────────────────────────────────
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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

MOODS = ["hyped", "chill", "bored", "gremlin", "flustered", "focused"]

MOOD_EMOJI = {
    "hyped":     "⚡",
    "chill":     "🌊",
    "bored":     "😑",
    "gremlin":   "👾",
    "flustered": "💦",
    "focused":   "🎯",
}


# ─────────────────────────────────────────────────────────
#  COUNTDOWN CANVAS WIDGET
# ─────────────────────────────────────────────────────────
class CountdownCanvas(tk.Canvas):
    def __init__(self, master, size=200, **kw):
        super().__init__(master, width=size, height=size,
                         bg=BG2, highlightthickness=0, **kw)
        self.size = size
        self.cx = size // 2
        self.cy = size // 2
        self.r  = size // 2 - 16
        self._total = 1
        self._remaining = 0
        self._anim_id = None
        self._draw_idle()

    def _draw_idle(self):
        self.delete("all")
        self._draw_ring(1.0, TEXT_DIM)
        self._draw_center_text("–", TEXT_DIM, 40)

    def _draw_ring(self, fraction, color):
        pad = 16
        x0, y0 = pad, pad
        x1, y1 = self.size - pad, self.size - pad
        self.create_arc(x0, y0, x1, y1, start=90, extent=360,
                        outline=BG3, width=10, style="arc")
        if fraction > 0:
            self.create_arc(x0, y0, x1, y1, start=90,
                            extent=-360 * fraction,
                            outline=color, width=10, style="arc")

    def _draw_center_text(self, text, color, size=44):
        self.create_text(self.cx, self.cy, text=text,
                         fill=color, font=("Segoe UI", size, "bold"))

    def start(self, total_seconds):
        self._total = max(1, total_seconds)
        self._remaining = total_seconds
        self._tick()

    def _tick(self):
        if self._remaining < 0:
            self.delete("all")
            self._draw_ring(0, VIOLET)
            self._draw_center_text("GO!", GREEN, 40)
            return
        frac = self._remaining / self._total
        color = VIOLET if frac > 0.5 else (ORANGE if frac > 0.25 else RED)
        self.delete("all")
        self._draw_ring(frac, color)
        self._draw_center_text(str(max(0, self._remaining)), color, 44)
        self._remaining -= 1
        self._anim_id = self.after(1000, self._tick)

    def reset(self):
        if self._anim_id:
            self.after_cancel(self._anim_id)
        self._draw_idle()

    def pulse(self, color=GREEN):
        self.delete("all")
        self._draw_ring(1.0, color)
        self._draw_center_text("✓", color, 44)


# ─────────────────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────────────────
class VideoStudioApp(ctk.CTkFrame):
    def __init__(self, master=None, llm=None, etts=None, vts=None):
        super().__init__(master, fg_color=BG)
        import queue
        self._ui_queue = queue.Queue()
        self.after(100, self._check_queue)
        
        self.llm  = llm
        self.etts = etts
        self.vts  = vts

        self._selected_mood = tk.StringVar(value="hyped")
        self._countdown_val = tk.IntVar(value=5)
        self._status_text   = tk.StringVar(value="Lista")
        self._words_text    = tk.StringVar(value="0 palabras · 0s")
        self._history: list[dict] = []
        self._engine = None
        self._gen_thread: Optional[threading.Thread] = None

        self._build_layout()

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=3, minsize=300)
        self.grid_columnconfigure(1, weight=5, minsize=380)
        self.grid_columnconfigure(2, weight=3, minsize=280)
        self.grid_rowconfigure(0, weight=1)
        self._build_left()
        self._build_center()
        self._build_right()

    # ── COLUMNA IZQUIERDA ────────────────────────────────
    def _build_left(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=16)
        col.grid(row=0, column=0, sticky="nsew", padx=(14, 6), pady=14)
        col.grid_rowconfigure(8, weight=1)
        col.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(col, text="✦ VIDEO STUDIO",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=VIOLET).grid(row=0, column=0,
                     sticky="w", padx=18, pady=(18, 4))

        ctk.CTkLabel(col, text="TEMA DEL VIDEO",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=1, column=0,
                     sticky="w", padx=18, pady=(10, 2))

        self._topic_entry = ctk.CTkTextbox(
            col, height=80, corner_radius=10,
            fg_color=BG3, border_color=VIOLET_DK, border_width=1,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 13))
        self._topic_entry.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 4))

        ctk.CTkLabel(col, text="Vacío = Aiko elige libremente",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=TEXT_DIM).grid(row=3, column=0,
                     sticky="w", padx=18, pady=(0, 8))

        ctk.CTkLabel(col, text="MOOD",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=4, column=0,
                     sticky="w", padx=18, pady=(4, 6))

        mood_frame = ctk.CTkFrame(col, fg_color="transparent")
        mood_frame.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 10))
        mood_frame.grid_columnconfigure((0, 1), weight=1)

        self._mood_btns = {}
        for i, mood in enumerate(MOODS):
            r, c = divmod(i, 2)
            btn = ctk.CTkButton(
                mood_frame,
                text=f"{MOOD_EMOJI[mood]} {mood}",
                height=36, corner_radius=8,
                fg_color=VIOLET_DK if mood == self._selected_mood.get() else BG3,
                hover_color=VIOLET_DK, border_color=VIOLET, border_width=1,
                text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                command=lambda m=mood: self._select_mood(m))
            btn.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            self._mood_btns[mood] = btn

        ctk.CTkLabel(col, text="COUNTDOWN (OBS)",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=6, column=0,
                     sticky="w", padx=18, pady=(8, 2))

        slider_row = ctk.CTkFrame(col, fg_color="transparent")
        slider_row.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 10))
        slider_row.grid_columnconfigure(0, weight=1)

        self._countdown_label = ctk.CTkLabel(
            slider_row, text="5s",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=VIOLET, width=36)
        self._countdown_label.grid(row=0, column=1, padx=(6, 0))

        self._slider = ctk.CTkSlider(
            slider_row, from_=0, to=15, number_of_steps=15,
            variable=self._countdown_val,
            button_color=VIOLET, button_hover_color=PINK,
            progress_color=VIOLET_DK, fg_color=BG3,
            command=self._on_slider)
        self._slider.grid(row=0, column=0, sticky="ew")

        ctk.CTkFrame(col, fg_color="transparent", height=1).grid(
            row=8, column=0, sticky="nsew")

        self._gen_btn = ctk.CTkButton(
            col, text="▶  GENERAR GUIÓN",
            height=48, corner_radius=12,
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            fg_color=VIOLET, hover_color=PINK, text_color="white",
            command=self._on_generate)
        self._gen_btn.grid(row=9, column=0, sticky="ew", padx=14, pady=(0, 16))

    def _select_mood(self, mood):
        self._selected_mood.set(mood)
        for m, btn in self._mood_btns.items():
            btn.configure(fg_color=VIOLET_DK if m == mood else BG3)

    def _on_slider(self, val):
        self._countdown_val.set(int(val))
        self._countdown_label.configure(text=f"{int(val)}s")

    # ── COLUMNA CENTRAL ──────────────────────────────────
    def _build_center(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=16)
        col.grid(row=0, column=1, sticky="nsew", padx=6, pady=14)
        col.grid_rowconfigure(1, weight=1)
        col.grid_columnconfigure(0, weight=1)

        # Header row
        hdr = ctk.CTkFrame(col, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr, text="GUIÓN",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w")

        self._words_lbl = ctk.CTkLabel(hdr, textvariable=self._words_text,
                                       font=ctk.CTkFont("Segoe UI", 11),
                                       text_color=TEXT_DIM)
        self._words_lbl.grid(row=0, column=1, sticky="e")

        # Script textarea
        self._script_box = ctk.CTkTextbox(
            col, corner_radius=10,
            fg_color=BG3, border_color=BG3, border_width=0,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 14),
            wrap="word", state="disabled")
        self._script_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))

        # Separator gradient line simulation
        sep = ctk.CTkFrame(col, fg_color=VIOLET_DK, height=2, corner_radius=1)
        sep.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))

        # Action buttons row
        btn_row = ctk.CTkFrame(col, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 16))
        btn_row.grid_columnconfigure((0, 1, 2), weight=1)

        self._regen_btn = ctk.CTkButton(
            btn_row, text="↺  Regenerar",
            height=40, corner_radius=10,
            fg_color=BG3, hover_color=VIOLET_DK,
            border_color=VIOLET, border_width=1,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            state="disabled", command=self._on_regenerate)
        self._regen_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self._rec_btn = ctk.CTkButton(
            btn_row, text="⏺  Grabar",
            height=40, corner_radius=10,
            fg_color=BG3, hover_color="#7C1D2E",
            border_color=RED, border_width=1,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            state="disabled", command=self._on_record)
        self._rec_btn.grid(row=0, column=1, padx=4, sticky="ew")

        self._discard_btn = ctk.CTkButton(
            btn_row, text="✕  Descartar",
            height=40, corner_radius=10,
            fg_color=BG3, hover_color="#3A1A2A",
            border_color=PINK_DK, border_width=1,
            text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            state="disabled", command=self._on_discard)
        self._discard_btn.grid(row=0, column=2, padx=(4, 0), sticky="ew")

        self._social_btn = ctk.CTkButton(
            col, text="📱 Abrir Social Studio (TikTok / Post / Comentarios)",
            height=40, corner_radius=10,
            fg_color=VIOLET_DK, hover_color=VIOLET,
            text_color="white", font=ctk.CTkFont("Segoe UI", 13, "bold"),
            command=self._on_social_studio)
        self._social_btn.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 16))


    # ── COLUMNA DERECHA ──────────────────────────────────
    def _build_right(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=16)
        col.grid(row=0, column=2, sticky="nsew", padx=(6, 14), pady=14)
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(4, weight=1)

        status_frame = ctk.CTkFrame(col, fg_color=BG3, corner_radius=10)
        status_frame.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        status_frame.grid_columnconfigure(1, weight=1)

        self._status_dot = ctk.CTkLabel(status_frame, text="\u25cf",
                                        font=ctk.CTkFont("Segoe UI", 16),
                                        text_color=TEXT_DIM)
        self._status_dot.grid(row=0, column=0, padx=(12, 6), pady=10)

        self._status_lbl = ctk.CTkLabel(status_frame,
                                        textvariable=self._status_text,
                                        font=ctk.CTkFont("Segoe UI", 13, "bold"),
                                        text_color=TEXT)
        self._status_lbl.grid(row=0, column=1, sticky="w", pady=10)

        ctk.CTkLabel(col, text="COUNTDOWN",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=1, column=0,
                     sticky="w", padx=18, pady=(4, 6))

        canvas_wrap = ctk.CTkFrame(col, fg_color=BG3, corner_radius=12)
        canvas_wrap.grid(row=2, column=0, padx=14, pady=(0, 12))

        self._countdown_canvas = CountdownCanvas(canvas_wrap, size=190)
        self._countdown_canvas.pack(padx=8, pady=8)

        ctk.CTkLabel(col, text="HISTORIAL",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=3, column=0,
                     sticky="w", padx=18, pady=(4, 4))

        self._history_frame = ctk.CTkScrollableFrame(
            col, fg_color=BG3, corner_radius=10,
            scrollbar_button_color=VIOLET_DK,
            scrollbar_button_hover_color=VIOLET)
        self._history_frame.grid(row=4, column=0, sticky="nsew",
                                 padx=14, pady=(0, 14))
        self._history_frame.grid_columnconfigure(0, weight=1)

        self._no_history_lbl = ctk.CTkLabel(
            self._history_frame, text="Sin videos aun",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TEXT_DIM)
        self._no_history_lbl.grid(row=0, column=0, pady=20)


    # ─────────────────────────────────────────────────────
    #  LOGICA: GENERAR GUION
    # ─────────────────────────────────────────────────────
    def _set_status(self, text, color=TEXT, dot_color=TEXT_DIM):
        self._status_text.set(text)
        self._status_lbl.configure(text_color=color)
        self._status_dot.configure(text_color=dot_color)

    def _set_script(self, text):
        self._script_box.configure(state="normal")
        self._script_box.delete("0.0", "end")
        self._script_box.insert("0.0", text)
        self._script_box.configure(state="normal")
        words = len(text.split())
        secs  = int(words / 2.8)
        self._words_text.set(f"{words} palabras \u00b7 ~{secs}s")

    def _enable_action_btns(self, enabled: bool):
        st = "normal" if enabled else "disabled"
        self._regen_btn.configure(state=st)
        self._rec_btn.configure(state=st)
        self._discard_btn.configure(state=st)

    
    def _check_queue(self):
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                if msg['type'] == 'script_ready':
                    self._on_script_ready(msg['script'])
                elif msg['type'] == 'script_error':
                    self._on_generate_error(msg['error'])
        except queue.Empty:
            pass
        finally:
            self.after(100, self._check_queue)

    def _on_generate(self):
        if self._gen_thread and self._gen_thread.is_alive():
            return
        topic = self._topic_entry.get("0.0", "end").strip()
        mood  = self._selected_mood.get()
        self._gen_btn.configure(state="disabled", text="Generando...")
        self._enable_action_btns(False)
        self._set_status("Generando...", color=ORANGE, dot_color=ORANGE)
        self._countdown_canvas.reset()
        self._gen_thread = threading.Thread(
            target=self._generate_worker, args=(topic, mood), daemon=True)
        self._gen_thread.start()

    def _on_social_studio(self):
        script = self._script_box.get('0.0', 'end').strip()
        topic = self._topic_entry.get('0.0', 'end').strip()
        mood = self._selected_mood.get()
        
        from modules.gui_social_studio import SocialStudioWindow
        SocialStudioWindow(self, self.llm, topic, script, mood)

    def _generate_worker(self, topic, mood):
        try:
            if self.llm is None:
                # Demo mode (no stack loaded)
                time.sleep(1.5)
                demo = (
                    f"[DEMO] Tema: {topic or 'libre'} | Mood: {mood}\n\n"
                    "Esto es un guion de prueba. El LLM no esta conectado. "
                    "Puedes editar este texto libremente antes de grabar. "
                    "Incluye expresiones como (risa) y (piensa) para animar a Aiko. "
                    "La duracion estimada aparece arriba. (guina)"
                )
                script = demo
            else:
                from modules.tiktok_video_mode import TikTokVideoMode
                engine = TikTokVideoMode(self.llm, self.etts, self.vts,
                                         countdown_seconds=0)
                script = engine._generate_script(topic=topic, mood=mood)
                self._engine = engine
            self._ui_queue.put({'type': 'script_ready', 'script': script})
        except Exception as e:
            print('Error in _generate_worker:', e)
            self._ui_queue.put({'type': 'script_error', 'error': str(e)})

    def _on_script_ready(self, script):
        if not script:
            self._on_generate_error("No se pudo generar el guion.")
            return
        self._set_script(script)
        self._enable_action_btns(True)
        self._gen_btn.configure(state="normal", text="Generar Guion")
        self._set_status("Listo", color=GREEN, dot_color=GREEN)

    def _on_generate_error(self, msg):
        self._set_script(f"[ERROR] {msg}")
        self._gen_btn.configure(state="normal", text="Generar Guion")
        self._set_status("Error", color=RED, dot_color=RED)

    def _on_regenerate(self):
        self._on_generate()

    def _on_discard(self):
        self._script_box.configure(state="normal")
        self._script_box.delete("0.0", "end")
        self._script_box.configure(state="disabled")
        self._words_text.set("0 palabras \u00b7 0s")
        self._enable_action_btns(False)
        self._set_status("Lista", color=TEXT, dot_color=TEXT_DIM)
        self._countdown_canvas.reset()


    # ─────────────────────────────────────────────────────
    #  LOGICA: GRABAR (COUNTDOWN + TTS)
    # ─────────────────────────────────────────────────────
    def _on_record(self):
        script = self._script_box.get("0.0", "end").strip()
        
        mood      = self._selected_mood.get()
        countdown = self._countdown_val.get()
        self._enable_action_btns(False)
        self._gen_btn.configure(state="disabled")
        self._set_status("Cuenta regresiva...", color=ORANGE, dot_color=ORANGE)

        if countdown > 0:
            self._countdown_canvas.start(countdown)

        threading.Thread(
            target=self._record_worker,
            args=(script, mood, countdown), daemon=True).start()

    def _record_worker(self, script, mood, countdown):
        try:
            if countdown > 0:
                time.sleep(countdown + 0.5)

            self.after(0, self._set_status, "Grabando...", RED, RED)

            if self.llm is not None and self._engine:
                self._engine._deliver_script(script, mood)
            else:
                # Demo: simular duracion
                words = len(script.split())
                time.sleep(max(2, words / 2.8))

            # Guardar en historial
            entry = {
                "time":  datetime.now().strftime("%H:%M"),
                "mood":  mood,
                "words": len(script.split()),
                "script": script[:80] + ("..." if len(script) > 80 else ""),
            }
            self.after(0, self._add_history_entry, entry)
            self.after(0, self._on_record_done)
        except Exception as e:
            self.after(0, self._on_generate_error, str(e))

    def _on_record_done(self):
        self._set_status("Listo", color=GREEN, dot_color=GREEN)
        self._countdown_canvas.pulse(GREEN)
        self._enable_action_btns(True)
        self._gen_btn.configure(state="normal", text="Generar Guion")

    def _add_history_entry(self, entry: dict):
        if self._no_history_lbl.winfo_exists():
            self._no_history_lbl.grid_forget()
        idx = len(self._history)
        self._history.append(entry)

        card = ctk.CTkFrame(self._history_frame, fg_color=BG, corner_radius=8)
        card.grid(row=idx, column=0, sticky="ew", pady=3, padx=4)
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top, text=f"{MOOD_EMOJI.get(entry['mood'], '')} {entry['mood']}",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=VIOLET).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(top, text=entry["time"],
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=TEXT_DIM).grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(card, text=entry["script"],
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=TEXT_DIM, wraplength=220,
                     justify="left").grid(row=1, column=0,
                     sticky="w", padx=8, pady=(0, 6))


# ─────────────────────────────────────────────────────────
#  ENTRYPOINT
# ─────────────────────────────────────────────────────────
def launch_studio(master=None, llm=None, etts=None, vts=None):
    if master is None:
        # Modo Standalone: crear root con el frame embebido
        root = ctk.CTk()
        root.title("✦ Aiko · Video Studio")
        root.geometry("1260x780")
        root.minsize(1100, 680)
        root.configure(fg_color=BG)
        app = VideoStudioApp(master=root, llm=llm, etts=etts, vts=vts)
        app.pack(fill="both", expand=True)
        root.mainloop()
    else:
        # Modo Embebido (Aiko Master) — ya es un Frame
        return VideoStudioApp(master=master, llm=llm, etts=etts, vts=vts)


if __name__ == "__main__":
    launch_studio()
