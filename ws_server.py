import asyncio
import websockets
import json
import httpx
import base64
import sys
import re
from typing import cast, Dict, Any, List, Optional
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
        async def start_periodic_analysis(self): pass
    brain = _BrainStub()
    async def brain_ws_handler(ws): pass

# ── Persistent HTTP client ────────────────────────────────────────────────────
httpx_client = httpx.AsyncClient(timeout=30.0)

async def warmup_dns():
    try:
        await httpx_client.head("https://api.sarvam.ai/text-to-speech", timeout=5.0)
        log("Sarvam DNS warmed up")
        brain.record("sarvam_tts", "dns_warmup", "DNS warmed up successfully", "ok")
    except Exception as e:
        log(f"DNS warmup failed: {e}")
        brain.record("sarvam_tts", "dns_warmup", f"DNS warmup failed: {e}", "warn")

# ── Sarvam TTS ────────────────────────────────────────────────────────────────
async def sarvam_tts(text_segment: str, ws) -> None:
    if not SARVAM_KEY or not text_segment.strip():
        return

    for attempt in range(1, 4):
        try:
            url = "https://api.sarvam.ai/text-to-speech"
            payload = {
                "inputs": [text_segment.strip()],
                "target_language_code": "en-IN",
                "speaker": "anushka",
                "model": "bulbul:v2",
                "audio_format": "mp3",
                "pace": 0.95,
                "pitch": 0,
                "loudness": 1.4,
                "enable_preprocessing": True
            }
            resp = await httpx_client.post(
                url,
                headers={
                    "api-subscription-key": SARVAM_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} — {resp.text[:120]}"
                log(f"Sarvam TTS error: {err_msg}")
                brain.record("sarvam_tts", "tts_request", err_msg, "error")
                return

            data = resp.json()
            if "audios" in data and data["audios"]:
                # Sarvam returns a list of base64 strings
                audio_base64 = data["audios"][0]
                audio_bytes = base64.b64decode(audio_base64)
                
                # Send binary audio directly
                await ws.send(audio_bytes)
                brain.record("sarvam_tts", "tts_request",
                             f"Sent {len(audio_bytes)} bytes for segment", "ok")
                return  # success
            else:
                log(f"Sarvam TTS returned no audios: {data}")
                brain.record("sarvam_tts", "tts_request", "No audios in response", "error")
                return

        except Exception as e:
            log(f"Sarvam TTS attempt {attempt} failed: {e}")
            brain.record("sarvam_tts", "tts_request",
                         f"Attempt {attempt} failed: {e}", "error" if attempt == 3 else "warn")
            await asyncio.sleep(0.5)

# ── OpenAI Streaming ──────────────────────────────────────────────────────────
async def openai_stream(prev: str, user_ans: str, next_q_prompt: str, ws) -> None:
    brain.record("openai_llm", "stream_start", f"prev_len={len(prev)} ans_len={len(user_ans)}", "info")

    prompt = f"""You are a conversational tech interviewer.
Previous Question: "{prev}"
User's Answer/Reply: "{user_ans}"

Your task:
Identify the user's intent and respond accordingly:
1. END: If the user asks to end or stop the interview, say goodbye and append exactly ONE "[[END_INTERVIEW]]" tag at the very end.
2. REPEAT: If the user asks to repeat the question, acknowledge and repeat exactly. Append exactly ONE "[[REPEAT]]" tag at the very end.
3. ELABORATE: If the user asks to elaborate, explain, or clarify the question, provide a helpful explanation without giving away the answer. Append exactly ONE "[[REPEAT]]" tag at the very end.
4. CHEATING: If the user asks for the answer (e.g. "tell me the answer"), politely decline, state that you cannot provide the answer, encourage them, and ask if they'd like to attempt it or move on. Append exactly ONE "[[REPEAT]]" tag at the very end.
5. RETRY: If the user says they want to try answering again, encourage them to go ahead and append exactly ONE "[[REPEAT]]" tag at the very end.
6. PREVIOUS / JUMP ONLY ON ERROR: If the user says the AI skipped a question, or asks to go back/jump to a previous question (e.g., "go to question 3"):
   - Allowed ONLY IF their previous answer was clearly missed by the system (e.g. they complain about a transcription failure or you see "[Transcription failed]"). If allowed, apologize and append exactly ONE "[[PREVIOUS]]" or "[[JUMP:X]]" tag (where X is the number).
   - Denied if they are just trying to change an already submitted answer. Politely tell them that submitted answers cannot be changed and move to: "{next_q_prompt}".
7. NO_AUDIO: If the user's answer is exactly "[Transcription failed]" or "[No speech detected]", or they didn't say anything, politely state that you didn't catch their response and ask them to repeat. Append exactly ONE "[[REPEAT]]" tag at the very end. Do NOT move to the next question.
8. ANSWERED: Otherwise, evaluate their answer briefly (1 short sentence) and move to: "{next_q_prompt}".

Speak naturally. Do not output multiple tags."""

    buffer_list: List[str] = []
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

                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line.strip() != "data: [DONE]":
                        try:
                            line_data = json.loads(line[6:])
                            choices = line_data.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            content = str(delta.get("content", ""))
                            if content:
                                buffer_list.append(content)
                                await ws.send(json.dumps({"type": "token", "text": content}))
                                if any(p in content for p in [".", "?", "!", "\n"]):
                                    sentence = "".join(buffer_list).strip()
                                    buffer_list = []
                                    if sentence:
                                        clean_sentence = re.sub(r'\[\[.*?\]\]', '', sentence).strip()
                                        if clean_sentence:
                                            await sarvam_tts(clean_sentence, ws)
                        except Exception as e:
                            log(f"Token parsing error: {e}")
                            continue

                final_text = "".join(buffer_list).strip()
                if final_text:
                    clean_buffer = re.sub(r'\[\[.*?\]\]', '', final_text).strip()
                    if clean_buffer:
                        await sarvam_tts(clean_buffer, ws)

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

    # Using additional_headers as a list of tuples for better compatibility
    headers = [("Authorization", f"Token {DEEPGRAM_KEY}")]
    try:
        async with websockets.connect(dg_url, additional_headers=headers) as ws_dg:
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
            except Exception as e:
                log(f"Handler error: {e}")
                continue
    except Exception as e:
        log(f"Root handler error: {e}")
    finally:
        brain.record("frontend_ws", "client_disconnect", "Session ended", "info")

async def main():
    log("Logic server running on ws://127.0.0.1:3002")
    brain.record("websocket_server", "startup", f"OpenAI={str(OPENAI_KEY)[:4]}... Deepgram={str(DEEPGRAM_KEY)[:4]}... Sarvam={str(SARVAM_KEY)[:4]}...", "ok")
    asyncio.create_task(warmup_dns())
    if BRAIN_ENABLED:
        brain.record("websocket_server", "listen", "Server started on ws://127.0.0.1:3002", "ok")
        asyncio.create_task(brain.start_periodic_analysis())
    async with websockets.serve(handler, "0.0.0.0", 3002):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
