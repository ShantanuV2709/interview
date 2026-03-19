# Sarvam Interview Demo

A conversational AI tech interviewer demonstration using OpenAI (LLM), Sarvam AI (TTS), and Deepgram (STT). This project features a local HTTP proxy server and a WebSocket-based logic server to handle real-time streaming of audio and text.

## Features

- **Real-time Conversational AI**: Tech interviewer powered by OpenAI's GPT-4o.
- **Multilingual TTS**: Natural-sounding Indian English speech using Sarvam's Bulbul model.
- **Robust STT**: Low-latency Speech-to-Text using Deepgram's Nova-2 model.
- **Proxy Architecture**: Securely handles API requests through a local server to protect keys.
- **WebSocket Streaming**: Optimized for real-time audio and text transmission.
- **Admin Dashboard**: Hardcoded admin login for secure access.

## Prerequisites

- **Python 3.x**
- API Keys for:
  - [OpenAI](https://platform.openai.com/)
  - [Sarvam AI](https://www.sarvam.ai/)
  - [Deepgram](https://developers.deepgram.com/)

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd interview
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   Create a `.env` file in the `interview/` directory with the following variables:
   ```env
   SARVAM_AI_API=your_sarvam_key_here
   OPENAI_API_KEY=your_openai_key_here
   DEEPGRAM_API_KEY=your_deepgram_key_here
   STATIC_USERNAME=admin
   STATIC_PASSWORD=sarvam123
   ```

## Running the Project

### Using the Shell Script (Recommended)
This starts both the Logic Server (WebSocket) and the HTTP Proxy Server.
```bash
bash run.sh
```

### Manual Start
If you prefer to start the servers separately:

1. **Start the WebSocket Logic Server**:
   ```bash
   python3 ws_server.py
   ```
   *Running on ws://localhost:3002*

2. **Start the HTTP Proxy Server**:
   ```bash
   python3 start.py
   ```
   *Running on http://localhost:3000*

3. **Open in Browser**:
   Visit `http://localhost:3000` to start the interview demo.

## Docker Setup

Alternatively, you can run the project using Docker and Docker Compose:

```bash
docker-compose up --build
```

The application will be accessible at `http://localhost:3000`.

## Project Structure

- `start.py`: HTTP Proxy server (handles frontend serving and API routing).
- `ws_server.py`: WebSocket server (handles LLM streaming, TTS synthesis, and STT proxying).
- `sarvam_demo.html`: The main frontend application.
- `login.html`: Admin login page.
- `openai_brain.py`: Logic for recording and analyzing interview metrics.
- `run.sh`: Convenience script to launch the full application.

## Troubleshooting

- **.env not found**: Ensure the `.env` file is in the same directory as `start.py`.
- **Port Conflict**: Ports 3000 and 3002 must be available.
- **API Errors**: Verify your API keys in the `.env` file and check the logs (`ws_server.log`).

---
*Built for the Sarvam AI Interview Demo.*
