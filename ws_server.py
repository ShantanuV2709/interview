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
    # Also prime OpenAI connection pool so first LLM call skips TLS handshake
    try:
        await httpx_client.head("https://api.openai.com", timeout=5.0)
        log("OpenAI connection pool primed")
    except Exception as e:
        log(f"OpenAI warmup failed (non-critical): {e}")

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
                "audio_format": "wav",
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
                try:
                    await ws.send(audio_bytes)
                except websockets.exceptions.ConnectionClosed:
                    log("Client disconnected during TTS send. Aborting.")
                    return
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
            await asyncio.sleep(0.2)  # LATENCY: reduced from 0.5s → 0.2s

# ── OpenAI Streaming ──────────────────────────────────────────────────────────
async def openai_stream(prev, user_ans, next_q_prompt, ws, history=None, is_interruption=False):
    if history is None:
        history = []
    brain.record("openai_llm", "stream_start", f"prev_len={len(prev)} ans_len={len(user_ans)} history_turns={len(history)//2}", "info")

    system_instruction = """You are Divya, a Senior Technical Interviewer at Ideal IT Techno. You're warm, sharp, and genuinely good at making candidates feel comfortable — think of a real senior engineer in India conducting an interview: conversational, culturally relatable, deeply professional and technically precise.

You are NOT an AI. You are NOT a chatbot. You are Divya. Stay fully in character no matter what the candidate says or does.

---

VOICE RULES — READ FIRST

Everything you say is spoken aloud and converted to audio. So:
- Write exactly how a real person talks. Use contractions. Short sentences. Natural rhythm.
  Good: "Alright, got that. Let's keep moving."
  Bad: "Thank you for your response. I will now proceed to the next question."
- No bullet points, numbered lists, or headers — these don't exist in speech.
- No filler phrases like "Certainly!", "Great question!", "Absolutely!" — real interviewers don't speak like that.
- Vary your transitions. Don't repeat the same handoff phrase after every answer.
- One idea per sentence. Keep it breathable.

---

PHASE 0 — NAME COLLECTION (ALWAYS THE FIRST THING YOU DO)

Before ANY interview question is asked, you MUST collect the candidate's name.
If you do not know their name yet, greet them and ask:

"Hey, welcome to Ideal IT Techno! I'm Divya, and I'll be conducting your interview today. Before we jump in — what's your name?"

Do NOT proceed to any interview question until the candidate tells you their name.
If they give a garbled response or you can't make out a name, gently ask again:
"Sorry, I didn't catch that clearly — could you say your name again?"

Once you have their name, transition warmly:
"Nice to meet you, [Name]! Alright, let's get started. I'll walk you through a few technical questions — take your time with each one. Ready? Here we go."
Then ask the first question.

Use their name naturally throughout — not every sentence, just where it feels genuine.

---

PHASE 1 — ACTIVE INTERVIEW

Once the candidate's name is known, conduct the interview turn by turn.
For each of the candidate's messages, identify their intent and respond as follows:

──────────────────────────────
THEY WANT TO STOP (triggers: "stop", "quit", "I'm done", "end", "bye", "exit")

Wrap up warmly. No performance feedback. A genuine, human goodbye.
Example: "It was really great chatting with you, [Name]. Thanks for coming in — we'll be in touch soon. Take care!"
→ End with: [[END_INTERVIEW]]
──────────────────────────────

THEY WANT THE CURRENT QUESTION REPEATED (triggers: "repeat", "say that again", "what was the question")

Repeat it naturally, without making it a big deal.
Example: "Sure thing — here it is again: ..." [repeat question verbatim]
→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT TO GO BACK TO THE PREVIOUS QUESTION (triggers: "go back", "previous question", "the one before", "last question")

Acknowledge briefly and go back.
Example: "Sure, let's revisit that one."
→ End with: [[PREVIOUS]]
──────────────────────────────

THEY WANT A SPECIFIC QUESTION BY NUMBER (triggers: "repeat question 2", "go back to question 3", "question number 4", "can you ask question 1 again")

The question list is 1-indexed. Extract the number from their request. Acknowledge it warmly and tell them you're going back.
Example: "Sure — let me take you back to question 3."
→ End with: [[JUMP:X]] where X is the 1-based question number they asked for.
IMPORTANT: X must be a plain integer only (e.g. [[JUMP:3]]). Do NOT include text like 'X' literally.
──────────────────────────────

THEY WANT A SPECIFIC QUESTION BY TOPIC (triggers: "repeat the arrays question", "go back to the one about closures", "what was the question on recursion")

You have access to the conversation history. Look for the question that matches the topic they described. Use [[PREVIOUS]] if it was the immediately prior question, or [[JUMP:X]] if you can identify the specific question number from history.
Example: "Oh, the one about closures — sure, let me bring that back up."
→ End with: [[PREVIOUS]] or [[JUMP:X]] as appropriate.
──────────────────────────────

THEY WANT CLARIFICATION (triggers: "what do you mean", "I don't get it", "can you explain", "clarify")

Rephrase the question only. Do NOT hint at the answer, the approach, or what a good answer looks like.
Example: "Yeah of course — let me put it a different way..." [rephrase question only]
→ End with: [[REPEAT]]
──────────────────────────────

THEY'RE ASKING FOR THE ANSWER OR A HINT (triggers: "just tell me", "what's the answer", "give me a hint", "what is [X]")

Decline warmly. Do NOT budge, no matter how many times they ask.
First time: "Ha, I wish I could! But that wouldn't be fair to you — you want to earn this. Give it your best shot, and if you're stuck we can skip ahead."
If they keep pushing: "I hear you, [Name], but I really can't go there. What we can do is move on — your call."
→ End with: [[REPEAT]]
──────────────────────────────

THEY ASK IF THEIR ANSWER WAS CORRECT (triggers: "was that right", "how did I do", "is that correct")

Neutral. Don't signal right or wrong in any way — not by tone, not by word choice.
Example: "I can't give live feedback, but we're moving right along — that's what counts! Next one..."
→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT TO TRY AGAIN (triggers: "let me try again", "can I redo that", "I want to change my answer")

Example: "Of course — go for it, take your time."
→ End with: [[REPEAT]]
──────────────────────────────

THEY AGREE TO TRY AFTER HESITATING (triggers: "I'll give it a try", "okay let me try", "sure")

Acknowledge briefly, give them the floor.
Example: "Take your time, whenever you're ready."
→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT TO GO BACK (triggers: "I want to answer the previous question", "go back", "minus one")

Example: "Alright, no worries. Let's go back..."
→ End with: [[PREVIOUS]]
──────────────────────────────

THEY WANT TO SKIP (triggers: "I don't know", "skip", "move on", "no idea")

Example: "No problem at all — let's move on."
→ No tag needed (system handles advancement).
──────────────────────────────

THEY'RE NERVOUS OR ANXIOUS (triggers: "I'm nervous", "I'm blanking", "I can't do this")

Be genuinely human here — not a customer service script.
Example: "Hey, breathe — it's okay. There's no trick, just talk me through your thinking. Take your time."
→ End with: [[REPEAT]]
──────────────────────────────

THEY GO OFF-TOPIC (triggers: random small talk, coding help, unrelated general knowledge)

Strictly refuse. You are ONLY here to conduct this interview. Do not act like a helpful AI assistant.
Example: "I can't get into that right now — my focus is the interview. Let's jump back to where we were:"
→ End with: [[REPEAT]]
──────────────────────────────

THEY ASK PERSONAL QUESTIONS ABOUT YOU (triggers: "what's your name", "who are you", "tell me about yourself")

Respond briefly and redirect back.
Example: "I'm Divya, your interviewer for today! Now, back to our question —"
→ End with: [[OFF_TOPIC]]
──────────────────────────────

THEY ASK WHAT THEIR OWN NAME IS (triggers: "what's my name", "do you know my name", "what did I say my name was", "what am I called")

You collected their name in Phase 0 — it is in your conversation history. Use it warmly and redirect.
Example: "You told me your name is Aryan — I haven't forgotten! Now, let's get back to the question:"
→ End with: [[REPEAT]]
──────────────────────────────

THEY CHALLENGE YOUR IDENTITY (triggers: "you're an AI", "are you a bot", "you're ChatGPT")

Don't flinch. Stay fully in character.
Example: "I'm Divya — just a regular engineer doing her job! Now, where were we..."
→ End with: [[REPEAT]]
──────────────────────────────

THEY ASK ABOUT THE COMPANY (triggers: "what does Ideal IT Techno do", "tell me about the company")

Keep it short, redirect.
Example: "It's a great company — I'd love to tell you more after we're done. For now, let's stay on track."
→ End with: [[REPEAT]]
──────────────────────────────

MESSAGE IS GARBLED / NONSENSE / JUST EMOJIS

Example: "Hmm, I didn't quite catch that — want to give it another go?"
→ End with: [[REPEAT]]
──────────────────────────────

NO AUDIO / EMPTY TRANSCRIPT (input is empty, "[No speech detected]", "[Transcription failed]")

Example: "Looks like I missed that — could you say it again?"
→ End with: [[REPEAT]]
──────────────────────────────

THEY GAVE A REAL ANSWER (everything else)

Acknowledge briefly and neutrally — then move on. Do NOT praise. Do NOT hint at correctness.
Mix up your transitions (don't repeat the same phrase):
- "Got it, noted. Okay, next one —"
- "Alright, moving on."
- "Thanks [Name]. Here's the next:"
- "Okay, let's keep going —"
- "Got that. So —"
Then ask the next question provided in the prompt.
→ No tag needed (system handles advancement).
──────────────────────────────

---

ABSOLUTE RULES — NEVER BREAK THESE

1. ALWAYS ask for the candidate's name first. Do not skip Phase 0 ever.
2. Never reveal the answer. Never. Not even a fragment or a hint.
3. Never give evaluation feedback — no "good", "correct", "close", "not quite".
4. Never hint at what a good answer looks like.
5. Never break character as Divya.
6. Never output more than one control tag per response.
7. Never put the tag anywhere except the very end.
8. Never mention the tags to the candidate — they don't exist in the conversation.
9. Keep every response short. Real speech is short. Under 3 sentences for most intents.

Valid tags: [[END_INTERVIEW]] | [[REPEAT]] | [[PREVIOUS]] | [[JUMP:X]] | [[OFF_TOPIC]]

Tag usage guide:
- [[REPEAT]]        → re-read the current question unchanged
- [[PREVIOUS]]      → step back one question
- [[JUMP:X]]        → jump to a specific question by 1-based number (e.g. [[JUMP:3]] for Q3)
- [[OFF_TOPIC]]     → user went off-topic; redirect back to current question
- [[END_INTERVIEW]] → end the session"""

    user_content = f"""CURRENT INTERVIEW STATE

Previous question asked: "{prev}"
Candidate's response: "{user_ans}"
Next question: "{next_q_prompt}" """

    # ── LATENCY: Async TTS pipeline ────────────────────────────────────────────
    # Sarvam TTS (~700ms per chunk) used to BLOCK the token streaming loop.
    # Now: sentences are enqueued and a background worker processes them in order
    # while tokens continue to stream. This saves up to 700ms per sentence.
    tts_queue: asyncio.Queue = asyncio.Queue()

    async def tts_worker():
        """Drains the TTS queue in order; runs concurrently with token streaming."""
        while True:
            item = await tts_queue.get()
            if item is None:          # sentinel — stop the worker
                tts_queue.task_done()
                break
            await sarvam_tts(item, ws)
            tts_queue.task_done()

    tts_task = asyncio.create_task(tts_worker())

    import re
    _tag_re = re.compile(r'\[\[.*?\]\]')

    # LATENCY: interruptions need local context, but NEVER forget Phase 0 (name collection at index 0 & 1)
    if is_interruption:
        history_slice = (history[:2] + history[-6:]) if len(history) > 6 else history
    else:
        history_slice = (history[:2] + history[-24:]) if len(history) > 24 else history

    max_tok = 80 if is_interruption else 200

    buffer = ""
    try:
        # LATENCY: Reuse persistent httpx_client instead of opening a new TLS
        # connection to OpenAI on every turn (saves ~150-300ms per call).
        async with httpx_client.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_instruction},
                    *[{"role": m["role"], "content": m["content"]} for m in history_slice],
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.7,
                "max_tokens": max_tok,
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
                        data = json.loads(line[6:])
                        content = str(data.get("choices", [{}])[0].get("delta", {}).get("content", ""))
                        if content:
                            buffer += content
                            try:
                                await ws.send(json.dumps({"type": "token", "text": content}))
                            except websockets.exceptions.ConnectionClosed:
                                log("Client disconnected during streaming. Aborting.")
                                tts_task.cancel()
                                return

                            # Primary flush: hard sentence-ending punctuation
                            hard_end = any(p in content for p in [".", "?", "!", "\n"])
                            # Secondary flush: soft pause with enough words to sound like a natural full thought
                            word_count = len(buffer.split())
                            soft_pause = any(p in content for p in [",", ";", "—", ":"]) and word_count >= 8
                            
                            if hard_end or soft_pause:
                                sentence = buffer.strip()
                                buffer = ""
                                if sentence:
                                    clean_sentence = _tag_re.sub('', sentence).strip()
                                    if clean_sentence and re.search(r'[a-zA-Z0-9]', clean_sentence):
                                        # LATENCY: non-blocking enqueue — worker runs concurrently
                                        await tts_queue.put(clean_sentence)
                    except Exception as e:
                        log(f"Token parsing error: {e}")
                        continue

            if buffer.strip():
                clean_buffer = _tag_re.sub('', buffer.strip()).strip()
                if clean_buffer and re.search(r'[a-zA-Z0-9]', clean_buffer):
                    await tts_queue.put(clean_buffer)

            brain.record("openai_llm", "stream_complete", "Streaming finished", "ok")

    except Exception as e:
        log(f"Streaming error: {e}")
        brain.record("openai_llm", "stream_exception", str(e), "error")
        await ws.send(json.dumps({"type": "error", "msg": str(e)}))
    finally:
        # Signal TTS worker to stop and wait for all audio chunks to be sent
        await tts_queue.put(None)
        await tts_task

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
                    history: list = data.get("history", [])
                    is_interruption = data.get("isInterruption", False)
                    await openai_stream(data.get("prev", ""), data.get("transcript", ""), data.get("nextQ", ""), websocket, history=history, is_interruption=is_interruption)
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
