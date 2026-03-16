import asyncio
import websockets
import json
import httpx
import base64
import sys
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = Path(__file__).parent / "ws_server.log"

def log(msg):
    full_msg = f"[LOG] {msg}"
    print(full_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_msg + "\n")

# ── Environment ───────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
envs = {}
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            envs[key.strip()] = val.strip()

OPENAI_KEY   = envs.get("OPENAI_API_KEY", "")
SARVAM_KEY   = envs.get("SARVAM_AI_API", "")
DEEPGRAM_KEY = envs.get("DEEPGRAM_API_KEY", "")

# ── OpenAI Brain import ───────────────────────────────────────────────────────
try:
    from openai_brain import brain, brain_ws_handler
    BRAIN_ENABLED = True
    log("OpenAI Brain loaded ✓")
except ImportError as e:
    BRAIN_ENABLED = False
    log(f"OpenAI Brain not available: {e}")
    # Stub so code below still works even without the brain module
    class _BrainStub:
        def record(self, *a, **kw): pass
    brain = _BrainStub()
    async def brain_ws_handler(ws): pass

# ── Persistent HTTP client ────────────────────────────────────────────────────
httpx_client = httpx.AsyncClient(timeout=30.0)

async def warmup_dns():
    try:
        await httpx_client.head("https://api.deepgram.com", timeout=5.0)
        log("Deepgram DNS warmed up")
        brain.record("deepgram_tts", "dns_warmup", "DNS warmed up successfully", "ok")
    except Exception as e:
        log(f"DNS warmup failed: {e}")
        brain.record("deepgram_tts", "dns_warmup", f"DNS warmup failed: {e}", "warn")

# ── Deepgram TTS ──────────────────────────────────────────────────────────────
async def deepgram_tts(text_segment: str, ws) -> None:
    if not DEEPGRAM_KEY or not text_segment.strip():
        return

    for attempt in range(1, 4):
        try:
            url = "https://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=mp3"
            resp = await httpx_client.post(
                url,
                headers={
                    "Authorization": f"Token {DEEPGRAM_KEY}",
                    "Content-Type": "application/json",
                },
                json={"text": text_segment.strip()},
            )

            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} — {resp.text[:120]}"
                log(f"Deepgram TTS error: {err_msg}")
                brain.record("deepgram_tts", "tts_request", err_msg, "error")
                return

            audio_bytes = resp.content
            # Send binary audio directly
            await ws.send(audio_bytes)
            brain.record("deepgram_tts", "tts_request",
                         f"Sent {len(audio_bytes)} bytes for segment", "ok")
            return  # success

        except Exception as e:
            log(f"Deepgram TTS attempt {attempt} failed: {e}")
            brain.record("deepgram_tts", "tts_request",
                         f"Attempt {attempt} failed: {e}", "error" if attempt == 3 else "warn")
            await asyncio.sleep(0.5)

# ── OpenAI Streaming ──────────────────────────────────────────────────────────
async def openai_stream(prev, user_ans, next_q_prompt, ws):
    brain.record("openai_llm", "stream_start", f"prev_len={len(prev)} ans_len={len(user_ans)}", "info")

    prompt = f"""You are a conversational tech interviewer.
Previous Question: "{prev}"
User's Answer/Reply: "{user_ans}"

Your task:
Identify if the user asked to repeat the question or end the interview.
If REPEAT: Repeat the question.
If END: Say goodbye and append "[[END_INTERVIEW]]".
Else: Acknowledge briefly and move to: "{next_q_prompt}".
Output RAW TEXT only. No JSON."""

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "system", "content": prompt}],
                    "temperature": 0.7,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                timeout=30.0,
            ) as response:
                if response.status_code != 200:
                    err = f"OpenAI HTTP {response.status_code}"
                    log(f"OpenAI error: {err}")
                    brain.record("openai_llm", "stream_error", err, "error")
                    await ws.send(json.dumps({"type": "error", "msg": err}))
                    return

                buffer = ""
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line.strip() != "data: [DONE]":
                        try:
                            data = json.loads(line[6:])
                            content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                buffer += content
                                await ws.send(json.dumps({"type": "token", "text": content}))
                                if any(p in content for p in [".", "?", "!", "\n"]):
                                    sentence = buffer.strip()
                                    buffer = ""
                                    if sentence:
                                        await deepgram_tts(sentence, ws)
                        except:
                            continue

                if buffer.strip():
                    await deepgram_tts(buffer.strip(), ws)

                brain.record("openai_llm", "stream_complete", "Streaming finished", "ok")

    except Exception as e:
        log(f"Streaming error: {e}")
        brain.record("openai_llm", "stream_exception", str(e), "error")
        await ws.send(json.dumps({"type": "error", "msg": str(e)}))

# ── Deepgram STT Proxy ────────────────────────────────────────────────────────
async def deepgram_proxy(ws_frontend, sample_rate=16000):
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language=en-IN&smart_format=true&punctuate=true&encoding=linear16&sample_rate={sample_rate}"
    
    if not DEEPGRAM_KEY:
        brain.record("deepgram_stt", "config_error", "Key missing", "error")
        return

    # Using extra_headers for older websockets versions, or additional_headers for newer.
    # We found version 10.4 uses extra_headers.
    try:
        async with websockets.connect(dg_url, extra_headers={"Authorization": f"Token {DEEPGRAM_KEY}"}) as ws_dg:
            brain.record("deepgram_stt", "connected", "Connected to Deepgram", "ok")

            async def forward():
                async for msg in ws_frontend:
                    await ws_dg.send(msg)
            
            async def backward():
                async for msg in ws_dg:
                    await ws_frontend.send(msg)

            await asyncio.gather(forward(), backward())
    except Exception as e:
        log(f"DG Proxy error: {e}")
        brain.record("deepgram_stt", "proxy_error", str(e), "error")

# ── WebSocket handler ─────────────────────────────────────────────────────────
async def handler(websocket):
    path = getattr(websocket, "path", "/")
    if path == "/brain":
        await brain_ws_handler(websocket)
        return

    brain.record("frontend_ws", "client_connect", f"Path: {path}", "info")
    try:
        async for msg in websocket:
            try:
                data = json.loads(msg)
                action = data.get("action")
                if action == "ask":
                    await openai_stream(data.get("prev", ""), data.get("transcript", ""), data.get("nextQ", ""), websocket)
                    await websocket.send(json.dumps({"type": "done"}))
                elif action == "stt":
                    await deepgram_proxy(websocket, data.get("sample_rate", 16000))
            except:
                continue
    except:
        pass
    finally:
        brain.record("frontend_ws", "client_disconnect", "Session ended", "info")

async def main():
    log("Logic server running on ws://127.0.0.1:3002")
    brain.record("websocket_server", "startup", f"OpenAI={OPENAI_KEY[:4]}... Deepgram={DEEPGRAM_KEY[:4]}...", "ok")
    asyncio.create_task(warmup_dns())
    if BRAIN_ENABLED:
        brain.record("websocket_server", "listen", "Server started on ws://127.0.0.1:3002", "ok")
        asyncio.create_task(brain.start_periodic_analysis())
    async with websockets.serve(handler, "127.0.0.1", 3002):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
