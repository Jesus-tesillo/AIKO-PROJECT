import time
from modules.tiktok_chat import TikTokChatReader
import yaml

def main():
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error cargando config: {e}")
        return

    username = config.get("tiktok", {}).get("username", "vt_aiko")
    print(f"Iniciando prueba de conexión TikTokLive para el usuario: @{username}")
    
    # Iniciar la clase de TikTokChatReader
    reader = TikTokChatReader(username)
    reader.start()

    print("\nLeyendo chat por 20 segundos... (si no está Live dirá que está offline)")
    
    start_time = time.time()
    while time.time() - start_time < 20:
        msg = reader.get_message()
        if msg:
            print(f">>> RECIBIDO TIKTOK MESSAGE: {msg['user']}: {msg['message']}")
        time.sleep(0.5)

    print("\nPrueba de TikTok finalizada.")
    reader.stop()

if __name__ == "__main__":
    main()
