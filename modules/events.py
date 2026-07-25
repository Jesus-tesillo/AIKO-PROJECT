"""
events.py — Stream Event Reactions Module.

Monitors a local JSON file for stream events (new follower, subscriber,
raid, bits donation) and generates special LLM prompts for each event.
Events can be written by a separate Twitch EventSub webhook script.
"""

import json
import os
import threading
import time
from datetime import datetime


class StreamEvents:
    """Monitor and process stream events from a local JSON file."""

    def __init__(self, events_file: str = "events/stream_events.json",
                 poll_interval: float = 3.0):
        """
        Args:
            events_file: Path to the events JSON file.
            poll_interval: How often to check for new events (seconds).
        """
        self.events_file = events_file
        self.poll_interval = poll_interval
        self._processed_count = 0
        self._pending_events = []
        self._lock = threading.Lock()
        self._thread = None
        self._running = False

        # Create events directory and file if they don't exist
        events_dir = os.path.dirname(self.events_file)
        if events_dir:
            os.makedirs(events_dir, exist_ok=True)
            
        if not os.path.exists(self.events_file):
            with open(self.events_file, "w", encoding="utf-8") as f:
                json.dump([], f)
        else:
            # Sync _processed_count with the length of existing events so it ignores past alerts.
            try:
                with open(self.events_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        events = json.loads(content)
                        if isinstance(events, list):
                            self._processed_count = len(events)
            except Exception:
                pass

    def start(self):
        """Start monitoring the events file in a background thread."""
        if self._running:
            return

        print(f"[Eventos] Monitoreando: {self.events_file} (Omitiendo {self._processed_count} viejos)")
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="StreamEvents"
        )
        self._thread.start()
        print("[Eventos] ✓ Monitor de eventos iniciado.")

    def stop(self):
        """Stop the event monitor."""
        self._running = False
        print("[Eventos] Detenido.")

    def _monitor_loop(self):
        """Continuously poll the events file for new entries."""
        while self._running:
            try:
                self._check_events()
            except Exception as e:
                print(f"[Eventos] Error revisando eventos: {e}")
            time.sleep(self.poll_interval)

    def _check_events(self):
        """Read events file and queue any new (unprocessed) events."""
        try:
            if not os.path.exists(self.events_file):
                return

            with open(self.events_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return
                events = json.loads(content)

            if not isinstance(events, list):
                return

            # Process only new events (beyond what we've already processed)
            new_events = events[self._processed_count:]
            if new_events:
                with self._lock:
                    for event in new_events:
                        self._pending_events.append(event)
                        event_type = event.get("type", "unknown")
                        user = event.get("user", "someone")
                        print(f"[Eventos] 🎉 Nuevo evento: {event_type} de {user}")

                self._processed_count = len(events)

        except json.JSONDecodeError:
            pass  # File might be mid-write
        except Exception as e:
            print(f"[Eventos] Error leyendo archivo de eventos: {e}")

    def get_next_event(self) -> dict:
        """
        Get the next pending event for processing.

        Returns:
            Event dict or None if no pending events.
        """
        with self._lock:
            if self._pending_events:
                return self._pending_events.pop(0)
        return None

    def has_events(self) -> bool:
        """Check if there are pending events to process."""
        with self._lock:
            return len(self._pending_events) > 0

    def generate_event_prompt(self, event: dict) -> str:
        """
        Generate a natural context prompt for a stream event.
        
        Instead of commanding the LLM to "react with enthusiasm", we inject
        the event as conversational context so Aiko responds organically
        in her own personality.

        Args:
            event: Event dict with 'type', 'user', optionally 'tier', 'amount'.

        Returns:
            A context string that the LLM will naturally respond to.
        """
        event_type = event.get("type", "unknown")
        user = event.get("user", "alguien")
        tier = event.get("tier", "")
        amount = event.get("amount", "")

        prompts = {
            "new_follower": (
                f"[NOTIFICACIÓN DE STREAM] {user} acaba de seguir el canal. "
                f"Salúdalo brevemente como si lo vieras entrar al stream."
            ),
            "new_subscriber": (
                f"[NOTIFICACIÓN DE STREAM] {user} se acaba de suscribir al canal. "
                f"Agradécele a tu manera, sin sonar forzada ni genérica."
            ),
            "raid": (
                f"[NOTIFICACIÓN DE STREAM] {user} está raideando el stream con gente nueva. "
                f"Dales la bienvenida como tú sabes."
            ),
            "bits_donation": (
                f"[NOTIFICACIÓN DE STREAM] {user} acaba de enviar un regalo: {amount}. "
                f"Reacciona como si alguien te diera un regalo en persona."
            ),
            "gift_sub": (
                f"[NOTIFICACIÓN DE STREAM] {user} regaló una suscripción en el canal. "
                f"Agradécele genuinamente."
            ),
        }

        return prompts.get(event_type, (
            f"[NOTIFICACIÓN DE STREAM] Algo pasó con {user} en el stream."
        ))

    def add_event(self, event_type: str, user: str, **kwargs):
        """
        Manually add an event (useful for testing).

        Args:
            event_type: Type of event (new_follower, new_subscriber, etc.)
            user: Username associated with the event.
            **kwargs: Additional event data (tier, amount, etc.)
        """
        event = {
            "type": event_type,
            "user": user,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }

        try:
            # Read existing events
            events = []
            if os.path.exists(self.events_file):
                with open(self.events_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        events = json.loads(content)

            # Append new event
            events.append(event)

            # Write back
            with open(self.events_file, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2, ensure_ascii=False)

            print(f"[Eventos] Evento añadido: {event_type} de {user}")

        except Exception as e:
            print(f"[Eventos] Error añadiendo evento: {e}")

    def clear_events(self):
        """Clear all events from the file."""
        try:
            with open(self.events_file, "w", encoding="utf-8") as f:
                json.dump([], f)
            self._processed_count = 0
            with self._lock:
                self._pending_events.clear()
            print("[Eventos] Todos los eventos limpiados.")
        except Exception as e:
            print(f"[Eventos] Error limpiando eventos: {e}")
