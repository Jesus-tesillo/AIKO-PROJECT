import sqlite3
import json
import random
import os
import threading
from datetime import datetime
from pathlib import Path

# Try to import Vector Dependencies (fallbacks if not installed yet)
try:
    from chromadb import PersistentClient
    from sentence_transformers import SentenceTransformer
    import warnings
    warnings.filterwarnings("ignore", message=".*TypedStorage.*")
    VECTOR_AVAILABLE = True
except ImportError:
    VECTOR_AVAILABLE = False

class VectorMemoryLayer:
    def __init__(self, db_path="data/chroma_db", collection_name="aiko_memories"):
        self.model = None
        self._model_ready = threading.Event()
        if not VECTOR_AVAILABLE:
            print("[VectorMemory] sentence-transformers o chromadb no disponibles. Memoria vectorial desactivada.")
            return

        Path(db_path).mkdir(exist_ok=True, parents=True)
        self.client = PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name=collection_name)
        self._load_thread = threading.Thread(target=self._load_model, daemon=True)
        self._load_thread.start()
        
    def _load_model(self):
        try:
            print("[VectorMemory] Cargando modelo semántico...")
            self.model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            print("[VectorMemory] ✓ Modelo semántico cargado.")
        except Exception as e:
            print(f"[VectorMemory] ✗ Error cargando modelo: {e}")
        finally:
            self._model_ready.set()

    def embed_and_store(self, memory_id: str, text: str, metadata: dict = None):
        if not self._model_ready.is_set():
            return  # modelo aún cargando, skip silencioso
        if not self.model:
            return
        try:
            embedding = self.model.encode(text).tolist()
            self.collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[metadata or {}]
            )
        except Exception as e:
            print(f"[VectorMemory] Error guardando vector: {e}")

    def search(self, query: str, top_k: int = 5) -> list:
        if not self._model_ready.wait(timeout=0.1):
            return []  # modelo aún cargando
        if not self.model:
            return []
        try:
            query_embedding = self.model.encode(query).tolist()
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )
            if not results['documents'] or not results['documents'][0]:
                return []
            
            docs = results['documents'][0]
            metas = results['metadatas'][0] if results['metadatas'] else [{}] * len(docs)
            
            formatted = []
            for doc, meta in zip(docs, metas):
                formatted.append({"content": doc, "metadata": meta})
            return formatted
        except Exception as e:
            print(f"[VectorMemory] Error buscando: {e}")
            return []


class MemoryEngine:
    def __init__(self, db_path="data/aiko.db"):
        Path("data").mkdir(exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()
        self.vector_memory = VectorMemoryLayer()

    def _create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            emotional_weight REAL DEFAULT 0.5,
            tags TEXT DEFAULT '[]',
            viewer TEXT,
            recalled_count INTEGER DEFAULT 0,
            last_recalled TEXT
        );

        CREATE TABLE IF NOT EXISTS viewers (
            username TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            times_chatted INTEGER DEFAULT 0,
            relationship_level TEXT DEFAULT 'desconocido',
            personality_notes TEXT DEFAULT '',
            inside_jokes TEXT DEFAULT '[]',
            memorable_moments TEXT DEFAULT '[]',
            sentiment REAL DEFAULT 0.5
        );

        CREATE TABLE IF NOT EXISTS aiko_life (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            entry_type TEXT,
            content TEXT,
            mood TEXT,
            triggered_by TEXT
        );

        CREATE TABLE IF NOT EXISTS stream_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time TEXT,
            mood_arc TEXT DEFAULT '[]',
            highlights TEXT DEFAULT '[]',
            content_played TEXT DEFAULT '[]',
            overall_vibe TEXT
        );

        CREATE TABLE IF NOT EXISTS gacha_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            banner TEXT,
            result_character TEXT,
            rarity TEXT,
            pity_count INTEGER DEFAULT 0,
            aiko_reaction TEXT,
            viewer_who_voted TEXT
        );

        CREATE TABLE IF NOT EXISTS tribunal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            case_text TEXT,
            submitted_by TEXT,
            verdict TEXT,
            reasoning TEXT,
            memorable INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS opinions (
            topic TEXT PRIMARY KEY,
            opinion TEXT,
            strength REAL DEFAULT 0.5,
            formed_date TEXT,
            last_updated TEXT,
            times_defended INTEGER DEFAULT 0
        );
        """)
        self.conn.commit()

    def remember(self, type: str, content: str, 
                 emotional_weight: float = 0.5,
                 tags: list = None, viewer: str = None):
        if tags is None:
            tags = []
        cursor = self.conn.execute("""
            INSERT INTO memories 
            (timestamp, type, content, emotional_weight, tags, viewer)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), type, content,
              emotional_weight, json.dumps(tags), viewer))
        mem_id = cursor.lastrowid
        self.conn.commit()
        
        # Save to vector memory
        self.vector_memory.embed_and_store(
            str(mem_id), 
            content, 
            metadata={"type": type, "viewer": viewer or "None", "weight": emotional_weight}
        )

    def recall_recent(self, limit: int = 10, 
                      type: str = None,
                      since_last_stream: bool = False) -> list:
        if type:
            cursor = self.conn.execute("""
                SELECT content, type, timestamp, emotional_weight
                FROM memories WHERE type = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (type, limit))
        else:
            cursor = self.conn.execute("""
                SELECT content, type, timestamp, emotional_weight
                FROM memories ORDER BY timestamp DESC LIMIT ?
            """, (limit,))
        rows = cursor.fetchall()
        return [{"content": r[0], "type": r[1], 
                 "timestamp": r[2], "weight": r[3]} for r in rows]

    def recall_about(self, topic: str, limit: int = 3) -> list:
        """Simple keyword search in memories"""
        words = topic.lower().split()[:3]
        results = []
        for word in words:
            cursor = self.conn.execute("""
                SELECT content FROM memories 
                WHERE LOWER(content) LIKE ?
                ORDER BY emotional_weight DESC LIMIT ?
            """, (f"%{word}%", limit))
            results.extend([r[0] for r in cursor.fetchall()])
        return list(set(results))[:limit]

    def recall_semantic(self, query: str, limit: int = 3) -> list:
        """Semantic vector search through memories"""
        results = self.vector_memory.search(query, top_k=limit)
        return [r["content"] for r in results]

    def get_contextual_memories(self, context_text: str) -> str:
        """Fetches semantically relevant memories to inject into LLM context."""
        if not context_text or len(context_text.strip()) < 5:
            return ""
        results = self.recall_semantic(context_text, limit=3)
        if not results:
            return ""
        return "\n".join([f"- {res}" for res in results])

    def recall_viewer(self, username: str) -> dict:
        cursor = self.conn.execute(
            "SELECT * FROM viewers WHERE username = ?", (username,))
        row = cursor.fetchone()
        if not row:
            return {}
        cols = ["username","first_seen","last_seen","times_chatted",
                "relationship_level","personality_notes",
                "inside_jokes","memorable_moments","sentiment"]
        return dict(zip(cols, row))

    def update_viewer(self, username: str, message: str,
                      sentiment_delta: float = 0):
        now = datetime.now().isoformat()
        existing = self.recall_viewer(username)
        if not existing:
            self.conn.execute("""
                INSERT INTO viewers 
                (username, first_seen, last_seen, times_chatted, sentiment)
                VALUES (?, ?, ?, 1, 0.5)
            """, (username, now, now))
        else:
            new_sentiment = max(0, min(1, 
                existing.get("sentiment", 0.5) + sentiment_delta))
            self.conn.execute("""
                UPDATE viewers SET 
                last_seen = ?,
                times_chatted = times_chatted + 1,
                sentiment = ?
                WHERE username = ?
            """, (now, new_sentiment, username))
        self.conn.commit()

    def add_life_entry(self, entry_type: str, content: str,
                       mood: str, triggered_by: str = None):
        self.conn.execute("""
            INSERT INTO aiko_life 
            (timestamp, entry_type, content, mood, triggered_by)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), entry_type, 
              content, mood, triggered_by))
        self.conn.commit()

    def get_life_context(self, limit: int = 5) -> str:
        cursor = self.conn.execute("""
            SELECT content, entry_type FROM aiko_life
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        if not rows:
            return "Sin pensamientos recientes."
        return "\n".join([f"[{r[1]}] {r[0]}" for r in rows])

    def get_random_old_memory(self) -> str:
        cursor = self.conn.execute("""
            SELECT content FROM memories
            WHERE emotional_weight > 0.6
            ORDER BY RANDOM() LIMIT 1
        """)
        row = cursor.fetchone()
        return row[0] if row else None

    def get_viewer_relationship_summary(self, username: str) -> str:
        viewer = self.recall_viewer(username)
        if not viewer:
            return f"{username} es nuevo, no lo conozco bien."
        level = viewer.get("relationship_level", "desconocido")
        chats = viewer.get("times_chatted", 0)
        notes = viewer.get("personality_notes", "")
        return (f"{username}: nivel '{level}', "
                f"ha chateado {chats} veces. {notes}")

    def save_gacha_pull(self, banner: str, character: str,
                        rarity: str, pity: int,
                        reaction: str, voter: str = None):
        self.conn.execute("""
            INSERT INTO gacha_history
            (timestamp, banner, result_character, rarity, 
             pity_count, aiko_reaction, viewer_who_voted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), banner, character,
              rarity, pity, reaction, voter))
        self.conn.commit()

    def save_tribunal_case(self, case_text: str, submitted_by: str,
                           verdict: str, reasoning: str,
                           memorable: bool = False):
        self.conn.execute("""
            INSERT INTO tribunal_history
            (timestamp, case_text, submitted_by, verdict, 
             reasoning, memorable)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), case_text, submitted_by,
              verdict, reasoning, int(memorable)))
        self.conn.commit()
        if memorable or random.random() < 0.3:
            self.remember(
                type="tribunal_verdict",
                content=f"Juzgué a {submitted_by}: '{case_text}' → {verdict}",
                emotional_weight=0.7 if memorable else 0.5,
                tags=["tribunal"],
                viewer=submitted_by
            )

    def close(self):
        self.conn.close()
