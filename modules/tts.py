"""
tts.py — Pipeline de voz para Aiko (OPTIMIZADO).

Pipeline: Texto -> edge-tts (es-MX-DaliaNeural) -> WAV -> RVC Worker (NekoGirl) -> WAV -> reproducir.

El worker RVC corre como un proceso persistente que carga el modelo UNA VEZ.
Cada conversion toma ~0.5s en vez de 3-4s.
"""

import os
import sys
import tempfile
import subprocess
import asyncio
import threading
import json
import time
import unicodedata
import requests
import sounddevice as sd
import soundfile as sf
import numpy as np


class RVCWorker:
    """Worker persistente para conversion de voz RVC via Applio."""

    def __init__(self, applio_path, pth_path, index_path="",
                 pitch=0, f0_method="rmvpe"):
        self.applio_path = applio_path
        self.pth_path = pth_path
        self.index_path = index_path
        self.pitch = pitch
        self.f0_method = f0_method
        self._process = None
        self._lock = threading.Lock()
        self.ready = False
        self.load_time = 0
        self._response_queue = None

    def start(self):
        """Iniciar el worker RVC como proceso persistente."""
        import queue as queue_mod
        self._response_queue = queue_mod.Queue()

        worker_script = os.path.join(self.applio_path, "rvc_worker.py")
        applio_python = os.path.join(self.applio_path, "env", "python.exe")

        if not os.path.exists(worker_script):
            print(f"[RVC-Worker] rvc_worker.py no encontrado en {self.applio_path}")
            return False

        if not os.path.exists(applio_python):
            print(f"[RVC-Worker] Python de Applio no encontrado")
            return False

        try:
            print("[RVC-Worker] Iniciando worker persistente...")
            self._process = subprocess.Popen(
                [applio_python, worker_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.applio_path,
                text=True,
                bufsize=1,
            )

            # Hilo lector de stderr (logs)
            threading.Thread(target=self._read_stderr, daemon=True).start()

            # Hilo lector continuo de stdout (respuestas)
            threading.Thread(target=self._read_stdout_loop, daemon=True).start()

            # Enviar configuracion inicial
            config = {
                "pth_path": self.pth_path,
                "index_path": self.index_path,
                "pitch": self.pitch,
                "f0_method": self.f0_method,
            }
            self._process.stdin.write(json.dumps(config) + "\n")
            self._process.stdin.flush()

            # Esperar la senal de listo
            print("[RVC-Worker] Cargando modelo (esto toma unos segundos)...")
            try:
                resp_line = self._response_queue.get(timeout=120)
                resp = json.loads(resp_line)
                if resp.get("ready"):
                    self.ready = True
                    self.load_time = resp.get("load_time", 0)
                    print(f"[RVC-Worker] Modelo cargado en {self.load_time}s")
                    return True
            except Exception:
                pass

            print("[RVC-Worker] El worker no respondio a tiempo.")
            self.stop()
            return False

        except Exception as e:
            print(f"[RVC-Worker] Error iniciando worker: {e}")
            return False

    def _read_stdout_loop(self):
        """Hilo que lee stdout linea por linea y pone en la cola."""
        try:
            while self._process and self._process.poll() is None:
                line = self._process.stdout.readline()
                if line:
                    stripped = line.strip()
                    if stripped and stripped.startswith("{"):
                        self._response_queue.put(stripped)
                    # Ignorar lineas que no son JSON (output de librerias)
                else:
                    break
        except Exception:
            pass

    def _read_stderr(self):
        """Leer stderr del worker y mostrar los logs."""
        try:
            while self._process and self._process.poll() is None:
                line = self._process.stderr.readline()
                if line:
                    print(line.strip())
        except Exception:
            pass

    def convert(self, input_path, output_path):
        """Enviar peticion de conversion al worker. Retorna True/False."""
        if not self.ready or not self._process:
            return False

        with self._lock:
            try:
                request = {
                    "input": input_path,
                    "output": output_path,
                }
                self._process.stdin.write(json.dumps(request) + "\n")
                self._process.stdin.flush()

                # Esperar respuesta (timeout 60s para primera vez)
                resp_line = self._response_queue.get(timeout=60)
                resp = json.loads(resp_line)
                return resp.get("ok", False)

            except Exception as e:
                print(f"[RVC-Worker] Error en conversion: {e}")
                return False

    def stop(self):
        """Detener el worker."""
        if self._process:
            try:
                self._process.stdin.write("QUIT\n")
                self._process.stdin.flush()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            self.ready = False


class GPTSoVITSClient:
    """Cliente para la API de GPT-SoVITS (api_v2.py FastAPI)."""
    
    def __init__(self, config: dict):
        self.api_url = config.get("api_url", "http://127.0.0.1:9880/tts")
        self.ref_audio_path = config.get("ref_audio_path", "")
        self.prompt_text = config.get("prompt_text", "")
        self.prompt_language = config.get("prompt_language", "auto")
        self.text_language = config.get("text_language", "auto")
        self.sovits_path = config.get("sovits_path", "")
        self.gpt_weight = config.get("gpt_weight_path", "")
        self.sovits_weight = config.get("sovits_weight_path", "")
        self.ready = False
        self._process = None
        # Contador de fallos consecutivos: si llega a _max_failures, se
        # desactiva GPT-SoVITS automaticamente para no spamear la consola.
        self._consecutive_failures = 0
        self._max_failures = 3
        self._check_config()

    def _check_config(self):
        if self.ref_audio_path and self.prompt_text:
            if self.sovits_path and os.path.exists(self.sovits_path):
                self._start_server()
            else:
                self.ready = True
                print("[TTS-GPTSoVITS] Cliente configurado (esperando API externa).")
        else:
            print("[TTS-GPTSoVITS] Advertencia: Falta configuración (ruta de audio o texto de referencia).")

    def _start_server(self):
        python_exe = os.path.join(self.sovits_path, "runtime", "python.exe")
        api_script = os.path.join(self.sovits_path, "api_v2.py")
        
        if not os.path.exists(python_exe) or not os.path.exists(api_script):
            print(f"[TTS-GPTSoVITS] Error: No se encontró python.exe o api_v2.py en {self.sovits_path}")
            return
            
        print("[TTS-GPTSoVITS] Iniciando servidor API V2 en segundo plano...")

        # ── Liberar puerto 9880 si quedó ocupado de una sesión anterior ────
        try:
            import subprocess as _sp
            _sp.run(
                ["powershell", "-Command",
                 "Get-NetTCPConnection -LocalPort 9880 -EA SilentlyContinue | "
                 "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        # Ruta al log del servidor (para diagnosticar errores de inferencia)
        _log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "gpt_sovits_server.log")
        try:
            _log_file = open(_log_path, "w", encoding="utf-8", errors="replace")
            print(f"[TTS-GPTSoVITS] Log del servidor: {_log_path}")
        except Exception:
            _log_file = open(os.devnull, 'w')
        try:
            self._process = subprocess.Popen(
                [python_exe, api_script, "-a", "127.0.0.1", "-p", "9880"],
                cwd=self.sovits_path,
                stdout=_log_file,
                stderr=_log_file,
                creationflags=0x08000000 if os.name == 'nt' else 0
            )
            self._wait_for_server()
        except Exception as e:
            print(f"[TTS-GPTSoVITS] Error iniciando servidor: {e}")

    def _wait_for_server(self):
        print("[TTS-GPTSoVITS] Esperando a que el servidor esté listo...")
        import socket
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", 9880), timeout=1):
                    print("[TTS-GPTSoVITS] ✓ Servidor listo y conectado!")
                    self.ready = True
                    self._set_custom_weights()
                    return
            except OSError:
                time.sleep(1)
        print("[TTS-GPTSoVITS] Timeout esperando al servidor.")

    def _set_custom_weights(self):
        base_url = self.api_url.replace("/tts", "")
        if self.gpt_weight:
            try:
                r = requests.get(f"{base_url}/set_gpt_weights", params={"weights_path": self.gpt_weight})
                if r.status_code == 200: 
                    print(f"[TTS-GPTSoVITS] Peso GPT cargado nativo.")
                else:
                    print(f"[TTS-GPTSoVITS] Error cargando GPT: {r.text}")
            except Exception as e: print(f"[TTS-GPTSoVITS] Error request GPT: {e}")
            
        if self.sovits_weight:
            try:
                r = requests.get(f"{base_url}/set_sovits_weights", params={"weights_path": self.sovits_weight})
                if r.status_code == 200: 
                    print(f"[TTS-GPTSoVITS] Peso SoVITS cargado nativo.")
                else:
                    print(f"[TTS-GPTSoVITS] Error cargando SoVITS: {r.text}")
            except Exception as e: print(f"[TTS-GPTSoVITS] Error request SoVITS: {e}")

    def stop(self):
        if self._process:
            try:
                print("[TTS-GPTSoVITS] Deteniendo servidor...")
                self._process.kill()
            except Exception:
                pass
            self._process = None
            self.ready = False

    def _strip_accents(self, text: str) -> str:
        """Convierte texto con acentos a ASCII plano.
        Documentacion GPT-SoVITS: el tokenizador BERT falla con acentos en
        modo auto/en. Quitar acentos resuelve el torch.cat() vacio."""
        # Normalizar a NFD (descompone acentos en char base + diacritic)
        nfd = unicodedata.normalize('NFD', text)
        # Filtrar solo caracteres no-combining (elimina diacriticos)
        ascii_text = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        # Manejar casos especiales que NFD no descompone bien
        ascii_text = ascii_text.replace('\u00f1', 'n').replace('\u00d1', 'N')  # n/N con tilde
        return ascii_text

    def synthesize(self, text: str, output_path: str, speed: float = 1.0) -> bool:
        """Envia texto a la API y guarda el WAV retornado."""
        if not self.ready:
            return False

        # Si acumulamos demasiados fallos consecutivos, desactivar definitivamente
        # para no spamear la consola ni agregar delay por reintentos inutiles.
        if self._consecutive_failures >= self._max_failures:
            self.ready = False
            print(f"[TTS-GPTSoVITS] Auto-deshabilitado tras {self._max_failures} fallos "
                  f"consecutivos. Edge-tts tomara el relevo permanentemente.")
            print(f"[TTS-GPTSoVITS] Causa probable: pesos del modelo incompatibles con v2Pro.")
            print(f"[TTS-GPTSoVITS] Para volver a activar: reinicia aiko.py.")
            return False

        import re

        # ── Limpieza 1: sustituir CJK/unicode por equivalentes ASCII ─────────
        for _src, _dst in [
            ('\u2500\u2500', '-'), ('\u2026', '...'), ('\u3002', '.'),
            ('\uff01', '!'), ('\uff1f', '?'), ('\uff0c', ','),
            ('\u3001', ','), ('\u2014', '-'), ('\u300c', '"'),
            ('\u300d', '"'), ('\u300e', '"'), ('\u300f', '"'),
            ('\u00b7', ' '), ('\u00bf', ''), ('\u00a1', ''),
        ]:
            text = text.replace(_src, _dst)

        # Garantia: latin-1 elimina todo lo que no es Europa occidental
        text = text.encode('latin-1', errors='ignore').decode('latin-1')

        # ── Limpieza 2: quitar acentos → ASCII plano ─────────────────────────
        # GPT-SoVITS BERT tokenizer falla con acentos en text_lang=en/auto.
        # Segun issues de GitHub, quitar acentos es la solucion correcta.
        text_ascii = self._strip_accents(text)

        # Quitar comas internas que crean segmentos vacios en el divisor
        text_ascii = re.sub(r'[,;:]', ' ', text_ascii)
        text_ascii = re.sub(r'\s+', ' ', text_ascii).strip()

        # Agregar punto final si falta
        if text_ascii and text_ascii[-1] not in '.!?':
            text_ascii += '.'

        if not text_ascii or not re.search(r'[A-Za-z]', text_ascii):
            return False

        words = text_ascii.split()

        # Idioma a usar: el configurado en config.yaml (normalmente "auto" o "es")
        # NO forzar "en" — el modelo esta entrenado en espanol y producira
        # audio vacio/de 1 segundo si se le pasa fonética inglesa.
        lang = self.text_language  # "auto", "es", etc.

        try:
            # Intento 1: texto completo, split=cut0
            ok = self._try_synthesize(text_ascii, output_path, speed, "cut0", force_lang=lang)
            if ok:
                self._consecutive_failures = 0  # exito: reiniciar contador
                return True

            # Fallo en intento 1 — no reintentar con variantes si son pesos incompatibles
            # (el problema es sistematico, no depende del largo del texto)
            preview = text_ascii[:80] + "..." if len(text_ascii) > 80 else text_ascii
            self._consecutive_failures += 1
            remaining = self._max_failures - self._consecutive_failures
            if remaining > 0:
                print(f'[TTS-GPTSoVITS] Fallo ({self._consecutive_failures}/{self._max_failures}): '
                      f'"{preview}"')
            return False

        except requests.exceptions.ConnectionError:
            print(f"[TTS-GPTSoVITS] No se pudo conectar a {self.api_url}. Esta corriendo la API?")
            self._consecutive_failures += 1
            return False
        except Exception as e:
            print(f"[TTS-GPTSoVITS] Error en síntesis: {e}")
            self._consecutive_failures += 1
            return False

    def _try_synthesize(self, text: str, output_path: str, speed: float, split_method: str, force_lang: str = None) -> bool:
        """Intenta sintetizar un texto. Retorna True si tuvo éxito."""
        payload = {
            "text": text,
            "text_lang": force_lang or self.text_language,
            "ref_audio_path": self.ref_audio_path,
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_language,
            "text_split_method": split_method,
            "speed": speed
        }
        
        t0 = time.time()
        response = requests.post(self.api_url, json=payload, timeout=30)
        
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            elapsed = time.time() - t0

            # ── Validar que el audio sea real (no silencio de 1s) ──────────
            # GPT-SoVITS con pesos incompatibles retorna HTTP 200 pero con
            # exactamente 1 segundo de silencio puro. Detectarlo aqui.
            try:
                import soundfile as sf
                import numpy as np
                _d, _sr = sf.read(output_path, dtype="float32")
                _dur = len(_d) / _sr
                _rms = float(np.sqrt(np.mean(_d ** 2)))
                if _dur < 0.5 or _rms < 0.001:
                    print(f"[TTS-GPTSoVITS] Audio invalido (dur={_dur:.2f}s rms={_rms:.6f}) — silencio o muy corto.")
                    return False
            except Exception:
                pass  # si no se puede leer, asumir valido

            print(f"[TTS-GPTSoVITS] Audio generado ({elapsed:.2f}s)")
            return True
        else:
            # Extraer solo el tipo de error para no llenar la terminal
            try:
                err_data = response.json()
                err_msg = err_data.get("Exception", response.text[:100])
            except Exception:
                err_msg = response.text[:100]
            print(f"[TTS-GPTSoVITS] Error API ({response.status_code}): {err_msg}")
            return False


class TTS:
    """Pipeline de TTS: edge-tts + RVC Worker persistente."""

    def __init__(self, voice_model="es-MX-DaliaNeural",
                 speed=1.1,
                 output_device="CABLE Input (VB-Audio Virtual Cable)",
                 applio_path=None,
                 rvc_model=None,
                 rvc_index="",
                 rvc_pitch=0,
                 rvc_f0_method="rmvpe",
                 gpt_sovits_cfg=None):
        self.voice_model = voice_model
        self.speed = speed
        self.output_device_name = output_device
        self.output_device_id = None
        self.monitor_device_id = None  # segundo dispositivo para escuchar en auriculares
        self.is_playing = False
        self.is_speaking = False  # True mientras cualquier audio de Aiko suena
        self.temp_dir = os.path.join(tempfile.gettempdir(), "ai_vtuber_tts")
        self._edge_tts_available = False
        self._piper_available = False  # compatibilidad con tts_emotion.py
        self._ffmpeg_path = None

        # Interrupción limpia
        self._interrupt_flag = threading.Event()
        self._current_stream = None

        # RVC Worker
        self.applio_path = applio_path
        self._rvc_worker = None
        self._rvc_available = False
        
        # GPT-SoVITS
        self.gpt_sovits = None
        if gpt_sovits_cfg and gpt_sovits_cfg.get("enabled", False):
            self.gpt_sovits = GPTSoVITSClient(gpt_sovits_cfg)

        os.makedirs(self.temp_dir, exist_ok=True)

        self._check_edge_tts()
        self._find_ffmpeg()
        self._find_output_device()

        # Iniciar RVC Worker solo si GPT-SoVITS no está activo
        if self.gpt_sovits:
            print("[TTS] GPT-SoVITS activo. Se omitirá inicialización de RVC.")
        else:
            # Iniciar RVC Worker persistente (carga modelo una sola vez)
            if applio_path and rvc_model and os.path.exists(rvc_model):
                self._rvc_worker = RVCWorker(
                    applio_path=applio_path,
                    pth_path=rvc_model,
                    index_path=rvc_index,
                    pitch=rvc_pitch,
                    f0_method=rvc_f0_method,
                )
                if self._rvc_worker.start():
                    self._rvc_available = True
                else:
                    print("[TTS-RVC] Worker no disponible, usando edge-tts directo.")
            elif applio_path:
                print(f"[TTS-RVC] Modelo RVC no encontrado: {rvc_model}")

    def _check_edge_tts(self):
        try:
            import edge_tts
            self._edge_tts_available = True
            self._piper_available = True
            print(f"[TTS] edge-tts disponible. Voz: {self.voice_model}")
        except ImportError:
            self._edge_tts_available = False
            self._piper_available = False
            print("[TTS] edge-tts no instalado. Ejecuta: pip install edge-tts")

    def _find_ffmpeg(self):
        """Buscar ffmpeg (en Applio o en PATH)."""
        if self.applio_path:
            candidate = os.path.join(self.applio_path, "ffmpeg.exe")
            if os.path.exists(candidate):
                self._ffmpeg_path = candidate
                return
        # Buscar en PATH
        import shutil
        found = shutil.which("ffmpeg")
        if found:
            self._ffmpeg_path = found

    def _find_output_device(self):
        if self.output_device_name.lower() == "default":
            self.output_device_id = None
            print("[TTS] Usando dispositivo de salida predeterminado.")
            return

        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if (self.output_device_name.lower() in dev["name"].lower()
                        and dev["max_output_channels"] > 0):
                    self.output_device_id = i
                    print(f"[TTS] Dispositivo: {dev['name']} (id: {i})")
                    return

            print(f"[TTS] Dispositivo '{self.output_device_name}' no encontrado.")
            print("[TTS]   Disponibles:")
            for i, dev in enumerate(devices):
                if dev["max_output_channels"] > 0:
                    print(f"[TTS]     [{i}] {dev['name']}")
            self.output_device_id = None

        except Exception as e:
            print(f"[TTS] Error dispositivos: {e}")
            self.output_device_id = None

    def set_monitor_device(self, device_name: str):
        """Configura un segundo dispositivo de salida para monitoreo (auriculares)."""
        if not device_name or device_name.lower() in ('', 'none', 'off'):
            self.monitor_device_id = None
            return
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if (device_name.lower() in dev["name"].lower()
                        and dev["max_output_channels"] > 0):
                    self.monitor_device_id = i
                    print(f"[TTS] Monitor: {dev['name']} (id: {i})")
                    return
            print(f"[TTS] Monitor '{device_name}' no encontrado — sin monitoreo.")
            self.monitor_device_id = None
        except Exception as e:
            print(f"[TTS] Error al buscar monitor: {e}")
            self.monitor_device_id = None

    def synthesize(self, text):
        """Sintetizar texto a WAV. Retorna ruta al archivo o None."""
        if not text or not text.strip():
            return None

        import re
        # Si no hay letras o números, es solo puntuación/emojis, no sintetizar
        if not re.search(r'[A-Za-z0-9ñÑáéíóúÁÉÍÓÚ]', text):
            return None

        timestamp = int(time.time() * 1000)
        output_path = os.path.join(self.temp_dir, f"audio_{timestamp}.wav")

        try:
            # Opcion A: GPT-SoVITS (voz personalizada de Aiko)
            if self.gpt_sovits and self.gpt_sovits.ready:
                if self.gpt_sovits.synthesize(text, output_path, speed=self.speed):
                    preview = text[:50] + "..." if len(text) > 50 else text
                    print(f'[TTS] GPT-SoVITS: "{preview}"')
                    return output_path
                else:
                    print("[TTS] GPT-SoVITS fallo. Usando edge-tts como fallback.")
                    # NO retornar None -- caer al Opcion B (edge-tts)

            # Opción B: edge-tts + RVC
            if not self._edge_tts_available:
                print("[TTS] edge-tts no disponible y GPT-SoVITS apagado/falló.")
                return None

            edge_output = os.path.join(self.temp_dir, f"edge_{timestamp}.wav")
            
            # Paso 1: edge-tts
            if not self._synthesize_edge_tts(text, edge_output):
                return None

            # Paso 2: RVC (si disponible)
            if self._rvc_available and self._rvc_worker:
                rvc_output = os.path.join(self.temp_dir, f"rvc_{timestamp}.wav")
                t0 = time.time()
                if self._rvc_worker.convert(edge_output, rvc_output):
                    elapsed = time.time() - t0
                    print(f"[TTS-RVC] Voz convertida ({elapsed:.2f}s)")
                    try:
                        os.remove(edge_output)
                    except Exception:
                        pass
                    return rvc_output
                else:
                    print("[TTS-RVC] Fallback a edge-tts directo.")
                    return edge_output
            else:
                return edge_output

        except Exception as e:
            print(f"[TTS] Error: {e}")
            return None

    def _synthesize_edge_tts(self, text, output_path):
        """Sintetizar con edge-tts."""
        try:
            import edge_tts

            rate_pct = int((self.speed - 1.0) * 100)
            rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"
            mp3_path = output_path.replace(".wav", ".mp3")

            async def _generate():
                comm = edge_tts.Communicate(text, self.voice_model, rate=rate_str)
                await comm.save(mp3_path)

            loop = asyncio.new_event_loop()
            loop.run_until_complete(_generate())
            loop.close()

            if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) < 100:
                print("[TTS] edge-tts genero archivo vacio.")
                return False

            # MP3 -> WAV
            self._mp3_to_wav(mp3_path, output_path)
            try:
                os.remove(mp3_path)
            except Exception:
                pass

            if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                preview = text[:50] + "..." if len(text) > 50 else text
                print(f'[TTS] edge-tts: "{preview}"')
                return True
            else:
                print("[TTS] Conversion MP3->WAV fallo.")
                return False

        except Exception as e:
            print(f"[TTS] Error edge-tts: {e}")
            return False

    def _mp3_to_wav(self, mp3_path, wav_path):
        """Convertir MP3 a WAV con ffmpeg."""
        if self._ffmpeg_path:
            result = subprocess.run(
                [self._ffmpeg_path, "-i", mp3_path, "-y",
                 "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", wav_path],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return

        # Fallback: pydub
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_mp3(mp3_path)
            audio.export(wav_path, format="wav")
            return
        except ImportError:
            pass

        raise RuntimeError("ffmpeg o pydub requerido para MP3->WAV")

    def interrupt(self):
        """Interrumpir reproducción activa de forma limpia.

        Activa el flag → el callback de OutputStream silencia la salida
        y lanza CallbackStop en el próximo bloque de audio (~10–50 ms).
        """
        if self.is_speaking or self.is_playing:
            self._interrupt_flag.set()
            # Parar la stream de sounddevice si está activa
            if self._current_stream is not None:
                try:
                    self._current_stream.stop()
                except Exception:
                    pass
            # Fallback: detener cualquier sd.play() residual
            try:
                sd.stop()
            except Exception:
                pass
            self.is_speaking = False
            self.is_playing = False
            print("[TTS] ⚡ Reproducción interrumpida.")

    def play_audio(self, audio_path, blocking=True, on_start=None, on_volume=None):
        """Reproducir archivo WAV con soporte de interrupción.
        
        on_start: optional callback fired on the very first audio frame.
        on_volume: optional callback(rms: float) fired each audio chunk with
                   the RMS volume level (0.0–1.0) for real-time lip sync.
        """
        if not audio_path or not os.path.exists(audio_path):
            print(f"[TTS] Archivo no encontrado: {audio_path}")
            return False

        # Si hay un flag de interrupción pendiente, no reproducir
        if self._interrupt_flag.is_set():
            return False

        try:
            data, samplerate = sf.read(audio_path, dtype="float32")
            # Asegurar shape (N, 1) — base mono
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            elif data.shape[1] > 2:
                data = data[:, :2]   # recortar a estéreo máx

            duration = len(data) / samplerate

            # ── Detectar canales reales de cada dispositivo ──────────────
            def _dev_channels(device_id, fallback=2):
                if device_id is None:
                    return fallback
                try:
                    info = sd.query_devices(device_id)
                    ch = int(info.get('max_output_channels', fallback))
                    # Limitar a 2 (estéreo); virtualmente todos los drivers
                    # aceptan 2ch aunque el dispositivo sea 16ch.
                    return max(1, min(ch, 2))
                except Exception:
                    return fallback

            out_ch = _dev_channels(self.output_device_id)

            # ── Upmix mono → N canales según dispositivo ─────────────────
            def _upmix(d, n_ch):
                if d.shape[1] == n_ch:
                    return d
                if d.shape[1] < n_ch:
                    return np.repeat(d, n_ch, axis=1)
                return d[:, :n_ch]

            data_out = _upmix(data, out_ch)

            self.is_playing = True
            self.is_speaking = True
            disp = self.output_device_id if self.output_device_id is not None else "predeterminado"
            print(f"[TTS] Reproduciendo... (dispositivo: {disp}, {duration:.2f}s, {out_ch}ch)")

            # ── Reproducción via OutputStream con callback de interrupción ──
            frame_pos = [0]          # posición de lectura en data
            interrupted = [False]
            started = [False]        # track if first frame has played
            CHUNK = 2048             # ~46 ms a 44100 Hz

            def _callback(outdata, frames, _time, status):
                if self._interrupt_flag.is_set():
                    outdata[:] = 0
                    interrupted[0] = True
                    raise sd.CallbackStop()
                
                # Fire on_start callback on the very first audio frame
                if not started[0]:
                    started[0] = True
                    if on_start:
                        try: on_start()
                        except: pass
                
                start = frame_pos[0]
                end   = start + frames
                chunk = data_out[start:end]
                if len(chunk) < frames:
                    # Fin del audio: rellenar con silencio
                    outdata[:len(chunk)] = chunk
                    outdata[len(chunk):] = 0
                    frame_pos[0] = len(data_out)
                    # Send zero volume for the silence
                    if on_volume:
                        try: on_volume(0.0)
                        except: pass
                    raise sd.CallbackStop()
                outdata[:] = chunk
                frame_pos[0] = end
                
                # Compute RMS for real-time lip sync
                if on_volume:
                    try:
                        rms = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
                        on_volume(rms)
                    except: pass

            stream_kwargs = dict(
                samplerate=samplerate,
                channels=out_ch,
                dtype="float32",
                callback=_callback,
                blocksize=CHUNK,
            )
            if self.output_device_id is not None:
                stream_kwargs["device"] = self.output_device_id

            with sd.OutputStream(**stream_kwargs) as stream:
                self._current_stream = stream

                # ── Monitoreo en paralelo: mismos datos → auriculares/altavoces ──
                # Usa su propio OutputStream (no sd.play global) para evitar
                # conflictos con el OutputStream principal ya activo.
                if self.monitor_device_id is not None:
                    mon_ch = _dev_channels(self.monitor_device_id)
                    data_mon = _upmix(data, mon_ch)
                    mon_pos = [0]
                    def _monitor_callback(outdata, frames, _time, status):
                        start = mon_pos[0]
                        end   = start + frames
                        chunk = data_mon[start:end]
                        if len(chunk) < frames:
                            outdata[:len(chunk)] = chunk
                            outdata[len(chunk):] = 0
                            mon_pos[0] = len(data_mon)
                            raise sd.CallbackStop()
                        outdata[:] = chunk
                        mon_pos[0] = end

                    def _play_monitor():
                        try:
                            with sd.OutputStream(
                                samplerate=samplerate,
                                channels=mon_ch,
                                dtype="float32",
                                device=self.monitor_device_id,
                                callback=_monitor_callback,
                                blocksize=CHUNK,
                            ) as mon_stream:
                                while mon_stream.active:
                                    time.sleep(0.01)
                            print(f"[TTS] Monitor OK ({mon_ch}ch)")
                        except Exception as e:
                            print(f"[TTS] Monitor error: {e}")
                    threading.Thread(target=_play_monitor, daemon=True).start()

                if blocking:
                    # Esperar a que el callback termine (o sea interrumpido)
                    while stream.active and not self._interrupt_flag.is_set():
                        time.sleep(0.01)

            self._current_stream = None
            self.is_playing = False
            if not interrupted[0]:
                self.is_speaking = False
                print("[TTS] Reproduccion completa.")
            return not interrupted[0]

        except Exception as e:
            print(f"[TTS] Error reproduciendo: {e}")
            self._current_stream = None
            self.is_playing = False
            self.is_speaking = False
            return False

    def speak(self, text, blocking=True):
        """Pipeline completo: sintetizar + reproducir (con soporte de interrupción)."""
        self._interrupt_flag.clear()
        self.is_speaking = True
        try:
            audio_path = self.synthesize(text)
            if not audio_path:
                return False
            if self._interrupt_flag.is_set():
                # Interrumpido durante la síntesis
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
                return False
            success = self.play_audio(audio_path, blocking=blocking)
            try:
                os.remove(audio_path)
            except Exception:
                pass
            return success
        finally:
            self.is_speaking = False

    def speak_fast(self, text: str) -> bool:
        """TTS ultra-rápido para reacciones cortas — omite RVC.

        Usa GPT-SoVITS si está disponible, sino edge-tts directo.
        Pensado para reacciones de interrupción (< 5 palabras).
        """
        self._interrupt_flag.clear()
        self.is_speaking = True
        timestamp = int(time.time() * 1000)
        fast_out = os.path.join(self.temp_dir, f"fast_{timestamp}.wav")
        
        try:
            # Opción A: GPT-SoVITS
            if self.gpt_sovits and self.gpt_sovits.ready:
                if self.gpt_sovits.synthesize(text, fast_out, speed=self.speed):
                    if self._interrupt_flag.is_set():
                        return False
                    return self.play_audio(fast_out, blocking=True)

            # Opción B: edge-tts (Fallback rápido)
            if not self._edge_tts_available:
                return False
                
            if not self._synthesize_edge_tts(text, fast_out):
                return False
            if self._interrupt_flag.is_set():
                return False
            return self.play_audio(fast_out, blocking=True)
            
        except Exception as e:
            print(f"[TTS-Fast] Error: {e}")
            return False
        finally:
            self.is_speaking = False
            try:
                if os.path.exists(fast_out):
                    os.remove(fast_out)
            except Exception:
                pass

    def get_audio_duration(self, audio_path):
        try:
            data, sr = sf.read(audio_path)
            return len(data) / sr
        except Exception:
            return 0.0

    def cleanup(self):
        """Cerrar worker RVC, API de GPT-SoVITS y limpiar temporales."""
        if self._rvc_worker:
            self._rvc_worker.stop()
            print("[TTS] Worker RVC cerrado.")
            
        if self.gpt_sovits:
            self.gpt_sovits.stop()

        try:
            if os.path.exists(self.temp_dir):
                for f in os.listdir(self.temp_dir):
                    try:
                        os.remove(os.path.join(self.temp_dir, f))
                    except Exception:
                        pass
            print("[TTS] Archivos temporales limpiados.")
        except Exception as e:
            print(f"[TTS] Error limpieza: {e}")
