import re
import os

class SafetyFilter:
    """
    Peripheral Safety Filter (Output Guardrail).
    Intercepts the LLM output before TTS to prevent broadcasting unsafe, 
    banned, or inappropriate words. Protects the stream from TOS violations.
    """
    # Fallbacks variados para que no suene robótico siempre lo mismo
    FALLBACK_RESPONSES = [
        "Uy, mi filtro de seguridad acaba de bloquear lo que iba a decir. Nakax me regañaría si digo eso.",
        "Mejor me callo, mi filtro me dice que no debería decir eso en stream.",
        "Se me activó el filtro. Punto para mi sistema de seguridad.",
    ]

    def __init__(self, config_path="data/banned_words.txt"):
        # Separar en palabras sueltas y frases multi-palabra
        single_words = []
        phrases = []

        raw_banned = [
            "nigger", "nigga", "faggot", "kys", 
            "kill yourself", "cp",
        ]

        # Cargar extras desde archivo si existe
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    for line in f:
                        word = line.strip().lower()
                        if word and not word.startswith("#"):
                            raw_banned.append(word)
            except Exception as e:
                print(f"[SafetyFilter] Error leyendo {config_path}: {e}")

        # Deduplicar
        seen = set()
        for w in raw_banned:
            if w and w not in seen:
                seen.add(w)
                if " " in w:
                    phrases.append(w)
                else:
                    single_words.append(w)

        # Construir patrones separados: \b funciona para palabras sueltas,
        # para frases multi-palabra usamos un simple "in" check
        self._phrases = phrases
        if single_words:
            pattern = r'\b(' + '|'.join(map(re.escape, single_words)) + r')\b'
            self._word_pattern = re.compile(pattern, re.IGNORECASE)
        else:
            self._word_pattern = None

        print(f"[SafetyFilter] ✓ Cargado: {len(single_words)} palabras + {len(phrases)} frases bloqueadas")

    def filter_response(self, text: str) -> str:
        """
        Revisa si el texto tiene contenido prohibido.
        Retorna el texto original si es seguro, o un mensaje de fallback si se bloquea.
        """
        if not text:
            return text

        text_lower = text.lower()

        # Check frases multi-palabra
        for phrase in self._phrases:
            if phrase in text_lower:
                print(f"\n[SafetyFilter] 🚨 Frase prohibida bloqueada: '{phrase}'")
                import random
                return random.choice(self.FALLBACK_RESPONSES)

        # Check palabras sueltas con word boundary
        if self._word_pattern:
            match = self._word_pattern.search(text)
            if match:
                print(f"\n[SafetyFilter] 🚨 Palabra prohibida bloqueada: '{match.group(0)}'")
                import random
                return random.choice(self.FALLBACK_RESPONSES)

        return text
