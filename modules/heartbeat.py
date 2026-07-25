import time
from datetime import datetime

class HeartbeatSystem:
    """
    Heartbeat System for LLM Grounding.
    Tracks stream uptime, real-world time, and dynamic metrics to keep the VTuber grounded in reality.
    This prevents the LLM from losing track of time during long streaming sessions.
    """
    def __init__(self):
        self.stream_start_time = None
        self.metrics = {"messages_read": 0, "events_triggered": 0}

    def start_stream(self):
        self.stream_start_time = time.time()
        self.metrics = {"messages_read": 0, "events_triggered": 0}

    def stop_stream(self):
        self.stream_start_time = None

    def log_event(self, event_type: str):
        self.metrics[event_type] = self.metrics.get(event_type, 0) + 1

    def get_heartbeat_context(self) -> str:
        """Genera contexto de stream para inyectar en el LLM.
        NO incluye fecha/hora — eso ya lo pone build_system_prompt."""
        if not self.stream_start_time:
            return ""

        uptime_seconds = time.time() - self.stream_start_time
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)

        if hours > 0:
            uptime_str = f"{hours}h {minutes}m"
        else:
            uptime_str = f"{minutes}m"

        msgs = self.metrics.get('messages_read', 0)
        evts = self.metrics.get('events_triggered', 0)

        return f"UPTIME STREAM: {uptime_str} | Mensajes leídos: {msgs} | Eventos: {evts}"
