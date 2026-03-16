import asyncio
import websockets
import json
import httpx
import base64
import sys
from pathlib import Path

# Set up logging to a file in case we can't see the terminal
LOG_FILE = Path(__file__).parent / "ws_server.log"

def log(msg):
    full_msg = f"[LOG] {msg}"
    print(full_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_msg + "\n")

env_path = Path(__file__).parent / ".env"
envs = {}
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            envs[key.strip()] = val.strip()

OPENAI_KEY  = envs.get("OPENAI_API_KEY", "")
SARVAM_KEY  = envs.get("SARVAM_AI_API", "")
DEEPGRAM_KEY = envs.get("DEEPGRAM_API_KEY", "")

# Persistent client for lower latency
httpx_client = httpx.AsyncClient(timeout=30.0)

# Warm up DNS cache for Deepgram
async def warmup_dns():
    try:
        await httpx_client.head("https://api.deepgram.com", timeout=5.0)
        log("Deepgram DNS warmed up")
    except:
        pass

async def deepgram_tts(text_segment: str, ws) -> None:
    if not DEEPGRAM_KEY or not text_segment.strip():
        return
    
    # Try with retries for DNS flakiness
    for attempt in range(1, 4): # Increased to 3 attempts
        try:
            url = "https://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=mp3"
            resp = await httpx_client.post(
                url,
                headers={
                    "Authorization": f"Token {DEEPGRAM_KEY}",
                    "Content-Type": "application/json"
                },
                json={"text": text_segment.strip()}
            )
            
            if resp.status_code != 200:
                log(f"Deepgram TTS error: {resp.status_code} - {resp.text}")
                return
                
            audio_bytes = resp.content
            await ws.send(json.dumps({"type": "audio_start"}))
            await ws.send(audio_bytes)
            await ws.send(json.dumps({"type": "audio_end"}))
            return # Success
        except Exception as e:
            log(f"Deepgram TTS attempt {attempt} failed: {e}")
            if attempt == 3:
                log(f"Deepgram TTS permanent failure: {e}")
                await ws.send(json.dumps({"type": "sentence", "text": text_segment}))
            await asyncio.sleep(0.5)

async def openai_stream(prev, user_ans, next_q_prompt, ws):
    prompt = f"""You are a conversational tech interviewer.
Previous Question: "{prev}"
User's Answer/Reply: "{user_ans}"

Your task:
Analyze if the user is asking you to repeat the question (e.g. "can you repeat that", "I didn't hear you", "what was the question").
If YES:
Respond with a brief acknowledgement followed by repeating the Previous Question exactly.
Else if the user is asking to END or STOP the interview (e.g. "I want to end", "stop the interview", "that's all"):
Respond with a polite closing and append "[[END_INTERVIEW]]" at the end.
If NO to both:
Respond with a brief acknowledgement or constructive response (1 short sentence). Then, smoothly move on to: "{next_q_prompt}".

Do not output ANY json or markup. Output EXACTLY the raw text of what you will say next. If ending, ensure "[[END_INTERVIEW]]" is the last thing you say."""

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", 
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "system", "content": prompt}],
                    "temperature": 0.7,
                    "stream": True,
                    "stream_options": {"include_usage": True}
                },
                timeout=30.0
            ) as response:
                total_input_toks = 0
                total_output_toks = 0
                total_tts_chars = 0
                buffer = ""
                
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line.strip() != "data: [DONE]":
                        try:
                            data = json.loads(line[6:])
                            
                            # Handle stream usage if present
                            if "usage" in data and data["usage"]:
                                usage = data["usage"]
                                total_input_toks = usage.get("prompt_tokens", 0)
                                total_output_toks = usage.get("completion_tokens", 0)
                                continue

                            choices = data.get("choices", [])
                            if not choices: continue
                            content = choices[0]["delta"].get("content", "")
                            
                            if content:
                                buffer += content
                                await ws.send(json.dumps({"type": "token", "text": content}))
                                if any(p in content for p in [".", "?", "!", "\n"]):
                                    sentence = buffer.strip()
                                    buffer = ""
                                    if sentence:
                                        total_tts_chars += len(sentence)
                                        await deepgram_tts(sentence, ws)
                        except Exception as e:
                            log(f"Token error: {e}")
                
                if buffer.strip():
                    total_tts_chars += len(buffer.strip())
                    await deepgram_tts(buffer.strip(), ws) 
                
                # Send total usage for this conversational turn
                await ws.send(json.dumps({
                    "type": "usage",
                    "openai": {
                        "input": total_input_toks,
                        "output": total_output_toks
                    },
                    "tts_chars": total_tts_chars
                }))
    except Exception as e:
        log(f"Streaming error: {e}")
        await ws.send(json.dumps({"type": "error", "msg": str(e)}))

async def deepgram_proxy(ws_frontend, sample_rate=16000):
    """Proxies audio chunks from frontend to Deepgram and results back."""
    dg_url = f"wss://api.deepgram.com/v1/listen?model=nova-2&language=en-IN&smart_format=true&punctuate=true&encoding=linear16&sample_rate={sample_rate}"
    
    if not DEEPGRAM_KEY:
        log("Error: DEEPGRAM_API_KEY missing")
        await ws_frontend.send(json.dumps({"type": "error", "msg": "Deepgram key missing on server"}))
        return

    auth_header = {"Authorization": f"Token {DEEPGRAM_KEY}"}
    
    # Try connecting with retries due to possible DNS flakiness
    ws_deepgram = None
    for attempt in range(1, 4):
        try:
            log(f"Connecting to Deepgram (Attempt {attempt}): {dg_url}")
            ws_deepgram = await websockets.connect(dg_url, additional_headers=auth_header)
            log(f"  [DG PROXY] Connected to Deepgram success (Rate: {sample_rate})")
            break
        except Exception as e:
            log(f"  [DG PROXY] Connection Attempt {attempt} failed: {e}")
            if attempt == 3:
                await ws_frontend.send(json.dumps({"type": "error", "msg": f"Failed to connect to Deepgram after 3 attempts: {e}"}))
                return
            await asyncio.sleep(1)

    try:
        if ws_deepgram:
            async def forward_to_deepgram():
                try:
                    async for message in ws_frontend:
                        if isinstance(message, (bytes, bytearray)):
                            if len(message) > 0:
                                await ws_deepgram.send(message)
                        else:
                            await ws_deepgram.send(message)
                except Exception as e:
                    log(f"  [DG PROXY] Frontend -> Deepgram Error: {e}")

            async def forward_to_frontend():
                try:
                    async for message in ws_deepgram:
                        await ws_frontend.send(message)
                except Exception as e:
                    log(f"  [DG PROXY] Deepgram -> Frontend Error: {e}")

            await asyncio.gather(forward_to_deepgram(), forward_to_frontend())
            log("  [DG PROXY] Session ended")
            
    except Exception as e:
        log(f"  [DG PROXY] Connection Error: {type(e).__name__}: {e}")
        await ws_frontend.send(json.dumps({"type": "error", "msg": f"Proxy connection failed: {e}"}))
    finally:
        if ws_deepgram:
            await ws_deepgram.close()
            log("  [DG PROXY] Deepgram connection closed")

log(f"Starting server. Keys: OpenAI={'SET' if OPENAI_KEY else 'MISSING'}, Sarvam={'SET' if SARVAM_KEY else 'MISSING'}, Deepgram={'SET' if DEEPGRAM_KEY else 'MISSING'}")

# Fix: websockets 14+ handler only takes one argument
async def handler(websocket):
    log("New connection established")
    try:
        async for msg in websocket:
            try:
                # If msg is binary, it's NOT a command, ignore or log
                if isinstance(msg, (bytes, bytearray)):
                    continue

                data = json.loads(msg)
                action = data.get("action")
                
                if action == "ask":
                    log(f"Action: ask | {data.get('prev', '')[:30]}...")
                    await openai_stream(data.get("prev", ""), data.get("transcript", ""), data.get("nextQ", ""), websocket)
                    await websocket.send(json.dumps({"type": "done"}))
                
                elif action == "stt":
                    log("Action: stt | Starting Deepgram Proxy")
                    sr = data.get("sample_rate", 16000)
                    await deepgram_proxy(websocket, sample_rate=sr)
                    break 

            except json.JSONDecodeError:
                log(f"Non-JSON message received: {msg[:100]}")
                continue
            except Exception as e:
                log(f"Handler processing error: {e}")

    except websockets.exceptions.ConnectionClosed:
        log("Connection closed")
    except Exception as e:
        log(f"Handler loop error: {e}")

async def main():
    log("WebSocket logic server running on ws://127.0.0.1:3002")
    # Warm up DNS at startup
    asyncio.create_task(warmup_dns())
    async with websockets.serve(handler, "127.0.0.1", 3002):
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Server stopped by user")
    except Exception as e:
        log(f"Main loop error: {e}")
