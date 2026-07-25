import random
import time

class AutonomyEngine:
    """
    Aiko decide qué hacer y cuándo.
    No es controlada — elige.
    """
    
    CONTENT_OPTIONS = [
        {"id": "just_chatting",   "weight": 0.35, "min_time": 120,
         "moods": ["hyped","chill","gremlin","neutral","flustered"]},
        {"id": "tribunal",        "weight": 0.25, "min_time": 180,
         "moods": ["gremlin","focused","bored","neutral"]},
        {"id": "gacha",           "weight": 0.25, "min_time": 150,
         "moods": ["hyped","gremlin","neutral"]},
        {"id": "game_wordle",     "weight": 0.08, "min_time": 300,
         "moods": ["focused","chill","neutral"]},
        {"id": "game_typeracer",  "weight": 0.07, "min_time": 300,
         "moods": ["hyped","focused","neutral"]},
    ]
    
    def __init__(self, memory_engine, identity):
        self.memory = memory_engine
        self.identity = identity
        self.current_activity = "just_chatting"
        self.activity_start_time = time.time()
        self.activity_history = []

    def decide_next_activity(self) -> str:
        elapsed = time.time() - self.activity_start_time
        current_mood = self.identity.get_current_mood()
        
        # Check minimum time for current activity
        current = next(
            (c for c in self.CONTENT_OPTIONS 
             if c["id"] == self.current_activity), 
            self.CONTENT_OPTIONS[0])
        
        if elapsed < current["min_time"]:
            return None  # stay on current
        
        # Filter by mood compatibility
        candidates = [
            c for c in self.CONTENT_OPTIONS
            if current_mood in c["moods"] or random.random() < 0.15
        ]
        
        # Reduce weight of recent activities
        weighted = []
        for c in candidates:
            w = c["weight"]
            if c["id"] in self.activity_history[-3:]:
                w *= 0.2  # strong penalty for repetition
            if c["id"] == self.current_activity:
                w *= 0.1  # very unlikely to repeat immediately
            weighted.append((c, w))
        
        if not weighted:
            return None
            
        options, weights = zip(*weighted)
        chosen = random.choices(list(options), 
                               weights=list(weights), k=1)[0]
        
        if chosen["id"] != self.current_activity:
            self.activity_history.append(self.current_activity)
            self.activity_history = self.activity_history[-10:]
            self.current_activity = chosen["id"]
            self.activity_start_time = time.time()
            return chosen["id"]
        
        return None

    def get_current_activity(self) -> str:
        return self.current_activity

    def force_activity(self, activity_id: str):
        self.current_activity = activity_id
        self.activity_start_time = time.time()
