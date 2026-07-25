"""
browser_intelligence.py — Comprensión visual y de contenido para Aiko.

Extrae texto del DOM, toma capturas, lee subtítulos de YouTube,
y genera reacciones ESPECÍFICAS (nunca genéricas) sobre lo que Aiko ve.
"""

import asyncio
import base64
import json
import re

from groq import Groq


class BrowserIntelligence:

    VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

    def __init__(self, groq_api_key: str):
        self._groq = Groq(api_key=groq_api_key)
        print("[Intelligence] ✓ Motor de comprensión web iniciado")

    # ══════════════════════════════════════════════════════════
    #  COMPRENSIÓN PRINCIPAL
    # ══════════════════════════════════════════════════════════

    async def understand_page(self, page, platform: str) -> dict:
        understanding = {
            "platform": platform,
            "url": page.url,
            "text_context": "",
            "screenshots": [],
            "video_captions": None,
            "page_state": "unknown",
            "video_info": {},
        }
        try:
            understanding["page_state"] = await self._detect_page_state(page, platform)
            understanding["text_context"] = await self._extract_text_context(page, platform)

            # Para videos: extraer info del reproductor
            if platform == "youtube" and understanding["page_state"] in (
                "watching_video", "watching_short"
            ):
                understanding["video_info"] = await self._extract_youtube_player_info(page)
                understanding["video_captions"] = await self._extract_youtube_captions(page)
                # Múltiples frames para videos
                understanding["screenshots"] = await self._capture_video_storyboard(page, num_frames=3)
            elif platform == "tiktok":
                understanding["screenshots"] = await self._capture_video_storyboard(page, num_frames=2)
            else:
                data = await page.screenshot(type="jpeg", quality=65)
                understanding["screenshots"] = [base64.b64encode(data).decode()]

        except Exception as e:
            print(f"[Intelligence] Error entendiendo página: {e}")

        return understanding

    # ══════════════════════════════════════════════════════════
    #  ESTADO DE PÁGINA
    # ══════════════════════════════════════════════════════════

    async def _detect_page_state(self, page, platform: str) -> str:
        url = page.url.lower()
        if platform == "youtube":
            if "watch?v=" in url:  return "watching_video"
            if "/shorts/"  in url: return "watching_short"
            if "results"   in url: return "search_results"
            if "/@"        in url: return "viewing_channel"
            return "feed"
        if platform == "twitter":
            if "/status/" in url: return "viewing_tweet"
            if "/search"  in url: return "search_results"
            return "feed"
        if platform == "tiktok":
            if "/video/" in url: return "watching_video"
            return "foryou_feed"
        if platform == "reddit":
            if "/comments/" in url: return "viewing_post"
            return "feed"
        return "browsing"

    # ══════════════════════════════════════════════════════════
    #  EXTRACCIÓN DE TEXTO POR PLATAFORMA
    # ══════════════════════════════════════════════════════════

    async def _extract_text_context(self, page, platform: str) -> str:
        try:
            if platform == "twitter": return await self._extract_twitter(page)
            if platform == "youtube": return await self._extract_youtube(page)
            if platform == "tiktok":  return await self._extract_tiktok(page)
            if platform == "reddit":  return await self._extract_reddit(page)
            return await self._extract_generic(page)
        except Exception as e:
            print(f"[Intelligence] Error DOM ({platform}): {e}")
            return ""

    async def _extract_twitter(self, page) -> str:
        return await page.evaluate("""() => {
            const arts = document.querySelectorAll('article[data-testid="tweet"]');
            const out = [];
            for (let i = 0; i < Math.min(arts.length, 4); i++) {
                const a = arts[i];
                const user = a.querySelector('[data-testid="User-Name"]');
                const text = a.querySelector('[data-testid="tweetText"]');
                const likes = a.querySelector('[data-testid="like"] span');
                const rts   = a.querySelector('[data-testid="retweet"] span');
                if (user && text) out.push({
                    user:  user.innerText.split('\\n')[0],
                    text:  text.innerText.substring(0, 280),
                    likes: likes ? likes.innerText : '0',
                    rts:   rts   ? rts.innerText   : '0',
                });
            }
            return JSON.stringify(out);
        }""") or "[]"

    async def _extract_youtube(self, page) -> str:
        return await page.evaluate("""() => {
            const title = document.querySelector(
                'yt-formatted-string.ytd-watch-metadata, #title h1 yt-formatted-string, h1.ytd-video-primary-info-renderer');
            const channel = document.querySelector('#channel-name a, ytd-channel-name yt-formatted-string a');
            const views   = document.querySelector('.ytd-video-view-count-renderer');
            const desc    = document.querySelector('#description-inline-expander, #description');
            if (title) return JSON.stringify({
                type: 'video',
                title: title.innerText.trim(),
                channel: channel ? channel.innerText.trim() : '',
                views: views ? views.innerText.trim() : '',
                description: desc ? desc.innerText.substring(0, 300) : '',
            });
            const vids = document.querySelectorAll('ytd-rich-item-renderer, ytd-video-renderer');
            const feed = [];
            for (let i = 0; i < Math.min(vids.length, 6); i++) {
                const v = vids[i];
                const t = v.querySelector('#video-title');
                const c = v.querySelector('#channel-name a, .ytd-channel-name a');
                const m = v.querySelector('#metadata-line span');
                if (t) feed.push({
                    title: t.innerText.trim(),
                    channel: c ? c.innerText.trim() : '',
                    meta: m ? m.innerText.trim() : '',
                });
            }
            return JSON.stringify({type: 'feed', videos: feed});
        }""") or "{}"

    async def _extract_tiktok(self, page) -> str:
        return await page.evaluate("""() => {
            const desc = document.querySelector('[data-e2e="browse-video-desc"], .tiktok-j2a19r-SpanText');
            const user = document.querySelector('[data-e2e="browse-username"], a[href*="/@"] span');
            const likes= document.querySelector('[data-e2e="browse-like-count"], [data-e2e="like-count"]');
            const tags = [...document.querySelectorAll('a[href*="/tag/"]')].slice(0,5).map(t=>t.innerText.trim());
            const music= document.querySelector('[data-e2e="browse-music-title"], .music-title-widget');
            return JSON.stringify({
                creator: user  ? '@'+user.innerText.trim() : '',
                description: desc ? desc.innerText.trim().substring(0,250) : '',
                likes: likes ? likes.innerText : '',
                hashtags: tags,
                music: music ? music.innerText.trim().substring(0,60) : '',
            });
        }""") or "{}"

    async def _extract_reddit(self, page) -> str:
        return await page.evaluate("""() => {
            const posts = document.querySelectorAll('article, [data-testid="post-container"], shreddit-post');
            const out = [];
            for (let i = 0; i < Math.min(posts.length, 5); i++) {
                const p = posts[i];
                const t = p.querySelector('h3, h1, a[slot="title"]');
                const v = p.querySelector('[id*="vote-arrows"], faceplate-number');
                const c = p.querySelector('[data-click-id="comments"]');
                if (t) out.push({
                    title: t.innerText.substring(0, 200),
                    votes: v ? v.innerText : '',
                    comments: c ? c.innerText : '',
                });
            }
            return JSON.stringify(out);
        }""") or "[]"

    async def _extract_generic(self, page) -> str:
        return await page.evaluate("""() => {
            const parts = [document.title || ''];
            document.querySelectorAll('h1, h2, p').forEach(el => {
                const t = el.innerText?.trim();
                if (t && t.length > 10 && t.length < 280) parts.push(t);
            });
            return parts.slice(0,8).join(' | ').substring(0,500);
        }""") or ""

    # ══════════════════════════════════════════════════════════
    #  INFO DEL REPRODUCTOR DE YOUTUBE
    # ══════════════════════════════════════════════════════════

    async def _extract_youtube_player_info(self, page) -> dict:
        """Progreso, duración, si está pausado."""
        try:
            return await page.evaluate("""() => {
                const v = document.querySelector('video');
                if (!v) return {};
                return {
                    current_time: Math.floor(v.currentTime),
                    duration:     Math.floor(v.duration || 0),
                    paused:       v.paused,
                    muted:        v.muted,
                };
            }""") or {}
        except Exception:
            return {}

    # ══════════════════════════════════════════════════════════
    #  SUBTÍTULOS DE YOUTUBE
    # ══════════════════════════════════════════════════════════

    async def _extract_youtube_captions(self, page) -> str | None:
        try:
            caption_url = await page.evaluate("""() => {
                for (const s of document.querySelectorAll('script')) {
                    const text = s.textContent;
                    if (text && text.includes('captionTracks')) {
                        try {
                            const m = text.match(/ytInitialPlayerResponse\\s*=\\s*({.+?});/s);
                            if (m) {
                                const d = JSON.parse(m[1]);
                                const tracks = d?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
                                if (tracks && tracks.length > 0) {
                                    const es = tracks.find(t => t.languageCode === 'es');
                                    const en = tracks.find(t => t.languageCode === 'en');
                                    return (es || en || tracks[0]).baseUrl;
                                }
                            }
                        } catch(e) {}
                    }
                }
                return null;
            }""")

            if caption_url:
                xml = await page.evaluate(f"""async () => {{
                    try {{ return await (await fetch('{caption_url}')).text(); }}
                    catch(e) {{ return null; }}
                }}""")
                if xml:
                    segs = re.findall(r'<text[^>]*>(.*?)</text>', xml, re.DOTALL)
                    if segs:
                        clean = []
                        for s in segs[:100]:
                            s = re.sub(r'<[^>]+>', '', s)
                            s = (s.replace('&amp;', '&').replace('&#39;', "'")
                                  .replace('&quot;', '"').replace('&lt;', '<')
                                  .replace('&gt;', '>').strip())
                            if s:
                                clean.append(s)
                        captions = " ".join(clean)
                        print(f"[Intelligence] ✓ Subtítulos: {len(captions)} chars")
                        return captions[:2500]

            # Fallback: subtítulos visibles en pantalla
            on_screen = await page.evaluate("""() => {
                const el = document.querySelector('.ytp-caption-window-container, .caption-visual-line');
                return el ? el.innerText : null;
            }""")
            return on_screen
        except Exception as e:
            print(f"[Intelligence] Error subtítulos: {e}")
            return None

    # ══════════════════════════════════════════════════════════
    #  CAPTURAS
    # ══════════════════════════════════════════════════════════

    async def _capture_video_storyboard(self, page, num_frames: int = 3,
                                         interval: float = 1.2) -> list:
        frames = []
        try:
            for i in range(num_frames):
                data = await page.screenshot(type="jpeg", quality=60)
                frames.append(base64.b64encode(data).decode())
                if i < num_frames - 1:
                    await asyncio.sleep(interval)
        except Exception as e:
            print(f"[Intelligence] Error storyboard: {e}")
        return frames

    # ══════════════════════════════════════════════════════════
    #  DECISIÓN DE REACCIÓN
    # ══════════════════════════════════════════════════════════

    def should_react(self, understanding: dict, platform: str,
                     mood: str, is_first_look: bool = False) -> str:
        """
        Analiza el contenido y retorna una reacción específica o "SOLO_SCROLL".
        Nunca produce reacciones genéricas — siempre referencia algo concreto.
        """
        screenshots = understanding.get("screenshots", [])
        if not screenshots:
            return "SOLO_SCROLL"

        content_desc = self._build_content_description(understanding)
        messages     = self._build_vision_messages(
            content_desc, screenshots,
            understanding.get("video_captions"),
            understanding.get("video_info", {}),
            platform, mood,
            understanding.get("page_state", ""),
            is_first_look,
        )

        try:
            resp = self._groq.chat.completions.create(
                model=self.VISION_MODEL,
                messages=messages,
                max_tokens=180,
                temperature=0.85,
            )
            reaction = resp.choices[0].message.content.strip().strip('"\'')

            if "SOLO_SCROLL" in reaction.upper() or not reaction:
                return "SOLO_SCROLL"

            # Si es demasiado larga o parece descripción sin opinión → descartar
            if len(reaction) > 280:
                return "SOLO_SCROLL"

            return reaction

        except Exception as e:
            print(f"[Intelligence] Error visión: {e}")
            return "SOLO_SCROLL"

    # ══════════════════════════════════════════════════════════
    #  CONSTRUCCIÓN DE PROMPTS
    # ══════════════════════════════════════════════════════════

    def _build_content_description(self, understanding: dict) -> str:
        platform = understanding.get("platform", "?")
        state    = understanding.get("page_state", "?")
        parts    = [f"Plataforma: {platform.upper()} | Estado: {state}"]

        text = understanding.get("text_context", "")
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    for item in data[:4]:
                        user  = item.get("user", item.get("title", ""))
                        body  = item.get("text", item.get("votes", ""))
                        likes = item.get("likes", "")
                        rts   = item.get("rts", "")
                        line  = f"@{user}: {body[:200]}"
                        if likes: line += f" ({likes} likes)"
                        if rts:   line += f" ({rts} rts)"
                        parts.append(line)
                elif isinstance(data, dict):
                    dtype = data.get("type", "")
                    if dtype == "video":
                        parts.append(
                            f"Video: «{data.get('title','')}» "
                            f"por {data.get('channel','')} | "
                            f"{data.get('views','')} vistas"
                        )
                        if data.get("description"):
                            parts.append(f"Desc: {data['description'][:200]}")
                    elif dtype == "feed":
                        for v in data.get("videos", [])[:5]:
                            parts.append(
                                f"• «{v.get('title','')}» — {v.get('channel','')} {v.get('meta','')}"
                            )
                    else:
                        if data.get("creator"):
                            parts.append(f"Creador: {data['creator']}")
                        if data.get("description"):
                            parts.append(f"Video: {data['description'][:220]}")
                        if data.get("likes"):
                            parts.append(f"Likes: {data['likes']}")
                        if data.get("hashtags"):
                            parts.append(f"Tags: {', '.join(data['hashtags'][:5])}")
                        if data.get("music"):
                            parts.append(f"Música: {data['music']}")
            except (json.JSONDecodeError, TypeError):
                if text:
                    parts.append(f"Contenido: {text[:300]}")

        # Info del player de YouTube
        vinfo = understanding.get("video_info", {})
        if vinfo:
            cur = vinfo.get("current_time", 0)
            dur = vinfo.get("duration", 0)
            if dur > 0:
                parts.append(f"Progreso del video: {cur}s / {dur}s")

        captions = understanding.get("video_captions")
        if captions:
            parts.append(f"\n[LO QUE DICE EL VIDEO]: {captions[:1000]}")

        return "\n".join(parts)

    def _build_vision_messages(self, content_desc: str, screenshots: list,
                                captions, video_info: dict,
                                platform: str, mood: str,
                                page_state: str,
                                is_first_look: bool) -> list:

        # Sistema base
        system = self._build_system_prompt(
            platform, page_state, mood, is_first_look,
            has_captions=bool(captions),
            has_video=bool(video_info),
            num_frames=len(screenshots),
        )

        user_parts = [{"type": "text", "text": content_desc}]
        for shot in screenshots[:3]:
            user_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{shot}",
                    "detail": "low",
                }
            })

        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_parts},
        ]

    def _build_system_prompt(self, platform: str, page_state: str,
                              mood: str, is_first_look: bool,
                              has_captions: bool, has_video: bool,
                              num_frames: int) -> str:
        """Prompt específico según el estado de la página."""

        base_persona = (
            "Eres Aiko, una VTuber con criterio propio navegando internet en vivo.\n"
            f"Tu humor ahora: {mood}.\n\n"
        )

        # ── Prompt específico por estado ──────────────────────
        if page_state in ("watching_video", "watching_short") and platform == "youtube":
            specific = (
                "Estás VIENDO un video de YouTube ahora mismo.\n"
            )
            if has_captions:
                specific += (
                    "Tienes acceso a LO QUE DICE el video (subtítulos reales). "
                    "Úsalos para hablar del CONTENIDO, no del thumbnail.\n"
                )
            if num_frames > 1:
                specific += (
                    f"Tienes {num_frames} capturas del video (tomadas con 1s de diferencia).\n"
                )
            specific += (
                "\nDECIDE: ¿este video es interesante, aburrido, gracioso o una pérdida de tiempo?\n"
                "Si el video es mediocre/normal → SOLO_SCROLL\n"
                "Si el video te parece interesante → comenta UNA sola cosa específica que dijeron o mostraron\n"
                "Si el video es puro clickbait o una basura → dilo con criterio\n"
            )

        elif page_state == "foryou_feed" and platform == "tiktok":
            specific = (
                "Estás en el FYP de TikTok. Ves un video en este momento.\n"
                "El internet está lleno de TikToks mediocres — SOLO reacciona si:\n"
                "- El video es genuinamente gracioso, absurdo o raro\n"
                "- El creador dijo algo cuestionable o estúpido\n"
                "- Los hashtags o la música son ridículos\n"
                "- Los likes no cuadran con la calidad del video\n"
                "Si el TikTok es normal → SOLO_SCROLL\n"
            )

        elif page_state == "feed" and platform == "twitter":
            specific = (
                "Estás viendo el feed de Twitter.\n"
                "Solo reacciona si un tweet específico llama tu atención:\n"
                "- Opinión polémica con muchos likes\n"
                "- Algo ridículo o sin sentido que la gente está apoyando\n"
                "- Noticia sorprendente\n"
                "- Tweet que te parece genuinamente gracioso\n"
                "Si los tweets son mundanos → SOLO_SCROLL\n"
            )

        elif page_state == "viewing_tweet" and platform == "twitter":
            specific = (
                "Estás leyendo un tweet completo. Tienes la imagen.\n"
                "Opina sobre lo que dice. ¿Estás de acuerdo? ¿Es una tontería?\n"
                "Referencia exactamente qué dijo el usuario.\n"
            )

        elif page_state == "feed" and platform == "youtube":
            specific = (
                "Estás viendo el feed de YouTube — una lista de videos.\n"
                "Mira los TÍTULOS disponibles. ¿Alguno llama tu atención?\n"
                "Puedes comentar un título específico que te parezca:\n"
                "- Clickbait obvio\n"
                "- Tema que te interesa (anime, gaming, música, tech)\n"
                "- Algo inesperado o curioso\n"
                "Si todos los títulos son mediocres → SOLO_SCROLL\n"
            )

        elif page_state in ("feed", "viewing_post") and platform == "reddit":
            specific = (
                "Estás en Reddit. Mira los títulos de los posts.\n"
                "Solo reacciona a posts que sean:\n"
                "- Genuinamente interesantes o informativos\n"
                "- Absurdamente polémicos\n"
                "- Relacionados con anime, gaming o tech\n"
                "Si el contenido es genérico → SOLO_SCROLL\n"
            )

        else:
            specific = (
                f"Estás navegando {platform}.\n"
                "Solo reacciona si hay algo genuinamente interesante.\n"
                "Contenido mundano → SOLO_SCROLL\n"
            )

        rules = (
            "\n━━━ REGLAS DE RESPUESTA ━━━\n"
            "Si decides reaccionar:\n"
            "• 1-2 oraciones en español informal\n"
            "• OBLIGATORIO referenciar algo ESPECÍFICO: título del video, "
            "nombre del usuario, cifra de likes, frase que dijo alguien\n"
            "• Opina — no describas. 'ese título es puro clickbait' ✓ "
            "  'hay un video' ✗\n"
            "• NO uses: 'interesante', 'qué gracioso', 'wow', 'increíble'\n"
            "• Si no hay nada que valga la pena comentar: SOLO_SCROLL\n"
        )

        if is_first_look:
            rules += "• (Primera vez que ves esta página en esta sesión)\n"

        return base_persona + specific + rules
