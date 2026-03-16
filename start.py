#!/usr/bin/env python3
"""
Sarvam Interview Demo — Local Server + API Proxy
-------------------------------------------------
Run:  python3 start.py
Then: open http://localhost:3000 in your browser

Keys are read from .env in the same folder:
    SARVAM_AI_API=your_sarvam_key_here
    OPENAI_API_KEY=your_openai_key_here
"""

import http.server
import httpx
import json
import sys
import socketserver
import threading
from pathlib import Path
from typing import Any

PORT      = 3000
HTML_FILE = "sarvam_demo.html"

OPENAI_BASE   = "https://api.openai.com"
SARVAM_BASE   = "https://api.sarvam.ai"
DEEPGRAM_BASE = "https://api.deepgram.com"

# Sarvam's Azure gateway returns 403 to bare Python — needs a browser-like User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Load API keys from .env ──────────────────────────────────────────────────
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("  WARN  .env not found — create one with SARVAM_AI_API and OPENAI_API_KEY")
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env

_ENV          = load_env()
SARVAM_KEY    = _ENV.get("SARVAM_AI_API", "")
OPENAI_KEY    = _ENV.get("OPENAI_API_KEY", "")
DEEPGRAM_KEY  = _ENV.get("DEEPGRAM_API_KEY", "")

# Persistent client to avoid repeated DNS/Handshake latency
HTTP_CLIENT = httpx.Client(timeout=60.0, follow_redirects=True)

# Warm up DNS cache and TLS session for Deepgram
def warmup_deepgram():
    try:
        # HEAD request primes the connection pool/DNS/TLS
        HTTP_CLIENT.head("https://api.deepgram.com/v1/speak", timeout=5.0)
        print("  [WARMUP] Deepgram TTS endpoint primed.")
        HTTP_CLIENT.head("https://api.deepgram.com/v1/listen", timeout=5.0)
        print("  [WARMUP] Deepgram STT endpoint primed.")
    except Exception as e:
        print(f"  [WARMUP] Error during Deepgram warmup: {e}")

threading.Thread(target=warmup_deepgram, daemon=True).start()

# ── Request handler ──────────────────────────────────────────────────────────
class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        """Override to prevent crashes when send_error passes int args."""
        try:
            first = args[0] if args else None
            if not isinstance(first, str):
                return
            parts  = first.split()
            method = parts[0] if len(parts) > 0 else "?"
            path   = parts[1] if len(parts) > 1 else "?"
            status = str(args[1]) if len(args) > 1 else "?"
            if path in ("/favicon.ico", "/robots.txt"):
                return
            icon = ">> PROXY" if "/proxy/" in path else "   PAGE "
            print(f"  {icon}  {method} {path} -> {status}")
        except Exception:
            pass

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, api-subscription-key",
        )

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def _respond(self, code: int, ctype: str, body: bytes) -> None:
        """Send a complete response without ever calling send_error."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/favicon.ico", "/robots.txt"):
            self._respond(204, "text/plain", b"")
            return

        if self.path == "/deepgram-key":
            body = json.dumps({"key": DEEPGRAM_KEY}).encode()
            self._respond(200, "application/json", body)
            return

        if self.path in ("/", f"/{HTML_FILE}"):
            html_path = Path(__file__).parent / HTML_FILE
            if not html_path.exists():
                self._respond(404, "text/plain",
                    f"ERROR: {HTML_FILE} not found next to start.py".encode())
                return
            self._respond(200, "text/html; charset=utf-8", html_path.read_bytes())
            return

        self._respond(404, "text/plain", b"Not found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""
        ctype  = str(self.headers.get("Content-Type", ""))

        if self.path.startswith("/proxy/openai/"):
            if not OPENAI_KEY:
                self._respond(500, "application/json",
                    b'{"error":{"message":"OPENAI_API_KEY not set in .env"}}')
                return
            target = OPENAI_BASE + self.path[len("/proxy/openai"):]
            hdrs: dict[str, str] = {
                "Content-Type":  ctype,
                "Authorization": f"Bearer {OPENAI_KEY}",
                "User-Agent":    USER_AGENT,
            }
            self._forward(target, body, hdrs)

        elif self.path.startswith("/proxy/sarvam/"):
            if not SARVAM_KEY:
                self._respond(500, "application/json",
                    b'{"error":{"message":"SARVAM_AI_API not set in .env"}}')
                return
            target = SARVAM_BASE + self.path[len("/proxy/sarvam"):]
            hdrs = {
                "api-subscription-key": SARVAM_KEY,
                "User-Agent":           USER_AGENT,
            }
            if ctype:
                hdrs["Content-Type"] = ctype
            if "multipart" not in ctype and len(body) < 2000:
                print(f"  DBG   {self.path}  {body.decode(errors='replace')[:300]}")
            self._forward(target, body, hdrs)

        elif self.path.startswith("/proxy/deepgram/"):
            if not DEEPGRAM_KEY:
                self._respond(500, "application/json",
                    b'{"error":{"message":"DEEPGRAM_API_KEY not set in .env"}}')
                return
            target = DEEPGRAM_BASE + self.path[len("/proxy/deepgram"):]
            hdrs = {
                "Authorization": f"Token {DEEPGRAM_KEY}",
                "User-Agent":    USER_AGENT,
            }
            if ctype:
                hdrs["Content-Type"] = ctype
            self._forward(target, body, hdrs)

        else:
            self._respond(404, "text/plain", b"Unknown proxy route")

    def _forward(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        try:
            resp = HTTP_CLIENT.post(url, content=body, headers=headers)
            resp_body = resp.content
            resp_code = resp.status_code
            resp_ctype = resp.headers.get("Content-Type", "application/json")
            self._respond(resp_code, resp_ctype, resp_body)

        except httpx.HTTPStatusError as e:
            err_body = e.response.content
            print(f"  ERROR  HTTP {e.response.status_code} from {url}")
            print(f"         {err_body[:500].decode(errors='replace')}")
            self._respond(e.response.status_code, "application/json", err_body)

        except Exception as ex:
            msg = json.dumps({"error": str(ex), "type": type(ex).__name__}).encode()
            print(f"  ERROR  Proxy exception: {ex}")
            self._respond(502, "application/json", msg)

# ── Server ───────────────────────────────────────────────────────────────────
class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def main() -> None:
    if not (Path(__file__).parent / HTML_FILE).exists():
        print(f"\nERROR: {HTML_FILE} not found in the same folder as start.py\n")
        sys.exit(1)

    key_status = []
    key_status.append(f"  Sarvam key   : {'SET ✓' if SARVAM_KEY else 'NOT SET — add SARVAM_AI_API to .env'}")
    key_status.append(f"  OpenAI key   : {'SET ✓' if OPENAI_KEY else 'NOT SET — add OPENAI_API_KEY to .env'}")
    key_status.append(f"  Deepgram key : {'SET ✓' if DEEPGRAM_KEY else 'NOT SET — add DEEPGRAM_API_KEY to .env'}")

    print(f"""
Sarvam Interview Demo
======================
  http://localhost:{PORT}   <-- open in browser
  Ctrl+C to stop

API Keys (from .env):
{''.join(chr(10) + s for s in key_status)}

Proxy routes:
  /proxy/openai/*   -> api.openai.com
  /proxy/sarvam/*   -> api.sarvam.ai
  /proxy/deepgram/* -> api.deepgram.com
""")

    with ReusableTCPServer(("", PORT), ProxyHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.\n")

if __name__ == "__main__":
    main()