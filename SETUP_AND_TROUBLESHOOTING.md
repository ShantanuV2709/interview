# Setup and Troubleshooting Guide (0 to 100)

This guide provides a comprehensive, step-by-step walkthrough for setting up the Sarvam Interview Demo project from scratch and resolving common issues.

---

## Part 1: Initial Setup (From 0)

### 1. Environment Requirements
- **Operating System**: Linux (recommended), macOS, or Windows (WSL2 recommended).
- **Python**: Version 3.8 or higher.
- **Node.js (Optional)**: Only if you plan to modify frontend build processes (not required for the current vanilla JS/HTML setup).

### 2. Install Python and Pip
If you don't have Python installed:
- **Ubuntu/Debian**:
  ```bash
  sudo apt update
  sudo apt install python3 python3-pip
  ```
- **macOS**:
  ```bash
  brew install python
  ```

### 3. Clone and Prepare the Repository
```bash
git clone <your-repository-url>
cd interview
```

### 4. Virtual Environment (Recommended)
Isolate dependencies to avoid system-wide conflicts:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 5. Install Dependencies
```bash
pip install -r requirements.txt
```
*Dependencies: `httpx`, `websockets`, `python-dotenv`*

---

## Part 2: API Configuration

You need active API keys from three providers.

### 1. Obtain Keys
- **OpenAI**: [platform.openai.com](https://platform.openai.com/api-keys)
- **Sarvam AI**: [sarvam.ai](https://www.sarvam.ai/dashboard)
- **Deepgram**: [console.deepgram.com](https://console.deepgram.com/)

### 2. Configure `.env`
Create a file named `.env` in the `interview/` directory:
```env
# API Keys
SARVAM_AI_API=sa_...
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...

# Admin Credentials
STATIC_USERNAME=admin
STATIC_PASSWORD=sarvam123
```

---

## Part 3: Running the Application

### The "All-in-One" Way
Run the shell script:
```bash
chmod +x run.sh
./run.sh
```

### The Manual Way (Two Terminals)
**Terminal 1 (Logic Server):**
```bash
python3 ws_server.py
```
**Terminal 2 (HTTP Proxy):**
```bash
python3 start.py
```

---

## Part 4: Troubleshooting Guide

### 1. Server Connectivity Issues

| Issue | Potential Cause | Resolution |
| :--- | :--- | :--- |
| `Address already in use` | Another process is using port 3000 or 3002. | Run `lsof -i :3000` and `kill -9 <PID>` to free the port. |
| `.env not found` | File is missing or in the wrong directory. | Ensure `.env` is inside the `interview/` folder. |
| `ImportError` | Missing dependencies. | Run `pip install -r requirements.txt` again. |

### 2. API & Authentication Failures

| Error | Cause | Resolution |
| :--- | :--- | :--- |
| `401 Unauthorized` | Invalid or expired API Key. | Double-check keys in `.env`. Ensure no extra spaces. |
| `429 Too Many Requests` | Rate limit or quota exceeded. | Check your billing status and usage limits on the provider's dashboard. |
| `502 Bad Gateway` | Proxy failed to connect to the target API. | Check your internet connection or the provider's status page. |

### 3. Frontend & WebSocket Issues

| Issue | Potential Cause | Resolution |
| :--- | :--- | :--- |
| "Connection Refused" (WS) | `ws_server.py` is not running. | Ensure Terminal 1 (WebSocket server) is active. |
| "Microphone not found" | Browser permissions blocked. | Click the "lock" icon in the address bar and allow microphone access. |
| "Static Login Failed" | Incorrect username/password. | Check `STATIC_USERNAME` and `STATIC_PASSWORD` in `.env`. |

### 4. Audio Processing Issues

| Issue | Potential Cause | Resolution |
| :--- | :--- | :--- |
| Audio lag/stutter | High latency or local network congestion. | Use a stable internet connection. Restart the servers. |
| No transcription | Deepgram API key missing or invalid. | Check `DEEPGRAM_API_KEY` in `.env`. |
| TTS not playing | Sarvam AI key missing or credit limit hit. | Check `SARVAM_AI_API` and Sarvam AI dashboard. |

---

## Part 5: Advanced Optimization

- **Port Mapping**: If you need to change ports, update `PORT` in `start.py` and the `websockets.serve` call in `ws_server.py`. Remember to also update the WebSocket URL in `sarvam_demo.html`.
- **Docker**: For a clean setup without worrying about local Python installation, use:
  ```bash
  docker-compose up --build
  ```

---
*Still facing issues? Check `ws_server.log` for detailed error tracebacks.*
