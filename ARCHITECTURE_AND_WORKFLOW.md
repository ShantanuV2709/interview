# System Architecture and Workflow

This document details the internal architecture of the Sarvam Interview Demo, explaining how components interact and the sequence of API calls during a live interview session.

---

## 1. High-Level Architecture Overview

The system follows a **Proxy-Backend-Frontend** architecture to ensure API keys remain secure on the server side while providing a low-latency, real-time experience in the browser.

```mermaid
graph TD
    User((User / Browser))
    
    subgraph "Local Server"
        Proxy[HTTP Proxy Server: start.py]
        WS[WebSocket Logic Server: ws_server.py]
        Brain[OpenAI Brain: openai_brain.py]
    end
    
    subgraph "External APIs"
        OpenAI[OpenAI: GPT-4o]
        Sarvam[Sarvam AI: Bulbul TTS]
        Deepgram[Deepgram: Nova-2 STT]
    end

    User -- HTTP GET/POST --> Proxy
    User -- WebSocket Binary/JSON --> WS
    WS -- RPC/Import --> Brain
    
    WS -- Streaming HTTPS --> OpenAI
    WS -- HTTPS POST --> Sarvam
    WS -- WebSocket Proxy --> Deepgram
```

---

## 2. Component Roles

### 🔘 HTTP Proxy Server (`start.py`)
- **Static File Serving**: Serves `sarvam_demo.html`, `login.html`, and CSS/JS assets.
- **Authentication**: Manages session cookies and the hardcoded admin login flow.
- **Security**: Acts as a gateway to the external APIs, injecting keys securely.

### 🔘 WebSocket Logic Server (`ws_server.py`)
- **Real-time Hub**: The central engine for the interview session.
- **STT Proxy**: Pipes raw audio bytes from the user's microphone directly to Deepgram and sends back the transcription.
- **LLM Orchestrator**: Sends user transcripts to OpenAI and manages the streaming response.
- **TTS Generator**: Detects complete sentences from the LLM stream and triggers Sarvam AI to generate speech.

### 🔘 OpenAI Brain (`openai_brain.py`)
- **Instrumentation**: Records every step (latency, errors, success) of the interview process.
- **Analysis**: Performs periodic analysis of the interview progress.

---

## 3. Data Flow & API Call Sequence

### Phase 1: Authentication & Loading
1. User visits `localhost:3000`.
2. `start.py` checks for a session cookie. If missing, redirects to `/login`.
3. User enters credentials -> `start.py` validates and sets a `session_id` cookie.
4. User accesses `sarvam_demo.html`.

### Phase 2: Speech-to-Text (STT) Workflow
1. User clicks "Start Speaking".
2. **Frontend**: Captures audio using Web Audio API and sends raw binary chunks to `ws://localhost:3002`.
3. **WS Server**: Receives binary chunks and forwards them immediately to **Deepgram's WebSocket API**.
4. **Deepgram**: Processes audio and sends back JSON transcription in real-time.
5. **WS Server**: Forwards transcription JSON to the **Frontend** via the same WebSocket.

### Phase 3: Conversational Logic & TTS Workflow
1. User submits their response.
2. **Frontend** sends `action: "ask"` with the cumulative transcript to the **WS Server**.
3. **WS Server** calls **OpenAI Chat Completions API** with `stream=True`.
4. **OpenAI**: Begins streaming text tokens back.
5. **WS Server**:
   - **Forwarding Tokens**: Sends raw tokens to the **Frontend** for real-time text display.
   - **Sentence Detection**: Buffers tokens until a punctuation mark (. ! ?) is found.
   - **TTS Request**: Sends the completed sentence to **Sarvam AI TTS API**.
6. **Sarvam AI**: Returns a base64-encoded MP3 file.
7. **WS Server**: Decodes and sends raw binary audio to the **Frontend**.
8. **Frontend**: Queues and plays the received audio chunks using a blob buffer.

---

## 4. Port Configuration

- **Port 3000 (HTTP)**: User-facing web interface.
- **Port 3002 (WebSocket)**: Internal communication between Frontend and Backend Logic.

---

## 5. Security & Key Management

All API keys are stored in the server-side `.env` file and **never** exposed to the browser. The `start.py` and `ws_server.py` processes act as the only entities authorized to communicate with OpenAI, Sarvam, and Deepgram.

---
*For setup instructions, refer to [README.md](README.md) and [SETUP_AND_TROUBLESHOOTING.md](SETUP_AND_TROUBLESHOOTING.md).*
