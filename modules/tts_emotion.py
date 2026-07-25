"""tts_emotion.py - Variación de Voz Emocional para TTS.
Envuelve el TTS base para variar velocidad/pausas por emoción y agrega efecto sutil de sala.
"""
import os, time, threading
import numpy as np


class EmotionalTTS:
    """TTS wrapper with emotion-driven speech variation."""

    EMOTION_SPEEDS = {
        "happy": 1.20, "excited": 1.35, "sad": 0.85, "surprised": 1.10,
        "thinking": 0.92, "neutral": 1.00, "bored": 0.88, "angry": 1.15,
    }
    PAUSE_BEFORE = {"surprised": 0.15, "thinking": 0.25}

    def __init__(self, base_tts, default_speed: float = 1.0):
        self.base_tts = base_tts
        self.default_speed = default_speed
        self.current_emotion = "neutral"
        self._lock = threading.Lock()
        print("[TTS-Emoción] ✓ Variación de voz emocional habilitada.")

    def set_emotion(self, emotion: str):
        with self._lock:
            self.current_emotion = emotion.lower()

    def get_speed_for_emotion(self, emotion: str = None) -> float:
        em = (emotion or self.current_emotion).lower()
        return self.default_speed * self.EMOTION_SPEEDS.get(em, 1.0)

    def synthesize(self, text: str, emotion: str = None) -> str:
        em = (emotion or self.current_emotion).lower()
        original_speed = self.base_tts.speed
        self.base_tts.speed = self.get_speed_for_emotion(em)
        audio_path = self.base_tts.synthesize(text)
        self.base_tts.speed = original_speed
        if audio_path and os.path.exists(audio_path):
            audio_path = self._apply_room_effect(audio_path)
        return audio_path

    def speak(self, text: str, emotion: str = None, blocking: bool = True) -> bool:
        em = (emotion or self.current_emotion).lower()
        pause = self.PAUSE_BEFORE.get(em, 0)
        if pause > 0:
            time.sleep(pause)
        audio_path = self.synthesize(text, em)
        if audio_path:
            success = self.base_tts.play_audio(audio_path, blocking=blocking)
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception:
                pass
            return success
        return False

    def _apply_room_effect(self, audio_path: str) -> str:
        """Apply subtle room reverb via numpy convolution."""
        try:
            import soundfile as sf
            data, sr = sf.read(audio_path, dtype="float32")
            ir_len = int(sr * 0.03)
            impulse = np.zeros(ir_len, dtype=np.float32)
            impulse[0] = 1.0
            for delay_s, vol in [(0.005, 0.15), (0.012, 0.08), (0.020, 0.04)]:
                idx = int(delay_s * sr)
                if idx < ir_len:
                    impulse[idx] = vol
            if data.ndim == 1:
                processed = np.convolve(data, impulse, mode="same")
            else:
                processed = np.zeros_like(data)
                for ch in range(data.shape[1]):
                    processed[:, ch] = np.convolve(data[:, ch], impulse, mode="same")
            peak = np.max(np.abs(processed))
            if peak > 0:
                processed = processed * (0.95 / peak)
            sf.write(audio_path, processed, sr)
            return audio_path
        except Exception as e:
            print(f"[TTS-Emoción] Efecto de sala saltado: {e}")
            return audio_path

    @property
    def is_playing(self) -> bool:
        return self.base_tts.is_playing

    @property
    def is_speaking(self) -> bool:
        return self.base_tts.is_speaking

    def interrupt(self):
        """Interrumpir reproducción activa propagando al TTS base."""
        self.base_tts.interrupt()

    def speak_fast(self, text: str) -> bool:
        """TTS ultra-rápido sin RVC para reacciones de interrupción."""
        return self.base_tts.speak_fast(text)

    def cleanup(self):
        self.base_tts.cleanup()
