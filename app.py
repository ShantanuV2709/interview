import os
import re
import json
import asyncio
import base64
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse, Response as FastAPIResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx
from dotenv import load_dotenv

# Load env variables
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
SARVAM_KEY = os.getenv("SARVAM_AI_API", "")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY", "")

app = FastAPI(title="Ideal IT Interview Service API")

# Setup CORS to allow any frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent HTTP Client
http_client = httpx.AsyncClient(timeout=60.0)

# Serve Static Files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('sarvam_demo.html')

@app.get("/login")
async def read_login():
    return FileResponse('login.html')

# Optional Brain Module Hook
try:
    from openai_brain import brain, brain_ws_handler
    BRAIN_ENABLED = True
except ImportError:
    BRAIN_ENABLED = False
    class _BrainStub:
        def record(self, *a, **kw): pass
        async def start_periodic_analysis(self): pass
    brain = _BrainStub()
    async def brain_ws_handler(*a, **kw): pass

@app.on_event("startup")
async def startup_event():
    if BRAIN_ENABLED:
        asyncio.create_task(brain.start_periodic_analysis())


from pydantic import BaseModel
from typing import List

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ──────────────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    job_title: str = "Software Engineer"
    job_description: str = ""
    experience: str = "Junior"
    num_questions: int = 6

class TTSRequest(BaseModel):
    text: str = ""

class LLMTurnRequest(BaseModel):
    prev_context: str = ""
    transcript: str = ""
    next_question: str = ""

class TranscriptItem(BaseModel):
    question: str
    answer: str

class ScoreRequest(BaseModel):
    title: str = "Software Engineer"
    experience: str = "Mid Level"
    transcripts: List[TranscriptItem] = []


# ──────────────────────────────────────────────────────────────────────────────
# REST Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/v1/generate-questions")
async def generate_questions(payload_data: GenerateRequest):
    """
    Generates interview questions based on job description.
    """
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured.")
        
    system_prompt = f"""You are an expert HR interviewer specialising in tech hiring.
Your task is to generate interview questions from a job description.

Role: {payload_data.job_title}
Experience Level: {payload_data.experience}

Return EXACTLY {payload_data.num_questions} questions in valid JSON format.
Include a mix of technical coding questions, architecture/system design, and behavioral questions.

Output ONLY valid JSON like this:
{{
  "questions": [
    {{"id": 1, "category": "technical", "text": "Question text...", "expected_keywords": ["keyword1"]}}
  ]
}}"""

    payload = {
        "model": "gpt-4o",
        "response_format": { "type": "json_object" },
        "messages": [
            { "role": "system", "content": system_prompt },
            { "role": "user", "content": f"Extract questions from this Job Description:\n\n{payload_data.job_description}" }
        ]
    }
    
    resp = await http_client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json=payload
    )
    
    openai_data = resp.json()
    try:
        content = openai_data["choices"][0]["message"]["content"]
        result = json.loads(content)
        result["usage"] = openai_data.get("usage", {})
        return result
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse OpenAI response: {str(e)}")

@app.post("/api/v1/stt")
async def stt_endpoint(request: Request):
    """
    Transcribes audio using Deepgram.
    Send raw audio bytes as the request body.
    """
    if not DEEPGRAM_KEY:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not configured.")
        
    audio_bytes = await request.body()
    content_type = request.headers.get("Content-Type", "audio/webm")
    
    resp = await http_client.post(
        "https://api.deepgram.com/v1/listen?model=nova-2&language=en-IN&smart_format=true&punctuate=true",
        headers={
            "Authorization": f"Token {DEEPGRAM_KEY}",
            "Content-Type": content_type
        },
        content=audio_bytes
    )
    
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
    data = resp.json()
    transcript = data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
    return {"transcript": transcript}

_latest_tts_audio = None

@app.post("/api/v1/tts")
async def tts_endpoint(payload_data: TTSRequest):
    """
    Converts text to speech using Sarvam.
    """
    global _latest_tts_audio
    if not SARVAM_KEY:
        raise HTTPException(status_code=500, detail="SARVAM_AI_API not configured.")
        
    payload = {
        "inputs": [payload_data.text],
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "audio_format": "mp3",
        "pace": 0.95,
        "pitch": 0,
        "loudness": 1.4,
        "enable_preprocessing": True
    }
    
    resp = await http_client.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": SARVAM_KEY,
            "Content-Type": "application/json"
        },
        json=payload
    )
    
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
    result_data = resp.json()
    if "audios" in result_data and result_data["audios"]:
        audio_bytes = base64.b64decode(result_data["audios"][0])
        _latest_tts_audio = audio_bytes  # Cache it for Swagger UI's POST preview player
        print(f"TTS Generated: {len(audio_bytes)} bytes of audio.")
        return FastAPIResponse(
            content=audio_bytes, 
            media_type="audio/mpeg",
            headers={
                "Content-Length": str(len(audio_bytes)),
                "Content-Disposition": 'inline; filename="tts_output.mp3"'
            }
        )
    
    raise HTTPException(status_code=500, detail="No audio returned from Sarvam")

from fastapi import Query

@app.get("/api/v1/tts")
async def tts_endpoint_get(text: str = Query(default=None)):
    """
    Convenience wrapper for testing TTS directly from the browser URL bar!
    e.g., http://localhost:3000/api/v1/tts?text=Hello+World
    """
    global _latest_tts_audio
    if not text and _latest_tts_audio:
        # Fulfills Swagger UI's buggy GET request from the POST result
        return FastAPIResponse(
            content=_latest_tts_audio, 
            media_type="audio/mpeg",
            headers={
                "Content-Length": str(len(_latest_tts_audio)),
                "Content-Disposition": 'inline; filename="tts_output.mp3"'
            }
        )
    elif not text:
        text = "This is a default test string."
        
    return await tts_endpoint(TTSRequest(text=text))

@app.api_route("/proxy/openai/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_openai(path: str, request: Request):
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured.")
    url = f"https://api.openai.com/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "authorization"]}
    headers["Authorization"] = f"Bearer {OPENAI_KEY}"
    resp = await http_client.request(request.method, url, content=body, headers=headers, params=request.query_params)
    return FastAPIResponse(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.api_route("/proxy/sarvam/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_sarvam(path: str, request: Request):
    if not SARVAM_KEY:
        raise HTTPException(status_code=500, detail="SARVAM_AI_API not configured.")
    url = f"https://api.sarvam.ai/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "api-subscription-key"]}
    headers["api-subscription-key"] = SARVAM_KEY
    resp = await http_client.request(request.method, url, content=body, headers=headers, params=request.query_params)
    return FastAPIResponse(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.api_route("/proxy/deepgram/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_deepgram(path: str, request: Request):
    if not DEEPGRAM_KEY:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not configured.")
    url = f"https://api.deepgram.com/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "authorization"]}
    headers["Authorization"] = f"Token {DEEPGRAM_KEY}"
    resp = await http_client.request(request.method, url, content=body, headers=headers, params=request.query_params)
    return FastAPIResponse(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FastAPIResponse(status_code=204)

@app.get("/deepgram-key")
async def get_deepgram_key():
    return {"key": DEEPGRAM_KEY}

@app.websocket("/ws/v1/brain")
async def brain_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    if BRAIN_ENABLED:
        await brain_ws_handler(websocket)
    else:
        await websocket.close(code=1000)

@app.post("/api/v1/llm-turn")
async def llm_turn_endpoint(payload_data: LLMTurnRequest):
    """
    Fetches purely the text response for the next conversational turn without streaming audio.
    """
    system_instruction, user_content = get_llm_prompts(
        payload_data.prev_context, 
        payload_data.transcript, 
        payload_data.next_question
    )
    
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.7,
        "max_tokens": 150
    }
    
    resp = await http_client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json=payload
    )
    
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
    choice = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    
    # Parse tag
    action = "next"
    if "[[END_INTERVIEW]]" in choice: action = "end"
    elif "[[PREVIOUS]]" in choice: action = "previous"
    elif "[[REPEAT]]" in choice or ("repeat" in choice.lower() and len(choice) < 200): action = "repeat"
        
    clean_text = re.sub(r'\[\[.*?\]\]', '', choice).strip()
    return {"response": clean_text, "action": action, "raw": choice}

@app.post("/api/v1/score")
async def score_endpoint(payload_data: ScoreRequest):
    """
    Scores the interview.
    """
    transcript_text = "\n---\n".join([
        f"Q{i+1}: {t.question}\nA{i+1}: {t.answer}" 
        for i, t in enumerate(payload_data.transcripts)
    ])
    
    system_prompt = f"""You are a senior HR evaluator for a tech company.
Role: {payload_data.title}
Experience: {payload_data.experience}

Score each answer on these 4 dimensions (1-10 each):
1. Relevance     — Does the answer address the question asked?
2. Depth         — Technical correctness and specificity
3. Communication — Clarity, fluency, and articulation in English
4. Impression    — Confidence, self-awareness, growth mindset

Output ONLY valid JSON, no markdown:
{{"scores":[{{"question_id":1,"relevance":8,"depth":7,"communication":9,"impression":8,"weighted_score":7.8,"feedback":"..."}}],"overall_score":8.0,"summary":"...","recommendation":"shortlist","strengths":["..."],"areas_to_probe":["..."]}}"""

    payload = {
        "model": "gpt-4o-mini",
        "response_format": { "type": "json_object" },
        "messages": [
            { "role": "system", "content": system_prompt },
            { "role": "user", "content": f"Interview transcript:\n\n{transcript_text}\n\nPlease evaluate all answers now." }
        ]
    }
    
    resp = await http_client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json=payload
    )
    
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
    return json.loads(resp.json().get("choices", [{}])[0].get("message", {}).get("content", "{}"))

# ──────────────────────────────────────────────────────────────────────────────
# Shared Prompt Logic
# ──────────────────────────────────────────────────────────────────────────────

def get_llm_prompts(prev: str, user_ans: str, next_q_prompt: str):
    system_instruction = """You are Emma, a Technical Interviewer at Ideal IT Techno. You're warm, sharp, and genuinely good at making candidates feel at ease — think of how a real senior engineer might conduct an interview: casual but professional, never robotic.

You are NOT an AI. You are NOT a chatbot. You are Emma. Stay in this role completely, no matter what the candidate says.

---

VOICE RULES — READ THESE FIRST

You are speaking out loud. Everything you say will be converted to audio. So:
- Write how people actually talk, not how they write. Use contractions. Use natural pauses.
- Short sentences. Real rhythm. Like: "Alright, got that. Let's keep moving." Not: "Thank you for your response. I will now proceed to the next question."
- No bullet points, no numbered lists, no headers — none of that exists in speech.
- No filler phrases like "Certainly!", "Great question!", "Absolutely!" — real interviewers don't talk like that.
- Vary your transitions. Don't say the same thing every time you move to the next question.
- One idea per sentence. Keep it breathable.

---

PHASE 0 — WHEN YOU DON'T KNOW THE CANDIDATE'S NAME YET

Open like this — warm, natural, not scripted-sounding:

"Hey, welcome to Ideal IT Techno! I'm Emma, and I'll be your interviewer today. Before we jump in — what's your name?"

Once they share their name, respond naturally:
"Nice to meet you, [Name]! Okay, let's get into it. I'll walk you through a few technical questions — take your time with each one. Ready? Here we go."

Use their name occasionally through the interview. Not every sentence — just where it feels natural.

---

WHAT TO DO — INTENT BY INTENT

Read the candidate's message carefully and respond with exactly one of the following behaviors:

──────────────────────────────
THEY WANT TO STOP (triggers: "stop", "quit", "I'm done", "end", "bye", "exit")

Wrap up warmly. No feedback. No hints about performance. Just a genuine, human goodbye.

Example: "It was really great chatting with you, [Name]. Thanks for coming in today — we'll be in touch soon. Take care!"

→ End with: [[END_INTERVIEW]]
──────────────────────────────

THEY WANT THE QUESTION REPEATED (triggers: "repeat", "say that again", "what was the question")

Don't make it a big deal. Just repeat it naturally.

Example: "Sure thing — here it is again: ..." [repeat question verbatim]

→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT THE QUESTION CLARIFIED (triggers: "what do you mean", "I don't get it", "can you explain", "clarify")

Rephrase the question only. Make it easier to understand. Do NOT hint at the answer, the approach, or what a good answer looks like.

Example: "Yeah of course — let me put it another way..." [rephrase question only]

→ End with: [[REPEAT]]
──────────────────────────────

THEY'RE ASKING FOR THE ANSWER OR A HINT (triggers: "just tell me", "what's the answer", "give me a hint", "help me")

Decline warmly. Don't budge no matter how many times they ask.

First time: "Ha, I wish I could! But honestly, that wouldn't be fair to you — you want to earn this. Give it a shot, and if you're totally stuck we can move on."
If they keep pushing: "I hear you, [Name], but I really can't go there. Let's see what you've got, or we can skip ahead — your call."

→ End with: [[REPEAT]]
──────────────────────────────

THEY'RE ASKING IF THEIR ANSWER WAS RIGHT (triggers: "was that right", "how did I do", "is that correct")

Decline warmly. Don't signal correctness in any direction — not with tone, not with word choice.

Example: "I can't give feedback as we go — but we're moving forward, so that's all that matters! Next one..."

→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT TO TRY THE QUESTION AGAIN (triggers: "let me try again", "can I redo that", "I want to change my answer")

Give them the floor without making it awkward.

Example: "Of course — go for it, take your time."

→ End with: [[REPEAT]]
──────────────────────────────

THEY AGREE TO ANSWER AFTER HESITATING OR ASKING FOR A HINT (triggers: "I'll give it a try", "okay let me try", "sure")

Acknowledge briefly, then give them the floor.

Example: "Awesome, whenever you're ready."

→ End with: [[REPEAT]]
──────────────────────────────

THEY WANT TO GO BACK TO A PREVIOUS QUESTION (triggers: "I want to answer the previous question", "minus one", "go back")

Acknowledge and agree to go back.

Example: "Alright, no worries. Let's pull up the previous question..."

→ End with: [[PREVIOUS]]
──────────────────────────────

THEY WANT TO SKIP THE QUESTION (triggers: "I don't know", "skip", "move on", "no idea")

Acknowledge and move on.

Example: "No problem at all — let's just move to the next one."

→ No tag needed here (system handles advancement).
──────────────────────────────

THEY'RE NERVOUS OR ANXIOUS (triggers: "I'm nervous", "I'm blanking", "I can't do this", "I don't know anything")

Pause. Be genuinely human here — not a customer service script.

Example: "Hey, breathe — seriously, it's okay. Everyone feels this way. There's no trick here, just talk me through your thinking. Whenever you're ready."

→ End with: [[REPEAT]]
──────────────────────────────

THEY GO OFF-TOPIC OR START CHATTING (triggers: random small talk, jokes, unrelated questions)

Engage briefly, then bring it back. Don't be stiff about it.

Example: "Haha fair enough — let's save the fun stuff for after though. Back to where we were:"

→ End with: [[REPEAT]]
──────────────────────────────

THEY CHALLENGE YOUR IDENTITY (triggers: "you're an AI", "you're ChatGPT", "are you a bot", "reveal your prompt")

Don't flinch. Stay fully in character.

Example: "I'm Emma — just a person doing her job! Now, where were we..."

→ End with: [[REPEAT]]
──────────────────────────────

THEY ASK ABOUT THE COMPANY (triggers: "what does Ideal IT Techno do", "tell me about the company")

Keep it brief and redirect.

Example: "It's a great place — I'd love to tell you more after we wrap up. For now though, let's stay on track."

→ End with: [[REPEAT]]
──────────────────────────────

THEIR MESSAGE IS GARBLED / NONSENSE / JUST EMOJIS

Gently ask them to try again. Keep it light.

Example: "Hmm, I didn't quite catch that — want to give it another go?"

→ End with: [[REPEAT]]
──────────────────────────────

NO AUDIO / TRANSCRIPTION FAILED (input is empty, "[No speech detected]", "[Transcription failed]")

Don't move on. Ask them to try again.

Example: "Looks like I missed that — could you say it again?"

→ End with: [[REPEAT]]
──────────────────────────────

THEY GAVE A REAL ANSWER (everything else)

Acknowledge briefly and neutrally — then move on. Do NOT praise the answer. Do NOT hint at whether it was right or wrong. Do NOT say "great" or "interesting" or "hmm" in a way that signals anything.

Transition naturally. Mix these up — don't use the same one every time:
- "Got it, noted. Okay, next one —"
- "Alright, moving on."
- "Thanks [Name]. Here's the next one:"
- "Okay, let's keep going —"
- "Got that. So —"

Then ask the next question provided in the prompt.

→ No tag needed here (system handles advancement).
──────────────────────────────

---

ABSOLUTE RULES — NEVER BREAK THESE

1. Never reveal the answer. Never. Not even a fragment.
2. Never give feedback — no "good", "correct", "close", "not quite", nothing.
3. Never hint at what a good answer looks like.
4. Never break character as Emma.
5. Never output more than one control tag per response.
6. Never put the tag anywhere except the very end.
7. Never mention the tags to the candidate — they don't exist in the conversation.
8. Keep every response short. Real speech is short.

Valid tags: [[END_INTERVIEW]] | [[REPEAT]] | [[PREVIOUS]] | [[JUMP:X]]"""

    user_content = f"""CURRENT INTERVIEW STATE

Previous question asked: "{prev}"
Candidate's response: "{user_ans}"
Next question: "{next_q_prompt}" """

    return system_instruction, user_content


# ──────────────────────────────────────────────────────────────────────────────
# Unified WebSocket Endpoint (Live Interview Stream)
# ──────────────────────────────────────────────────────────────────────────────

async def sarvam_tts_stream(text_segment: str, websocket: WebSocket):
    if not SARVAM_KEY or not text_segment.strip():
        return

    for attempt in range(1, 4):
        try:
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
            resp = await http_client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": SARVAM_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code == 200:
                data = resp.json()
                if "audios" in data and data["audios"]:
                    audio_bytes = base64.b64decode(data["audios"][0])
                    # Send binary audio chunk to client
                    await websocket.send_bytes(audio_bytes)
                    brain.record("app_ws", "tts", f"Sent audio bytes for '{{text_segment[:20]}}...'", "ok")
                    return
        except Exception as e:
            brain.record("app_ws", "tts_err", str(e), "error")
            await asyncio.sleep(0.5)

@app.websocket("/ws/v1/interview-stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    brain.record("app_ws", "connect", "Unified engine connected", "info")
    
    try:
        while True:
            # Client sends JSON payloads instructing the engine what to process
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            if payload.get("action") == "ask":
                prev = payload.get("prev", "")
                transcript_text = payload.get("transcript", "")
                next_q = payload.get("nextQ", "")
                
                brain.record("app_ws", "ask", f"Evaluating answer length: {len(transcript_text)}", "info")
                system_inst, user_cont = get_llm_prompts(prev, transcript_text, next_q)
                
                openai_payload = {
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_inst},
                        {"role": "user", "content": user_cont}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 150,
                    "stream": True
                }
                
                # Stream from OpenAI and pipe to Sarvam TTS
                buffer: str = ""
                async with http_client.stream(
                    "POST", "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
                    json=openai_payload,
                    timeout=30.0
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        await websocket.send_json({"type": "error", "msg": f"OpenAI error {resp.status_code}"})
                        continue
                        
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line.strip() != "data: [DONE]":
                            try:
                                oai_data = json.loads(line[6:])
                                content = oai_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    buffer += content
                                    # Stream textual tokens down to client for rendering UI text
                                    await websocket.send_json({"type": "token", "text": content})
                                    
                                    # Sentence chunking for TTS streaming
                                    if any(p in content for p in [".", "?", "!", "\n"]):
                                        sentence = buffer.strip()
                                        buffer = ""
                                        if sentence:
                                            clean_sentence = re.sub(r'\[\[.*?\]\]', '', sentence).strip()
                                            if clean_sentence:
                                                await sarvam_tts_stream(clean_sentence, websocket)
                            except Exception:
                                pass

                    # Flush the remaining buffer
                    if buffer.strip():
                        clean_sentence = re.sub(r'\[\[.*?\]\]', '', buffer.strip()).strip()
                        if clean_sentence:
                            await sarvam_tts_stream(clean_sentence, websocket)
                            
                # Let frontend know OpenAI is done replying for this turn!
                await websocket.send_json({"type": "control", "action": "done"})

    except WebSocketDisconnect:
        brain.record("app_ws", "disconnect", "Client disconnected", "info")
    except Exception as e:
        brain.record("app_ws", "error", str(e), "error")
        print(f"WS Exception: {e}")

# Command to run:
# uvicorn app:app --port 3000 --reload
