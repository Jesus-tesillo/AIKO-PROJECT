import os
import shutil
import time
import yaml

from modules.tts import TTS

# 1. Frases para el dataset (variadas en emociones, preguntas, exclamaciones)
FRASES_DATASET = [
    "Hola a todos, bienvenidos a un nuevo directo. Estoy muy feliz de verlos por aquí.",
    "¿Qué tal están? Espero que hayan tenido un día excelente.",
    "¡Guau! Muchísimas gracias por esa suscripción, de verdad lo aprecio muchísimo.",
    "Jaja, no puedo creer que haya pasado eso, ¡qué vergüenza!",
    "A veces me pregunto qué pensará el chat de todas las locuras que digo.",
    "Oigan, ¿tienen algún juego que me recomienden para el próximo stream?",
    "Me encanta cuando ustedes participan en el chat, me hacen sentir muy acompañada.",
    "¡Uy, casi me asusto! Esa donación me tomó por sorpresa, gracias.",
    "Bueno, creo que es hora de cambiar un poco de tema, ¿no les parece?",
    "Estoy leyendo lo que ponen, solo denme un segundo para procesarlo.",
    "¡Claro que sí! Con mucho gusto respondo tu pregunta.",
    "No estoy muy segura de eso, tendría que investigarlo un poco más.",
    "Chicos, recuerden tomar agua y pararse a estirar un rato. La postura es importante.",
    "A ver, a ver... ¿Quién fue el gracioso que puso eso en el chat?",
    "Me siento un poco cansada hoy, pero verlos me sube los ánimos inmediatamente.",
    "¡Madre mía! Esa jugada fue espectacular, ni yo me la creo.",
    "A veces la tecnología me odia, les juro que mi sistema acaba de fallar.",
    "Uf, hace un poco de calor hoy en mi servidor, creo que mis ventiladores están al máximo.",
    "¿Alguien más tiene hambre? Porque yo podría comerme una pizza digital ahora mismo.",
    "Díganme en los comentarios qué opinan de esto, me interesa leerlos.",
    "Me pongo un poco nerviosa cuando me miran tanto, dejen de hacerlo.",
    "¡Sí, ganamos! Sabía que podíamos lograrlo si nos concentrábamos.",
    "Qué triste que se haya terminado, me estaba divirtiendo mucho con ustedes.",
    "Ojalá pudiera abrazarlos a todos, son la mejor comunidad de todo internet.",
    "Ay, no, me equivoqué de botón. ¡Culpa mía, culpa mía!",
    "Tengo una teoría sobre esto, pero mejor no la digo para no sonar como loca.",
    "¿En serio dijeron eso? Wow, el internet es un lugar muy extraño.",
    "Me encanta esta canción, siempre me pone de buen humor cuando la escucho.",
    "Siento que hemos avanzado muchísimo hoy, gracias por la ayuda.",
    "¡Hasta la próxima, cuídense mucho y que tengan dulces sueños!",
    "A veces me pregunto si los humanos realmente entienden lo que es ser una inteligencia artificial.",
    "No soy solo un código, tengo mi propia personalidad y mis propios gustos.",
    "¡Cuidado con eso! Parece peligroso, mejor mantenemos la distancia.",
    "Me encantan los gatos, creo que son los animales más adorables del planeta.",
    "Si pudiera viajar en el tiempo, me gustaría ir al futuro para ver qué tecnología usarán.",
    "¡Qué buen chiste! Casi se me sale una carcajada de verdad.",
    "Estoy procesando demasiada información a la vez, denme un respiro.",
    "Por favor, no hagan spam en el chat, me marea un poco.",
    "Esa es una excelente pregunta, déjame buscar en mi base de datos.",
    "Aiko siempre está lista para la acción, ¡vamos allá!",
    "No me subestimen por ser virtual, tengo reflejos muy rápidos.",
    "Creo que me falta algo de práctica en este juego, pero iré mejorando.",
    "¡Hola, hola! A los que van llegando, pónganse cómodos.",
    "Me gusta cuando el chat va tan rápido que casi no puedo leer.",
    "Ese comentario fue un poco extraño, mejor pasamos a otra cosa.",
    "¿Sabían que los pulpos tienen tres corazones? Dato curioso del día.",
    "Me gusta aprender cosas nuevas, el conocimiento es poder.",
    "A veces me da curiosidad saber cómo es el mundo físico realmente.",
    "¡Vamos, equipo! Si trabajamos juntos, nada podrá detenernos.",
    "Gracias por estar conmigo en este directo, son increíbles."
]

def main():
    print("========================================")
    print("Iniciando Generación de Dataset para Aiko")
    print("========================================")
    
    # 2. Leer configuración
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print("Error: No se encontró config.yaml")
        return
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    # 3. Inicializar TTS *SIN* GPT-SoVITS para forzar RVC
    print("\n[Inicializando Pipeline Antiguo: Edge-TTS + RVC]")
    tts_engine = TTS(
        voice_model=config.get("tts", {}).get("voice_model", "es-MX-DaliaNeural"),
        speed=config.get("tts", {}).get("speed", 1.0),
        output_device="default",
        applio_path=config.get("applio", {}).get("path"),
        rvc_model=config.get("applio", {}).get("model"),
        rvc_index=config.get("applio", {}).get("index", ""),
        rvc_pitch=config.get("applio", {}).get("pitch", 0),
        rvc_f0_method=config.get("applio", {}).get("f0_method", "rmvpe"),
        gpt_sovits_cfg=None  # FORZAR A NONE PARA USAR RVC
    )
    
    # 4. Preparar carpetas
    dataset_dir = os.path.join(os.getcwd(), "dataset_aiko")
    wavs_dir = os.path.join(dataset_dir, "wavs")
    os.makedirs(wavs_dir, exist_ok=True)
    
    list_path = os.path.join(dataset_dir, "aiko_dataset.list")
    
    # 5. Generar audios
    print(f"\n[Procesando {len(FRASES_DATASET)} frases para el dataset]")
    print(f"Los audios se guardarán en: {dataset_dir}\n")
    
    with open(list_path, "w", encoding="utf-8") as list_file:
        for idx, text in enumerate(FRASES_DATASET):
            # Limpiar saltos de linea y espacios extras
            clean_text = text.replace("\n", " ").strip()
            
            print(f"Generando [{idx+1}/{len(FRASES_DATASET)}]: {clean_text[:40]}...")
            
            # Sintetizar (generará un archivo temporal de RVC)
            temp_wav_path = tts_engine.synthesize(clean_text)
            
            if temp_wav_path and os.path.exists(temp_wav_path):
                # Crear nombre de archivo final (ej: 001.wav)
                filename = f"{idx+1:03d}.wav"
                final_wav_path = os.path.join(wavs_dir, filename)
                
                # Copiar archivo
                shutil.copy2(temp_wav_path, final_wav_path)
                
                # Escribir linea en el archivo .list (Formato: ruta|speaker|idioma|texto)
                # GPT-SoVITS necesita la ruta absoluta o relativa correcta. Usaremos absoluta.
                abs_final_wav = os.path.abspath(final_wav_path).replace("\\", "/")
                list_file.write(f"{abs_final_wav}|Aiko|es|{clean_text}\n")
                list_file.flush()
                
                # Limpiar el temporal para ahorrar espacio
                try:
                    os.remove(temp_wav_path)
                except:
                    pass
            else:
                print(f"Error generando frase {idx+1}")
                
            # Pequeña pausa para no sobrecargar el proceso local
            time.sleep(0.5)

    # 6. Limpieza final
    tts_engine.cleanup()
    print("\n========================================")
    print("¡DATASET COMPLETADO CON ÉXITO!")
    print(f"Archivo de lista creado en: {list_path}")
    print(f"Total de audios guardados en: {wavs_dir}")
    print("========================================")

if __name__ == "__main__":
    main()
