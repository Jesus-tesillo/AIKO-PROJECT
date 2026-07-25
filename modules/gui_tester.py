"""
gui_tester.py — Panel de Pruebas Live2D para Aiko.

Interfaz gráfica para probar expresiones, emociones, accesorios,
motions y controles físicos del modelo Live2D en tiempo real.
Se integra como un Frame dentro del panel maestro (aiko.py).
"""

import customtkinter as ctk
import tkinter as tk
import threading
import time
from typing import Optional

# ── Colores (misma paleta del Video Studio) ──
BG        = "#0D0B1A"
BG2       = "#13101F"
BG3       = "#1A1628"
VIOLET    = "#A855F7"
PINK      = "#EC4899"
VIOLET_DK = "#7C3AED"
TEXT      = "#F0EAFF"
TEXT_DIM  = "#7A6E94"
GREEN     = "#22D3A5"
RED       = "#F43F5E"
ORANGE    = "#FB923C"
CYAN      = "#22D3EE"
YELLOW    = "#FACC15"


# ═══════════════════════════════════════════════════════════
#  EXPRESIONES / ACCESORIOS / EMOCIONES DEL MODELO
# ═══════════════════════════════════════════════════════════

# Mapeo de expressions nativas (.exp3.json) del modelo IceGirl
# index → (nombre interno, nombre bonito, emoji, categoría)
NATIVE_EXPRESSIONS = [
    (0,  "星星眼",    "Star Eyes",         "⭐",  "expression"),
    (1,  "爱心眼",    "Heart Eyes",        "💖",  "expression"),
    (2,  "惊讶",      "Shocked",           "😲",  "expression"),
    (3,  "生气",      "Angry Face",        "😡",  "expression"),
    (4,  "白眼",      "Eye Roll",          "🙄",  "expression"),
    (5,  "脸红",      "Blush",             "😊",  "expression"),
    (6,  "脸黑",      "Dark Face",         "😈",  "expression"),
    (7,  "流泪",      "Tears",             "😢",  "expression"),
    (8,  "舌头",      "Tongue Out",        "😛",  "expression"),
    (9,  "疑惑",      "Question Marks",    "❓",  "expression"),
    (10, "猫耳",      "Cat Ears",          "🐱",  "accessory"),
    (11, "王冠",      "Crown",             "👑",  "accessory"),
    (12, "翅膀",      "Wings",             "🪽",  "accessory"),
    (13, "金钱眼",    "Money Eyes",        "🤑",  "accessory"),
    (14, "手柄",      "Gamepad",           "🎮",  "accessory"),
    (15, "披发",      "Hair Down",         "💇",  "hairstyle"),
    (16, "马尾",      "Ponytail",          "🎀",  "hairstyle"),
    (17, "直播套装",  "Streaming Outfit",  "👗",  "outfit"),
    (18, "←歪嘴",     "Smirk Left",        "😏",  "expression"),
    (19, "歪嘴→",     "Smirk Right",       "😏",  "expression"),
]

# Emociones del AnimationEngine (parametricas, no expresiones nativas)
EMOTION_PRESETS = [
    ("neutral",     "Neutral",      "😐"),
    ("happy",       "Happy",        "😊"),
    ("laughing",    "Laughing",     "🤣"),
    ("surprised",   "Surprised",    "😱"),
    ("annoyed",     "Annoyed",      "😒"),
    ("sad",         "Sad",          "😢"),
    ("thinking",    "Thinking",     "🤔"),
    ("smug",        "Smug",         "😏"),
    ("excited",     "Excited",      "🤩"),
    ("embarrassed", "Embarrassed",  "😳"),
    ("bored",       "Bored",        "😑"),
    ("disgust",     "Disgust",      "🤢"),
    ("shy",         "Shy",          "🥺"),
    ("angry",       "Angry",        "😡"),
    ("wink",        "Wink",         "😉"),
    ("greet",       "Greet",        "👋"),
    ("laugh",       "Laugh",        "😂"),
]

# Motions disponibles en el modelo
MOTIONS = [
    ("DaiJi",    "Idle (DaiJi)",     "🌀"),
    ("HuiShou",  "Wave (HuiShou)",   "👋"),
    ("MeiYan",   "Pose (MeiYan)",    "✨"),
]


class Live2DTesterPanel(ctk.CTkFrame):
    """
    Panel completo de pruebas Live2D.
    Se integra como un Frame dentro de la interfaz maestra.
    """

    def __init__(self, master, vts=None, **kwargs):
        super().__init__(master, fg_color=BG, **kwargs)
        self.vts = vts
        self._active_accessories = set()   # track de accesorios activos
        self._build_layout()

    def set_vts(self, vts):
        """Inyectar la referencia a Live2DBridge después de la inicialización."""
        self.vts = vts
        self._update_connection_status()

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ──
        header = ctk.CTkFrame(self, fg_color=BG2, corner_radius=12, height=50)
        header.grid(row=0, column=0, columnspan=3, sticky="ew",
                    padx=14, pady=(14, 8))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text="🎭 LIVE2D TESTER",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=VIOLET).pack(side="left", padx=18, pady=12)

        self._conn_label = ctk.CTkLabel(
            header, text="● Desconectado",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=RED)
        self._conn_label.pack(side="right", padx=18, pady=12)

        # ── Tres columnas ──
        self._build_col_expressions()
        self._build_col_emotions()
        self._build_col_controls()

    # ─────────────────────────────────────────────────────
    #  COLUMNA 1: Expresiones Nativas + Accesorios
    # ─────────────────────────────────────────────────────
    def _build_col_expressions(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=14)
        col.grid(row=1, column=0, sticky="nsew", padx=(14, 6), pady=(0, 14))
        col.grid_columnconfigure(0, weight=1)

        # Scroll container
        scroll = ctk.CTkScrollableFrame(
            col, fg_color=BG2, corner_radius=0,
            scrollbar_button_color=VIOLET_DK,
            scrollbar_button_hover_color=VIOLET)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)
        scroll.grid_columnconfigure((0, 1), weight=1)

        # ── Expresiones Faciales ──
        ctk.CTkLabel(scroll, text="EXPRESIONES FACIALES",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=CYAN).grid(row=0, column=0, columnspan=2,
                     sticky="w", padx=12, pady=(12, 6))

        row = 1
        for idx, internal, label, emoji, cat in NATIVE_EXPRESSIONS:
            if cat != "expression":
                continue
            btn = ctk.CTkButton(
                scroll, text=f"{emoji} {label}",
                height=36, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=VIOLET, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda i=idx, n=label: self._trigger_expression(i, n))
            r, c = divmod(row - 1, 2)
            btn.grid(row=r + 1, column=c, padx=4, pady=3, sticky="ew")
            row += 1

        # ── Accesorios ──
        acc_row = (row // 2) + 2
        ctk.CTkLabel(scroll, text="ACCESORIOS",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=YELLOW).grid(row=acc_row, column=0,
                     columnspan=2, sticky="w", padx=12, pady=(16, 6))

        acc_row += 1
        col_idx = 0
        for idx, internal, label, emoji, cat in NATIVE_EXPRESSIONS:
            if cat not in ("accessory", "hairstyle", "outfit"):
                continue
            btn = ctk.CTkButton(
                scroll, text=f"{emoji} {label}",
                height=36, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=YELLOW, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda i=idx, n=label: self._toggle_accessory(i, n))
            r, c = divmod(col_idx, 2)
            btn.grid(row=acc_row + r, column=c, padx=4, pady=3, sticky="ew")
            col_idx += 1

    # ─────────────────────────────────────────────────────
    #  COLUMNA 2: Emociones (AnimationEngine)
    # ─────────────────────────────────────────────────────
    def _build_col_emotions(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=14)
        col.grid(row=1, column=1, sticky="nsew", padx=6, pady=(0, 14))
        col.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            col, fg_color=BG2, corner_radius=0,
            scrollbar_button_color=VIOLET_DK,
            scrollbar_button_hover_color=VIOLET)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)
        scroll.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(scroll, text="EMOCIONES (REACCIONES)",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=PINK).grid(row=0, column=0, columnspan=2,
                     sticky="w", padx=12, pady=(12, 6))

        for i, (key, label, emoji) in enumerate(EMOTION_PRESETS):
            btn = ctk.CTkButton(
                scroll, text=f"{emoji} {label}",
                height=36, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=PINK, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda k=key, l=label: self._trigger_emotion(k, l))
            r, c = divmod(i, 2)
            btn.grid(row=r + 1, column=c, padx=4, pady=3, sticky="ew")

        # ── Motions ──
        motion_start = (len(EMOTION_PRESETS) // 2) + 3
        ctk.CTkLabel(scroll, text="MOTIONS (ANIMACIONES)",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=GREEN).grid(row=motion_start, column=0,
                     columnspan=2, sticky="w", padx=12, pady=(16, 6))

        for i, (key, label, emoji) in enumerate(MOTIONS):
            btn = ctk.CTkButton(
                scroll, text=f"{emoji} {label}",
                height=36, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=GREEN, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda k=key: self._play_motion(k))
            btn.grid(row=motion_start + 1 + i, column=0, columnspan=2,
                     padx=4, pady=3, sticky="ew")

    # ─────────────────────────────────────────────────────
    #  COLUMNA 3: Controles Físicos y Utilidades
    # ─────────────────────────────────────────────────────
    def _build_col_controls(self):
        col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=14)
        col.grid(row=1, column=2, sticky="nsew", padx=(6, 14), pady=(0, 14))
        col.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            col, fg_color=BG2, corner_radius=0,
            scrollbar_button_color=VIOLET_DK,
            scrollbar_button_hover_color=VIOLET)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)
        scroll.grid_columnconfigure(0, weight=1)

        # ── Acciones Físicas ──
        ctk.CTkLabel(scroll, text="ACCIONES FÍSICAS",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=ORANGE).grid(row=0, column=0,
                     sticky="w", padx=12, pady=(12, 6))

        actions = [
            ("🔄 Girar (Spin)",  lambda: self._send_action("spin", times=1)),
            ("⬆️ Saltar (Jump)", lambda: self._send_action("jump")),
            ("🔄🔄 Doble Spin",   lambda: self._send_action("spin", times=3)),
        ]
        for i, (label, cmd) in enumerate(actions):
            btn = ctk.CTkButton(
                scroll, text=label, height=40, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=ORANGE, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                command=cmd)
            btn.grid(row=i + 1, column=0, padx=6, pady=4, sticky="ew")

        # ── Lip Sync Test ──
        ctk.CTkLabel(scroll, text="PRUEBA DE LIP-SYNC",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=CYAN).grid(row=5, column=0,
                     sticky="w", padx=12, pady=(20, 6))

        talk_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        talk_frame.grid(row=6, column=0, sticky="ew", padx=6, pady=4)
        talk_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            talk_frame, text="🗣️ Empezar Hablar", height=40,
            fg_color=GREEN, hover_color="#16a085",
            text_color="white",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._start_talking
        ).grid(row=0, column=0, padx=3, sticky="ew")

        ctk.CTkButton(
            talk_frame, text="🤐 Parar", height=40,
            fg_color=RED, hover_color="#c0392b",
            text_color="white",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._stop_talking
        ).grid(row=0, column=1, padx=3, sticky="ew")

        # ── Mood Presets ──
        ctk.CTkLabel(scroll, text="MOOD (HUMOR)",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=VIOLET).grid(row=7, column=0,
                     sticky="w", padx=12, pady=(20, 6))

        moods = [
            ("⚡ Hyped",      "hyped"),
            ("🌊 Chill",      "chill"),
            ("😑 Bored",      "bored"),
            ("👾 Gremlin",    "gremlin"),
            ("💦 Flustered",  "flustered"),
            ("🎯 Focused",    "focused"),
        ]
        for i, (label, mood) in enumerate(moods):
            btn = ctk.CTkButton(
                scroll, text=label, height=34, corner_radius=8,
                fg_color=BG3, hover_color=VIOLET_DK,
                border_color=VIOLET, border_width=1,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 11),
                command=lambda m=mood: self._set_mood(m))
            btn.grid(row=8 + i, column=0, padx=6, pady=2, sticky="ew")

        # ── Log de Acciones ──
        ctk.CTkLabel(scroll, text="LOG",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=TEXT_DIM).grid(row=20, column=0,
                     sticky="w", padx=12, pady=(20, 6))

        self._log_box = ctk.CTkTextbox(
            scroll, height=140, corner_radius=8,
            fg_color=BG3, text_color=TEXT_DIM,
            font=ctk.CTkFont("Consolas", 10),
            state="disabled")
        self._log_box.grid(row=21, column=0, padx=6, pady=(0, 12), sticky="ew")

    # ─────────────────────────────────────────────────────
    #  ACCIONES
    # ─────────────────────────────────────────────────────

    def _log(self, msg: str):
        """Agregar mensaje al log visual."""
        self._log_box.configure(state="normal")
        timestamp = time.strftime("%H:%M:%S")
        self._log_box.insert("end", f"[{timestamp}] {msg}\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _update_connection_status(self):
        """Actualizar el indicador de conexión."""
        if self.vts and self.vts.connected:
            self._conn_label.configure(
                text="● Conectado", text_color=GREEN)
        else:
            self._conn_label.configure(
                text="● Desconectado", text_color=RED)

    def _trigger_expression(self, index: int, name: str):
        """Activar una expresión nativa (.exp3.json) por índice."""
        if not self.vts:
            self._log(f"✗ VTS no disponible")
            return
        self.vts._send({
            "action": "react",
            "emotion": "neutral",  # no importa, usamos el índice directamente
            "duration": 3.0,
        })
        # Enviar el comando directo de expresión por índice
        self.vts._send({
            "action": "expression",
            "name": index,
        })
        self._log(f"⚡ Expression #{index}: {name}")
        self._update_connection_status()

    def _toggle_accessory(self, index: int, name: str):
        """Activar/desactivar un accesorio (toggle)."""
        if not self.vts:
            self._log(f"✗ VTS no disponible")
            return
        self.vts._send({
            "action": "expression",
            "name": index,
        })
        if index in self._active_accessories:
            self._active_accessories.discard(index)
            self._log(f"🔴 Accesorio OFF: {name}")
        else:
            self._active_accessories.add(index)
            self._log(f"🟢 Accesorio ON: {name}")
        self._update_connection_status()

    def _trigger_emotion(self, key: str, label: str):
        """Disparar una reacción emocional vía Live2DBridge."""
        if not self.vts:
            self._log(f"✗ VTS no disponible")
            return
        self.vts.trigger_react(key)
        self._log(f"💫 Reaction: {label} ({key})")
        self._update_connection_status()

    def _play_motion(self, name: str):
        """Reproducir un motion del modelo."""
        if not self.vts:
            self._log(f"✗ VTS no disponible")
            return
        self.vts.play_motion(name)
        self._log(f"🎬 Motion: {name}")
        self._update_connection_status()

    def _send_action(self, action: str, **kwargs):
        """Enviar una acción genérica al visor."""
        if not self.vts:
            self._log(f"✗ VTS no disponible")
            return
        data = {"action": action, **kwargs}
        self.vts._send(data)
        self._log(f"🔧 {action}: {kwargs or ''}")
        self._update_connection_status()

    def _start_talking(self):
        """Simular que Aiko está hablando (lip-sync test)."""
        if not self.vts:
            return
        self.vts.set_talking(True, mood="neutral")
        # Simular volumen de boca durante unos segundos
        def _sim():
            import math
            t = 0
            while t < 5.0:
                if not self.vts:
                    break
                vol = abs(math.sin(t * 4)) * 0.7 + 0.1
                self.vts._send({"action": "mouth_volume", "value": vol})
                time.sleep(0.05)
                t += 0.05
            self.vts.set_talking(False)
        threading.Thread(target=_sim, daemon=True).start()
        self._log("🗣️ Lip-sync test (5s)")

    def _stop_talking(self):
        """Parar lip-sync."""
        if self.vts:
            self.vts.set_talking(False)
            self._log("🤐 Lip-sync stopped")

    def _set_mood(self, mood: str):
        """Cambiar el mood del AnimationEngine."""
        if not self.vts:
            return
        self.vts.set_mood(mood)
        self._log(f"🎭 Mood → {mood}")
        self._update_connection_status()
