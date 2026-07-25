"""Quick audio test — plays a beep on each output device so you can find yours."""
import numpy as np
import sounddevice as sd
import time

# List all output devices
print("\n=== DISPOSITIVOS DE SALIDA ===\n")
devices = sd.query_devices()
output_devs = []
for i, dev in enumerate(devices):
    if dev["max_output_channels"] > 0:
        output_devs.append((i, dev["name"]))
        print(f"  [{i}] {dev['name']}")

# Generate a simple beep tone (440Hz, 1 second)
samplerate = 44100
duration = 1.5
t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

print("\n=== PRUEBA: Voy a reproducir un BEEP en cada dispositivo ===")
print("    Escucha con atención cuál suena en tus auriculares.\n")

for dev_id, dev_name in output_devs:
    try:
        short_name = dev_name[:45]
        print(f"  ▶ Probando [{dev_id}] {short_name}...", end=" ", flush=True)
        sd.play(tone, samplerate, device=dev_id)
        sd.wait()
        print("✓ Listo")
        time.sleep(0.5)
    except Exception as e:
        print(f"✗ Error: {e}")

print("\n¿En cuál escuchaste el BEEP? Anota el número entre corchetes [X]")
print("Ese número es tu dispositivo correcto.\n")
