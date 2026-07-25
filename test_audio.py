
# -*- coding: utf-8 -*-
"""
test_audio.py -- Diagnostico completo de audio para Aiko VTuber
Ejecutar: python test_audio.py
"""
import sys
import os
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
import tempfile
import requests

# Forzar UTF-8 en la terminal si es posible
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def sep(title=""):
    print("\n" + "="*60)
    if title:
        print(title)
        print("="*60)

# ─────────────────────────────────────────────────────────────────────────────
# PASO 0: Listar dispositivos
# ─────────────────────────────────────────────────────────────────────────────
def step0_list_devices():
    sep("PASO 0: Dispositivos de audio disponibles")
    devices = sd.query_devices()
    output_devs = []
    for i, dev in enumerate(devices):
        marker = ""
        if dev["max_output_channels"] > 0:
            output_devs.append((i, dev))
            marker += "  [SALIDA]"
        if dev["max_input_channels"] > 0:
            marker += "  [ENTRADA]"
        print(f"  [{i:2d}] {dev['name']}{marker}  "
              f"(out:{dev['max_output_channels']}ch in:{dev['max_input_channels']}ch)")

    default_out = sd.default.device[1]
    dname = devices[default_out]['name'] if default_out is not None else "N/A"
    print(f"\n  >> DEFAULT del sistema: [{default_out}] {dname}")
    return output_devs, devices

# ─────────────────────────────────────────────────────────────────────────────
# PASO 1: Beep en cada dispositivo
# ─────────────────────────────────────────────────────────────────────────────
def make_beep(freq=440, duration=0.7, samplerate=44100):
    t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
    wave = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    return wave, samplerate

def step1_test_beeps(output_devs, devices):
    sep("PASO 1: Probando BEEP en cada dispositivo de salida")
    results = {}
    mono_wave, sr = make_beep(440, 0.7)

    for dev_id, dev in output_devs:
        max_ch = min(dev["max_output_channels"], 2)
        if max_ch == 1:
            wave = mono_wave.reshape(-1, 1)
        else:
            wave = np.column_stack([mono_wave, mono_wave])

        try:
            print(f"\n  [{dev_id:2d}] {dev['name']} ({max_ch}ch)... ", end="", flush=True)
            sd.play(wave, samplerate=sr, device=dev_id, blocking=True)
            sd.stop()
            print("OK")
            results[dev_id] = "OK"
        except Exception as e:
            print(f"ERROR: {e}")
            results[dev_id] = f"ERROR: {e}"

    return results

# ─────────────────────────────────────────────────────────────────────────────
# PASO 2: Probar GPT-SoVITS
# ─────────────────────────────────────────────────────────────────────────────
def step2_test_gptsovits():
    sep("PASO 2: Probando API de GPT-SoVITS (http://127.0.0.1:9880)")

    try:
        r = requests.get("http://127.0.0.1:9880", timeout=3)
        print(f"  Servidor responde: HTTP {r.status_code} OK")
    except Exception as e:
        print(f"  Servidor NO responde: {e}")
        print("  >> Saltando test de sintesis de voz.")
        return None

    ref_audio = "C:/Users/Usuario/Desktop/Aprte/my-ai-vtuber/aiko_ref.mp3"
    if not os.path.exists(ref_audio):
        print(f"  Audio de referencia no encontrado: {ref_audio}")
        return None

    payload = {
        "text": "Hola. Soy Aiko. Esta es una prueba de audio.",
        "text_lang": "en",
        "ref_audio_path": ref_audio,
        "prompt_text": "Hola, me llamo Aiko, soy una inteligencia artificial y estoy lista para transmitir.",
        "prompt_lang": "auto",
        "text_split_method": "cut0",
        "speed": 1.0,
    }

    tmp_wav = os.path.join(tempfile.gettempdir(), "aiko_test.wav")
    try:
        print("  Sintetizando...", end=" ", flush=True)
        t0 = time.time()
        r = requests.post("http://127.0.0.1:9880/tts", json=payload, timeout=30)
        elapsed = time.time() - t0
        if r.status_code == 200:
            with open(tmp_wav, "wb") as f:
                f.write(r.content)
            size = os.path.getsize(tmp_wav)
            print(f"OK  ({elapsed:.2f}s, {size} bytes)")
            return tmp_wav
        else:
            print(f"ERROR HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# PASO 3: Reproducir WAV en cada dispositivo
# ─────────────────────────────────────────────────────────────────────────────
def step3_play_wav(wav_path, output_devs, devices):
    sep("PASO 3: Reproduciendo VOZ sintetizada en cada dispositivo")

    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    duration = len(data) / sr
    print(f"  Duracion: {duration:.2f}s | SR: {sr}Hz | Shape: {data.shape}")

    results = {}
    for dev_id, dev in output_devs:
        max_ch = min(dev["max_output_channels"], 2)
        if data.shape[1] < max_ch:
            wav = np.repeat(data, max_ch, axis=1)
        else:
            wav = data[:, :max_ch]

        try:
            print(f"\n  [{dev_id:2d}] {dev['name']} ({max_ch}ch)... ", end="", flush=True)
            sd.play(wav, samplerate=sr, device=dev_id, blocking=True)
            sd.stop()
            print("OK  << Escuchaste la voz aqui?")
            results[dev_id] = "OK"
        except Exception as e:
            print(f"ERROR: {e}")
            results[dev_id] = f"ERROR: {e}"

    return results

# ─────────────────────────────────────────────────────────────────────────────
# PASO 4: Verificar config.yaml
# ─────────────────────────────────────────────────────────────────────────────
def step4_check_config(output_devs, devices):
    sep("PASO 4: Verificando config.yaml")
    try:
        import yaml
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        tts_cfg = cfg.get("tts", {})
        out_name = tts_cfg.get("output_device", "")
        mon_name = tts_cfg.get("monitor_device", "")
        print(f"  output_device  : '{out_name}'")
        print(f"  monitor_device : '{mon_name}'")

        def find_dev(name):
            if not name or name.lower() == "default":
                return None, "default"
            for did, dev in output_devs:
                if name.lower() in dev["name"].lower():
                    return did, dev["name"]
            return None, "NO ENCONTRADO"

        out_id, out_match = find_dev(out_name)
        mon_id, mon_match = find_dev(mon_name)
        print(f"\n  output_device  -> [{out_id}] {out_match}")
        print(f"  monitor_device -> [{mon_id}] {mon_match}")

        if out_id is None and out_name.lower() != "default":
            print(f"\n  PROBLEMA: '{out_name}' no matchea ningun dispositivo de salida!")
        if mon_id is None and mon_name:
            print(f"\n  PROBLEMA: monitor '{mon_name}' no matchea ningun dispositivo!")

        return out_id, mon_id
    except Exception as e:
        print(f"  Error leyendo config: {e}")
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# RESUMEN
# ─────────────────────────────────────────────────────────────────────────────
def resumen(beep_results, play_results, out_id, mon_id, devices):
    sep("RESUMEN")
    beep_ok  = [did for did, res in beep_results.items() if res == "OK"]
    play_ok  = [did for did, res in play_results.items() if res == "OK"]
    print(f"\n  Dispositivos con beep OK : {beep_ok}")
    print(f"  Dispositivos con voz OK  : {play_ok}")
    print(f"\n  output_device en config  : [{out_id}]")
    print(f"  monitor_device en config : [{mon_id}]")

    if mon_id is not None:
        r = play_results.get(mon_id, "NO PROBADO")
        print(f"\n  Monitor [{mon_id}] resultado: {r}")
        if r != "OK":
            print("  >> El monitor tiene errores. Cambia monitor_device en config.yaml")
            for wid in play_ok:
                if wid != out_id and wid is not None:
                    print(f"     Sugerencia: usar [{wid}] {devices[wid]['name']}")
                    break

    print("\n  ACCION: Di aqui en que numero de dispositivo")
    print("  escuchaste el beep o la voz, y lo corrijo en config.yaml.")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*50)
    print("   TEST DE AUDIO -- AIKO VTUBER")
    print("="*50)
    print("\n  Sube el volumen al maximo en tus altavoces/auriculares.")
    print("  El test reproducira un beep y la voz en CADA dispositivo.")
    input("\n  Presiona ENTER para comenzar...\n")

    output_devs, devices = step0_list_devices()

    input("\n  Presiona ENTER para empezar test de beeps...\n")
    beep_results = step1_test_beeps(output_devs, devices)

    wav_path = step2_test_gptsovits()

    play_results = {}
    if wav_path and os.path.exists(wav_path):
        input("\n  Presiona ENTER para reproducir la VOZ en cada dispositivo...\n")
        play_results = step3_play_wav(wav_path, output_devs, devices)
    else:
        print("\n  (Sin WAV de GPT-SoVITS, usando resultados de beep)")
        play_results = beep_results

    out_id, mon_id = step4_check_config(output_devs, devices)
    resumen(beep_results, play_results, out_id, mon_id, devices)

    print("\n" + "="*60)
    print("TEST COMPLETADO -- Pega la salida aqui para corregir config.")
    print("="*60 + "\n")
