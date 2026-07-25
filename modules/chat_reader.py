"""
chat_reader.py - Twitch chat reader using raw IRC over WebSocket.

Connects directly to Twitch IRC (no twitchio dependency issues).
Only needs an OAuth token — no client_id, client_secret, or bot_id.
Puts incoming messages into a thread-safe queue for processing.
"""

import asyncio
import threading
import queue
import time
import re


class ChatReader:
    """Reads Twitch chat messages via raw IRC and queues them for processing."""

    def __init__(self, channel: str, bot_name: str, token: str,
                 min_interval: float = 0.8):
        """
        Args:
            channel: Twitch channel name to join (lowercase).
            bot_name: The bot's display name (to filter self-messages).
            token: Twitch OAuth token (oauth:xxxxx).
            min_interval: Minimum seconds between queued messages (spam filter).
        """
        self.channel = channel.lower().strip()
        self.bot_name = bot_name.lower().strip()
        self.token = token.strip()
        self.min_interval = min_interval
        self.message_queue = queue.Queue(maxsize=100)
        self.connected = False
        self._thread = None
        self._last_queue_time = 0
        self._running = False
        self._message_count = 0

        # Ensure token starts with oauth:
        if not self.token.startswith("oauth:"):
            self.token = "oauth:" + self.token

        print(f"[Chat] Configurado para canal: #{self.channel}")

    def start(self):
        """Start the chat reader in a background thread."""
        if self._running:
            print("[Chat] Ya está corriendo.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_irc,
            daemon=True,
            name="ChatReader"
        )
        self._thread.start()
        print("[Chat] Iniciando conexión IRC de Twitch...")

    def stop(self):
        """Stop the chat reader."""
        self._running = False
        self.connected = False
        print("[Chat] Detenido.")

    def _run_irc(self):
        """Connect to Twitch IRC using raw WebSocket and read messages."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self._running:
            try:
                loop.run_until_complete(self._irc_loop())
            except Exception as e:
                if self._running:
                    print(f"[Chat] ✗ Desconectado: {e}. Reconectando en 5s...")
                    self.connected = False
                    time.sleep(5)

    async def _irc_loop(self):
        """Main IRC loop using websockets library."""
        try:
            import websockets
        except ImportError:
            print("[Chat] ✗ websockets no instalado. Ejecuta: pip install websockets")
            self._running = False
            return

        uri = "wss://irc-ws.chat.twitch.tv:443"
        print(f"[Chat] Conectando al IRC de Twitch ({uri})...")

        async with websockets.connect(uri) as ws:
            # 1. CAP REQ debe ir ANTES que PASS/NICK (requerimiento de Twitch)
            await ws.send("CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands")
            await ws.send(f"PASS {self.token}")
            await ws.send(f"NICK {self.bot_name}")

            # 2. Esperar confirmación del servidor (001 = bienvenida, NOTICE = error)
            auth_ok = False
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 12
            while loop.time() < deadline and not auth_ok:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    break
                for line in raw.strip().split("\r\n"):
                    if not line:
                        continue
                    print(f"[Chat] IRC→ {line[:140]}")
                    if " 001 " in line:          # Welcome = auth OK
                        auth_ok = True
                    if "Login authentication failed" in line:
                        raise ConnectionError(
                            "[Chat] ✗ Token de Twitch inválido o expirado. "
                            "Genera uno nuevo en https://twitchapps.com/tmi/"
                        )
                    if "Improperly formatted auth" in line:
                        raise ConnectionError("[Chat] ✗ Formato de token incorrecto.")

            if not auth_ok:
                raise ConnectionError(
                    "[Chat] ✗ No se recibió confirmación del servidor Twitch IRC."
                )

            # 3. Auth confirmada — unirse al canal
            await ws.send(f"JOIN #{self.channel}")
            self.connected = True
            print(f"[Chat] ✓ Autenticado como {self.bot_name}")
            print(f"[Chat] ✓ Unido al canal: #{self.channel}")

            # 4. Loop de lectura de mensajes
            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    await ws.send("PING :tmi.twitch.tv")  # keepalive real
                    continue

                for line in raw.strip().split("\r\n"):
                    if not line:
                        continue
                    if line.startswith("PING"):
                        await ws.send("PONG :tmi.twitch.tv")
                        continue
                    self._parse_message(line)

    # IRC message pattern: :username!user@user.tmi.twitch.tv PRIVMSG #channel :message
    _MSG_PATTERN = re.compile(
        r":(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.+)"
    )

    def _parse_message(self, line: str):
        """Parse a raw IRC line and queue the message if valid."""
        match = self._MSG_PATTERN.search(line)
        if not match:
            return

        username = match.group(1)
        message_text = match.group(2).strip()

        # Skip bot's own messages
        if username.lower() == self.bot_name:
            return

        # Skip empty messages
        if not message_text:
            return

        # Rate limiting
        now = time.time()
        if now - self._last_queue_time < self.min_interval:
            return

        msg_data = {
            "user": username,
            "message": message_text,
            "timestamp": now
        }

        try:
            self.message_queue.put_nowait(msg_data)
            self._last_queue_time = now
            self._message_count += 1
            print(f"[Chat] 💬 {username}: {message_text}")
        except queue.Full:
            pass

    def get_message(self) -> dict:
        """Get the next message from the queue (non-blocking)."""
        try:
            return self.message_queue.get_nowait()
        except queue.Empty:
            return None

    def has_messages(self) -> bool:
        """Return True if there are pending messages."""
        return not self.message_queue.empty()

    def get_message_count(self) -> int:
        """Return total number of messages received."""
        return self._message_count


class MultiChatReader:
    """Aggregates messages from multiple chat readers (Twitch, TikTok, etc) into a single queue.
    
    Provides the exact same interface as ChatReader so main.py can use it seamlessly.
    """
    
    def __init__(self, readers=None):
        self.readers = readers or []
    
    def add_reader(self, reader):
        self.readers.append(reader)
        
    def start(self):
        for r in self.readers:
            r.start()
            
    def stop(self):
        for r in self.readers:
            r.stop()
            
    @property
    def connected(self):
        return any(r.connected for r in self.readers)
        
    def get_message(self) -> dict:
        # Check all readers for a message in round-robin fashion
        # To be fair, could shuffle them, but a simple loop is fine
        for r in self.readers:
            msg = r.get_message()
            if msg:
                return msg
        return None

    def has_messages(self) -> bool:
        return any(hasattr(r, 'has_messages') and r.has_messages() for r in self.readers)
        
    def get_message_count(self) -> int:
        return sum(r.get_message_count() for r in self.readers)
