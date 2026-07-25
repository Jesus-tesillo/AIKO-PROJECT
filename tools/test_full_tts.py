"""Test del pipeline TTS optimizado con worker persistente."""
import sys, time
sys.path.insert(0, ".")
from modules.tts import TTS

print("=" * 50)
print("  TEST: Pipeline OPTIMIZADO (worker persistente)")
print("=" * 50)

tts = TTS(
    voice_model="es-MX-DaliaNeural",
    speed=1.1,
    output_device="Altavoces (USB Audio Device)",
    applio_path="C:/Users/Usuario/Downloads/Applio-3.6.2/Applio-3.6.2",
    rvc_model="C:/Users/Usuario/Downloads/Applio-3.6.2/Applio-3.6.2/logs/mi/NekoGirl.pth",
    rvc_index="",
    rvc_pitch=0,
    rvc_f0_method="fcpe",
)

# Primera conversion (puede ser mas lenta por warmup)
print("\n--- Frase 1 (warmup) ---")
t0 = time.time()
audio = tts.synthesize("Hola chat, soy Aiko!")
t1 = time.time()
print(f"Tiempo total: {t1-t0:.2f}s")
if audio:
    tts.play_audio(audio, blocking=True)
    import os; os.remove(audio)

# Segunda conversion (ya todo caliente)
print("\n--- Frase 2 (caliente) ---")
t0 = time.time()
audio = tts.synthesize("O sea, no mames, esto es mucho mas rapido ahora!")
t1 = time.time()
print(f"Tiempo total: {t1-t0:.2f}s")
if audio:
    tts.play_audio(audio, blocking=True)
    import os; os.remove(audio)

# Tercera
print("\n--- Frase 3 ---")
t0 = time.time()
audio = tts.synthesize("Miren mi nueva voz, estoy increible!")
t1 = time.time()
print(f"Tiempo total: {t1-t0:.2f}s")
if audio:
    tts.play_audio(audio, blocking=True)
    import os; os.remove(audio)

tts.cleanup()
print("\nTest completado!")
