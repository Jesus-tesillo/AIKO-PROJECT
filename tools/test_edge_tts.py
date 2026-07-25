"""Test rápido de edge-tts."""
import asyncio
import os
import edge_tts

async def test():
    text = "Hola, soy Aiko. Esto es una prueba de voz en español."
    comm = edge_tts.Communicate(text, "es-MX-DaliaNeural")
    await comm.save("test_edge.mp3")
    size = os.path.getsize("test_edge.mp3")
    print(f"Tamaño del audio: {size} bytes")
    os.remove("test_edge.mp3")
    print("edge-tts funciona correctamente!")

asyncio.run(test())
