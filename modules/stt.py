"""
stt.py - Speech-to-Text using faster-whisper for mic input.

Listens to the microphone and transcribes speech to text.
Runs on CPU using the faster-whisper library (CTranslate2 backend).
This module is OPTIONAL and disabled by default in config.yaml.
"""

import threading
import queue
import time
import numpy as np


class STT:
    """Speech-to-Text module using faster-whisper."""

    def __init__(self, model_size: str = "small", language: str = "en",
                 enabled: bool = False):
        self.model_size = model_size
        self.language = language
        self.enabled = enabled
        self.model = None
        self.is_listening = False
        self.transcript_queue = queue.Queue()
        self._listen_thread = None
        self._stop_event = threading.Event()

        if not self.enabled:
            print("[STT] Disabled in config. Set stt.enabled: true to activate.")
            return

        # Try to load the model
        self._load_model()

    def _load_model(self):
        """Load the faster-whisper model."""
        try:
            from faster_whisper import WhisperModel

            print(f"[STT] Loading whisper model '{self.model_size}' on CPU...")
            self.model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8"  # Optimized for CPU
            )
            print(f"[STT] ✓ Whisper model '{self.model_size}' loaded successfully.")

        except ImportError:
            print("[STT] ✗ faster-whisper not installed. Run: pip install faster-whisper")
            self.enabled = False
        except Exception as e:
            print(f"[STT] ✗ Error loading model: {e}")
            self.enabled = False

    def start_listening(self):
        """Start listening to the microphone in a background thread."""
        if not self.enabled or self.model is None:
            print("[STT] Cannot start: module is disabled or model not loaded.")
            return

        if self.is_listening:
            print("[STT] Already listening.")
            return

        self._stop_event.clear()
        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="STT-Listener"
        )
        self._listen_thread.start()
        self.is_listening = True
        print("[STT] 🎤 Listening to microphone...")

    def stop_listening(self):
        """Stop the microphone listening thread."""
        self._stop_event.set()
        self.is_listening = False
        print("[STT] Stopped listening.")

    def _listen_loop(self):
        """
        Main listening loop. Records audio chunks from the mic
        and transcribes them when speech is detected.
        """
        try:
            import sounddevice as sd

            # Audio recording parameters
            samplerate = 16000  # Whisper expects 16kHz
            block_duration = 3  # Record in 3-second chunks
            block_size = int(samplerate * block_duration)
            silence_threshold = 0.01  # RMS threshold for speech detection

            print(f"[STT] Recording at {samplerate}Hz, "
                  f"{block_duration}s chunks, threshold={silence_threshold}")

            while not self._stop_event.is_set():
                try:
                    # Record a chunk of audio
                    audio = sd.rec(
                        block_size,
                        samplerate=samplerate,
                        channels=1,
                        dtype="float32"
                    )
                    sd.wait()

                    # Check if there's speech (not just silence)
                    rms = np.sqrt(np.mean(audio ** 2))
                    if rms < silence_threshold:
                        continue  # Skip silent chunks

                    # Transcribe the audio
                    segments, info = self.model.transcribe(
                        audio.flatten(),
                        language=self.language,
                        beam_size=3,
                        vad_filter=True  # Filter out non-speech
                    )

                    # Collect transcribed text
                    text = " ".join(
                        segment.text.strip()
                        for segment in segments
                    ).strip()

                    if text and len(text) > 2:  # Ignore very short artifacts
                        print(f"[STT] 🎤 Heard: \"{text}\"")
                        self.transcript_queue.put({
                            "text": text,
                            "timestamp": time.time()
                        })

                except Exception as e:
                    if not self._stop_event.is_set():
                        print(f"[STT] Error in listen loop: {e}")
                    time.sleep(0.5)

        except Exception as e:
            print(f"[STT] Fatal error in listener: {e}")
            self.is_listening = False

    def get_transcript(self) -> dict:
        """
        Get the next transcribed text from the queue (non-blocking).

        Returns:
            Dict with 'text' and 'timestamp', or None if queue is empty.
        """
        try:
            return self.transcript_queue.get_nowait()
        except queue.Empty:
            return None

    def has_transcript(self) -> bool:
        """Check if there's a pending transcript."""
        return not self.transcript_queue.empty()
