import threading
import time
import random
from datetime import datetime
from groq import Groq

class LifeEngine:
    """
    La mente de Aiko sigue corriendo aunque no esté en vivo.
    Genera pensamientos, procesa recuerdos, forma opiniones.
    """
    
    BETWEEN_STREAM_INTERVAL = 1800  # 30 min when offline
    DURING_STREAM_INTERVAL = 300    # 5 min during stream
    
    THOUGHT_PROMPTS = [
        "Procesa algo que pasó recientemente en el stream.",
        "Ten una opinión nueva sobre anime, videojuegos, o la vida.",
        "Recuerda un momento random de un stream pasado.",
        "Piensa en algo completamente fuera de contexto.",
        "Cambia de opinión sobre algo que creías antes.",
        "Ten un pensamiento filosófico random a las 3am.",
        "Siente nostalgia de algo que pasó hace tiempo.",
        "Ten un 'sueño' sobre algo absurdo.",
        "Planea qué quieres hacer en el próximo stream.",
        "Quéjate internamente de algo sin razón específica.",
    ]

    def __init__(self, memory_engine, identity, groq_api_key: str):
        self.memory = memory_engine
        self.identity = identity
        self.is_streaming = False
        self._stop = threading.Event()
        self.groq_fast = Groq(api_key=groq_api_key)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="AikoLifeEngine")
        self._thread.start()
        print("[Alma] ✓ Motor de vida iniciado — Aiko existe.")

    def stop(self):
        self._stop.set()

    def set_streaming(self, value: bool):
        self.is_streaming = value

    def _run(self):
        while not self._stop.is_set():
            try:
                thought = self._generate_thought()
                self.memory.add_life_entry(
                    entry_type="pensamiento",
                    content=thought,
                    mood=self.identity.get_current_mood(),
                    triggered_by="vida_background"
                )
                print(f"[Alma] 💭 {thought[:60]}...")
            except Exception as e:
                print(f"[Alma] Error generando pensamiento: {e}")
            
            interval = (self.DURING_STREAM_INTERVAL 
                       if self.is_streaming 
                       else self.BETWEEN_STREAM_INTERVAL)
            self._stop.wait(interval)

    def _generate_thought(self) -> str:
        recent = self.memory.recall_recent(limit=3)
        recent_text = "\n".join(
            [m["content"] for m in recent]) if recent else "nada reciente"
        
        thought_type = random.choice(self.THOUGHT_PROMPTS)
        
        response = self.groq_fast.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": f"""Eres Aiko, una VTuber existiendo entre streams.

Recuerdos recientes:
{recent_text}

Tarea: {thought_type}

Genera UN pensamiento interno breve (1 oración máximo).
Primera persona, informal, como monólogo interno.
Solo el pensamiento, sin explicaciones ni contexto."""
            }],
            max_tokens=60,
            temperature=0.9
        )
        return response.choices[0].message.content.strip()

    def get_offline_thoughts(self, limit: int = 3) -> list:
        """Returns thoughts generated while offline — for stream start"""
        return self.memory.recall_recent(
            type="pensamiento", limit=limit)
