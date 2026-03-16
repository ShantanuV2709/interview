import asyncio
import websockets
import os
import json
from pathlib import Path

env_path = Path(".env")
key = ""
for line in env_path.read_text().splitlines():
    if line.startswith("SARVAM_AI_API="):
        key = line.split("=", 1)[1].strip()

async def test():
    url = "wss://api.sarvam.ai/speech-to-text/ws"
    headers = {"api-subscription-key": key}
    async with websockets.connect(url, extra_headers=headers) as ws:
        # try sending a config message
        await ws.send(json.dumps({
            "model": "saarika:v2.5",
            "language_code": "en-IN"
        }))
        res = await asyncio.wait_for(ws.recv(), timeout=2.0)
        print("Received:", res)

asyncio.run(test())
