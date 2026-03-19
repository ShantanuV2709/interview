# Artificial Intelligence Interview Agent - API Integration & Microservice Report

**Date:** March 19, 2026

This document outlines the refactored system architecture of the standalone AI Interviewer microservice, the step-by-step instructions for integrating it into the `hr-solution` frontend, the potential risks to mitigate, and a comprehensive summary of the system improvements implemented today.

## 1. Integration Blueprint

**The Pre-Interview Stage**
Before the candidate even joins the screen, your `hr-solution` backend or database will trigger a request to `POST /api/v1/generate-questions` with the Job Description. You will save those 6 JSON questions into your database for that specific interview session.

**The Live Interview Loop**
Once the user clicks "Start Interview" in your front-end (React/Next.js), you bypass Retell entirely using this flow:
* **The Microphone:** Let the frontend record the candidate's voice natively (using `MediaRecorder`).
* **Transcription:** When the candidate stops talking, the frontend shoots that audio completely independently to `POST /api/v1/stt` to fetch the transcribed text string.
* **The Brain (WebSocket):** The frontend opens a persistent connection to `ws://[YOUR_SERVER_IP]:3000/ws/v1/interview-stream`. As soon as the frontend gets the STT string, it passes a JSON payload into the socket containing the text and the current question.
* **The Response:** The WebSocket instantly spits back a stream of tokens (for typing out the UI transcript) and tightly packed MP3 Audio arrays (for the browser to play out loud seamlessly). 

**The Verdict**
When "Emma" signals `[[END_INTERVIEW]]`, your frontend drops the WebSocket connection, compiles the 6 questions and 6 transcribed answers into a single array, and posts it to `POST /api/v1/score` to generate the final candidate report card!

---

## 2. Implementation Risks & Gotchas

While this custom architecture eliminates massive Retell subscription costs, it introduces system-level responsibilities for the `hr-solution` frontend. 

1. **WebSocket Dropouts (Connection Risk)**
   WebSockets are fragile. If a candidate's internet flickers for even 2 seconds on a train, the Socket will instantly drop. Your frontend **must** have robust `.onclose` auto-reconnection logic. If it snaps, the UI needs to silently re-establish the socket and pass Emma the exact same question she was on so the candidate doesn't notice.
2. **Concurrency Rate-Limiting (Scaling Risk)**
   Since you are chaining OpenAI (`gpt-4o`) directly into Sarvam (`bulbul:v2`), you are strictly subject to their API rate limits. If you have 50 candidates doing an interview strictly at the same minute, Sarvam or OpenAI might throw a `429 Too Many Requests` error, causing Emma to go completely silent. You need to verify your tier limits before scaling.
3. **CORS & Domain Hijacking (Security Risk)**
   Right now, the FastAPI app natively accepts cross-origin requests (`allow_origins=["*"]`). This is perfect for local testing, but it fundamentally means *any* website in the world could secretly connect to your API and use your expensive Sarvam/Deepgram credits for free! Before deploying this to production, you must explicitly change `"*"` to `"https://your-main-domain.com"` in `app.py`.
4. **Apple iOS Autoplay Restrictions (Browser Risk)**
   iPhones strictly block `.mp3` browser audio from "auto-playing" unless the user explicitly taps a button first. Your React frontend must have a giant "Click here to Begin the Interview" button that fires a silent, blank `<audio>` track the very millisecond they click it, thus "unlocking" the speakers for Emma to talk freely afterwards.

---

## 3. Session Report: What We Accomplished Today

Today's session focused on dramatically increasing the latency, human-likeness, predictability, and scalability of the Interview Agent, transforming it from a brittle HTML prototype into a fully headless, portable Microservice. 

### Phase 1: Conversation & Agent Engineering
* **Prompt Overhaul:** Completely rewrote "Emma's" system prompt. Forced her to use contractions, natural breathing breaks, and completely banned generic robotic affirmations ("Great question!", "Certainly!"). She now explicitly handles edge cases like candidates begging for hints or answers, and politely deflects them.
* **Interruption UX fix:** We increased the VAD (Voice Activity Detection) threshold to `2500ms`. The agent used to aggressively cut candidates off if they took a breath mid-sentence. Now, it respects natural human speaking pauses.
* **The Scoring Engine Hotfix:** Discovered and patched a massive algorithmic bug in the final HR Transcript aggregator where retried answers wiped the `storedTranscripts` array structure, resulting in perfect interviews scoring a `0`. The algorithm now surgically targets the final index length of the attempt array.

### Phase 2: Latency Demolition
* **Zero-Chunking Pipeline:** We eliminated a convoluted and incredibly slow STT strategy that was slicing candidate audio into PCM buffered HTTP chunks. By rewiring the `transcribeAnswer` function to pipe the direct blob straight to Deepgram, we collapsed processing time from 3-5 seconds to essentially instant transcription.

### Phase 3: The Microservice Migration
Because integrating the static `sarvam_demo` package statically into the main `hr-solutions` hub was fundamentally unfeasible, we completed a total surgical extraction of the system.
* **Headless API Engine:** Built a completely decoupled `FastAPI` instance (`app.py`), shifting the entire architecture from scattered proxy nodes to a unified backend.
* **REST Ecosystem:** Deployed 5 standalone `POST` endpoints (`/api/v1/generate-questions`, `/stt`, `/tts`, `/llm-turn`, `/score`).
* **Instant Swagger Documentation:** Injected strict Data Validation (`pydantic Models`) into the endpoints to automatically generate an interactive visual testing suite at `http://localhost:3000/docs`.
* **The Retell Replacement:** Collapsed `ws_server.py` into a highly optimized WebSocket stream endpoint (`/ws/v1/interview-stream`).
* **Quality of Life Debugging Cache:** Built an in-memory byte caching layer intercepting the `/api/v1/tts` endpoint to instantly bypass a known Swagger UI bug, allowing engineers to natively stream POST-generated MP3 fragments perfectly in the browser.
