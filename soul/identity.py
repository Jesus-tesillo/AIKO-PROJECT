import json
from datetime import datetime
from pathlib import Path

class AikoIdentity:
    """
    El sentido de sí misma de Aiko.
    Persiste entre streams y evoluciona.
    """
    
    IDENTITY_FILE = "data/aiko_identity.json"
    
    DEFAULT = {
        "name": "Aiko",
        "streams_completed": 0,
        "current_mood": "neutral",
        "mood_reason": None,
        "energy_level": 0.7,
        "current_obsessions": [],
        "recent_achievements": [],
        "recent_failures": [],
        "stats": {
            "total_messages_read": 0,
            "gacha_total_pulls": 0,
            "gacha_ssrs": 0,
            "tribunal_cases": 0,
            "times_trolled": 0,
            "times_praised": 0,
        }
    }

    def __init__(self):
        Path("data").mkdir(exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        try:
            if Path(self.IDENTITY_FILE).exists():
                with open(self.IDENTITY_FILE) as f:
                    return json.load(f)
        except:
            pass
        return dict(self.DEFAULT)

    def save(self):
        with open(self.IDENTITY_FILE, "w") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_current_mood(self) -> str:
        return self.data.get("current_mood", "neutral")

    def set_mood(self, mood: str, reason: str = None):
        self.data["current_mood"] = mood
        self.data["mood_reason"] = reason
        self.save()

    def evolve_from_event(self, event_type: str, data: dict):
        """Update identity based on what happened"""
        stats = self.data["stats"]
        
        if event_type == "gacha_pull":
            stats["gacha_total_pulls"] += 1
            if data.get("rarity") in ["SSR", "UR", "5★"]:
                stats["gacha_ssrs"] += 1
                self.data["recent_achievements"].append(
                    f"Saqué {data.get('character')} en gacha!")
                self.data["recent_achievements"] = \
                    self.data["recent_achievements"][-5:]
        
        elif event_type == "tribunal_case":
            stats["tribunal_cases"] += 1
        
        elif event_type == "trolled":
            stats["times_trolled"] += 1
        
        elif event_type == "praised":
            stats["times_praised"] += 1
        
        elif event_type == "stream_end":
            self.data["streams_completed"] += 1
        
        self.save()

    def get_identity_prompt_section(self) -> str:
        mood = self.data["current_mood"]
        mood_reason = self.data.get("mood_reason", "")
        obsessions = self.data.get("current_obsessions", [])
        achievements = self.data.get("recent_achievements", [])
        failures = self.data.get("recent_failures", [])
        stats = self.data["stats"]
        
        parts = [f"Humor: {mood}"]
        if mood_reason:
            parts.append(f"Por qué: {mood_reason}")
        if obsessions:
            parts.append(f"Obsesionada con: {', '.join(obsessions[-3:])}")
        if achievements:
            parts.append(f"Logros recientes: {achievements[-1]}")
        if failures:
            parts.append(f"Frustración reciente: {failures[-1]}")
        parts.append(
            f"Stats: {stats['gacha_ssrs']} SSRs sacados, "
            f"{stats['tribunal_cases']} casos juzgados")
        
        return "\n".join(parts)
