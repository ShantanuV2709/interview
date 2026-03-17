import asyncio, websockets
async def handler(websocket):
    print("request path:", getattr(websocket.request, "path", None))
    print("request object:", type(websocket.request))
    await websocket.send("ok")
    
async def main():
    async with websockets.serve(handler, "127.0.0.1", 3005):
        try:
            async with websockets.connect("ws://127.0.0.1:3005/brain") as ws:
                msg = await ws.recv()
                print("received", msg)
        except Exception as e:
            print("Client error", e)

asyncio.run(main())
