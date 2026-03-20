import asyncio
import websockets
import json

async def test():
    try:
        async with websockets.connect('ws://localhost:3002') as ws:
            print("Connected.")
            await ws.send(json.dumps({
                'action': 'ask',
                'prev': 'Hello?',
                'transcript': 'My name is Shantal Verma.',
                'nextQ': 'What is React.js?',
                'history': [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}]
            }))
            print("Sent request.")
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    print(f'Received audio chunk: {len(msg)} bytes')
                else:
                    data = json.loads(msg)
                    print(f'Received JSON: {data}')
                    if data.get('type') in ['done', 'error']:
                        break
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
