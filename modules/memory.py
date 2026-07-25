"""
memory.py - Enhanced memory with per-user tracking and topic extraction.
Thread-safe JSON-based memory for chat context and user profiles.
"""
import json, os, re, threading
from datetime import datetime, date


# Common topic keywords to detect in messages
TOPIC_KEYWORDS = {
    "minecraft": ["minecraft", "creeper", "enderman", "steve", "netherite"],
    "anime": ["anime", "manga", "waifu", "otaku", "naruto", "one piece", "demon slayer"],
    "gaming": ["game", "gaming", "fps", "rpg", "mmorpg", "steam", "playstation", "xbox"],
    "music": ["music", "song", "album", "playlist", "spotify", "band", "concert"],
    "art": ["art", "drawing", "painting", "sketch", "digital art", "commission"],
    "cats": ["cat", "cats", "kitten", "kitty", "meow", "neko"],
    "dogs": ["dog", "dogs", "puppy", "doggo", "pupper", "woof"],
    "food": ["food", "cooking", "recipe", "pizza", "sushi", "ramen", "hungry"],
    "movies": ["movie", "film", "cinema", "marvel", "disney", "netflix"],
    "coding": ["code", "coding", "programming", "python", "javascript", "dev"],
    "vtuber": ["vtuber", "vtubing", "live2d", "model", "avatar", "stream"],
    "memes": ["meme", "memes", "funny", "lmao", "lol", "based", "poggers"],
}


class Memory:
    """Thread-safe short-term memory for the AI VTuber."""

    def __init__(self, max_messages: int = 50, persist: bool = True,
                 memory_dir: str = "memories"):
        self.max_messages = max_messages
        self.persist = persist
        self.memory_dir = memory_dir
        self.memory_file = os.path.join(memory_dir, "chat_history.json")
        self.lock = threading.Lock()
        self.messages = []
        os.makedirs(self.memory_dir, exist_ok=True)
        if self.persist:
            self._load_from_disk()
        print(f"[Memoria] Inicializada ({len(self.messages)} msgs, máx {self.max_messages})")

    def add_message(self, role: str, username: str, content: str):
        with self.lock:
            self.messages.append({
                "role": role, "username": username,
                "content": content, "timestamp": datetime.now().isoformat()
            })
            if len(self.messages) > self.max_messages:
                self.messages = self.messages[-self.max_messages:]
            if self.persist:
                self._save_to_disk()

    def get_context(self, last_n: int = None) -> list:
        with self.lock:
            return list(self.messages[-last_n:]) if last_n else list(self.messages)

    def get_formatted_context(self, last_n: int = None) -> str:
        msgs = self.get_context(last_n)
        return "\n".join(f"[{m['username']}]: {m['content']}" for m in msgs)

    def clear(self):
        with self.lock:
            self.messages = []
            if self.persist:
                self._save_to_disk()

    def _save_to_disk(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Memoria] Error al guardar: {e}")

    def _load_from_disk(self):
        try:
            if os.path.exists(self.memory_file):
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    self.messages = json.load(f)
                if len(self.messages) > self.max_messages:
                    self.messages = self.messages[-self.max_messages:]
        except Exception as e:
            print(f"[Memoria] Error al cargar: {e}")
            self.messages = []

    def get_message_count(self) -> int:
        with self.lock:
            return len(self.messages)


class UserMemory:
    """Persistent per-user tracking with topic detection."""

    def __init__(self, memory_dir: str = "memories"):
        self.memory_dir = memory_dir
        self.users_file = os.path.join(memory_dir, "users.json")
        self.users = {}
        self._lock = threading.Lock()
        os.makedirs(self.memory_dir, exist_ok=True)
        self._load()
        print(f"[Memoria de Usuarios] Rastreando {len(self.users)} usuarios.")

    def _load(self):
        try:
            if os.path.exists(self.users_file):
                with open(self.users_file, "r", encoding="utf-8") as f:
                    self.users = json.load(f)
        except Exception as e:
            print(f"[Memoria de Usuarios] Error al cargar: {e}")
            self.users = {}

    def _save(self):
        try:
            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump(self.users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Memoria de Usuarios] Error al guardar: {e}")

    def update_user(self, username: str, message: str):
        """Update a user's profile with a new message. Auto-extracts topics."""
        with self._lock:
            uname = username.lower()
            if uname not in self.users:
                self.users[uname] = {
                    "username": username,
                    "first_seen": date.today().isoformat(),
                    "times_chatted": 0,
                    "topics_mentioned": [],
                    "last_message": "",
                    "is_regular": False,
                }

            user = self.users[uname]
            user["times_chatted"] += 1
            user["last_message"] = message
            user["username"] = username  # Keep latest casing

            # Regular status: 10+ messages
            if user["times_chatted"] >= 10:
                user["is_regular"] = True

            # Extract topics
            msg_lower = message.lower()
            for topic, keywords in TOPIC_KEYWORDS.items():
                if any(kw in msg_lower for kw in keywords):
                    if topic not in user["topics_mentioned"]:
                        user["topics_mentioned"].append(topic)

            self._save()

    def get_user_context(self, username: str) -> str:
        """Get a human-readable summary of a user for LLM context."""
        with self._lock:
            user = self.users.get(username.lower())
            if not user:
                return f"{username} es un viewer nuevo."

            parts = [f"{user['username']}"]
            if user["is_regular"]:
                parts.append(f"es un viewer regular ({user['times_chatted']} mensajes)")
            else:
                parts.append(f"ha enviado {user['times_chatted']} mensaje(s)")

            if user["topics_mentioned"]:
                topics = ", ".join(user["topics_mentioned"][:5])
                parts.append(f"le gusta: {topics}")

            return ". ".join(parts) + "."

    def is_regular(self, username: str) -> bool:
        with self._lock:
            user = self.users.get(username.lower())
            return user.get("is_regular", False) if user else False

    def get_top_viewers(self, n: int = 5) -> list:
        """Get the top N most active viewers."""
        with self._lock:
            sorted_users = sorted(
                self.users.values(),
                key=lambda u: u["times_chatted"],
                reverse=True
            )
            return [
                {"name": u["username"], "count": u["times_chatted"]}
                for u in sorted_users[:n]
            ]

    def get_user_count(self) -> int:
        with self._lock:
            return len(self.users)

class LoreMemory:
    """Manages static backstory and dynamic personal memories."""
    
    def __init__(self, memory_dir: str = "memories", lore_file: str = "lore.txt"):
        self.lore_file = lore_file
        self.self_memories_file = os.path.join(memory_dir, "self_memories.json")
        self.static_lore = []
        self.dynamic_memories = []
        self._lock = threading.Lock()
        
        self.load_static_lore()
        self.load_dynamic_memories()

    def load_static_lore(self):
        try:
            if os.path.exists(self.lore_file):
                with open(self.lore_file, "r", encoding="utf-8") as f:
                    self.static_lore = [
                        line.strip() for line in f 
                        if line.strip() and not line.strip().startswith("#")
                    ]
                print(f"[Lore] Cargados {len(self.static_lore)} recuerdos ancla.")
        except Exception as e:
            print(f"[Lore] Error cargando lore estático: {e}")

    def load_dynamic_memories(self):
        try:
            if os.path.exists(self.self_memories_file):
                with open(self.self_memories_file, "r", encoding="utf-8") as f:
                    self.dynamic_memories = json.load(f)
                print(f"[Lore] Autocargados {len(self.dynamic_memories)} recuerdos dinámicos.")
        except Exception as e:
            self.dynamic_memories = []

    def save_dynamic_memories(self):
        try:
            with open(self.self_memories_file, "w", encoding="utf-8") as f:
                # Keep max 50 memories so it doesn't get infinitely big
                json.dump(self.dynamic_memories[-50:], f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Lore] Error guardando: {e}")

    def add_dynamic_memory(self, memory_text: str):
        with self._lock:
            if memory_text not in self.dynamic_memories:
                self.dynamic_memories.append(memory_text)
                self.save_dynamic_memories()

    def get_random_lore(self) -> str:
        """Returns 1 random static lore and 1 random dynamic memory."""
        with self._lock:
            parts = []
            import random
            if self.static_lore:
                parts.append("Dato sobre tu vida actual: " + random.choice(self.static_lore))
            if self.dynamic_memories:
                parts.append("Recuerdo de algo que pasó en stream: " + random.choice(self.dynamic_memories))
            return "\n".join(parts)
