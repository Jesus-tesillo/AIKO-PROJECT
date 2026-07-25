import asyncio, websockets, json, sys
sys.stdout.reconfigure(encoding='utf-8')

async def test():
    ws = await websockets.connect('ws://localhost:8765')
    
    tests = [
        ('react', 'surprised', 4, 'SURPRISE - should show shock marks'),
        ('react', 'thinking', 4, 'THINKING - should show question marks'),
        ('react', 'angry', 4, 'ANGRY - should show angry face'),
        ('react', 'excited', 4, 'EXCITED - should show star eyes'),
        ('react', 'happy', 4, 'HAPPY - should show heart eyes'),
        ('react', 'sad', 4, 'SAD - should show tears'),
        ('react', 'annoyed', 4, 'ANNOYED - should show eye roll'),
        ('react', 'smug', 4, 'SMUG - should show tongue'),
    ]
    
    for action, emotion, dur, desc in tests:
        cmd = json.dumps({'action': action, 'emotion': emotion, 'duration': dur})
        await ws.send(cmd)
        print(f'>> {desc}')
        await asyncio.sleep(dur + 0.5)
    
    print('ALL DONE')
    await ws.close()

asyncio.run(test())
