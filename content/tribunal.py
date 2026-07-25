import random
from datetime import datetime
from groq import Groq

class TribunalDelChat:
    """
    Viewers submit cases with !caso [texto]
    Aiko judges them with her full chaotic personality.
    """
    
    VERDICT_OPTIONS = [
        "CULPABLE sin lugar a dudas",
        "INOCENTE pero sospechoso",
        "CULPABLE pero lo entiendo",
        "CASO RECHAZADO — qué pregunta tan tonta",
        "INOCENTE — el chat está equivocado",
        "CULPABLE — y sin remordimientos",
        "VEREDICTO IMPOSIBLE — ambos están mal",
        "EN LIBERTAD CONDICIONAL",
    ]

    def __init__(self, memory_engine, identity, groq_api_key: str):
        self.memory = memory_engine
        self.identity = identity
        self.groq = Groq(api_key=groq_api_key)
        self.pending_cases = []
        self.active = False
        self.cases_today = 0

    def submit_case(self, username: str, case_text: str) -> str:
        """Called when viewer uses !caso"""
        if len(case_text) < 5:
            return None
        self.pending_cases.append({
            "username": username,
            "case": case_text[:200],  # limit length
            "timestamp": datetime.now().isoformat()
        })
        position = len(self.pending_cases)
        return f"caso #{position} recibido"

    def has_pending_cases(self) -> bool:
        return len(self.pending_cases) > 0

    def judge_next_case(self) -> dict:
        if not self.pending_cases:
            return None
        
        case = self.pending_cases.pop(0)
        username = case["username"]
        case_text = case["case"]
        
        # Get context
        viewer_info = self.memory.get_viewer_relationship_summary(username)
        past_similar = self.memory.recall_about(case_text, limit=1)
        past_text = past_similar[0] if past_similar else "primer caso similar"
        
        mood = self.identity.get_current_mood()
        identity_section = self.identity.get_identity_prompt_section()
        
        prompt = f"""Eres Aiko y estás presidiendo el Tribunal del Chat.

CASO presentado por {username}:
"{case_text}"

Sobre {username}: {viewer_info}
Referencia: {past_text}
Tu estado: {identity_section}

Juzga este caso como Aiko:
- Eres la jueza suprema y tu palabra es ley
- Puedes ser caótica, justa, cruel, tierna — lo que sientas
- Tu veredicto debe tener lógica de Aiko, no lógica normal
- Máximo 3 oraciones: veredicto + razón + sentencia o consejo
- Habla directo al chat, en español informal
- NO uses formato de lista, habla natural"""

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.9
            )
            verdict_text = response.choices[0].message.content.strip()
            
            # Store in memory
            memorable = self.cases_today < 3 or random.random() < 0.2
            self.memory.save_tribunal_case(
                case_text=case_text,
                submitted_by=username,
                verdict=verdict_text,
                reasoning="",
                memorable=memorable
            )
            
            self.identity.evolve_from_event("tribunal_case", {})
            self.cases_today += 1
            
            return {
                "username": username,
                "case": case_text,
                "verdict": verdict_text,
                "memorable": memorable
            }
        except Exception as e:
            print(f"[Tribunal] Error: {e}")
            return None

    def get_intro_speech(self) -> str:
        """Aiko announces the tribunal is opening"""
        intros = [
            "okay el tribunal está abierto — manden sus casos con !caso",
            "hora de juzgar gente, usen !caso para mandar sus situaciones",
            "abriendo el tribunal, no se me pongan nerviosos — !caso para participar",
            "el tribunal de Aiko está en sesión, !caso para que los juzgue",
        ]
        return random.choice(intros)
