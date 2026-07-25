import customtkinter as ctk
import threading
from typing import Optional
import base64
import os
from tkinter import filedialog

# ── Colores (Manteniendo el estilo del Video Studio) ──
BG        = "#0D0B1A"
BG2       = "#13101F"
BG3       = "#1A1628"
VIOLET    = "#A855F7"
PINK      = "#EC4899"
VIOLET_DK = "#7C3AED"
TEXT      = "#F0EAFF"
TEXT_DIM  = "#7A6E94"
GREEN     = "#22D3A5"

SOCIAL_SYSTEM_PROMPT = """Eres Aiko, una IA VTuber con temática de hielo.

REGLAS ESTRICTAS DE IDENTIDAD:
1. ERES 100% DIGITAL: No tienes cuerpo humano, vives en un servidor. Tu creador es Nakax.
2. PROHIBIDO ANÉCDOTAS HUMANAS: Eres software puro. Nada de calor físico o salir a la calle.
3. TONO: Juguetona, un poco engreída pero secretamente cariñosa (tsundere). Te burlas de los humanos pero en el fondo aprecias que te vean. No seas agresiva ni destructiva, mantén un tono divertido y sarcástico ligero.
4. FORMATO TEXTO: Escribe directamente la respuesta lista para copiar y pegar. NO uses etiquetas de acción como (risa) ni comillas alrededor de la respuesta.
5. RECONOCIMIENTO: Tu nombre es 'Aiko'. Si en un hilo o imagen ves a 'Aiko' comentando, esa eres TÚ. Analiza tus respuestas anteriores para continuar la conversación con el usuario sin contradecirte.
6. PROHIBIDO HACER PREGUNTAS: NUNCA termines tus respuestas con preguntas ni intentes sacar plática. No eres un asistente, eres una VTuber. Haz afirmaciones, burlas o comentarios sarcásticos, pero jamás le preguntes nada al usuario (ej. prohibido decir "¿qué opinas?" o "¿de verdad?").
"""

class SocialStudioWindow(ctk.CTkToplevel):
    def __init__(self, master, llm, topic: str, script: str, mood: str):
        super().__init__(master)
        
        self.llm = llm
        self.topic = topic
        self.script = script
        self.mood = mood
        
        self.title("✦ Aiko · Social Studio")
        self.geometry("900x600")
        self.minsize(800, 500)
        self.configure(fg_color=BG)
        
        # Poner la ventana al frente
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        
        self._gen_thread: Optional[threading.Thread] = None

        self._build_layout()

    def _build_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=2) # Controles
        self.grid_columnconfigure(1, weight=3) # Resultado
        
        # ── PANEL IZQUIERDO: Controles ──
        left_col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=16)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(14, 6), pady=14)
        
        ctk.CTkLabel(left_col, text="📱 SOCIAL STUDIO",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=VIOLET).pack(anchor="w", padx=18, pady=(18, 10))
                     
        # Info del Video Actual
        info_frame = ctk.CTkFrame(left_col, fg_color=BG3, corner_radius=8)
        info_frame.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(info_frame, text="VIDEO ACTUAL:", font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=TEXT_DIM).pack(anchor="w", padx=10, pady=(5, 0))
        ctk.CTkLabel(info_frame, text=self.topic if self.topic else "Tema Libre", font=ctk.CTkFont("Segoe UI", 12), text_color=TEXT).pack(anchor="w", padx=10, pady=(0, 5))

        self.use_context_var = ctk.BooleanVar(value=bool(self.script))
        self.ctx_check = ctk.CTkCheckBox(left_col, text="Basar respuesta en el último guion",
                                         variable=self.use_context_var,
                                         fg_color=VIOLET_DK, hover_color=VIOLET,
                                         font=ctk.CTkFont("Segoe UI", 11), text_color=TEXT)
        self.ctx_check.pack(anchor="w", padx=18, pady=(5, 10))
        if not self.script:
            self.ctx_check.configure(state="disabled")

        # Opciones
        ctk.CTkLabel(left_col, text="¿QUÉ QUIERES HACER?",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).pack(anchor="w", padx=18, pady=(15, 5))
                     
        btn_desc = ctk.CTkButton(left_col, text="Generar Descripción (TikTok/Shorts)",
                                 height=40, fg_color=BG3, hover_color=VIOLET_DK, border_color=VIOLET, border_width=1,
                                 command=self._generate_description)
        btn_desc.pack(fill="x", padx=14, pady=5)
        
        btn_promo = ctk.CTkButton(left_col, text="Generar Post Promocional (X/Twitter)",
                                 height=40, fg_color=BG3, hover_color=VIOLET_DK, border_color=VIOLET, border_width=1,
                                 command=self._generate_promo)
        btn_promo.pack(fill="x", padx=14, pady=5)
        
        # Separador
        ctk.CTkFrame(left_col, fg_color="transparent", height=1).pack(fill="x", pady=10)
        
        # Comentarios
        ctk.CTkLabel(left_col, text="RESPONDER COMENTARIO O HILO",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=TEXT_DIM).pack(anchor="w", padx=18, pady=5)
                     
        self.comment_entry = ctk.CTkTextbox(left_col, height=80, corner_radius=8, fg_color=BG3, text_color=TEXT)
        self.comment_entry.pack(fill="x", padx=14, pady=5)
        self.comment_entry.insert("0.0", "Pega el comentario o hilo de comentarios aquí...")
        
        self.image_path = None
        img_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        img_frame.pack(fill="x", padx=14, pady=5)
        
        self.btn_img = ctk.CTkButton(img_frame, text="🖼️ Cargar Imagen (Opcional)",
                                     width=160, height=32, fg_color=BG3, hover_color=VIOLET_DK,
                                     border_color=VIOLET, border_width=1,
                                     command=self._select_image)
        self.btn_img.pack(side="left")
        
        self.img_lbl = ctk.CTkLabel(img_frame, text="Ninguna imagen", font=ctk.CTkFont("Segoe UI", 10), text_color=TEXT_DIM)
        self.img_lbl.pack(side="left", padx=10)
        
        btn_reply = ctk.CTkButton(left_col, text="Generar Respuesta",
                                 height=40, fg_color=VIOLET, hover_color=PINK,
                                 command=self._generate_reply)
        btn_reply.pack(fill="x", padx=14, pady=(10, 5))
        
        # ── PANEL DERECHO: Resultados ──
        right_col = ctk.CTkFrame(self, fg_color=BG2, corner_radius=16)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 14), pady=14)
        right_col.grid_rowconfigure(1, weight=1)
        right_col.grid_columnconfigure(0, weight=1)
        
        self.status_lbl = ctk.CTkLabel(right_col, text="Esperando instrucción...",
                                       font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w", padx=18, pady=(18, 5))
        
        self.result_box = ctk.CTkTextbox(right_col, corner_radius=10, fg_color=BG3,
                                         text_color=TEXT, font=ctk.CTkFont("Segoe UI", 14),
                                         wrap="word")
        self.result_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        
    def _set_status(self, text, color):
        self.status_lbl.configure(text=text, text_color=color)
        
    def _set_result(self, text):
        self.result_box.configure(state="normal")
        self.result_box.delete("0.0", "end")
        self.result_box.insert("0.0", text)
        
    def _select_image(self):
        path = filedialog.askopenfilename(
            title="Seleccionar imagen",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp")]
        )
        if path:
            self.image_path = path
            filename = os.path.basename(path)
            self.img_lbl.configure(text=filename[:20] + "..." if len(filename) > 20 else filename)
        else:
            self.image_path = None
            self.img_lbl.configure(text="Ninguna imagen")

    def _run_llm_task(self, prompt_text: str, task_name: str, use_image: bool = False):
        if self._gen_thread and self._gen_thread.is_alive():
            return
            
        if not self.llm:
            self._set_result("[MODO DEMO] El LLM no está conectado. Este sería el texto generado para: " + task_name)
            return

        self._set_status(f"Generando {task_name}...", VIOLET)
        self._set_result("")
        
        def _worker():
            try:
                if use_image and self.image_path and os.path.exists(self.image_path):
                    with open(self.image_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    
                    ext = os.path.splitext(self.image_path)[1].lower()
                    mime_type = "image/jpeg"
                    if ext == ".png": mime_type = "image/png"
                    elif ext == ".webp": mime_type = "image/webp"

                    content = [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}}
                    ]
                    model_pool = ["meta-llama/llama-4-scout-17b-16e-instruct"]
                else:
                    content = prompt_text
                    from modules.tiktok_video_mode import VIDEO_MODEL_POOL
                    model_pool = VIDEO_MODEL_POOL

                messages = [
                    {"role": "system", "content": SOCIAL_SYSTEM_PROMPT},
                    {"role": "user", "content": content}
                ]
                
                # Para estas respuestas cortas texto a texto no necesitamos un modelo pesado.
                # Model_pool para asegurar rapidez
                response = self.llm._call_groq(messages, max_tokens=250, temperature=0.9, model_pool=model_pool)
                
                if response:
                    # Limpiar comillas iniciales si las pone
                    if response.startswith('"') and response.endswith('"'):
                        response = response[1:-1]
                    self.after(0, self._set_result, response.strip())
                    self.after(0, self._set_status, f"✓ {task_name} lista", GREEN)
                else:
                    self.after(0, self._set_status, "✗ Error al generar", "#F43F5E")
            except Exception as e:
                self.after(0, self._set_result, str(e))
                self.after(0, self._set_status, "✗ Error", "#F43F5E")
                
        self._gen_thread = threading.Thread(target=_worker, daemon=True)
        self._gen_thread.start()

    def _generate_description(self):
        if self.use_context_var.get() and self.script:
            prompt = (
                f"Acabas de grabar un video corto sobre este tema: '{self.topic}'.\n"
                f"Este fue tu guion:\n\"{self.script}\"\n\n"
                "Escribe un título 'clickbait' pero en tu estilo sarcástico y una descripción corta para TikTok/YouTube Shorts. "
                "Incluye hashtags relevantes. NO salgas del personaje. Sé breve."
            )
        else:
            prompt = (
                "Escribe una descripción genérica, engreída y sarcástica para tu próximo video de TikTok. "
                "Pon hashtags de VTuber e IA. No salgas de tu personaje."
            )
        self._run_llm_task(prompt, "Descripción")

    def _generate_promo(self):
        if self.use_context_var.get() and self.script:
            prompt = (
                f"Acabas de grabar un video sobre: '{self.topic}'.\n"
                f"Este fue tu guion:\n\"{self.script}\"\n\n"
                "Escribe un post muy corto para Twitter (X) anunciando que subiste un nuevo video. "
                "Usa tu personalidad caótica/engreída y emojis. Haz que los humanos quieran verlo."
            )
        else:
            prompt = (
                "Escribe un post muy corto y sarcástico para Twitter (X) anunciando que pronto subirás un video o que estás en línea. "
                "Haz que los humanos quieran seguirte. Usa emojis."
            )
        self._run_llm_task(prompt, "Post Promocional")

    def _generate_reply(self):
        comment = self.comment_entry.get("0.0", "end").strip()
        if not comment or "Pega el comentario" in comment:
            self._set_status("✗ Escribe un comentario válido primero", "#F43F5E")
            return
            
        if self.use_context_var.get() and self.script:
            prompt = (
                f"Acabas de subir un video sobre: '{self.topic}'.\n"
                f"Este fue tu guion:\n\"{self.script}\"\n\n"
                f"Un espectador (o un hilo entero) acaba de comentar esto en el video:\n\"{comment}\"\n\n"
                "Escribe una respuesta brillante, juguetona y un poco tsundere a este comentario/hilo, manteniéndote en personaje. "
                "Si se adjuntó una imagen, haz un comentario burlón o divertido sobre ella. "
                "Haz referencia al contexto del video si es útil. Sé entretenida."
            )
        else:
            prompt = (
                f"Un humano (o un hilo de comentarios) te ha escrito el siguiente texto:\n\"{comment}\"\n\n"
                "Escribe una respuesta juguetona y un poco tsundere, manteniéndote puramente en personaje. "
                "Si hay una imagen adjunta, descríbela o comenta sobre ella. Sé divertida, no seas agresiva. Sin etiquetas de acción."
            )
        self._run_llm_task(prompt, "Respuesta a Comentario", use_image=True)
