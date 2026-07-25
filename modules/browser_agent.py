"""
browser_agent.py — Navegador controlado por el Prompter.

El navegador se abre y espera. Navega SOLO cuando el Prompter
decide (action='browse'). Una sesión = 1-3 sitios, reacciones, fin.
"""

import asyncio
import json
import os
import random
import threading
import time

os.environ["NODE_OPTIONS"] = "--no-deprecation"


class BrowserAgent:

    FAVORITE_SITES = [
        {
            "url": "https://www.youtube.com/feed/subscriptions",
            "platform": "youtube",
            "mood_match": ["chill", "bored", "neutral", "focused"],
            "weight": 40,
            "description": "ver YouTube",
            "announce": [
                "voy a ver qué subieron en YouTube",
                "déjenme checar YouTube un momento",
                "a ver qué hay en YouTube",
            ],
        },
        {
            "url": "https://x.com/home",
            "platform": "twitter",
            "mood_match": ["gremlin", "bored", "neutral", "hyped"],
            "weight": 30,
            "description": "revisar Twitter",
            "announce": [
                "a ver qué dramas hay en Twitter",
                "vamos a Twitter",
                "a ver qué estupideces están pasando",
            ],
        },
        {
            "url": "https://www.tiktok.com/foryou",
            "platform": "tiktok",
            "mood_match": ["hyped", "bored", "gremlin", "chill"],
            "weight": 25,
            "description": "ver TikToks",
            "announce": [
                "vamos al FYP",
                "a ver TikToks",
                "déjenme ver el for you",
            ],
        },
        {
            "url": "https://www.reddit.com/r/anime",
            "platform": "reddit",
            "mood_match": ["focused", "chill", "neutral"],
            "weight": 10,
            "description": "ver Reddit",
            "announce": [
                "a ver qué hay en reddit",
                "déjenme checar r/anime",
            ],
        },
    ]

    SCROLL_INTERVALS = {
        "youtube": 4.5,
        "twitter": 3.5,
        "tiktok":  5.0,
        "reddit":  3.5,
        "default": 4.0,
    }

    def __init__(self, config: dict, intelligence=None):
        self.config       = config or {}
        self.intelligence = intelligence

        self.profile_path  = os.path.abspath(
            self.config.get("profile_path", "data/aiko_browser_profile")
        )
        self.window_width  = self.config.get("window_width",  1280)
        self.window_height = self.config.get("window_height", 720)

        # Merge config extra sites
        for url in (self.config.get("favorite_sites") or []):
            if isinstance(url, str) and not any(
                s["url"] == url for s in self.FAVORITE_SITES
            ):
                platform = self._detect_platform(url)
                self.FAVORITE_SITES.append({
                    "url": url, "platform": platform,
                    "mood_match": ["bored", "neutral"], "weight": 15,
                    "description": f"ver {platform}",
                    "announce": [f"a ver {platform}"],
                })

        # Browser state
        self._browser_context = None
        self.page             = None
        self._playwright      = None
        self._running         = False
        self._current_url     = ""
        self.current_platform = None
        self.is_browsing      = False  # True durante una sesión activa

        # Contexto actual (para que el chat lo referencie)
        self._current_content: dict = {}

        # Flags de concurrencia
        self._aiko_speaking    = threading.Event()  # set mientras Aiko habla
        self._browse_interrupt = threading.Event()  # set para parar sesión

        # Callbacks
        self._tts_callback    = None   # func(text, emotion)
        self._prompter_signal = None   # objeto Prompter

        # Timing de reacciones
        self._last_reaction_time    = 0.0
        self._min_reaction_interval = 15.0   # min seg entre reacciones del browser

        # Loop
        self._loop   = None
        self._thread = None

        os.makedirs(self.profile_path, exist_ok=True)

    # ──────────────────────────────────────────────────
    #  CALLBACKS PÚBLICOS
    # ──────────────────────────────────────────────────

    def set_tts_callback(self, callback):
        """callback(text: str, emotion: str = 'neutral')"""
        self._tts_callback = callback

    def set_prompter_signal(self, prompter):
        self._prompter_signal = prompter

    def set_speaking(self, speaking: bool):
        """main.py llama esto cuando Aiko empieza/termina de hablar."""
        if speaking:
            self._aiko_speaking.set()
        else:
            self._aiko_speaking.clear()

    def get_current_context(self) -> str:
        """Qué está viendo Aiko ahora mismo (para inyectar en respuestas de chat)."""
        c = self._current_content
        if not c:
            return ""
        platform = c.get("platform", "")
        state    = c.get("page_state", "")
        summary  = c.get("summary", "")
        if summary:
            return f"[Aiko está en {platform} viendo: '{summary[:70]}']"
        if platform:
            return f"[Aiko está navegando {platform} ({state})]"
        return ""

    # ──────────────────────────────────────────────────
    #  INICIO / PARADA
    # ──────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        try:
            from playwright.async_api import async_playwright  # noqa
        except ImportError:
            print("[Browser] ✗ Playwright no instalado. "
                  "Ejecuta: pip install playwright && playwright install chromium")
            return
        os.makedirs(self.profile_path, exist_ok=True)
        self._running = True
        self._thread  = threading.Thread(
            target=self._run_async_loop, daemon=True, name="BrowserAgent"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self.is_browsing = False
        self._browse_interrupt.set()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_playwright(), self._loop)

    # ──────────────────────────────────────────────────
    #  PROPIEDADES DINÁMICAS
    # ──────────────────────────────────────────────────

    @property
    def _mood(self) -> str:
        if self._prompter_signal:
            return getattr(self._prompter_signal, "current_mood", "neutral")
        return "neutral"

    def _can_react_now(self) -> bool:
        """¿Puede Aiko hablar sobre el navegador ahora?"""
        if self._aiko_speaking.is_set():
            return False
        if time.time() - self._last_reaction_time < self._min_reaction_interval:
            return False
        return True

    def _speak(self, text: str, emotion: str = "neutral"):
        if not text or not text.strip():
            return
        if self._tts_callback:
            try:
                self._tts_callback(text, emotion)
            except Exception as e:
                print(f"[Browser] TTS error: {e}")
        else:
            print(f"[Browser] (sin TTS): {text}")

    # ──────────────────────────────────────────────────
    #  LOOP PRINCIPAL — solo mantiene el event loop vivo
    # ──────────────────────────────────────────────────

    def _run_async_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._init_playwright())
            # Idle: solo mantiene vivo el loop. La navegación la decide el Prompter.
            self._loop.run_until_complete(self._idle_keep_alive())
        except Exception as e:
            print(f"[Browser] Error fatal: {e}")
        finally:
            self._loop.close()

    async def _idle_keep_alive(self):
        """Mantiene el event loop vivo sin navegar. El Prompter llama start_browsing()."""
        while self._running:
            await asyncio.sleep(0.5)

    # ──────────────────────────────────────────────────
    #  SESIÓN DE NAVEGACIÓN (activada por el Prompter)
    # ──────────────────────────────────────────────────

    def start_browsing(self, url: str = None, mood: str = "neutral") -> bool:
        """
        El Prompter llama esto cuando Aiko decide navegar.
        Ejecuta una sesión completa (1-3 sitios) y bloquea hasta que termina.
        """
        if self.is_browsing or not self.page:
            return False

        self.is_browsing = True
        self._browse_interrupt.clear()
        if self._prompter_signal:
            self._prompter_signal.set("browsing", True)

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._run_session(mood=mood, forced_url=url), self._loop
            )
            future.result(timeout=300)   # máx 5 min por sesión
        except Exception as e:
            if "timeout" not in str(e).lower():
                print(f"[Browser] Error en sesión: {e}")
        finally:
            self.is_browsing = False
            if self._prompter_signal:
                self._prompter_signal.set("browsing", False)
            self._browse_interrupt.clear()

        return True

    async def _run_session(self, mood: str = "neutral", forced_url: str = None):
        """
        Una sesión de navegación: 1-3 sitios, varios scrolls cada uno.
        Se detiene si llega chat (browse_interrupt) o al terminar los sitios.
        """
        if not self.page:
            return

        sites_to_visit = random.randint(1, 3)
        visited = 0

        while (visited < sites_to_visit
               and self._running
               and not self._browse_interrupt.is_set()):

            # Elegir sitio
            if forced_url and visited == 0:
                site = self._url_to_site_info(forced_url)
            else:
                site = self.choose_site(mood)

            await self._navigate_to_site(site, announce=True)

            scroll_count     = 0
            site_max_scrolls = random.randint(4, 8)
            boring_streak    = 0
            boredom_limit    = random.randint(2, 4)
            is_first         = True
            platform         = site["platform"]

            while (scroll_count < site_max_scrolls
                   and boring_streak < boredom_limit
                   and self._running
                   and not self._browse_interrupt.is_set()):

                scroll_interval = self.SCROLL_INTERVALS.get(platform, 4.0)

                # Esperar con check de interrupción
                t0 = time.time()
                while time.time() - t0 < scroll_interval:
                    if not self._running or self._browse_interrupt.is_set():
                        return
                    if self._aiko_speaking.is_set():
                        await asyncio.sleep(0.5)
                        continue
                    await asyncio.sleep(0.25)

                if self._browse_interrupt.is_set():
                    break

                # Scroll
                await self._platform_scroll(platform)
                scroll_count += 1

                # YouTube: intentar entrar a video al 2do scroll
                if platform == "youtube" and scroll_count == 2:
                    clicked = await self._click_youtube_video()
                    if clicked:
                        boring_streak = 0
                        is_first = True
                        await asyncio.sleep(3.0)
                        platform = self.current_platform or platform

                # Captura + reacción
                if self._can_react_now():
                    reacted = await self._capture_and_react(platform, is_first)
                    if reacted:
                        boring_streak = 0
                        self._last_reaction_time = time.time()
                    else:
                        boring_streak += 1
                else:
                    boring_streak += 1

                is_first = False

            visited += 1

            # Pausa natural entre sitios con anuncio de salida
            if visited < sites_to_visit and not self._browse_interrupt.is_set():
                if self._can_react_now():
                    exits = [
                        f"ya vi suficiente de {platform}, siguiente",
                        "meh, vamos a otro lado",
                        "siguiente",
                    ]
                    self._speak(random.choice(exits), "bored")
                    self._last_reaction_time = time.time()
                await asyncio.sleep(1.5)

        # Fin de sesión
        if not self._browse_interrupt.is_set() and self._can_react_now():
            endings = [
                "bueno, ya vi suficiente por ahora",
                "ok ya, regresando al stream",
                "cerrando el browser por ahora",
            ]
            self._speak(random.choice(endings), "neutral")

    # ──────────────────────────────────────────────────
    #  NAVEGACIÓN DE SITIO
    # ──────────────────────────────────────────────────

    async def _navigate_to_site(self, site: dict, announce: bool = True):
        self.current_platform = site["platform"]
        self.is_browsing      = True
        if self._prompter_signal:
            self._prompter_signal.set("browser_platform", site["platform"])

        if announce and self._can_react_now():
            opts = site.get("announce", [f"a ver {site['description']}"])
            self._speak(random.choice(opts), "neutral")
            await asyncio.sleep(0.8)

        success = await self._navigate_async(site["url"])
        if success:
            await asyncio.sleep(2.0)
            print(f"[Browser] 🌐 {site['platform']}: {site['url'][:60]}")

    # ──────────────────────────────────────────────────
    #  CAPTURA + REACCIÓN
    # ──────────────────────────────────────────────────

    async def _capture_and_react(self, platform: str, is_first: bool) -> bool:
        if not self.intelligence or not self.page:
            return False
        try:
            understanding = await self.intelligence.understand_page(self.page, platform)
            if not understanding or not understanding.get("screenshots"):
                return False

            # Guardar contexto actual
            self._store_context(understanding)

            reaction = self.intelligence.should_react(
                understanding,
                platform=platform,
                mood=self._mood,
                is_first_look=is_first,
            )

            if reaction and reaction != "SOLO_SCROLL":
                print(f"[Browser] 👀 {platform}: {reaction[:70]}")
                self._speak(reaction, self._mood)
                return True

            return False
        except Exception as e:
            print(f"[Browser] Error captura: {e}")
            return False

    def _store_context(self, understanding: dict):
        """Guarda qué está viendo Aiko para que el chat lo referencie."""
        platform   = understanding.get("platform", "")
        page_state = understanding.get("page_state", "")
        text       = understanding.get("text_context", "")
        summary    = ""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("type") == "video":
                    summary = data.get("title", "")
                elif data.get("type") == "feed":
                    vids = data.get("videos", [])
                    summary = vids[0].get("title", "") if vids else ""
                else:
                    summary = data.get("description", "")[:60]
            elif isinstance(data, list) and data:
                first = data[0]
                summary = first.get("text", first.get("title", ""))[:60]
        except Exception:
            summary = text[:60] if text else ""

        self._current_content = {
            "platform": platform,
            "page_state": page_state,
            "summary": summary,
        }

    # ──────────────────────────────────────────────────
    #  INTERACCIÓN CON YOUTUBE
    # ──────────────────────────────────────────────────

    async def _click_youtube_video(self) -> bool:
        """Hace click en un video del feed de YouTube."""
        if not self.page:
            return False
        try:
            url = self.page.url.lower()
            if "watch?v=" in url or "/shorts/" in url:
                return False   # ya dentro de un video
            el = await self.page.query_selector(
                "a#video-title-link, ytd-rich-item-renderer a#thumbnail"
            )
            if el:
                await el.click()
                await asyncio.sleep(3.0)
                self.current_platform = "youtube"
                print("[Browser] ▶ Entró a un video de YouTube")
                await self._ensure_youtube_playing()
                return True
        except Exception as e:
            print(f"[Browser] Error click YouTube: {e}")
        return False

    async def _ensure_youtube_playing(self):
        """Asegura que el video esté reproduciendo."""
        if not self.page:
            return
        try:
            is_paused = await self.page.evaluate("""() => {
                const v = document.querySelector('video');
                return v ? v.paused : true;
            }""")
            if is_paused:
                await self.page.click("video", timeout=3000)
            await asyncio.sleep(0.5)
            # Verificar de nuevo
            still_paused = await self.page.evaluate("""() => {
                const v = document.querySelector('video');
                return v ? v.paused : true;
            }""")
            if still_paused:
                # Intentar el botón de play
                await self.page.click(".ytp-play-button", timeout=2000)
        except Exception:
            pass

    # ──────────────────────────────────────────────────
    #  SCROLL POR PLATAFORMA
    # ──────────────────────────────────────────────────

    async def _platform_scroll(self, platform: str):
        if not self.page:
            return
        try:
            if platform == "tiktok":
                await self.page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
            elif platform == "youtube":
                url = self.page.url.lower()
                if "/shorts/" in url:
                    await self.page.keyboard.press("ArrowDown")
                elif "watch?v=" in url:
                    # En video: scroll para ver recomendaciones
                    await self.page.evaluate("window.scrollBy(0, 300)")
                else:
                    await self.page.keyboard.press("PageDown")
            elif platform in ("twitter", "reddit"):
                await self.page.evaluate(
                    "window.scrollBy(0, window.innerHeight * 0.8)"
                )
            else:
                await self.page.keyboard.press("PageDown")
        except Exception as e:
            if "closed" in str(e).lower():
                self.page = None
                self.is_browsing = False

    # ──────────────────────────────────────────────────
    #  CONTROL EXTERNO: búsqueda desde chat
    # ──────────────────────────────────────────────────

    def search_from_chat(self, query: str, platform: str = "google"):
        """Búsqueda disparada por el chat — navega inmediatamente."""
        urls = {
            "youtube": (
                "https://www.youtube.com/results"
                f"?search_query={query.replace(' ', '+')}"
            ),
            "twitter": (
                "https://x.com/search"
                f"?q={query.replace(' ', '%20')}&src=typed_query"
            ),
            "google": (
                "https://www.google.com/search"
                f"?q={query.replace(' ', '+')}"
            ),
            "tiktok": (
                "https://www.tiktok.com/search"
                f"?q={query.replace(' ', '%20')}"
            ),
        }
        url = urls.get(platform, urls["google"])

        def _do():
            if self._loop and self._loop.is_running():
                site = self._url_to_site_info(url)
                asyncio.run_coroutine_threadsafe(
                    self._navigate_to_site(site, announce=False), self._loop
                )

        threading.Thread(target=_do, daemon=True, name="BrowserSearch").start()

    def interrupt_browsing(self):
        self._browse_interrupt.set()

    # ──────────────────────────────────────────────────
    #  DECISIÓN DE SITE
    # ──────────────────────────────────────────────────

    def choose_site(self, mood: str = "neutral") -> dict:
        matching = [s for s in self.FAVORITE_SITES if mood in s.get("mood_match", [])]
        if not matching:
            matching = self.FAVORITE_SITES
        weights = [s["weight"] for s in matching]
        return random.choices(matching, weights=weights, k=1)[0]

    # ──────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────

    @staticmethod
    def _detect_platform(url: str) -> str:
        url_l = url.lower()
        for p in ["youtube", "tiktok", "reddit"]:
            if p in url_l:
                return p
        if "x.com" in url_l or "twitter" in url_l:
            return "twitter"
        return "default"

    def _url_to_site_info(self, url: str) -> dict:
        platform = self._detect_platform(url)
        return {
            "url": url, "platform": platform,
            "description": f"ver {platform}", "weight": 1,
            "mood_match": [], "announce": [f"a ver {platform}"],
        }

    def get_current_url(self) -> str:
        return self._current_url

    def navigate_to(self, url: str) -> bool:
        return self._run_async(self._navigate_async(url), timeout=20) or False

    def get_browse_announcement(self, site: dict, mood: str = "neutral") -> str:
        opts = site.get("announce", [f"voy a {site.get('description', 'navegar')}"])
        return random.choice(opts)

    # ──────────────────────────────────────────────────
    #  ASYNC INTERNALS + PLAYWRIGHT
    # ──────────────────────────────────────────────────

    def _run_async(self, coro, timeout=10):
        if not self._loop or not self._loop.is_running():
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            return None

    async def _init_playwright(self):
        from playwright.async_api import async_playwright
        try:
            self._playwright = await async_playwright().start()
            print("[Browser] Iniciando Chrome con perfil persistente...")
            self._browser_context = (
                await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=self.profile_path,
                    channel="chrome",
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--test-type",
                        f"--window-size={self.window_width},{self.window_height}",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
            )
            while len(self._browser_context.pages) > 1:
                await self._browser_context.pages[-1].close()

            self.page = (
                self._browser_context.pages[0]
                if self._browser_context.pages
                else await self._browser_context.new_page()
            )
            await self.page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
            self._current_url = self.page.url
            print("[Browser] ✓ Navegador listo — esperando decisión del Prompter")
            print("[Browser]   OBS: Window Capture → Chrome")
        except Exception as e:
            err = str(e).splitlines()[0]
            print(f"[Browser] ✗ Error iniciando: {err}")

    async def _stop_playwright(self):
        try:
            if self._browser_context:
                await self._browser_context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        if self._loop:
            self._loop.stop()

    async def _navigate_async(self, url: str) -> bool:
        if not self.page:
            return False
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self._current_url = self.page.url
            self.current_platform = self._detect_platform(self._current_url)
            print(f"[Browser] → {self._current_url[:80]}")
            await asyncio.sleep(1.5)
            return True
        except Exception as e:
            if "closed" in str(e).lower():
                self.page = None
            print(f"[Browser] Error navegando: {str(e)[:60]}")
            return False
