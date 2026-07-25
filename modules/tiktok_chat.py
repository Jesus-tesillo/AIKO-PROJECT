"""
tiktok_chat.py — Aiko TikTok Live Chat Reader v2.

Conexión directa via WebSocket usando la librería TikTokLive.
Sin TikFinity. Sin API limits. Reconexión persistente con backoff.

Eventos soportados:
  - CommentEvent  → mensajes de chat
  - GiftEvent     → regalos (con acumulación de streaks)
  - FollowEvent   → nuevos seguidores (batching inteligente)
  - LikeEvent     → likes (milestones: 100, 500, 1000)
  - SubscribeEvent→ suscripciones
  - RoomUserSeqEvent → conteo de viewers en tiempo real
  - LiveEndEvent  → fin del live
"""

import asyncio
import queue
import random
import threading
import time
from datetime import datetime


class TikTokChatReader:
    """Lector de chat de TikTok Live vía WebSocket directo."""

    RECONNECT_BASE_DELAY = 8
    MAX_RECONNECT_DELAY = 120
    MAX_RECONNECTS = 50

    # Diamantes mínimos para reaccionar a un regalo
    GIFT_MIN_DIAMONDS = 1

    def __init__(self, username: str = "", min_interval: float = 0.3,
                 event_callback=None):
        """
        Args:
            username:       TikTok username (sin @).
            min_interval:   Mínimo entre mensajes de chat procesados.
            event_callback: fn(event_type: str, data: dict) — handler
                           externo para eventos (regalos, follows, etc).
                           Si es None, los eventos se encolan en event_queue.
        """
        self.username = username.strip().lstrip("@")
        self.message_queue = queue.Queue(maxsize=200)
        self.event_queue = queue.Queue(maxsize=50)
        self.connected = False
        self._running = False
        self._thread = None
        self._client = None
        self._loop = None
        self._message_count = 0
        self._min_interval = min_interval
        self._last_msg_time = 0.0
        self._reconnect_count = 0

        # Callback externo para eventos (alternativa a la cola)
        self._event_callback = event_callback

        # Viewer count en tiempo real
        self._viewer_count = 0

        # Detección de chat activo: timestamps recientes de mensajes
        self._recent_msg_times: list = []

        # Batching de follows: evitar reaccionar a cada follow individual
        self._follow_queue: list = []
        self._last_follow_flush = 0.0
        self._follow_batch_interval = 15.0  # agrupar follows cada 15s

        # Batching de likes: milestones
        self._like_batch = 0
        self._last_like_reaction = 0.0

        # Streak cache para regalos: key = f"{uid}_{gift_name}"
        self._streak_cache: dict = {}

        print(f"[TikTok] Chat reader configurado para @{self.username}")

    # ──────────────────────────────────────────────────
    #  API PÚBLICA
    # ──────────────────────────────────────────────────

    def start(self):
        """Inicia la conexión en un hilo daemon separado."""
        if self._running:
            return
        if not self.username:
            print("[TikTok] ⚠ Configura tiktok.username en config.yaml")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="TikTokChat",
        )
        self._thread.start()

    def stop(self):
        """Detiene la conexión y el event loop de forma limpia."""
        self._running = False
        if self._client and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._client.disconnect(), self._loop
                ).result(timeout=3)
            except Exception:
                pass
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def get_message(self) -> dict:
        """Devuelve el siguiente mensaje de chat (no bloqueante)."""
        try:
            return self.message_queue.get_nowait()
        except queue.Empty:
            return None

    def has_messages(self) -> bool:
        return not self.message_queue.empty()

    def get_event(self) -> dict:
        """Devuelve el siguiente evento de regalo/follow/sub (no bloqueante)."""
        try:
            return self.event_queue.get_nowait()
        except queue.Empty:
            return None

    def get_message_count(self) -> int:
        return self._message_count

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def viewer_count(self) -> int:
        return self._viewer_count

    def chat_is_active(self, window_secs: float = 30.0) -> bool:
        """True si hubo mensajes en los últimos `window_secs` segundos."""
        now = time.time()
        self._recent_msg_times = [
            t for t in self._recent_msg_times if now - t < window_secs
        ]
        return len(self._recent_msg_times) > 0

    def messages_per_minute(self) -> float:
        """Tasa de mensajes por minuto en la última ventana de 60s."""
        now = time.time()
        self._recent_msg_times = [
            t for t in self._recent_msg_times if now - t < 60.0
        ]
        return len(self._recent_msg_times)

    # ──────────────────────────────────────────────────
    #  LOOP PRINCIPAL DEL HILO
    # ──────────────────────────────────────────────────

    def _run_loop(self):
        """
        Hilo daemon: crea un event loop propio y gestiona reconexiones
        con backoff exponencial (capped a MAX_RECONNECT_DELAY).
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        while self._running:
            try:
                self._loop.run_until_complete(self._connect_and_listen())
                # Salida limpia (desconexión normal)
                self._reconnect_count = 0
            except Exception as e:
                err_str = str(e)
                err = err_str if err_str.strip() else repr(e)
                self.connected = False

                # Mensajes de error claros y accionables
                err_lower = err.lower()
                if "offline" in err_lower or "not_found" in err_lower:
                    print(
                        f"[TikTok] @{self.username} no está en Live. "
                        f"Reintentando..."
                    )
                elif "connect" in err_lower or "timeout" in err_lower:
                    print(f"[TikTok] Sin conexión a TikTok.")
                elif "rate" in err_lower or "429" in err:
                    print(f"[TikTok] Rate limit de TikTok.")
                else:
                    print(f"[TikTok] Error: {err[:120]}")
            finally:
                self.connected = False

            if not self._running:
                break

            self._reconnect_count += 1
            if self._reconnect_count > self.MAX_RECONNECTS:
                print("[TikTok] Demasiados reintentos, deteniendo.")
                break

            delay = min(
                self.RECONNECT_BASE_DELAY * min(self._reconnect_count, 5),
                self.MAX_RECONNECT_DELAY,
            )
            print(f"[TikTok] Reintentando en {int(delay)}s "
                  f"(#{self._reconnect_count})...")
            time.sleep(delay)

        # Cerrar el loop al salir
        try:
            self._loop.close()
        except Exception:
            pass

    # ──────────────────────────────────────────────────
    #  CONEXIÓN Y HANDLERS DE EVENTOS
    # ──────────────────────────────────────────────────

    async def _connect_and_listen(self):
        """
        Crea el cliente TikTokLive, registra handlers y ejecuta.
        Compatible con TikTokLive 6.x (API asíncrona).
        """
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import (
            ConnectEvent, DisconnectEvent, LiveEndEvent,
            CommentEvent, GiftEvent, FollowEvent,
            LikeEvent, SubscribeEvent, RoomUserSeqEvent,
        )

        client = TikTokLiveClient(unique_id=f"@{self.username}")
        self._client = client

        # ── Conexión / Desconexión ──────────────────────────────────

        @client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            self.connected = True
            self._reconnect_count = 0
            print(f"[TikTok] ✓ Conectado al live de @{self.username}")
            self._emit_event("tiktok_connected", {
                "username": self.username,
                "timestamp": datetime.now().isoformat(),
            })

        @client.on(DisconnectEvent)
        async def on_disconnect(event: DisconnectEvent):
            self.connected = False
            print(f"[TikTok] Desconectado del live de @{self.username}")

        @client.on(LiveEndEvent)
        async def on_live_end(event: LiveEndEvent):
            self.connected = False
            print("[TikTok] El live terminó")
            self._emit_event("tiktok_live_end", {})

        # ── Mensajes de chat ────────────────────────────────────────

        @client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            now = time.time()

            # Rate limiting por tiempo mínimo entre mensajes
            if now - self._last_msg_time < self._min_interval:
                return
            self._last_msg_time = now

            # Extraer datos del usuario de forma segura
            try:
                user_obj = event.user
                username = (
                    getattr(user_obj, "unique_id", None)
                    or getattr(user_obj, "nickname", None)
                    or "anon"
                )
                comment = event.comment or ""
            except Exception:
                username = "anon"
                comment = str(getattr(event, "comment", ""))

            if not comment.strip() or len(comment.strip()) < 2:
                return

            msg = {
                "user": username,
                "message": comment.strip(),
                "platform": "tiktok",
                "timestamp": now,
            }

            # Registrar para métricas de actividad
            self._recent_msg_times.append(now)

            print(f"[TikTok] 💬 {username}: {comment.strip()[:80]}")

            try:
                self.message_queue.put_nowait(msg)
                self._message_count += 1
            except queue.Full:
                # Descartar el más viejo para hacer hueco al nuevo
                try:
                    self.message_queue.get_nowait()
                    self.message_queue.put_nowait(msg)
                except Exception:
                    pass

        # ── Regalos ─────────────────────────────────────────────────

        @client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            try:
                user_obj = event.user
                username = (
                    getattr(user_obj, "unique_id", None)
                    or getattr(user_obj, "nickname", None)
                    or "anon"
                )

                gift_obj = event.gift
                gift_name = getattr(gift_obj, "name", None) or "regalo"
                repeat_count = getattr(event, "repeat_count", 1) or 1
                diamond_count = getattr(gift_obj, "diamond_count", 0) or 0

                # Streaking: acumular hasta que termine el streak
                if event.streaking:
                    key = f"{username}_{gift_name}"
                    self._streak_cache[key] = repeat_count
                    return

                # Streak terminó o regalo no-streakable: procesar
                key = f"{username}_{gift_name}"
                count = self._streak_cache.pop(key, repeat_count)
                total_diamonds = diamond_count * count

                if total_diamonds < self.GIFT_MIN_DIAMONDS:
                    return

                print(f"[TikTok] 🎁 {username}: {gift_name} x{count} "
                      f"({total_diamonds}💎)")

                evt = {
                    "type": "gift",
                    "user": username,
                    "gift": str(gift_name),
                    "count": count,
                    "diamonds": total_diamonds,
                    "reaction_level": self._gift_level(total_diamonds),
                    "platform": "tiktok",
                }
                self._emit_event("tiktok_gift", evt)
                self._enqueue_event(evt)

            except Exception as e:
                print(f"[TikTok] Error procesando regalo: {e}")

        # ── Follows ─────────────────────────────────────────────────

        @client.on(FollowEvent)
        async def on_follow(event: FollowEvent):
            try:
                user_obj = event.user
                username = (
                    getattr(user_obj, "unique_id", None)
                    or getattr(user_obj, "nickname", None)
                    or "anon"
                )

                self._follow_queue.append(username)
                now = time.time()

                # Batching: agrupar follows para no reaccionar 1 por 1
                if now - self._last_follow_flush < self._follow_batch_interval:
                    return

                self._last_follow_flush = now
                batch = self._follow_queue[:]
                self._follow_queue.clear()

                if len(batch) == 1:
                    print(f"[TikTok] 👤 Nuevo seguidor: {batch[0]}")
                    evt = {
                        "type": "follow",
                        "user": batch[0],
                        "platform": "tiktok",
                    }
                    self._emit_event("tiktok_follow", evt)
                    self._enqueue_event(evt)
                elif batch:
                    print(f"[TikTok] 👤 {len(batch)} nuevos seguidores")
                    evt = {
                        "type": "follow_batch",
                        "user": batch[0],
                        "usernames": batch,
                        "count": len(batch),
                        "platform": "tiktok",
                    }
                    self._emit_event("tiktok_follow_batch", evt)
                    self._enqueue_event(evt)

            except Exception as e:
                print(f"[TikTok] Error follow: {e}")

        # ── Likes ───────────────────────────────────────────────────

        @client.on(LikeEvent)
        async def on_like(event: LikeEvent):
            try:
                like_count = getattr(event, "count", 1) or 1
                self._like_batch += like_count
                now = time.time()

                # Solo reaccionar cada 30s y en milestones
                if now - self._last_like_reaction < 30:
                    return

                if self._like_batch >= 100:
                    username = (
                        getattr(event.user, "unique_id", None)
                        or getattr(event.user, "nickname", "alguien")
                    )
                    likes = self._like_batch
                    self._like_batch = 0
                    self._last_like_reaction = now

                    self._emit_event("tiktok_likes_milestone", {
                        "count": likes,
                        "username": username,
                    })

            except Exception:
                pass

        # ── Suscripciones ───────────────────────────────────────────

        @client.on(SubscribeEvent)
        async def on_subscribe(event: SubscribeEvent):
            try:
                user_obj = event.user
                username = (
                    getattr(user_obj, "unique_id", None)
                    or getattr(user_obj, "nickname", None)
                    or "anon"
                )
                print(f"[TikTok] ⭐ Nueva suscripción: {username}")
                evt = {
                    "type": "subscribe",
                    "user": username,
                    "platform": "tiktok",
                }
                self._emit_event("tiktok_subscribe", evt)
                self._enqueue_event(evt)
            except Exception:
                pass

        # ── Viewer Count ────────────────────────────────────────────

        @client.on(RoomUserSeqEvent)
        async def on_viewers(event: RoomUserSeqEvent):
            try:
                self._viewer_count = getattr(event, "total_user", 0) or 0
            except Exception:
                pass

        # ── Ejecutar el cliente (bloqueante hasta desconexión) ──────
        print(f"[TikTok] Conectando al live de @{self.username}...")
        await client.connect()

    # ──────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────

    def _emit_event(self, event_type: str, data: dict):
        """Emite un evento al callback externo si existe."""
        if self._event_callback:
            try:
                self._event_callback(event_type, data)
            except Exception as e:
                print(f"[TikTok] Error en event_callback: {e}")

    def _enqueue_event(self, evt: dict):
        """Encola un evento para polling (compatibilidad con main.py)."""
        try:
            self.event_queue.put_nowait(evt)
        except queue.Full:
            pass

    @staticmethod
    def _gift_level(diamonds: int) -> str:
        """Clasifica el regalo por nivel de generosidad."""
        thresholds = [
            (5000, "legendario"),
            (1000, "epico"),
            (500,  "muy_generoso"),
            (100,  "generoso"),
            (10,   "simpatico"),
            (1,    "pequeno"),
        ]
        for threshold, label in thresholds:
            if diamonds >= threshold:
                return label
        return "pequeno"
